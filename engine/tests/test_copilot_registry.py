"""The operation registry and the undo stack: one behaviour, two front doors.

Every operation the copilot may call is run against a real (small, generated)
project and then undone; the project's decision rows must come back byte for
byte, and the taste events the change wrote must be gone. The same change
through the controls' route and through the copilot must leave the same plan
and the same taste event, apart from its ``source``.
"""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import Harness
from copilot_support import ScriptedModel, event_ids, events, make_project, run, state_text, turns

from repcut.captions import service as captions_service
from repcut.copilot import agent, tools
from repcut.db.models import HistoryEntry, TasteEvent
from repcut.edit import service as edit_service
from repcut.export import service as export_service
from repcut.export.presets import config as export_config
from repcut.grading.packs import PackLibrary

UPLOAD = Callable[..., Awaitable[httpx.Response]]


async def _valid_args(api: Harness, project: str) -> dict[str, dict[str, object]]:
    """For every copilot operation, arguments that change this project."""
    async with api.session_factory() as session:
        state = await edit_service.current(session, api.data_dir, project)
        clips = await edit_service.project_clips(session, project)
        captions = await captions_service.state(session, api.data_dir, project, edit=state)
        export = await export_service.load_settings(session, project)
    themes = [t.id for t in PackLibrary().load(edit_service.PACK_ID).themes]
    shots = state.plan.shots
    hook_on = state.decisions.hook != "off"
    other_hook = [
        c.segment_id
        for c in state.hook_candidates
        if state.hook is None or c.segment_id != state.hook.segment_id
    ]
    style = next(s for s in ("classic", "minimal", "punch") if s != captions.style.id)
    preset = next(p.id for p in export_config().presets if p.id != export.preset_id)
    word = captions.lines[0].words[0]
    slow = next((s for s in shots if s.slow), None)
    return {
        "set_theme": {"theme_id": next(t for t in themes if t != state.theme.id)},
        "set_intensity": {"intensity": 37, "clip_id": clips[0].id},
        "set_music_track": {"track": "off"},
        "set_target_duration": {"seconds": 12.0},
        "toggle_hook": {"on": not hook_on},
        "choose_hook": {"segment_id": other_hook[0]},
        "remove_cut": {"shot_id": shots[1].id},
        "lock_cut": {"shot_id": shots[2].id},
        "force_include_segment": {"segment_id": state.segments[0].id},
        "set_slowmo": {
            "segment_id": (slow.segment_id if slow else state.heroes[0].segment_id),
            "on": slow is None,
        },
        "set_caption_style": {"style_id": style},
        "edit_caption_text": {"word_id": word.id, "text": "fünf"},
        "set_export_preset": {"preset_id": preset},
        "regenerate": {},
    }


async def test_every_operation_then_undo_restores_the_state_byte_for_byte(
    api: Harness, upload_clip: UPLOAD, make_clip: Callable[..., Path]
) -> None:
    project = await make_project(api, upload_clip, make_clip)
    cases = await _valid_args(api, project)
    # Every operation the copilot is offered is covered, apart from undo itself.
    assert set(cases) == set(tools.COPILOT_OPERATIONS) - {"undo_last"}
    changed: list[str] = []
    for name, args in cases.items():
        before, ids_before = await state_text(api, project), await event_ids(api, project)
        done = await run(api, project, name, args, source="copilot", copilot=True)
        assert done.changed, f"{name} changed nothing with {args}"
        assert await state_text(api, project) != before, name
        changed.append(name)
        undone = await run(api, project, "undo_last", {}, source="copilot", copilot=True)
        assert undone.entry_id == done.entry_id
        assert await state_text(api, project) == before, f"undo of {name} is not byte-identical"
        assert await event_ids(api, project) == ids_before, f"undo of {name} left taste events"
    assert changed == list(cases)
    # Nothing left to undo once every change is undone.
    with pytest.raises(tools.ToolError, match="nothing to undo"):
        await run(api, project, "undo_last")


def _rows_without_ids(text: str) -> dict[str, Any]:
    rows: dict[str, Any] = json.loads(text)
    for key, row in rows.items():
        if key.startswith("grade_choices:") and isinstance(row, dict):
            row.pop("id", None)
    return rows


def _shape(event: TasteEvent) -> dict[str, Any]:
    skip = {"id", "created_at", "source"}
    return {
        c.name: getattr(event, c.key) for c in TasteEvent.__table__.columns if c.name not in skip
    }


async def test_set_theme_by_button_and_by_chat_is_the_same_change(
    api: Harness, upload_clip: UPLOAD, make_clip: Callable[..., Path]
) -> None:
    project = await make_project(api, upload_clip, make_clip)
    async with api.session_factory() as session:
        clip = (await edit_service.project_clips(session, project))[0]
        current = (await edit_service.current(session, api.data_dir, project)).theme.id
    theme = "moody" if current != "moody" else "hardstyle"

    async def seen() -> str:
        # As the panel sees it: the edit read back (a read re-plans the derived plan).
        assert (await api.client.get(f"/projects/{project}/edit")).status_code == 200
        return await state_text(api, project)

    start = await seen()

    # Door 1: the grading panel's button.
    response = await api.client.put(f"/media/{clip.id}/grade", json={"theme_id": theme})
    assert response.status_code == 200, response.text
    by_button = await seen()
    button_events = await events(api, project)
    assert (await api.client.post(f"/projects/{project}/history/undo", json={})).status_code == 200
    assert await seen() == start

    # Door 2: the copilot, with a model that asks for exactly that.
    model = ScriptedModel(turns([("set_theme", {"theme_id": theme, "clip_id": clip.id})]))
    async with api.session_factory() as session:
        reply = await agent.respond(session, api.data_dir, project, "darker look", model)
    assert reply.outcome == "done" and [a.operation for a in reply.actions] == ["set_theme"]
    by_chat = await seen()
    chat_events = await events(api, project)

    assert _rows_without_ids(by_chat) == _rows_without_ids(by_button)
    assert len(button_events) == len(chat_events) == 1
    assert _shape(chat_events[0]) == _shape(button_events[0])
    assert (button_events[0].source, chat_events[0].source) == ("ui", "copilot")
    history = (await api.client.get(f"/projects/{project}/history")).json()["entries"]
    assert [(e["source"], e["operation"], e["undone"]) for e in history] == [
        ("copilot", "set_theme", False),
        ("ui", "set_theme", True),
    ]


async def test_a_call_the_plan_cannot_take_is_a_clean_error_and_changes_nothing(
    api: Harness, upload_clip: UPLOAD, make_clip: Callable[..., Path]
) -> None:
    project = await make_project(api, upload_clip, make_clip)
    start = await state_text(api, project)
    bad: list[tuple[str, dict[str, object], str]] = [
        ("remove_cut", {"shot_id": "0" * 16}, ""),  # well-formed, not in this edit
        ("choose_hook", {"segment_id": "0" * 16 + "-0-1"}, "opening candidates"),
        ("set_slowmo", {"segment_id": "0" * 16 + "-0-1", "on": True}, "not in this project"),
        ("set_theme", {"theme_id": "neon-dream"}, ""),
        (
            "set_theme",
            {"theme_id": "moody", "clip_id": "0" * 8 + "-0000-0000-0000-" + "0" * 12},
            "not in this project",
        ),
        ("set_music_track", {"track": "deadbeef"}, "no single track"),
        ("edit_caption_text", {"word_id": "0" * 16 + "-0-0", "text": "x"}, ""),
        ("set_export_preset", {"preset_id": "cinema"}, ""),
        ("set_intensity", {"intensity": 101}, "intensity"),
        ("set_target_duration", {"seconds": 2}, "seconds"),
        ("remove_cut", {"shot_id": "../../etc"}, "shot_id"),
        ("set_theme", {"theme_id": "moody", "path": "/x"}, "path"),
        ("set_caption_style", {}, "say which"),
    ]
    for name, args, words in bad:
        with pytest.raises(tools.ToolError, match=words or None):
            await run(api, project, name, args, source="copilot", copilot=True)
        assert await state_text(api, project) == start, f"{name}{args} changed the plan"
    # Outside the allowlist: refused before anything runs, by name.
    for name in ("delete_project", "set_scene_boundary", "__import__", ""):
        with pytest.raises(tools.UnknownOperationError):
            await run(api, project, name, {}, copilot=True)
    async with api.session_factory() as session:
        assert (await session.execute(HistoryEntry.__table__.select())).first() is None
    assert await state_text(api, project) == start


async def test_the_controls_routes_go_through_the_stack_and_undo_by_entry(
    api: Harness, upload_clip: UPLOAD, make_clip: Callable[..., Path]
) -> None:
    project = await make_project(api, upload_clip, make_clip)
    start = await state_text(api, project)
    edit = (await api.client.get(f"/projects/{project}/edit")).json()
    shot = edit["shots"][1]["id"]
    removed = await api.client.post(
        f"/projects/{project}/edit/overrides", json={"kind": "cut_removed", "shot_id": shot}
    )
    assert removed.status_code == 200, removed.text
    after_cut = await state_text(api, project)
    preset = await api.client.post(
        f"/projects/{project}/export/settings", json={"preset_id": "square"}
    )
    assert preset.status_code == 200, preset.text
    entries = (await api.client.get(f"/projects/{project}/history")).json()["entries"]
    assert [e["operation"] for e in entries] == ["set_export_preset", "remove_cut"]
    # Undo the older change by its card: the newer one (another row) stays.
    older = entries[1]["id"]
    undone = await api.client.post(f"/projects/{project}/history/undo", json={"entry_id": older})
    assert undone.status_code == 200, undone.text
    assert json.loads(await state_text(api, project))["export_settings"]["preset_id"] == "square"
    again = await api.client.post(f"/projects/{project}/history/undo", json={"entry_id": older})
    assert again.status_code == 409 and again.json()["error"]["code"] == "undo_unavailable"
    assert (await api.client.post(f"/projects/{project}/history/undo", json={})).status_code == 200
    assert await state_text(api, project) == start
    assert after_cut != start
    nothing = await api.client.post(f"/projects/{project}/history/undo", json={})
    assert nothing.status_code == 409
