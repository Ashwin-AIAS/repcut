"""The cut planner and re-sync: the guide's planner criteria, as unit tests."""

import random
from dataclasses import replace
from functools import lru_cache

import pytest

from repcut.edit.planner import PlanInputs, lock, plan
from repcut.edit.resync import Override, OverrideError, apply, timed_replan
from repcut.edit.types import BeatGrid, BoundaryOverride, Decisions, Pace, Plan, Segment
from repcut.music.analysis import TrackAnalysis, analyse
from repcut.music.synthetic import generate_track

SNAP_TOLERANCE = 0.040
TARGETS = (15.0, 30.0, 60.0)


@lru_cache(maxsize=4)
def _analysis(bpm: float, first_beat: float = 0.25) -> TrackAnalysis:
    track = generate_track(
        bpm=bpm, bars=48, intro_bars=8, build_bars=8, drop_bars=16, first_beat_seconds=first_beat
    )
    return analyse(track.samples, track.sample_rate)


def _grid(bpm: float = 128.0, first_beat: float = 0.25, sha: str = "a" * 64) -> BeatGrid:
    return BeatGrid.from_analysis(_analysis(bpm, first_beat), sha)


def _segments(seed: int = 3, clips: int = 4, per_clip: int = 6) -> tuple[Segment, ...]:
    rng = random.Random(seed)  # noqa: S311 - fixture data, not security
    segments = []
    for clip in range(clips):
        sha = f"{clip:x}" * 64
        position = 0.0
        for _ in range(per_clip):
            length = rng.uniform(1.5, 9.0)
            segments.append(
                Segment(
                    Segment.make_id(sha, position, position + length),
                    sha,
                    position,
                    position + length,
                    quality=rng.random(),
                    energy=rng.random(),
                )
            )
            position += length + rng.uniform(0.5, 2.0)
    return tuple(segments)


def _plan(target: float, **changes: object) -> Plan:
    inputs = PlanInputs(_segments(), _grid(), target, Pace(), seed=7)
    return plan(replace(inputs, **changes))  # type: ignore[arg-type]


def _beat_error(plan_: Plan, grid: BeatGrid, cuts: tuple[float, ...]) -> float:
    beats = [beat - plan_.music_start_seconds for beat in grid.beats]
    return max((min(abs(cut - beat) for beat in beats) for cut in cuts), default=0.0)


@pytest.mark.parametrize("target", TARGETS)
def test_every_cut_is_on_a_beat(target: float) -> None:
    result = _plan(target)
    assert result.snapped
    assert _beat_error(result, _grid(), result.cuts) <= SNAP_TOLERANCE


@pytest.mark.parametrize("target", TARGETS)
def test_duration_is_within_five_percent(target: float) -> None:
    result = _plan(target)
    assert abs(result.duration_seconds - target) <= 0.05 * target
    assert result.shots[0].timeline_in == 0.0
    assert result.shots[-1].timeline_out == pytest.approx(result.duration_seconds)


@pytest.mark.parametrize("target", TARGETS)
def test_no_shot_is_shorter_than_the_minimum(target: float) -> None:
    result = _plan(target)
    assert min(shot.duration for shot in result.shots) >= Pace().min_shot_seconds - 1e-6


@pytest.mark.parametrize("target", TARGETS)
def test_the_same_seed_gives_the_same_plan_and_another_seed_another(target: float) -> None:
    assert _plan(target).to_json() == _plan(target).to_json()
    assert _plan(target, seed=8).to_json() != _plan(target).to_json()


@pytest.mark.parametrize("target", TARGETS)
def test_shots_tile_the_timeline_and_use_footage_once(target: float) -> None:
    result = _plan(target)
    for before, after in zip(result.shots, result.shots[1:], strict=False):
        assert before.timeline_out == pytest.approx(after.timeline_in)
    used: dict[str, list[tuple[float, float]]] = {}
    for shot in result.shots:
        assert shot.source_out - shot.source_in == pytest.approx(shot.duration, abs=2e-3)
        for start, end in used.get(shot.sha256, []):
            assert shot.source_out <= start + 1e-6 or shot.source_in >= end - 1e-6
        used.setdefault(shot.sha256, []).append((shot.source_in, shot.source_out))


@pytest.mark.parametrize("target", TARGETS)
def test_the_drop_opens_on_a_cut_with_top_energy_footage(target: float) -> None:
    result = _plan(target)
    grid = _grid()
    analysis = _analysis(128.0)
    assert analysis.drop_seconds is not None
    drop = analysis.drop_seconds - result.music_start_seconds
    bar = 4 * 60.0 / grid.bpm
    opening = min(result.shots, key=lambda shot: abs(shot.timeline_in - drop))
    assert abs(opening.timeline_in - drop) <= bar, "no cut within one bar of the drop"
    energies = sorted(segment.energy for segment in _segments())
    chosen = next(s.energy for s in _segments() if s.id == opening.segment_id)
    assert energies.index(chosen) / (len(energies) - 1) >= 0.75


def test_a_new_track_re_snaps_every_unlocked_cut_and_keeps_locked_shots() -> None:
    first = _plan(30.0)
    locked = (lock(first, first.shots[2].id), lock(first, first.shots[5].id))
    other = _grid(100.0, first_beat=0.6, sha="b" * 64)
    second = plan(PlanInputs(_segments(), other, 30.0, Pace(), seed=7, locked=locked))
    for shot in locked:
        assert shot.to_json() in [s.to_json() for s in second.shots]
    edges = {edge for shot in locked for edge in (shot.timeline_in, shot.timeline_out)}
    unlocked = tuple(cut for cut in second.cuts if cut not in edges)
    assert unlocked
    assert _beat_error(second, other, unlocked) <= SNAP_TOLERANCE
    assert second.track_sha256 == "b" * 64


def test_an_empty_library_still_produces_an_edit() -> None:
    result = _plan(30.0, grid=None)
    assert not result.snapped and result.track_sha256 is None
    assert abs(result.duration_seconds - 30.0) <= 1.5
    assert len(result.shots) >= 3


def test_removed_footage_is_never_used_and_forced_footage_always_is() -> None:
    first = _plan(30.0)
    removed = first.shots[0].segment_id
    unused = next(s for s in _segments() if s.id not in {x.segment_id for x in first.shots})
    second = _plan(30.0, removed=frozenset({removed}), forced=frozenset({unused.id}))
    ids = {shot.segment_id for shot in second.shots}
    assert removed not in ids
    assert unused.id in ids


def test_too_little_footage_is_said_not_hidden() -> None:
    short = (Segment(Segment.make_id("c" * 64, 0, 3), "c" * 64, 0.0, 3.0, 0.5, 0.5),)
    result = _plan(30.0, segments=short)
    assert result.warnings


def test_resync_applies_each_override_and_keeps_every_earlier_decision() -> None:
    grid = _grid()
    segments = _segments()
    decisions = Decisions(target_seconds=30.0, seed=7)
    current = timed_replan(segments, grid, Pace(), decisions).plan
    decisions = apply(decisions, current, Override("cut_locked", shot_id=current.shots[1].id))
    decisions = apply(decisions, current, Override("cut_removed", shot_id=current.shots[3].id))
    other = _grid(100.0, first_beat=0.6, sha="b" * 64)
    decisions = apply(decisions, current, Override("music_track", track_sha256="b" * 64))
    result = timed_replan(segments, other, Pace(), decisions)
    assert result.seconds < 3.0
    assert decisions.locked[0].to_json() in [s.to_json() for s in result.plan.shots]
    gone = current.shots[3]
    for shot in result.plan.shots:
        if shot.sha256 == gone.sha256:
            assert shot.source_out <= gone.source_in or shot.source_in >= gone.source_out
    assert Decisions.from_json(decisions.to_json()) == decisions


def test_regenerate_changes_the_plan_but_not_the_decisions_a_person_made() -> None:
    grid = _grid()
    decisions = Decisions(target_seconds=30.0, seed=7)
    current = timed_replan(_segments(), grid, Pace(), decisions).plan
    decisions = apply(decisions, current, Override("cut_locked", shot_id=current.shots[4].id))
    again = apply(decisions, current, Override("regenerate"))
    regenerated = timed_replan(_segments(), grid, Pace(), again).plan
    assert again.locked == decisions.locked and again.seed == decisions.seed + 1
    assert regenerated.to_json() != current.to_json()
    assert decisions.locked[0] in regenerated.shots


def test_an_override_naming_a_missing_cut_is_refused() -> None:
    current = _plan(30.0)
    with pytest.raises(OverrideError):
        apply(Decisions(), current, Override("cut_locked", shot_id="nope"))
    with pytest.raises(OverrideError):
        apply(Decisions(), current, Override("target_duration", target_seconds=1000.0))


def test_a_boundary_correction_replaces_one_at_the_same_place() -> None:
    current = _plan(30.0)
    first = apply(
        Decisions(),
        current,
        Override("scene_boundary", boundary=BoundaryOverride("d" * 64, "add", 4.0)),
    )
    second = apply(
        first,
        current,
        Override("scene_boundary", boundary=BoundaryOverride("d" * 64, "remove", 4.01)),
    )
    assert len(second.boundaries) == 1 and second.boundaries[0].action == "remove"
