"""The copilot's loop: words in, registry operations out, a sentence back.

One turn:

1. The natural-only guard (``principles``) runs first. A request to generate,
   replace or reshape footage is refused with the principle named and logged
   as unmet - no model is asked.
2. The model gets Repcut's principles, a compact summary of this edit
   (``summary``), the last few lines of the conversation, and the function
   declarations of the allowlist plus three speech acts: ask a clarifying
   question, refuse on a principle, report something unsupported.
3. Each function call is checked against the allowlist and validated
   (``tools.validate``), then executed through the undo stack with
   ``source=copilot``. A validation error goes back to the model as the
   function's response, so it can correct itself or ask.
4. A call to anything outside the allowlist ends the turn: every change made
   earlier in the turn is undone, nothing more runs, and the call is logged
   (``copilot_unmet_requests``, kind ``blocked_call``). That is the prompt-
   injection answer: whatever a frame or a caption says, the model can only
   reach these operations, and trying anything else costs the whole turn.

The model is behind a small protocol so the gate can replay answers; the real
one is Gemini through the shared client, limiter and key handling.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.analysis import gemini_client
from repcut.analysis.cache import get_rate_limiter
from repcut.config import Settings
from repcut.copilot import history, principles, tools
from repcut.copilot import summary as summaries
from repcut.db.models import (
    CopilotMessage,
    CopilotRole,
    CopilotUnmetRequest,
    TasteSource,
    UnmetKind,
)
from repcut.logging import get_logger

logger = get_logger(__name__)

PROMPT_VERSION = 1
MAX_STEPS = 6
HISTORY_LINES = 6
MAX_REQUEST_CHARS = 500

Outcome = Literal["done", "asked", "refused", "unmet", "blocked", "unavailable", "said"]

ASK = "ask_clarifying_question"
REFUSE = "refuse_request"
UNSUPPORTED = "report_unsupported"
SPEECH_ACTS = (ASK, REFUSE, UNSUPPORTED)

NO_KEY = "The copilot is off: no Gemini key is configured. Every control still works."
NO_BUDGET = (
    "The copilot is paused: today's free Gemini budget is used up. Every control still works."
)
OFFLINE = "The copilot could not reach Gemini just now. Every control still works; try again later."
BLOCKED = (
    "I stopped there: that asked for an operation Repcut does not allow, so nothing was "
    "changed. It is logged."
)
UNSUPPORTED_REPLY = "I can't do that yet. I've noted it as a feature request."

# Engineered here, versioned by PROMPT_VERSION, quoted verbatim in the build report.
SYSTEM_PROMPT = (Path(__file__).parent / "system_prompt.txt").read_text(encoding="utf-8").strip()


class CopilotUnavailableError(RuntimeError):
    """The copilot cannot answer now. ``str()`` is a fixed sentence for the panel."""


class Model(Protocol):
    async def generate(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class Action:
    operation: str
    summary: str
    entry_id: str | None
    changed: bool

    def to_json(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "summary": self.summary,
            "entry_id": self.entry_id,
            "changed": self.changed,
        }


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    outcome: Outcome
    actions: tuple[Action, ...] = ()
    calls: tuple[str, ...] = field(default=())


# --- function declarations ---------------------------------------------------


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    """A pydantic schema reduced to the OpenAPI subset Gemini's declarations accept."""
    raw = model.model_json_schema()

    def reduce(node: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if "anyOf" in node:
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            out = reduce(options[0]) if options else {"type": "string"}
            out["nullable"] = True
            if "description" in node:
                out["description"] = node["description"]
            return out
        for key in ("type", "description", "enum", "minimum", "maximum"):
            if key in node:
                out[key] = node[key]
        if "const" in node:
            out["enum"] = [node["const"]]
        if node.get("type") == "object" or "properties" in node:
            out["type"] = "object"
            out["properties"] = {k: reduce(v) for k, v in node.get("properties", {}).items()}
            if node.get("required"):
                out["required"] = list(node["required"])
        return out

    reduced = reduce(raw)
    reduced.setdefault("type", "object")
    reduced.setdefault("properties", {})
    return reduced


def declarations() -> list[dict[str, Any]]:
    out = [
        {
            "name": name,
            "description": tools.REGISTRY[name].description,
            "parameters": _schema(tools.REGISTRY[name].args),
        }
        for name in tools.COPILOT_OPERATIONS
    ]
    out.extend(
        [
            {
                "name": ASK,
                "description": "Ask one short clarifying question instead of guessing.",
                "parameters": {
                    "type": "object",
                    "properties": {"question": {"type": "string"}},
                    "required": ["question"],
                },
            },
            {
                "name": REFUSE,
                "description": "Refuse a request that breaks one of Repcut's principles.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "principle": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["principle", "reason"],
                },
            },
            {
                "name": UNSUPPORTED,
                "description": "Say that no function can do this yet.",
                "parameters": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
            },
        ]
    )
    return out


ALLOWED: frozenset[str] = frozenset((*tools.COPILOT_OPERATIONS, *SPEECH_ACTS))


# --- the real model ----------------------------------------------------------


class GeminiModel:
    """Gemini through the shared client: key in a header, the shared limiter first."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    async def generate(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        settings = self._settings
        if settings.gemini_api_key is None or not settings.gemini_api_key_set:
            raise CopilotUnavailableError(NO_KEY)
        limiter = get_rate_limiter(settings)
        if not await limiter.try_acquire():
            raise CopilotUnavailableError(NO_BUDGET)
        client = self._client or httpx.AsyncClient()
        try:
            payload = await gemini_client.generate_content(
                request,
                api_key=settings.gemini_api_key.get_secret_value(),
                api_base=settings.gemini_api_base,
                client=client,
            )
        except gemini_client.GeminiAPIError as error:
            if error.quota.scope == "per_day":
                await limiter.exhaust_today()
                raise CopilotUnavailableError(NO_BUDGET) from error
            raise CopilotUnavailableError(OFFLINE) from error
        except gemini_client.GeminiTransportError as error:
            raise CopilotUnavailableError(OFFLINE) from error
        finally:
            if self._client is None:
                await client.aclose()
        logger.info("copilot_gemini_call", requests=1)
        return payload if isinstance(payload, Mapping) else {}


def availability(settings: Settings) -> str | None:
    """None when the copilot can be asked; else the sentence the panel shows."""
    if settings.gemini_api_key is None or not settings.gemini_api_key_set:
        return NO_KEY
    if not get_rate_limiter(settings).has_budget_today():
        return NO_BUDGET
    return None


# --- the loop ----------------------------------------------------------------


def _parts(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return []
    content = candidates[0].get("content") if isinstance(candidates[0], Mapping) else None
    parts = content.get("parts") if isinstance(content, Mapping) else None
    return [p for p in parts if isinstance(p, Mapping)] if isinstance(parts, list) else []


def _clean(text: str, limit: int = 400) -> str:
    return " ".join(text.split())[:limit]


async def _unmet(
    session: AsyncSession, project_id: str, kind: UnmetKind, request: str, detail: str | None
) -> None:
    session.add(
        CopilotUnmetRequest(
            project_id=project_id,
            kind=kind.value,
            request=_clean(request, MAX_REQUEST_CHARS),
            detail=None if detail is None else _clean(detail, 200),
        )
    )
    await session.commit()


async def _conversation(session: AsyncSession, project_id: str) -> list[dict[str, Any]]:
    statement = (
        select(CopilotMessage)
        .where(CopilotMessage.project_id == project_id)
        .order_by(CopilotMessage.created_at.desc(), CopilotMessage.id.desc())
        .limit(HISTORY_LINES)
    )
    rows = list((await session.execute(statement)).scalars())[::-1]
    return [
        {
            "role": "user" if r.role == CopilotRole.USER.value else "model",
            "parts": [{"text": r.text}],
        }
        for r in rows
    ]


async def _save(session: AsyncSession, project_id: str, text: str, reply: Reply) -> None:
    session.add(CopilotMessage(project_id=project_id, role=CopilotRole.USER.value, text=text))
    await session.flush()
    session.add(
        CopilotMessage(
            project_id=project_id,
            role=CopilotRole.ASSISTANT.value,
            text=reply.text,
            actions_json=json.dumps([a.to_json() for a in reply.actions]),
            outcome=reply.outcome,
        )
    )
    await session.commit()


def request_body(
    summary: str, conversation: Sequence[Mapping[str, Any]], turn: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "system_instruction": {"parts": [{"text": f"{SYSTEM_PROMPT}\n\nEDIT SUMMARY\n{summary}"}]},
        "contents": [*conversation, *turn],
        "tools": [{"function_declarations": declarations()}],
        "tool_config": {"function_calling_config": {"mode": "AUTO"}},
        "generation_config": {"temperature": 0},
    }


async def _rollback_turn(session: AsyncSession, project_id: str, actions: list[Action]) -> None:
    for action in reversed(actions):
        if action.changed and action.entry_id is not None and action.operation != "undo_last":
            try:
                await history.undo(session, project_id, action.entry_id)
            except history.UndoError:
                logger.warning("copilot_turn_rollback_conflict", operation=action.operation)


async def respond(
    session: AsyncSession,
    data_dir: Path,
    project_id: str,
    text: str,
    model: Model,
) -> Reply:
    """One turn of the conversation. Saves both lines; returns the reply."""
    text = _clean(text, MAX_REQUEST_CHARS)
    verdict = principles.check(text)
    if not verdict.allowed:
        await _unmet(session, project_id, UnmetKind.PRINCIPLE, text, verdict.rule)
        reply = Reply(text=principles.REFUSAL, outcome="refused", calls=(REFUSE,))
        await _save(session, project_id, text, reply)
        return reply

    summary = await summaries.build(session, data_dir, project_id)
    conversation = await _conversation(session, project_id)
    turn: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": text}]}]
    ctx = tools.ToolContext(session, data_dir, project_id, source=TasteSource.COPILOT.value)
    actions: list[Action] = []
    calls: list[str] = []
    said: list[str] = []
    errors: list[str] = []
    outcome: Outcome = "said"
    try:
        for _ in range(MAX_STEPS):
            response = await model.generate(request_body(summary.text, conversation, turn))
            parts = _parts(response)
            function_calls = [
                p["functionCall"] for p in parts if isinstance(p.get("functionCall"), Mapping)
            ]
            said.extend(str(p["text"]) for p in parts if isinstance(p.get("text"), str))
            if not function_calls:
                break
            turn.append({"role": "model", "parts": [{"functionCall": c} for c in function_calls]})
            responses: list[dict[str, Any]] = []
            for call in function_calls:
                name = str(call.get("name", ""))[:64]
                args = call.get("args") if isinstance(call.get("args"), Mapping) else {}
                calls.append(name)
                if name not in ALLOWED:
                    await _rollback_turn(session, project_id, actions)
                    await _unmet(session, project_id, UnmetKind.BLOCKED_CALL, text, name)
                    logger.warning("copilot_blocked_call", operation=_clean(name, 64))
                    reply = Reply(text=BLOCKED, outcome="blocked", calls=tuple(calls))
                    await _save(session, project_id, text, reply)
                    return reply
                if name == ASK:
                    question = (
                        _clean(str(args.get("question", "")), 300) or "Which one do you mean?"
                    )
                    reply = Reply(question, "asked", tuple(actions), tuple(calls))
                    await _save(session, project_id, text, reply)
                    return reply
                if name == REFUSE:
                    reason = _clean(str(args.get("reason", "")), 200)
                    await _unmet(session, project_id, UnmetKind.PRINCIPLE, text, reason)
                    reply = Reply(principles.REFUSAL, "refused", tuple(actions), tuple(calls))
                    await _save(session, project_id, text, reply)
                    return reply
                if name == UNSUPPORTED:
                    await _unmet(session, project_id, UnmetKind.UNSUPPORTED, text, None)
                    reply = Reply(UNSUPPORTED_REPLY, "unmet", tuple(actions), tuple(calls))
                    await _save(session, project_id, text, reply)
                    return reply
                try:
                    done = await tools.execute(ctx, name, args, copilot=True)
                except tools.ToolError as error:
                    errors.append(str(error))
                    responses.append(
                        {"functionResponse": {"name": name, "response": {"error": str(error)}}}
                    )
                    continue
                actions.append(Action(done.operation, done.summary, done.entry_id, done.changed))
                outcome = "done"
                responses.append(
                    {
                        "functionResponse": {
                            "name": name,
                            "response": {"ok": True, "changed": done.summary},
                        }
                    }
                )
            turn.append({"role": "user", "parts": responses})
            if actions:
                summary = await summaries.build(session, data_dir, project_id)
    except CopilotUnavailableError as error:
        reply = Reply(str(error), "unavailable", tuple(actions), tuple(calls))
        await _save(session, project_id, text, reply)
        return reply

    message = _clean(" ".join(said), 400) or (
        " ".join(a.summary for a in actions) if actions else "Nothing changed."
    )
    if not actions and errors:
        # Whatever the model said, nothing ran: say why, in the engine's words.
        message = _clean(f"Nothing changed: {errors[-1]}.", 400)
    reply = Reply(message, outcome, tuple(actions), tuple(calls))
    await _save(session, project_id, text, reply)
    return reply


__all__ = [
    "ALLOWED",
    "PROMPT_VERSION",
    "SPEECH_ACTS",
    "SYSTEM_PROMPT",
    "Action",
    "CopilotUnavailableError",
    "GeminiModel",
    "Model",
    "Reply",
    "availability",
    "declarations",
    "request_body",
    "respond",
]
