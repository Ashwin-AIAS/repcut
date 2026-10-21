"""The operation registry: every edit action a person can take, typed and validated.

One behaviour, two front doors. The UI's routes and the copilot both call
:func:`execute` with an operation name and its arguments; the registry is the
only way either reaches the engine. For the copilot it is also the permission
boundary: a name not in :data:`REGISTRY` is refused before anything runs, and
arguments are validated by a strict model (``extra="forbid"``, bounds,
patterns) and then against the current plan, so a call naming a cut that is
not in the edit is a fixed sentence, never a corrupt plan.

Every mutation runs through :func:`recorded`: the project's decision rows are
snapshotted before and after, the change goes onto the undo stack
(``history``), and the taste events it wrote carry the door it came through
(``taste_source``). The services that write those events never know which
door it was, which is what keeps the two from drifting apart.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.captions import service as captions_service
from repcut.copilot import history
from repcut.db.models import MediaFile, TasteSource, taste_source
from repcut.edit import service as edit_service
from repcut.edit.resync import Override, OverrideError
from repcut.edit.types import BoundaryOverride
from repcut.export import service as export_service
from repcut.grading import service as grading_service
from repcut.grading.packs import ThemeNotFoundError

_DIGEST = r"^[0-9a-f]{64}$"
_SHOT = r"^[0-9a-f]{16}$"
_SEGMENT = r"^[0-9a-f]{16}-[0-9]{1,9}-[0-9]{1,9}$"
_SLUG = r"^[a-z0-9][a-z0-9-]{0,39}$"
_UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_WORD = r"^[0-9a-f]{16}-[0-9]{1,6}-[0-9]{1,4}$"
_TRACK = r"^(suggested|off|[0-9a-f]{8,64})$"


class ToolError(ValueError):
    """An operation that cannot run against this plan. A fixed sentence, safe to show."""


class UnknownOperationError(ToolError):
    """A name outside the registry: refused before anything runs."""


@dataclass(frozen=True, slots=True)
class ToolContext:
    session: AsyncSession
    data_dir: Path
    project_id: str
    source: str = TasteSource.UI.value


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one operation did. ``entry_id`` is its undo-stack entry; None when nothing changed."""

    operation: str
    summary: str
    entry_id: str | None
    changed: bool


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoArgs(_Args):
    pass


ClipId = Annotated[str, Field(pattern=_UUID, description="a clip's id, from the summary")]


class ThemeArgs(_Args):
    theme_id: str = Field(pattern=_SLUG, description="a theme id from the summary")
    clip_id: ClipId | None = Field(default=None, description="one clip; omit for every clip")


class IntensityArgs(_Args):
    intensity: int = Field(ge=0, le=100, description="grade strength, 0 to 100")
    clip_id: ClipId | None = Field(default=None, description="one clip; omit for every clip")


class TrackArgs(_Args):
    track: str = Field(
        pattern=_TRACK,
        description='"suggested", "off", or a track id (at least its first 8 characters)',
    )


class DurationArgs(_Args):
    seconds: float = Field(ge=5.0, le=180.0, description="target length of the edit")


class HookToggleArgs(_Args):
    on: bool = Field(description="true opens on the best moment; false removes the opening")


class HookChooseArgs(_Args):
    segment_id: str = Field(pattern=_SEGMENT, description="a hook candidate's segment id")


class ShotArgs(_Args):
    shot_id: str = Field(pattern=_SHOT, description="a cut's id, from the summary")


class LockArgs(_Args):
    shot_id: str = Field(pattern=_SHOT, description="a cut's id, from the summary")
    locked: bool = Field(default=True, description="false unlocks it")


class ForceArgs(_Args):
    segment_id: str = Field(pattern=_SEGMENT, description="a stretch of footage's id")
    include: bool = Field(default=True, description="false lets the planner decide again")


class SlowmoArgs(_Args):
    segment_id: str = Field(pattern=_SEGMENT, description="a hero moment's segment id")
    on: bool = Field(description="true asks for slow motion; false turns it off")


class CaptionStyleArgs(_Args):
    style_id: str | None = Field(default=None, pattern=_SLUG, description="a caption style id")
    enabled: bool | None = Field(default=None, description="false hides captions")


class CaptionTextArgs(_Args):
    word_id: str = Field(pattern=_WORD, description="a caption word's id")
    text: str = Field(min_length=1, max_length=200, description="the corrected word")


class PresetArgs(_Args):
    preset_id: str = Field(pattern=_SLUG, description="an export preset id")


class BoundaryArgs(_Args):
    sha256: str = Field(pattern=_DIGEST)
    action: Literal["add", "remove"]
    seconds: float = Field(ge=0.0, le=24 * 3600)


Runner = Callable[[ToolContext, Any], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    args: type[_Args]
    run: Runner
    copilot: bool = True


# --- helpers -----------------------------------------------------------------


async def _clips(ctx: ToolContext, clip_id: str | None) -> list[MediaFile]:
    clips = await edit_service.project_clips(ctx.session, ctx.project_id)
    if clip_id is None:
        if not clips:
            raise ToolError("this project has no clips yet")
        return clips
    chosen = [c for c in clips if c.id == clip_id]
    if not chosen:
        raise ToolError("that clip is not in this project")
    return chosen


async def _override(ctx: ToolContext, override: Override) -> None:
    await edit_service.apply_override(ctx.session, ctx.data_dir, ctx.project_id, override)


async def _state(ctx: ToolContext) -> edit_service.EditState:
    return await edit_service.current(ctx.session, ctx.data_dir, ctx.project_id)


async def _grade(ctx: ToolContext, clip_id: str | None, changes: dict[str, object]) -> int:
    written = 0
    for clip in await _clips(ctx, clip_id):
        grade = await grading_service.clip_grade(ctx.session, ctx.data_dir, clip)
        written += await grading_service.apply_choice(
            ctx.session, grade, scene_id=None, changes=changes
        )
    return written


# --- the operations ----------------------------------------------------------


async def _set_theme(ctx: ToolContext, args: ThemeArgs) -> str:
    await _grade(ctx, args.clip_id, {"theme_id": args.theme_id})
    where = "every clip" if args.clip_id is None else "that clip"
    return f"Theme set to {args.theme_id} on {where}."


async def _set_intensity(ctx: ToolContext, args: IntensityArgs) -> str:
    await _grade(ctx, args.clip_id, {"intensity": args.intensity})
    where = "every clip" if args.clip_id is None else "that clip"
    return f"Grade strength set to {args.intensity} on {where}."


async def _set_music_track(ctx: ToolContext, args: TrackArgs) -> str:
    if args.track == "off":
        await _override(ctx, Override(kind="music_off"))
        return "Music off; the edit is cut to the theme's pace."
    if args.track == "suggested":
        await _override(ctx, Override(kind="music_suggested"))
        return "Back to the suggested track."
    tracks = [t.sha256 for _, t in await edit_service.library(ctx.session)]
    matches = [t for t in tracks if t.startswith(args.track)]
    if len(matches) != 1:
        raise ToolError("no single track in the library has that id")
    await _override(ctx, Override(kind="music_track", track_sha256=matches[0]))
    return f"Music changed to track {matches[0][:8]}; cuts re-snapped to its beats."


async def _set_target_duration(ctx: ToolContext, args: DurationArgs) -> str:
    await _override(ctx, Override(kind="target_duration", target_seconds=args.seconds))
    return f"Target length set to {args.seconds:g} s."


async def _toggle_hook(ctx: ToolContext, args: HookToggleArgs) -> str:
    await _override(ctx, Override(kind="hook_on" if args.on else "hook_off"))
    return "The edit opens on its best moment." if args.on else "Opening moment removed."


async def _choose_hook(ctx: ToolContext, args: HookChooseArgs) -> str:
    state = await _state(ctx)
    if not any(c.segment_id == args.segment_id for c in state.hook_candidates):
        raise ToolError("that moment is not one of the opening candidates")
    await _override(ctx, Override(kind="hook_choose", segment_id=args.segment_id))
    return "Opening moment changed."


async def _remove_cut(ctx: ToolContext, args: ShotArgs) -> str:
    await _override(ctx, Override(kind="cut_removed", shot_id=args.shot_id))
    return "Cut removed; the rest re-synced."


async def _lock_cut(ctx: ToolContext, args: LockArgs) -> str:
    kind: Literal["cut_locked", "cut_unlocked"] = "cut_locked" if args.locked else "cut_unlocked"
    await _override(ctx, Override(kind=kind, shot_id=args.shot_id))
    return "Cut locked in place." if args.locked else "Cut unlocked."


async def _force_include(ctx: ToolContext, args: ForceArgs) -> str:
    state = await _state(ctx)
    if not any(s.id == args.segment_id for s in state.segments):
        raise ToolError("that footage is not in this project")
    kind: Literal["segment_forced", "segment_unforced"] = (
        "segment_forced" if args.include else "segment_unforced"
    )
    await _override(ctx, Override(kind=kind, segment_id=args.segment_id))
    return "That footage is now in the edit." if args.include else "That footage is optional again."


async def _set_slowmo(ctx: ToolContext, args: SlowmoArgs) -> str:
    state = await _state(ctx)
    if not any(s.id == args.segment_id for s in state.segments):
        raise ToolError("that footage is not in this project")
    kind: Literal["slowmo_on", "slowmo_off"] = "slowmo_on" if args.on else "slowmo_off"
    await _override(ctx, Override(kind=kind, segment_id=args.segment_id))
    return "Slow motion on for that moment." if args.on else "Slow motion off for that moment."


async def _caption(ctx: ToolContext, change: captions_service.CaptionOverride) -> None:
    await captions_service.apply_override(ctx.session, ctx.data_dir, ctx.project_id, change)


async def _set_caption_style(ctx: ToolContext, args: CaptionStyleArgs) -> str:
    if args.style_id is None and args.enabled is None:
        raise ToolError("say which caption style, or whether captions are on")
    said: list[str] = []
    if args.style_id is not None:
        await _caption(ctx, captions_service.CaptionOverride(kind="style", style_id=args.style_id))
        said.append(f"Caption style set to {args.style_id}.")
    if args.enabled is not None:
        await _caption(ctx, captions_service.CaptionOverride(kind="enabled", enabled=args.enabled))
        said.append("Captions on." if args.enabled else "Captions off.")
    return " ".join(said)


async def _edit_caption_text(ctx: ToolContext, args: CaptionTextArgs) -> str:
    await _caption(
        ctx, captions_service.CaptionOverride(kind="word", word_id=args.word_id, text=args.text)
    )
    return f"Caption word changed to “{args.text}”."


async def _set_export_preset(ctx: ToolContext, args: PresetArgs) -> str:
    await export_service.set_preset(ctx.session, ctx.project_id, args.preset_id)
    return f"Export preset set to {args.preset_id}."


async def _regenerate(ctx: ToolContext, args: NoArgs) -> str:
    await _override(ctx, Override(kind="regenerate"))
    return "A fresh take on the edit; locked cuts stayed."


async def _never(ctx: ToolContext, args: NoArgs) -> str:
    raise ToolError("undo is handled by the history, not run as a change")


async def _scene_boundary(ctx: ToolContext, args: BoundaryArgs) -> str:
    boundary = BoundaryOverride(args.sha256, args.action, args.seconds)
    await _override(ctx, Override(kind="scene_boundary", boundary=boundary))
    return "Scene boundary corrected."


_TABLE: tuple[tuple[str, str, type[_Args], Runner], ...] = (
    ("set_theme", "Change the colour theme of every clip, or of one clip.", ThemeArgs, _set_theme),
    (
        "set_intensity",
        "Change how strongly the theme is applied (0-100).",
        IntensityArgs,
        _set_intensity,
    ),
    (
        "set_music_track",
        "Use a library track, the suggestion, or no music.",
        TrackArgs,
        _set_music_track,
    ),
    (
        "set_target_duration",
        "Change the edit's target length in seconds.",
        DurationArgs,
        _set_target_duration,
    ),
    ("toggle_hook", "Open the edit on its best moment, or not.", HookToggleArgs, _toggle_hook),
    ("choose_hook", "Open on a different candidate moment.", HookChooseArgs, _choose_hook),
    ("remove_cut", "Take one cut out of the edit.", ShotArgs, _remove_cut),
    ("lock_cut", "Keep one cut exactly where it is, or unlock it.", LockArgs, _lock_cut),
    (
        "force_include_segment",
        "Make sure a stretch of footage is in the edit.",
        ForceArgs,
        _force_include,
    ),
    ("set_slowmo", "Slow motion on or off for one moment (2x at most).", SlowmoArgs, _set_slowmo),
    (
        "set_caption_style",
        "Change the caption style, or captions on/off.",
        CaptionStyleArgs,
        _set_caption_style,
    ),
    ("edit_caption_text", "Correct one caption word.", CaptionTextArgs, _edit_caption_text),
    ("set_export_preset", "Choose the export preset (platform).", PresetArgs, _set_export_preset),
    ("regenerate", "Make a fresh take on the edit, keeping locked cuts.", NoArgs, _regenerate),
    ("undo_last", "Undo the most recent change.", NoArgs, _never),
)

# The allowlist, in the order the copilot is shown it. The controls' scene
# boundary correction is on the same stack but not offered to the copilot.
REGISTRY: dict[str, Tool] = {
    **{name: Tool(name, description, args, run) for name, description, args, run in _TABLE},
    "set_scene_boundary": Tool(
        "set_scene_boundary", "Correct a scene boundary.", BoundaryArgs, _scene_boundary, False
    ),
}

# What a service raises for a value this edit does not have. The controls'
# routes name each one; for the copilot they become the function's error.
DOMAIN_ERRORS: tuple[type[Exception], ...] = (
    OverrideError,
    ThemeNotFoundError,
    grading_service.GradeChoiceError,
    grading_service.GradingNotReadyError,
    captions_service.CaptionOverrideError,
    export_service.ExportSettingsError,
    edit_service.EditNotReadyError,
)

COPILOT_OPERATIONS: tuple[str, ...] = tuple(name for name, t in REGISTRY.items() if t.copilot)


def validate(name: str, raw: Mapping[str, object] | None, *, copilot: bool) -> tuple[Tool, _Args]:
    """The tool and its parsed arguments, or ``ToolError``. Runs nothing."""
    tool = REGISTRY.get(name)
    if tool is None or (copilot and not tool.copilot):
        raise UnknownOperationError("that is not an operation Repcut has")
    try:
        args = tool.args.model_validate(dict(raw or {}))
    except ValidationError as error:
        fields = sorted({".".join(str(p) for p in e["loc"]) or "arguments" for e in error.errors()})
        raise ToolError(f"invalid arguments for {name}: {', '.join(fields)}") from error
    return tool, args


async def recorded(
    ctx: ToolContext, operation: str, change: Callable[[], Awaitable[str]]
) -> Outcome:
    """Run one change with the undo stack and the taste source around it."""
    before = await history.snapshot(ctx.session, ctx.project_id)
    ids_before = await history.event_ids(ctx.session, ctx.project_id)
    token = taste_source.set(ctx.source)
    try:
        summary = await change()
    except BaseException:
        await ctx.session.rollback()
        raise
    finally:
        taste_source.reset(token)
    after = await history.snapshot(ctx.session, ctx.project_id)
    new_ids = sorted(await history.event_ids(ctx.session, ctx.project_id) - ids_before)
    if not history.changed_keys(before, after):
        return Outcome(operation=operation, summary=summary, entry_id=None, changed=False)
    entry = await history.record(
        ctx.session,
        ctx.project_id,
        source=ctx.source,
        operation=operation,
        summary=summary,
        before=before,
        after=after,
        event_ids=new_ids,
    )
    return Outcome(operation=operation, summary=summary, entry_id=entry.id, changed=True)


async def execute(
    ctx: ToolContext, name: str, raw: Mapping[str, object] | None = None, *, copilot: bool = False
) -> Outcome:
    """Validate and run one registry operation, through the undo stack."""
    tool, args = validate(name, raw, copilot=copilot)
    if name == "undo_last":
        try:
            undone = await history.undo(ctx.session, ctx.project_id)
        except history.UndoError as error:
            raise ToolError(str(error)) from error
        return Outcome(
            operation="undo_last",
            summary=f"Undone: {undone.summary}",
            entry_id=undone.entry_id,
            changed=True,
        )
    if not copilot:
        return await recorded(ctx, name, lambda: tool.run(ctx, args))
    try:
        return await recorded(ctx, name, lambda: tool.run(ctx, args))
    except DOMAIN_ERRORS as error:
        # The copilot reads the sentence back as the function's response.
        raise ToolError(str(error)) from error


__all__ = [
    "COPILOT_OPERATIONS",
    "REGISTRY",
    "Outcome",
    "Tool",
    "ToolContext",
    "ToolError",
    "UnknownOperationError",
    "execute",
    "recorded",
    "validate",
]
