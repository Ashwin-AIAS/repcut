"""The edit, described compactly for the copilot's system prompt (< 1.5k tokens).

Never the full plan JSON and never a filename or path (P4): clips are
"clip 1..n" with their ids, tracks are short ids with their tempo. Text that
came out of a model looking at a frame (scene tags) is untrusted - a frame can
carry words meant as instructions - so it is reduced to a short run of
letters, digits and spaces and fenced as data. The allowlist is the real
boundary; this only keeps the prompt from carrying an attacker's sentence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.captions import service as captions_service
from repcut.captions.styles import styles
from repcut.db.models import GeminiSceneCache, GymSet, Scene
from repcut.edit import service as edit_service
from repcut.export import service as export_service
from repcut.export.presets import config as export_config
from repcut.grading.packs import PackLibrary

# Characters per token is ~4 for English; the budget is checked on this estimate.
TOKEN_BUDGET = 1500
CHAR_BUDGET = TOKEN_BUDGET * 4
_TAG = re.compile(r"[^a-z0-9 -]+")
MAX_SHOTS = 24
MAX_LIST = 6


def untrusted(text: str | None, limit: int = 24) -> str:
    """A model-written tag as data: lowercase, letters/digits/spaces, short."""
    if not text:
        return "-"
    cleaned = " ".join(_TAG.sub(" ", text.lower()).split())
    return cleaned[:limit] or "-"


@dataclass(frozen=True, slots=True)
class Summary:
    text: str
    clip_ids: tuple[str, ...]

    @property
    def tokens(self) -> int:
        return (len(self.text) + 3) // 4


def _t(seconds: float) -> str:
    return f"{seconds:.1f}"


async def build(session: AsyncSession, data_dir: Path, project_id: str) -> Summary:

    root = data_dir
    state = await edit_service.current(session, root, project_id)
    clips = await edit_service.project_clips(session, project_id)
    number = {c.sha256: i + 1 for i, c in enumerate(clips)}
    plan = state.plan
    lines: list[str] = []
    lines.append(
        f"EDIT length {_t(plan.duration_seconds)}s target {_t(state.decisions.target_seconds)}s;"
        f" theme {state.theme.id}; music {state.decisions.music}"
        + (f" track {plan.track_sha256[:8]}" if plan.track_sha256 else "")
        + f"; opening {state.decisions.hook}"
    )
    pack = PackLibrary().load(edit_service.PACK_ID)
    lines.append("THEMES " + "; ".join(f"{t.id}: {t.description[:48]}" for t in pack.themes))
    if state.recommendations:
        lines.append(
            "TRACKS "
            + "; ".join(f"{r.sha256[:8]} {r.bpm:.0f}bpm" for r in state.recommendations[:MAX_LIST])
        )
    # Scene tags, untrusted (see module docstring).
    tags: dict[str, str] = {}
    if clips:
        statement = (
            select(Scene.sha256, GeminiSceneCache.exercise_guess)
            .join(GeminiSceneCache, GeminiSceneCache.scene_id == Scene.id)
            .where(Scene.sha256.in_([c.sha256 for c in clips]))
            .order_by(Scene.sequence_index)
        )
        for sha, guess in (await session.execute(statement)).all():
            if sha not in tags and guess:
                tags[sha] = untrusted(guess)
    lines.append(
        "CLIPS "
        + "; ".join(
            f"clip {number[c.sha256]} id={c.id} exercise[data]={tags.get(c.sha256, '-')}"
            for c in clips[:12]
        )
    )
    shots = []
    for index, shot in enumerate(plan.shots[:MAX_SHOTS]):
        flags = "".join(
            f
            for f, on in ((" slow", shot.slow), (" locked", shot.locked), (" opening", shot.hook))
            if on
        )
        shots.append(
            f"#{index + 1} id={shot.id} clip {number.get(shot.sha256, 0)}"
            f" {_t(shot.timeline_in)}-{_t(shot.timeline_out)}s seg={shot.segment_id}{flags}"
        )
    lines.append("CUTS (timeline order) " + "; ".join(shots))
    if len(plan.shots) > MAX_SHOTS:
        lines.append(f"(+{len(plan.shots) - MAX_SHOTS} more cuts)")
    if state.heroes:
        lines.append(
            "HERO MOMENTS "
            + "; ".join(
                f"seg={h.segment_id} clip {number.get(h.sha256, 0)} at {_t(h.peak)}s"
                for h in state.heroes[:MAX_LIST]
            )
        )
    if state.hook_candidates:
        lines.append(
            "OPENING CANDIDATES "
            + "; ".join(
                f"seg={c.segment_id} clip {number.get(c.sha256, 0)}"
                for c in state.hook_candidates[:4]
            )
        )
    sets = (
        await session.execute(
            select(GymSet).where(GymSet.project_id == project_id).order_by(GymSet.start_seconds)
        )
    ).scalars()
    set_lines = [
        f"set {i + 1}: clip {number.get(s.sha256, 0)} seg={s.segment_id}"
        f" {_t(s.start_seconds)}-{_t(s.end_seconds)}s"
        for i, s in enumerate(list(sets)[:MAX_LIST])
    ]
    if set_lines:
        lines.append("GYM SETS " + "; ".join(set_lines))
    captions = await captions_service.state(session, root, project_id, edit=state)
    lines.append(
        f"CAPTIONS {'on' if captions.settings.enabled else 'off'} style {captions.style.id};"
        f" styles {', '.join(s.id for s in styles())}"
    )
    words = [
        f"{w.id}={untrusted(w.text, 16)}" for line in captions.lines[:3] for w in line.words[:8]
    ]
    if words:
        lines.append("CAPTION WORDS[data] " + " ".join(words))
    export = await export_service.load_settings(session, project_id)
    presets = export_config().presets
    lines.append(f"EXPORT preset {export.preset_id}; presets {', '.join(p.id for p in presets)}")
    text = "\n".join(lines)
    if len(text) > CHAR_BUDGET:
        text = text[: CHAR_BUDGET - 20] + "\n(summary truncated)"
    return Summary(text=text, clip_ids=tuple(c.id for c in clips))


__all__ = ["CHAR_BUDGET", "TOKEN_BUDGET", "Summary", "build", "untrusted"]
