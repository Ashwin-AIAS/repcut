"""Hero moments in the plan: half-speed shots, drop-aligned, reversible, overridable (P1, P2)."""

from dataclasses import replace

import pytest
from test_planner import _analysis, _grid, _segments

from repcut.edit.planner import (
    ASKED_REASON,
    SLOWMO_EVERY_SECONDS,
    SLOWMO_MAX_SECONDS,
    SLOWMO_MIN_SECONDS,
    PlanInputs,
    lock,
    plan,
)
from repcut.edit.resync import Override, OverrideError, apply, timed_replan
from repcut.edit.types import Decisions, Pace, Plan, Shot
from repcut.highlights.detector import HeroMoment
from repcut.slowmo.limits import SLOWEST_SPEED


def _heroes(count: int = 3) -> tuple[HeroMoment, ...]:
    """The longest segments as heroes, so a slow shot always has its full window."""
    longest = sorted(_segments(), key=lambda s: (-s.duration, s.id))[:count]
    return tuple(
        HeroMoment(
            segment_id=s.id,
            sha256=s.sha256,
            peak=round(s.start + 0.6 * s.duration, 3),
            score=round(0.9 - 0.1 * rank, 3),
            rank=rank + 1,
            reasons=("the most movement in this clip",),
            signals={},
        )
        for rank, s in enumerate(longest)
    )


def _plan(target: float = 30.0, **changes: object) -> Plan:
    inputs = PlanInputs(_segments(), _grid(), target, Pace(), seed=7, heroes=_heroes())
    return plan(replace(inputs, **changes))  # type: ignore[arg-type]


def _slow(result: Plan) -> list[Shot]:
    return [shot for shot in result.shots if shot.slow]


@pytest.mark.parametrize("target", [15.0, 30.0, 60.0])
def test_heroes_become_half_speed_shots_within_the_allowance(target: float) -> None:
    slow = _slow(_plan(target))
    assert 1 <= len(slow) <= max(1, int(target // SLOWMO_EVERY_SECONDS))
    hero_ids = {h.segment_id for h in _heroes()}
    for shot in slow:
        assert shot.segment_id in hero_ids
        assert shot.speed == SLOWEST_SPEED
        assert shot.source_out - shot.source_in == pytest.approx(shot.duration / 2, abs=2e-3)
        assert SLOWMO_MIN_SECONDS - 1e-6 <= shot.duration <= SLOWMO_MAX_SECONDS + 1e-6
        assert shot.hero_score is not None and shot.hero_reason


def test_every_slow_shot_keeps_a_full_length_fallback_inside_its_footage() -> None:
    by_id = {s.id: s for s in _segments()}
    for shot in _slow(_plan(60.0)):
        segment = by_id[shot.segment_id]
        assert shot.fallback_in is not None
        assert segment.start - 1e-6 <= shot.fallback_in
        assert shot.fallback_in + shot.duration <= segment.end + 1e-3
        assert shot.fallback_in - 1e-6 <= shot.source_in
        assert shot.source_out <= shot.fallback_in + shot.duration + 1e-3


def test_slow_shot_footage_is_never_reused_by_another_shot() -> None:
    result = _plan(60.0)
    for shot in result.shots:
        start, end = shot.footprint()
        for other in result.shots:
            if other is shot or other.sha256 != shot.sha256:
                continue
            o_start, o_end = other.footprint()
            assert end <= o_start + 1e-6 or start >= o_end - 1e-6


def test_the_best_hero_opens_the_drop_when_the_drop_shot_suits_slow_motion() -> None:
    result = _plan(30.0)
    analysis = _analysis(128.0)
    assert analysis.drop_seconds is not None
    drop = analysis.drop_seconds - result.music_start_seconds
    opening = min(result.shots, key=lambda shot: abs(shot.timeline_in - drop))
    if SLOWMO_MIN_SECONDS <= opening.duration <= SLOWMO_MAX_SECONDS:
        assert opening.slow
        assert opening.segment_id == _heroes()[0].segment_id


def test_cuts_stay_on_the_beat_and_the_plan_is_deterministic() -> None:
    first = _plan(30.0)
    assert first.to_json() == _plan(30.0).to_json()
    beats = [b - first.music_start_seconds for b in _grid().beats]
    for cut in first.cuts:
        assert min(abs(cut - b) for b in beats) <= 0.040


def test_turning_a_hero_off_plays_it_normally_and_is_kept_on_replan() -> None:
    segments, grid = _segments(), _grid()
    decisions = Decisions(target_seconds=30.0, seed=7)
    before = timed_replan(segments, grid, Pace(), decisions, _heroes()).plan
    hero = _slow(before)[0]
    decisions = apply(decisions, before, Override("slowmo_off", segment_id=hero.segment_id))
    after = timed_replan(segments, grid, Pace(), decisions, _heroes()).plan
    assert all(s.segment_id != hero.segment_id for s in _slow(after))
    regenerated = apply(decisions, after, Override("regenerate"))
    again = timed_replan(segments, grid, Pace(), regenerated, _heroes()).plan
    assert all(s.segment_id != hero.segment_id for s in _slow(again))


def test_asking_for_slow_motion_on_other_footage_places_it() -> None:
    segments, grid = _segments(), _grid()
    hero_ids = {h.segment_id for h in _heroes()}
    chosen = max(
        (s for s in segments if s.id not in hero_ids and s.duration >= 4.0),
        key=lambda s: (s.duration, s.id),
    )
    decisions = Decisions(target_seconds=30.0, seed=7)
    before = timed_replan(segments, grid, Pace(), decisions, _heroes()).plan
    decisions = apply(decisions, before, Override("slowmo_on", segment_id=chosen.id))
    after = timed_replan(segments, grid, Pace(), decisions, _heroes()).plan
    asked = [s for s in _slow(after) if s.segment_id == chosen.id]
    assert asked and asked[0].hero_reason == ASKED_REASON


def test_a_locked_slow_shot_is_kept_verbatim_and_its_speed_needs_an_unlock() -> None:
    first = _plan(30.0)
    slow = _slow(first)[0]
    locked = lock(first, slow.id)
    second = _plan(30.0, locked=(locked,), seed=9)
    assert locked.to_json() in [s.to_json() for s in second.shots]
    decisions = Decisions(locked=(locked,))
    with pytest.raises(OverrideError, match="unlock"):
        apply(decisions, first, Override("slowmo_off", segment_id=slow.segment_id))


def test_removing_a_slow_cut_removes_its_whole_window() -> None:
    first = _plan(30.0)
    slow = _slow(first)[0]
    decisions = apply(
        Decisions(target_seconds=30.0, seed=7), first, Override("cut_removed", shot_id=slow.id)
    )
    gone = decisions.removed_ranges[-1]
    assert (gone.start, gone.end) == pytest.approx(slow.footprint())


def test_without_heroes_nothing_is_slowed() -> None:
    assert not _slow(_plan(30.0, heroes=()))


def test_slow_motion_decisions_round_trip() -> None:
    decisions = Decisions(slowmo_off=frozenset({"x"}), slowmo_on=frozenset({"y"}))
    assert Decisions.from_json(decisions.to_json()) == decisions
    shot = _slow(_plan(30.0))[0]
    assert Shot.from_json(shot.to_json()) == shot
