"""The hook: a top hero moment opens the edit, inside the duration budget (P2 throughout)."""

from dataclasses import replace

import pytest
from test_highlights import SHA, _session
from test_planner import _grid, _segments
from test_slowmo_planner import _heroes

from repcut.edit import hook as hooks
from repcut.edit.planner import HOOK_DUPLICATE_MIN_TARGET, PlanInputs, lock, plan
from repcut.edit.resync import Override, OverrideError, apply
from repcut.edit.signals import SIGNALS_VERSION, ClipSignals
from repcut.edit.types import Decisions, Pace, Plan, Segment, Shot
from repcut.highlights.detector import detect

DURATION_TOLERANCE = 0.05


def _flat_signals(sha: str, seconds: float = 90.0, width: float = 0.5) -> ClipSignals:
    count = int(seconds / width)
    return ClipSignals(
        version=SIGNALS_VERSION,
        duration_seconds=seconds,
        window_seconds=width,
        audio_db=tuple(-30.0 for _ in range(count)),
        motion=tuple(3.0 + (i % 7) for i in range(count)),
        shake=tuple(0.0 for _ in range(count)),
        speech=None,
    )


def _options() -> tuple[hooks.HookCandidate, ...]:
    segments = _segments()
    signals = {s.sha256: _flat_signals(s.sha256) for s in segments}
    return hooks.candidates(_heroes(), segments, signals)


def _plan(target: float, *, grid: bool = True, seed: int = 7, **changes: object) -> Plan:
    inputs = PlanInputs(
        _segments(),
        _grid() if grid else None,
        target,
        Pace(),
        seed=seed,
        heroes=_heroes(),
        hook=_options()[0],
    )
    return plan(replace(inputs, **changes))  # type: ignore[arg-type]


def _hook(result: Plan) -> Shot:
    found = [shot for shot in result.shots if shot.hook]
    assert len(found) == 1, "exactly one teaser"
    return found[0]


# --- choosing the moment --------------------------------------------------------


def test_every_candidate_is_a_top_three_hero_moment() -> None:
    signals, segments = _session()
    heroes = detect(segments, {SHA: signals})
    options = hooks.candidates(heroes, segments, {SHA: signals})
    assert options, "a session with heroes has a hook"
    assert len(options) <= hooks.HOOK_CANDIDATES
    hero_ids = {h.segment_id for h in heroes}
    assert {o.segment_id for o in options} <= hero_ids
    assert [o.rank for o in options] == list(range(1, len(options) + 1))
    assert all(o.hero_rank <= 3 for o in options)


def test_on_the_generated_session_the_hook_is_the_hardest_sets_finish() -> None:
    signals, segments = _session()
    heroes = detect(segments, {SHA: signals})
    best, warning = hooks.choose(hooks.candidates(heroes, segments, {SHA: signals}), "on", None)
    assert warning is None and best is not None
    assert best.segment_id == heroes[0].segment_id
    assert best.start <= best.peak <= best.end


def test_candidates_rank_by_hero_score_times_visual_impact() -> None:
    options = _options()
    scores = [o.score for o in options]
    assert scores == sorted(scores, reverse=True)
    for option in options:
        assert option.score == pytest.approx(option.hero_score * option.visual_impact, abs=1e-3)
        assert hooks.IMPACT_FLOOR <= option.visual_impact <= 1.0
        assert option.reasons[-1].startswith("movement fills")


def test_a_still_but_loud_moment_loses_to_a_moving_one() -> None:
    sha = "b" * 64
    width = 0.5
    motion = [0.2] * 20 + [9.0] * 20
    signals = ClipSignals(
        SIGNALS_VERSION, 20.0, width, (-30.0,) * 40, tuple(motion), (0.0,) * 40, None
    )
    still = Segment(Segment.make_id(sha, 0.0, 9.0), sha, 0.0, 9.0, 0.7, 0.5)
    moving = Segment(Segment.make_id(sha, 10.0, 19.0), sha, 10.0, 19.0, 0.7, 0.5)
    heroes = (
        replace(_heroes(1)[0], segment_id=still.id, sha256=sha, peak=4.0, score=0.8, rank=1),
        replace(_heroes(1)[0], segment_id=moving.id, sha256=sha, peak=14.0, score=0.7, rank=2),
    )
    options = hooks.candidates(heroes, [still, moving], {sha: signals})
    assert options[0].segment_id == moving.id


def test_the_window_is_at_most_two_seconds_and_inside_its_segment() -> None:
    segments = {s.id: s for s in _segments()}
    for option in _options():
        segment = segments[option.segment_id]
        assert option.duration <= hooks.HOOK_MAX_SECONDS + 1e-6
        assert segment.start - 1e-6 <= option.start and option.end <= segment.end + 1e-6


def test_choose_off_manual_and_a_manual_pick_that_is_gone() -> None:
    options = _options()
    assert hooks.choose(options, "off", None) == (None, None)
    picked, warning = hooks.choose(options, "manual", options[-1].segment_id)
    assert picked == options[-1] and warning is None
    fallback, warning = hooks.choose(options, "manual", "gone")
    assert fallback == options[0] and warning == hooks.MANUAL_GONE
    assert hooks.choose((), "on", None) == (None, None)


# --- placing it -------------------------------------------------------------------


@pytest.mark.parametrize("target", [15.0, 20.0, 30.0, 45.0, 60.0])
@pytest.mark.parametrize("grid", [True, False])
@pytest.mark.parametrize("seed", [1, 7, 23])
def test_with_a_hook_the_duration_stays_within_five_percent(
    target: float, grid: bool, seed: int
) -> None:
    result = _plan(target, grid=grid, seed=seed)
    teaser = _hook(result)
    assert abs(result.duration_seconds - target) <= DURATION_TOLERANCE * target
    without = _plan(target, grid=grid, seed=seed, hook=None)
    assert result.duration_seconds == without.duration_seconds, "the hook spends no extra time"
    assert teaser.timeline_in == 0.0
    assert teaser.duration <= hooks.HOOK_MAX_SECONDS + 1e-6
    assert teaser.speed == 1.0, "a teaser is real-time footage"
    assert teaser.beat_in is not None and teaser.beat_out is not None


@pytest.mark.parametrize("target", [15.0, 30.0, 60.0])
def test_the_timeline_has_no_gap_and_no_overlap(target: float) -> None:
    result = _plan(target)
    cursor = 0.0
    for shot in result.shots:
        assert shot.timeline_in == pytest.approx(cursor, abs=1e-6)
        cursor = shot.timeline_out
    assert cursor == pytest.approx(result.duration_seconds, abs=1e-6)


def test_a_short_edit_moves_the_moment_a_long_one_may_show_it_again() -> None:
    short = _plan(HOOK_DUPLICATE_MIN_TARGET - 5.0)
    teaser = _hook(short)
    assert short.hook_placement == "moved"
    start, end = teaser.footprint()
    for shot in short.shots:
        if shot is teaser or shot.sha256 != teaser.sha256:
            continue
        assert shot.footprint()[1] <= start + 1e-6 or shot.footprint()[0] >= end - 1e-6
    assert not any(s.slow and s.segment_id == teaser.segment_id for s in short.shots)

    long = _plan(60.0)
    teaser = _hook(long)
    assert long.hook_placement == "duplicated"
    again = [s for s in long.shots if not s.hook and s.segment_id == teaser.segment_id]
    assert again, "the moment plays again in place"


def test_the_hook_is_deterministic_and_regenerate_keeps_it_first() -> None:
    assert _plan(30.0).to_json() == _plan(30.0).to_json()
    other = _plan(30.0, seed=8)
    assert _hook(other).segment_id == _hook(_plan(30.0)).segment_id


def test_a_locked_cut_at_the_opening_wins_over_the_hook() -> None:
    base = _plan(30.0, hook=None)
    first = lock(base, base.shots[0].id)
    result = _plan(30.0, locked=(first,))
    assert not any(s.hook for s in result.shots)
    assert hooks.LOCKED_OPENING in result.warnings
    assert first in result.shots


def test_a_locked_hook_stays_exactly_as_it_is() -> None:
    teaser = _hook(_plan(30.0))
    locked = replace(teaser, locked=True)
    result = _plan(30.0, locked=(locked,))
    assert [s for s in result.shots if s.hook] == [locked]
    assert hooks.LOCKED_OPENING not in result.warnings


def test_a_removed_hook_moment_means_no_teaser_and_says_so() -> None:
    option = _options()[0]
    segment = next(s for s in _segments() if s.id == option.segment_id)
    result = _plan(30.0, removed=frozenset({segment.id}))
    assert not any(s.hook for s in result.shots)
    assert hooks.NO_ROOM_FOR_HOOK in result.warnings


# --- overrides (P2) ---------------------------------------------------------------


def test_hook_overrides_fold_into_the_decisions() -> None:
    current = _plan(30.0)
    off = apply(Decisions(), current, Override(kind="hook_off"))
    assert off.hook == "off"
    picked = apply(off, current, Override(kind="hook_choose", segment_id="seg"))
    assert (picked.hook, picked.hook_segment) == ("manual", "seg")
    back = apply(picked, current, Override(kind="hook_on"))
    assert (back.hook, back.hook_segment) == ("on", None)
    with pytest.raises(OverrideError):
        apply(Decisions(), current, Override(kind="hook_choose"))


def test_decisions_and_plans_round_trip_the_hook() -> None:
    decisions = Decisions(hook="manual", hook_segment="abc")
    assert Decisions.from_json(decisions.to_json()) == decisions
    assert Decisions.from_json({}).hook == "on", "an edit saved before hooks gets the AI's pick"
    with pytest.raises(ValueError):
        Decisions.from_json({"hook": "sometimes"})
    teaser = _hook(_plan(30.0))
    assert Shot.from_json(teaser.to_json()) == teaser
