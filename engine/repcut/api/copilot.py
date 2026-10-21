"""Copilot and undo routes: the conversation, the change history, undo.

The copilot route answers with the reply and the changes it made, each one an
entry on the same undo stack the controls use; undo takes an entry id (a
change card's "undo") or none (the newest change). Offline, or with no key or
no budget, the copilot reports why and every other route is unaffected (P5).
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi import Path as PathParam
from fastapi.requests import HTTPConnection
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from repcut.api.deps import SessionDep, SettingsDep
from repcut.api.errors import (
    EditNotReadyApiError,
    ProjectNotFoundError,
    UndoUnavailableError,
)
from repcut.copilot import agent, history
from repcut.db.models import CopilotMessage, Project
from repcut.edit.service import EditNotReadyError

router = APIRouter(tags=["copilot"])

_UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
ProjectId = Annotated[str, PathParam(pattern=_UUID)]
MAX_MESSAGES = 100


class ActionResponse(BaseModel):
    operation: str
    summary: str
    entry_id: str | None
    changed: bool
    undone: bool = False


class MessageResponse(BaseModel):
    id: str
    role: str
    text: str
    outcome: str | None
    actions: list[ActionResponse]


class CopilotResponse(BaseModel):
    project_id: str
    available: bool
    reason: str | None = Field(description="Why the copilot is off; null when it is on")
    messages: list[MessageResponse]


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=agent.MAX_REQUEST_CHARS)


class ReplyResponse(BaseModel):
    reply: MessageResponse
    available: bool
    reason: str | None


class HistoryEntryResponse(BaseModel):
    id: str
    source: str
    operation: str
    summary: str
    undone: bool


class HistoryResponse(BaseModel):
    project_id: str
    entries: list[HistoryEntryResponse]


class UndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str | None = Field(default=None, pattern=_UUID)


class UndoResponse(BaseModel):
    entry_id: str
    operation: str
    summary: str


def _model(connection: HTTPConnection, settings: SettingsDep) -> agent.Model:
    injected: agent.Model | None = getattr(connection.app.state, "copilot_model", None)
    return injected or agent.GeminiModel(settings)


def _reason(connection: HTTPConnection, settings: SettingsDep) -> str | None:
    injected = getattr(connection.app.state, "copilot_model", None)
    if injected is not None:
        reason: str | None = getattr(injected, "unavailable_reason", None)
        return reason
    return agent.availability(settings)


async def _project(session: SessionDep, project_id: str) -> None:
    if await session.get(Project, project_id) is None:
        raise ProjectNotFoundError("no project with that id")


async def _undone(session: SessionDep, project_id: str) -> set[str]:
    return {e.id for e in await history.entries(session, project_id, 500) if e.undone}


def _message(row: CopilotMessage, undone: set[str]) -> MessageResponse:
    actions = json.loads(row.actions_json or "[]")
    return MessageResponse(
        id=row.id,
        role=row.role,
        text=row.text,
        outcome=row.outcome,
        actions=[
            ActionResponse(**a, undone=a.get("entry_id") in undone)
            for a in actions
            if isinstance(a, dict)
        ],
    )


@router.get(
    "/projects/{project_id}/copilot",
    response_model=CopilotResponse,
    summary="The copilot's state and this project's conversation",
)
async def get_copilot(
    project_id: ProjectId, session: SessionDep, settings: SettingsDep, connection: HTTPConnection
) -> CopilotResponse:
    await _project(session, project_id)
    statement = (
        select(CopilotMessage)
        .where(CopilotMessage.project_id == project_id)
        .order_by(CopilotMessage.created_at.desc(), CopilotMessage.id.desc())
        .limit(MAX_MESSAGES)
    )
    rows = list((await session.execute(statement)).scalars())[::-1]
    undone = await _undone(session, project_id)
    reason = _reason(connection, settings)
    return CopilotResponse(
        project_id=project_id,
        available=reason is None,
        reason=reason,
        messages=[_message(r, undone) for r in rows],
    )


@router.post(
    "/projects/{project_id}/copilot/messages",
    response_model=ReplyResponse,
    summary="Say what you want; the copilot changes the edit through the same operations",
)
async def post_message(
    project_id: ProjectId,
    body: MessageRequest,
    session: SessionDep,
    settings: SettingsDep,
    connection: HTTPConnection,
) -> ReplyResponse:
    await _project(session, project_id)
    reason = _reason(connection, settings)
    if reason is not None:
        return ReplyResponse(
            reply=MessageResponse(
                id="", role="assistant", text=reason, outcome="unavailable", actions=[]
            ),
            available=False,
            reason=reason,
        )
    try:
        reply = await agent.respond(
            session, settings.data_dir, project_id, body.text, _model(connection, settings)
        )
    except EditNotReadyError as error:
        raise EditNotReadyApiError(str(error)) from error
    statement = (
        select(CopilotMessage)
        .where(CopilotMessage.project_id == project_id)
        .order_by(CopilotMessage.created_at.desc(), CopilotMessage.id.desc())
        .limit(1)
    )
    row = (await session.execute(statement)).scalars().first()
    message = (
        _message(row, set())
        if row is not None
        else MessageResponse(
            id="", role="assistant", text=reply.text, outcome=reply.outcome, actions=[]
        )
    )
    after = _reason(connection, settings)
    return ReplyResponse(reply=message, available=after is None, reason=after)


@router.get(
    "/projects/{project_id}/history",
    response_model=HistoryResponse,
    summary="Every change on this project's undo stack, newest first",
)
async def get_history(
    project_id: ProjectId,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> HistoryResponse:
    await _project(session, project_id)
    rows = await history.entries(session, project_id, limit)
    return HistoryResponse(
        project_id=project_id,
        entries=[
            HistoryEntryResponse(
                id=r.id, source=r.source, operation=r.operation, summary=r.summary, undone=r.undone
            )
            for r in rows
        ],
    )


@router.post(
    "/projects/{project_id}/history/undo",
    response_model=UndoResponse,
    summary="Undo one change (the newest when no entry is named)",
)
async def post_undo(project_id: ProjectId, body: UndoRequest, session: SessionDep) -> UndoResponse:
    await _project(session, project_id)
    try:
        undone = await history.undo(session, project_id, body.entry_id)
    except history.UndoError as error:
        raise UndoUnavailableError(str(error)) from error
    return UndoResponse(
        entry_id=undone.entry_id, operation=undone.operation, summary=undone.summary
    )


__all__ = ["router"]
