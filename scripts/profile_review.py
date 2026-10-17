#!/usr/bin/env python3
"""The style review: the same session edited with your profile off, then on.

For the deferred check (docs/manual-checks/prompt-10.md): "does profile-on feel
like YOUR style?". Two reel exports of one project, rendered from the same
footage and the same decisions; the only difference is the style profile.
The page plays them side by side and lists, for each, the theme, pacing, music
and caption style it used, and every sentence of evidence the profile acted on.

The profile is your real one (learned from your taste events). With fewer
overrides than it needs to be active, ``--replay`` uses a synthetic history
instead (a moody, fast-cut person; ``repcut/profile/replay.py``) - for the
page's shape, not for the taste check. Nothing is written to the database:
the profile is handed to the planner directly.

Stays on this machine; written to ``$DATA_DIR/reviews/prompt-10/`` (outside
the repo, outside any synced folder).

    .venv/Scripts/python.exe scripts/profile_review.py [--project UUID] [--replay]
"""

from __future__ import annotations

import argparse
import asyncio
import html
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "engine"))

from repcut.config import detect_sync_root, get_settings  # noqa: E402
from repcut.db import create_engine, create_session_factory  # noqa: E402
from repcut.db.models import EditPlan  # noqa: E402
from repcut.edit import service as edit_service  # noqa: E402
from repcut.export import service as export_service  # noqa: E402
from repcut.export.render import render_export  # noqa: E402
from repcut.grading.packs import PackLibrary  # noqa: E402
from repcut.profile import service as profile_service  # noqa: E402
from repcut.profile.learn import LearnContext, learn  # noqa: E402
from repcut.profile.replay import moody_fast_cut  # noqa: E402
from repcut.profile.types import ProfileItem, StyleProfile  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

NOT_ENOUGH = (
    "Your profile has nothing active yet (it needs a few overrides per item). Edit a few "
    "sessions first, or run with --replay to see the page with a synthetic history."
)


async def _project(session: AsyncSession, wanted: str | None) -> str | None:
    if wanted:
        return wanted
    statement = select(EditPlan.project_id).order_by(EditPlan.updated_at.desc())
    found: str | None = (await session.execute(statement)).scalars().first()
    return found


Variant = tuple[edit_service.EditState, str]


def _describe(variant: Variant) -> list[str]:
    state, style = variant
    plan = state.plan
    shots = [s.timeline_out - s.timeline_in for s in plan.shots]
    mean = sum(shots) / len(shots) if shots else 0.0
    slow = sum(1 for s in plan.shots if s.slow)
    music = state.decisions.music
    if state.recommendations and music != "off":
        music = f"{music}, {state.recommendations[0].display_name}"
    return [
        f"Theme: {state.theme.name}",
        f"Shots: {len(shots)}, {mean:.2f} s on average; {slow} in slow motion",
        f"Music: {music}",
        f"Caption style: {style}",
    ]


async def _render(
    session: AsyncSession,
    data_dir: Path,
    project: str,
    profile: StyleProfile,
    name: str,
    out: Path,
) -> Variant:
    decisions, _ = await edit_service.load_decisions(session, project)
    state = await edit_service.build(session, data_dir, project, decisions, profile=profile)
    preset = export_service.config().preset("reel")
    inputs, _ = await export_service.gather(
        session, data_dir, project, preset=preset, edit=state, profile=profile
    )
    result = await render_export(inputs, out / f"work-{name}", out / f"{name}.mp4")
    if result.problems:
        print(f"the {name} export failed its platform check: {'; '.join(result.problems)}")
    return state, (inputs.style.name if inputs.style else "captions off")


def _items(lines: list[str]) -> str:
    return "".join(f"<li>{html.escape(line)}</li>" for line in lines)


def _side(title: str, name: str, variant: Variant, extra: str = "") -> str:
    return (
        f"<section><h2>{title}</h2>"
        f'<video src="{name}.mp4" controls muted></video>'
        f"<ul>{_items(_describe(variant))}</ul>{extra}</section>"
    )


def _page(off: Variant, on: Variant, profile: StyleProfile, replay: bool) -> str:
    evidence = [
        f"{item.value}: {e.why} ({e.events} overrides, {'active' if e.active else 'not active'})"
        for item in ProfileItem
        if (e := profile.evidence.get(item)) is not None and e.events
    ]
    source = "a SYNTHETIC history (--replay), not yours" if replay else "your own overrides"
    changed = _items(list(on[0].profile_notes) or ["nothing"])
    play = (
        "<button onclick=\"for (const v of document.querySelectorAll('video'))"
        ' {v.currentTime=0; v.play();}">Play both from the start</button>'
    )
    return "\n".join(
        [
            '<!doctype html><meta charset="utf-8"><title>Repcut - profile off vs on</title>',
            "<style>body{background:#111;color:#eee;font:15px system-ui;margin:24px}",
            ".two{display:flex;gap:24px} video{height:70vh;background:#000}</style>",
            "<h1>Does profile-on feel like YOUR style?</h1>",
            "<p>The same footage and decisions; only the style profile differs. "
            f"Profile learned from {source}.</p>",
            play,
            '<div class="two">',
            _side("Profile off", "off", off),
            _side("Profile on", "on", on, f"<h3>What the profile changed</h3><ul>{changed}</ul>"),
            "</div>",
            "<h2>Everything the profile learned</h2>",
            f"<ul>{_items(evidence or ['nothing yet'])}</ul>",
        ]
    )


async def run(arguments: argparse.Namespace) -> int:
    settings = get_settings()
    out = settings.data_dir / "reviews" / "prompt-10"
    if detect_sync_root(out) is not None:
        print("DATA_DIR is inside a cloud-sync folder; the review holds real footage. Not run.")
        return 1
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    try:
        async with sessions() as session:
            project = await _project(session, arguments.project)
            if project is None:
                print("no project has an edit yet: open one in `make dev` first")
                return 1
            if arguments.replay:
                pack = PackLibrary().load("gym")
                profile = learn(
                    moody_fast_cut(10),
                    LearnContext(theme_names={t.id: t.name for t in pack.themes}),
                )
            else:
                profile = (await profile_service.view(session, settings.data_dir)).learned
            if not profile.notes():
                print(NOT_ENOUGH)
                return 1
            out.mkdir(parents=True, exist_ok=True)
            off = await _render(session, settings.data_dir, project, StyleProfile(), "off", out)
            on = await _render(session, settings.data_dir, project, profile, "on", out)
    finally:
        await engine.dispose()
    (out / "index.html").write_text(_page(off, on, profile, arguments.replay), encoding="utf-8")
    print("written: $DATA_DIR/reviews/prompt-10/index.html (open it in a browser)")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--project")
    parser.add_argument("--replay", action="store_true")
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
