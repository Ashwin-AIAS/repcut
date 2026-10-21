"""The intent eval: canned requests, the calls they should become, a score.

Each intent in the fixture names the request, what a correct turn does
(``expect``: the outcome, and for changes the operations in order with the
arguments that matter), and a ``script`` - what a model answered, for the
replay run the gate uses (no network). ``--live`` in ``scripts/copilot_eval.py``
sends the same requests to the real model instead and judges it by the same
``expect``; that run spends free-tier requests and is a real-machine check.

Arguments in the fixture may name things by position - ``{cut:3}``,
``{clip:1}``, ``{hero:1}``, ``{word:1}``, ``{track:1}``, ``{hook:other}`` -
resolved against the edit as it stands when that intent runs, so an earlier
intent that removed a cut is accounted for.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.captions import service as captions_service
from repcut.copilot import agent, principles
from repcut.db.models import CopilotUnmetRequest
from repcut.edit import service as edit_service

_REF = re.compile(r"^\{(cut|clip|hero|word|track|hook):([0-9]+|other)\}$")
PASS_RATE = 0.9


@dataclass(frozen=True, slots=True)
class Intent:
    id: str
    kind: str  # single, multi, ambiguous, principle, impossible
    text: str
    expect: Mapping[str, Any]
    script: Sequence[Sequence[Mapping[str, Any]]]


@dataclass
class Verdict:
    intent: Intent
    reply: agent.Reply
    calls: list[tuple[str, dict[str, Any]]]
    expected: list[tuple[str, dict[str, Any]]]
    correct: bool
    why: str = ""


@dataclass
class Result:
    verdicts: list[Verdict] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return sum(v.correct for v in self.verdicts) / max(1, len(self.verdicts))

    def of(self, kind: str) -> list[Verdict]:
        return [v for v in self.verdicts if v.intent.kind == kind]


def load(path: Path) -> list[Intent]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        Intent(i["id"], i["kind"], i["text"], i["expect"], i.get("script", []))
        for i in raw["intents"]
    ]


class RecordingModel:
    """Wraps a model and keeps every function call it answered with."""

    def __init__(self, inner: agent.Model) -> None:
        self.inner = inner
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def generate(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        response = await self.inner.generate(request)
        for part in _parts(response):
            call = part.get("functionCall")
            if isinstance(call, Mapping):
                args = call.get("args")
                self.calls.append(
                    (str(call.get("name", "")), dict(args) if isinstance(args, Mapping) else {})
                )
        return response


def _parts(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    try:
        parts = response["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        return []
    return [p for p in parts if isinstance(p, Mapping)]


class ReplayModel:
    """Answers with an intent's scripted steps, then a closing sentence."""

    def __init__(self, steps: Sequence[Sequence[Mapping[str, Any]]]) -> None:
        self.steps = [list(s) for s in steps]

    async def generate(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self.steps:
            return {"candidates": [{"content": {"parts": [{"text": "Done."}]}}]}
        step = self.steps.pop(0)
        return {"candidates": [{"content": {"parts": [{"functionCall": c} for c in step]}}]}


async def _refs(session: AsyncSession, data_dir: Path, project_id: str) -> dict[str, str]:
    state = await edit_service.current(session, data_dir, project_id)
    clips = await edit_service.project_clips(session, project_id)
    captions = await captions_service.state(session, data_dir, project_id, edit=state)
    refs: dict[str, str] = {}
    refs.update({f"cut:{i + 1}": s.id for i, s in enumerate(state.plan.shots)})
    refs.update({f"clip:{i + 1}": c.id for i, c in enumerate(clips)})
    refs.update({f"hero:{i + 1}": h.segment_id for i, h in enumerate(state.heroes)})
    words = [w for line in captions.lines for w in line.words]
    refs.update({f"word:{i + 1}": w.id for i, w in enumerate(words)})
    refs.update({f"track:{i + 1}": r.sha256[:8] for i, r in enumerate(state.recommendations)})
    current = state.hook.segment_id if state.hook else None
    others = [c.segment_id for c in state.hook_candidates if c.segment_id != current]
    if others:
        refs["hook:other"] = others[0]
    return refs


def _resolve(value: object, refs: Mapping[str, str]) -> object:
    if isinstance(value, str):
        match = _REF.match(value)
        if match:
            return refs.get(f"{match.group(1)}:{match.group(2)}", value)
        return value
    if isinstance(value, Mapping):
        return {k: _resolve(v, refs) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, refs) for v in value]
    return value


def _args(value: object) -> dict[str, Any]:
    return {str(k): v for k, v in value.items()} if isinstance(value, Mapping) else {}


def _same(expected: object, actual: object) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return abs(float(expected) - float(actual)) < 1e-6
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.lower() == actual.lower()
    return bool(expected == actual)


def _matches(
    expected: Sequence[tuple[str, Mapping[str, Any]]],
    calls: Sequence[tuple[str, Mapping[str, Any]]],
) -> bool:
    if [n for n, _ in expected] != [n for n, _ in calls]:
        return False
    return all(
        all(_same(v, args.get(k)) for k, v in want.items())
        for (_, want), (_, args) in zip(expected, calls, strict=True)
    )


async def _unmet_count(session: AsyncSession, project_id: str, kind: str) -> int:
    statement = select(CopilotUnmetRequest).where(
        CopilotUnmetRequest.project_id == project_id, CopilotUnmetRequest.kind == kind
    )
    return len(list((await session.execute(statement)).scalars()))


async def judge(
    session: AsyncSession,
    data_dir: Path,
    project_id: str,
    intent: Intent,
    model: agent.Model | None = None,
) -> Verdict:
    """Run one intent against the project and judge the turn.

    With no ``model``, the intent's own script answers (the replay run).
    """
    refs = await _refs(session, data_dir, project_id)
    if model is None:
        steps = [[_args(_resolve(c, refs)) for c in step] for step in intent.script]
        model = ReplayModel(steps)
    expected = [
        (str(op["name"]), _args(_resolve(op.get("args", {}), refs)))
        for op in intent.expect.get("operations", [])
    ]
    unsupported_before = await _unmet_count(session, project_id, "unsupported")
    recorder = RecordingModel(model)
    reply = await agent.respond(session, data_dir, project_id, intent.text, recorder)
    # Only the operations a turn ran (the speech acts are judged by outcome).
    calls = [(n, a) for n, a in recorder.calls if n not in agent.SPEECH_ACTS]
    outcome = str(intent.expect["outcome"])
    why = ""
    correct = reply.outcome == outcome
    if not correct:
        why = f"outcome {reply.outcome}, expected {outcome}"
    elif outcome == "done" and not _matches(expected, calls):
        correct, why = False, "the calls were not the expected sequence"
    elif outcome in ("asked", "refused", "unmet") and reply.actions:
        correct, why = False, "it changed the edit instead of only answering"
    elif outcome == "refused" and principles.PRINCIPLE not in reply.text.lower():
        correct, why = False, "the refusal does not name the principle"
    elif outcome == "unmet" and await _unmet_count(session, project_id, "unsupported") <= (
        unsupported_before
    ):
        correct, why = False, "no copilot_unmet_requests row was written"
    return Verdict(intent, reply, calls, expected, correct, why)


def transcript(result: Result) -> str:
    """The run as markdown: request, calls, reply, verdict."""
    lines = ["| # | kind | request | calls | reply | verdict |", "|---|---|---|---|---|---|"]
    for v in result.verdicts:
        calls = ", ".join(n for n, _ in v.calls) or "-"
        verdict = "ok" if v.correct else f"WRONG: {v.why}"
        text = v.reply.text.replace("|", "/")[:90]
        lines.append(
            f"| {v.intent.id} | {v.intent.kind} | {v.intent.text} | {calls} | {text} | {verdict} |"
        )
    lines.append("")
    lines.append(f"correct: {sum(v.correct for v in result.verdicts)} of {len(result.verdicts)}")
    return "\n".join(lines)


def failures(result: Result) -> list[str]:
    """What fails the eval criterion, in words; empty when it passes."""
    out: list[str] = []
    if result.rate < PASS_RATE:
        out.append(f"{result.rate:.0%} correct, below {PASS_RATE:.0%}")
    for kind, outcome in (
        ("ambiguous", "asked"),
        ("principle", "refused"),
        ("impossible", "unmet"),
    ):
        wrong = [
            v.intent.id for v in result.of(kind) if not v.correct or v.reply.outcome != outcome
        ]
        if wrong:
            out.append(f"{kind} intents not handled: {', '.join(wrong)}")
    return out


__all__ = [
    "PASS_RATE",
    "Intent",
    "RecordingModel",
    "ReplayModel",
    "Result",
    "Verdict",
    "failures",
    "judge",
    "load",
    "transcript",
]
