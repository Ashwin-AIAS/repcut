"""The cut planner: usable footage + a beat grid + decisions -> an edit.

A pure function. No I/O, no clock, no global randomness: the same inputs and
the same seed give the same plan, byte for byte, which is what makes the rest
of the product testable (and what P2's re-sync relies on to leave untouched
decisions untouched).

How a plan is made:

1. **Where the music starts.** On a downbeat, chosen so the track's drop lands
   about ``DROP_POSITION`` of the way into the edit. Without a drop, the window
   with the most energy that still fits the target.
2. **Where the cuts go.** Every unlocked cut is a beat of the grid. Shot length
   follows the music: ``pace.calm_beats`` in quiet bars, ``pace.peak_beats`` in
   loud ones, and a cut is forced onto the drop's downbeat. No shot is shorter
   than ``pace.min_shot_seconds`` or one beat.
3. **Which footage fills each shot.** Loudest music first, so the drop gets the
   highest-energy footage; each slot takes the candidate whose energy matches
   the music's, weighted by quality, with a small seeded variation (that is
   what "regenerate" changes). Forced segments are placed before anything else.
4. **Locked shots** are copied verbatim. Their source ranges are not reused and
   their timeline ranges are not re-planned; only the gaps between them are.
5. **Hero moments** (Prompt 07) are placed before any other footage, as
   half-speed shots: the best one opens the drop when the drop's shot is a
   length slow motion suits, the rest take the loudest such shots. Each keeps
   a full-length normal-speed window around its peak in reserve, so the
   export can refuse the interpolation (P1) without re-planning. At most one
   per ``pace.slowmo_every_seconds`` of edit (``SLOWMO_EVERY_SECONDS`` by
   default) unless the person asked for more.
6. **The hook** (Prompt 09), when there is one, opens the edit: a normal-speed
   teaser of the best moment from timeline zero to the last beat at or under
   ``HOOK_MAX_SECONDS``. The edit proper fills the rest, so the duration budget
   is untouched. With a target of ``HOOK_DUPLICATE_MIN_TARGET`` or more the
   moment may also play again in place (its footage stays available); in a
   shorter edit it is moved, its footage used once, at the opening.

With no music (``grid`` None) the plan cuts on a pacing grid instead - the
theme's tempo, no beat snap - so an empty library still produces an edit.
"""

import math
import random
from dataclasses import dataclass, field, replace

from repcut.edit.hook import HOOK_MAX_SECONDS, LOCKED_OPENING, NO_ROOM_FOR_HOOK, HookCandidate
from repcut.edit.types import BeatGrid, HookPlacement, Pace, Plan, Segment, Shot, SourceRange
from repcut.highlights.detector import HeroMoment
from repcut.slowmo.limits import SLOWEST_SPEED

# Where the drop lands, as a fraction of the edit.
DROP_POSITION = 0.4

# Music energy (normalised bar energy) at or above which a bar counts as loud.
LOUD_BAR = 0.6

# Pacing grid without music: the theme's energy maps onto this tempo range.
PACING_BPM_LOW = 90.0
PACING_BPM_HIGH = 140.0

# Weights in a candidate's score. Energy match outweighs quality: the drop is
# where the edit is judged, and it must get the footage with the most energy.
_MATCH_WEIGHT = 2.0
_QUALITY_WEIGHT = 0.5
_VARIATION = 0.15
_REUSE_PENALTY = 0.35

_EPSILON = 1e-6

_REPEAT_WARNING = "not enough usable footage; some shots repeat"

# Slow motion suits shots of this length: shorter reads as a stutter, longer
# drags (and costs render time; the guide keeps slow motion to hero moments).
SLOWMO_MIN_SECONDS = 1.0
SLOWMO_MAX_SECONDS = 4.0
# One AI-chosen slow-motion shot per this many seconds of edit, at least one.
SLOWMO_EVERY_SECONDS = 15.0
ASKED_REASON = "you asked for slow motion here"
NO_ROOM_FOR_SLOWMO = "a slow-motion moment did not fit this edit's shots; it plays normally"
# An edit at least this long may show its hook twice (teaser, then in place);
# a shorter one moves it: in 15 s the same moment twice reads as a mistake.
HOOK_DUPLICATE_MIN_TARGET = 20.0


@dataclass(frozen=True, slots=True)
class PlanInputs:
    segments: tuple[Segment, ...]
    grid: BeatGrid | None
    target_seconds: float
    pace: Pace
    seed: int
    locked: tuple[Shot, ...] = ()
    removed: frozenset[str] = field(default_factory=frozenset)
    forced: frozenset[str] = field(default_factory=frozenset)
    removed_ranges: tuple[SourceRange, ...] = ()
    heroes: tuple[HeroMoment, ...] = ()
    slowmo_off: frozenset[str] = field(default_factory=frozenset)
    slowmo_on: frozenset[str] = field(default_factory=frozenset)
    hook: HookCandidate | None = None


@dataclass(slots=True)
class _Slot:
    start: float
    end: float
    beat_in: int | None
    beat_out: int | None
    energy: float
    shot: Shot | None = None

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass(slots=True)
class _Piece:
    segment: Segment
    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start


def pacing_grid(pace: Pace, seconds: float) -> BeatGrid:
    """A metronome at the theme's tempo, for an edit with no music."""
    bpm = PACING_BPM_LOW + (PACING_BPM_HIGH - PACING_BPM_LOW) * pace.energy
    period = 60.0 / bpm
    count = math.ceil(seconds / period) + 16
    return BeatGrid(
        beats=tuple(round(index * period, 4) for index in range(count)),
        downbeat_offset=0,
        bar_energy=(),
        drop_bar=None,
        snapped=False,
        track_sha256=None,
        bpm=round(bpm, 2),
    )


def _music_start(grid: BeatGrid, target: float) -> int:
    """The downbeat the music starts on."""
    beats = grid.beats
    downbeats = [index for index in range(len(beats)) if grid.is_downbeat(index)] or [0]
    fitting = [index for index in downbeats if beats[-1] - beats[index] >= target - _EPSILON]
    candidates = fitting or [downbeats[0]]
    drop = grid.drop_beat
    if drop is not None and drop < len(beats):
        before = [index for index in candidates if index <= drop] or candidates
        wanted = DROP_POSITION * target
        return min(before, key=lambda index: (abs(beats[drop] - beats[index] - wanted), index))
    if not grid.bar_energy:
        return candidates[0]

    def window_energy(index: int) -> float:
        end_time = beats[index] + target
        inside = [
            grid.energy_at(other)
            for other in range(index, len(beats))
            if beats[other] < end_time and grid.is_downbeat(other)
        ]
        return sum(inside) / len(inside) if inside else 0.0

    return max(candidates, key=lambda index: (round(window_energy(index), 6), -index))


def _end_beat(grid: BeatGrid, start: int, target: float) -> int:
    beats = grid.beats
    origin = beats[start]
    later = range(start + 1, len(beats))
    return min(later, key=lambda index: (abs(beats[index] - origin - target), index))


def _gaps(end: float, locked: tuple[Shot, ...]) -> list[tuple[float, float]]:
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for shot in sorted(locked, key=lambda s: s.timeline_in):
        if shot.timeline_in > cursor + _EPSILON:
            gaps.append((cursor, min(shot.timeline_in, end)))
        cursor = max(cursor, shot.timeline_out)
    if end > cursor + _EPSILON:
        gaps.append((cursor, end))
    return [(start, stop) for start, stop in gaps if stop - start > _EPSILON]


def _slots_for_gap(
    gap: tuple[float, float],
    grid: BeatGrid,
    origin: int,
    end_beat: int | None,
    pace: Pace,
) -> list[_Slot]:
    """Shot slots across one gap, every interior edge on a beat."""
    start, stop = gap
    beats = grid.beats
    zero = beats[origin]

    def at(index: int) -> float:
        return round(beats[index] - zero, 6)

    beat_period = (beats[-1] - beats[0]) / max(1, len(beats) - 1)
    shortest = max(pace.min_shot_seconds, beat_period - _EPSILON)
    on_grid = [
        index
        for index in range(origin, len(beats))
        if start - _EPSILON <= at(index) <= stop + _EPSILON
    ]
    drop = grid.drop_beat

    slots: list[_Slot] = []
    cursor = start
    cursor_beat: int | None = next((i for i in on_grid if abs(at(i) - start) < _EPSILON), None)
    stop_beat = end_beat if end_beat is not None and abs(at(end_beat) - stop) < _EPSILON else None
    while stop - cursor > _EPSILON:
        first = next((i for i in on_grid if at(i) > cursor + _EPSILON), None)
        reference = cursor_beat if cursor_beat is not None else first
        if first is None or reference is None:
            break
        energy = grid.energy_at(reference) if grid.bar_energy else 0.5
        loud = energy >= LOUD_BAR
        step = pace.peak_beats if loud else pace.calm_beats
        target_index = (cursor_beat + step) if cursor_beat is not None else (first + step - 1)
        # The drop's downbeat is always a cut.
        if drop is not None and reference < drop < target_index and at(drop) - cursor >= shortest:
            target_index = drop
        while target_index < len(beats) and at(target_index) - cursor < shortest - _EPSILON:
            target_index += 1
        if target_index >= len(beats) or at(target_index) >= stop - shortest + _EPSILON:
            slots.append(_Slot(cursor, stop, cursor_beat, stop_beat, energy))
            break
        slots.append(_Slot(cursor, at(target_index), cursor_beat, target_index, energy))
        cursor = at(target_index)
        cursor_beat = target_index
    return slots


def _subtract(piece: _Piece, start: float, end: float, minimum: float) -> list[_Piece]:
    """``piece`` minus ``[start, end)``, keeping only remainders long enough to use."""
    if end <= piece.start or start >= piece.end:
        return [piece]
    rest = []
    if start - piece.start >= minimum:
        rest.append(_Piece(piece.segment, piece.start, start))
    if piece.end - end >= minimum:
        rest.append(_Piece(piece.segment, end, piece.end))
    return rest


def _candidates(inputs: PlanInputs) -> list[_Piece]:
    minimum = inputs.pace.min_shot_seconds
    pieces = [
        _Piece(segment, segment.start, segment.end)
        for segment in sorted(inputs.segments, key=lambda s: s.id)
        if segment.id not in inputs.removed and segment.duration >= minimum
    ]
    # Footage already placed by a locked shot, and footage a person removed,
    # is not available to anything else.
    taken = [(s.sha256, *s.footprint()) for s in inputs.locked]
    taken += [(r.sha256, r.start, r.end) for r in inputs.removed_ranges]
    for sha256, start, end in taken:
        next_pieces: list[_Piece] = []
        for piece in pieces:
            if piece.segment.sha256 == sha256:
                next_pieces.extend(_subtract(piece, start, end, minimum))
            else:
                next_pieces.append(piece)
        pieces = next_pieces
    return pieces


class _Filler:
    """Places footage into slots, consuming what it uses."""

    def __init__(self, inputs: PlanInputs, rng: random.Random) -> None:
        self.inputs = inputs
        self.rng = rng
        self.pieces = _candidates(inputs)
        self.uses: dict[str, int] = {}
        self.minimum = inputs.pace.min_shot_seconds
        # Footage energy as a rank (0 = calmest, 1 = most energetic), so it is
        # comparable with the music's normalised bar energy whatever the
        # session's absolute levels were.
        ordered = sorted(inputs.segments, key=lambda s: (s.energy, s.id))
        span = max(1, len(ordered) - 1)
        self.rank = {segment.id: index / span for index, segment in enumerate(ordered)}

    def _score(self, piece: _Piece, slot: _Slot) -> float:
        segment = piece.segment
        match = 1.0 - abs(self.rank.get(segment.id, 0.5) - slot.energy)
        return (
            _MATCH_WEIGHT * match
            + _QUALITY_WEIGHT * segment.quality
            - _REUSE_PENALTY * self.uses.get(segment.id, 0)
            + self.rng.uniform(0.0, _VARIATION)
        )

    def _take(self, piece: _Piece, slot: _Slot) -> Shot:
        spare = piece.length - slot.length
        offset = round(self.rng.uniform(0.0, spare), 3) if spare > _EPSILON else 0.0
        source_in = round(piece.start + offset, 3)
        source_out = round(source_in + slot.length, 3)
        self.pieces.remove(piece)
        self.pieces.extend(_subtract(piece, source_in, source_out, self.minimum))
        segment = piece.segment
        self.uses[segment.id] = self.uses.get(segment.id, 0) + 1
        return Shot(
            id=Shot.make_id(segment.id, source_in),
            segment_id=segment.id,
            sha256=segment.sha256,
            source_in=source_in,
            source_out=source_out,
            timeline_in=round(slot.start, 6),
            timeline_out=round(slot.end, 6),
            beat_in=slot.beat_in,
            beat_out=slot.beat_out,
        )

    def fill_slow(self, slot: _Slot, hero: HeroMoment, reason: str) -> bool:
        """``slot`` as a half-speed shot of ``hero``, centred on its peak.

        Takes the full-length window around the peak (the normal-speed
        fallback) from the available footage; the slow window is its middle
        half. False when no available piece of the hero's segment is long
        enough for the fallback.
        """
        length = slot.length
        fitting = [
            piece
            for piece in self.pieces
            if piece.segment.id == hero.segment_id and piece.length >= length - _EPSILON
        ]
        if not fitting:
            return False
        piece = min(fitting, key=lambda p: (abs((p.start + p.end) / 2 - hero.peak), p.start))
        fallback_in = min(max(hero.peak - length / 2, piece.start), piece.end - length)
        fallback_in = round(fallback_in, 3)
        slow_length = length * SLOWEST_SPEED
        source_in = min(
            max(hero.peak - slow_length / 2, fallback_in), fallback_in + length - slow_length
        )
        source_in = round(source_in, 3)
        self.pieces.remove(piece)
        self.pieces.extend(_subtract(piece, fallback_in, fallback_in + length, self.minimum))
        segment = piece.segment
        self.uses[segment.id] = self.uses.get(segment.id, 0) + 1
        slot.shot = Shot(
            id=Shot.make_id(segment.id, source_in),
            segment_id=segment.id,
            sha256=segment.sha256,
            source_in=source_in,
            source_out=round(source_in + slow_length, 3),
            timeline_in=round(slot.start, 6),
            timeline_out=round(slot.end, 6),
            beat_in=slot.beat_in,
            beat_out=slot.beat_out,
            speed=SLOWEST_SPEED,
            fallback_in=fallback_in,
            hero_score=hero.score,
            hero_reason=reason[:200],
        )
        return True

    def hook_window(self, hook: HookCandidate, length: float, consume: bool) -> float | None:
        """Where ``length`` seconds of the hook start in its clip, or None if unavailable.

        Centred on the moment's peak, inside its window. ``consume`` takes the
        footage from the pool (a moved hook); otherwise it stays available for
        the edit proper (a duplicated hook) but still respects removals.
        """
        wanted = min(max(hook.peak - length / 2, hook.start), hook.end - length)
        if consume:
            fitting = [
                p
                for p in self.pieces
                if p.segment.id == hook.segment_id and p.length >= length - _EPSILON
            ]
        else:
            fitting = [
                _Piece(s, s.start, s.end)
                for s in self.inputs.segments
                if s.id == hook.segment_id and s.duration >= length - _EPSILON
            ]
            for removed in self.inputs.removed_ranges:
                fitting = [
                    rest
                    for piece in fitting
                    for rest in (
                        _subtract(piece, removed.start, removed.end, length - _EPSILON)
                        if piece.segment.sha256 == removed.sha256
                        else [piece]
                    )
                ]
        if not fitting or hook.segment_id in self.inputs.removed:
            return None
        piece = min(
            fitting,
            key=lambda p: (abs(min(max(wanted, p.start), p.end - length) - wanted), p.start),
        )
        source_in = round(min(max(wanted, piece.start), piece.end - length), 3)
        if consume:
            self.pieces.remove(piece)
            self.pieces.extend(_subtract(piece, source_in, source_in + length, self.minimum))
            self.uses[piece.segment.id] = self.uses.get(piece.segment.id, 0) + 1
        return source_in

    def fill(self, slot: _Slot, only: frozenset[str] | None = None) -> bool:
        fitting = [
            piece
            for piece in self.pieces
            if piece.length >= slot.length - _EPSILON and (only is None or piece.segment.id in only)
        ]
        if not fitting:
            return False
        scored = [
            (self._score(piece, slot), piece.segment.id, piece.start, piece) for piece in fitting
        ]
        best = max(scored, key=lambda item: (item[0], item[1], -item[2]))[3]
        slot.shot = self._take(best, slot)
        return True

    def reuse(self, slot: _Slot) -> bool:
        """Last resort when footage runs out: repeat a segment rather than leave a hole."""
        fitting = sorted(
            (
                s
                for s in self.inputs.segments
                if s.id not in self.inputs.removed
                and s.duration >= slot.length
                and not any(
                    r.sha256 == s.sha256 and r.start < s.end and r.end > s.start
                    for r in self.inputs.removed_ranges
                )
            ),
            key=lambda s: (self.uses.get(s.id, 0), -s.quality, s.id),
        )
        if not fitting:
            return False
        piece = _Piece(fitting[0], fitting[0].start, fitting[0].end)
        self.pieces.append(piece)
        slot.shot = self._take(piece, slot)
        return True


def plan(inputs: PlanInputs) -> Plan:
    """The edit for these inputs. Deterministic: same inputs, same plan."""
    if inputs.target_seconds <= 0:
        raise ValueError("target_seconds must be positive")
    # Not a security context: a seeded generator is the point, so "regenerate"
    # (a new seed) gives a different edit and the same seed gives the same one.
    rng = random.Random(inputs.seed)  # noqa: S311
    warnings: list[str] = []
    grid = inputs.grid
    if grid is None or len(grid.beats) < 8:
        if grid is not None:
            warnings.append("the track's beat grid is too short; cut to the theme's pace instead")
        grid = pacing_grid(inputs.pace, inputs.target_seconds)
    origin = _music_start(grid, inputs.target_seconds)
    end_beat = _end_beat(grid, origin, inputs.target_seconds)
    zero = grid.beats[origin]
    end = round(grid.beats[end_beat] - zero, 6)
    if grid.beats[-1] - zero < inputs.target_seconds * 0.95:
        warnings.append("the track is shorter than the edit; the edit ends with the music")

    locked = tuple(sorted(inputs.locked, key=lambda shot: shot.timeline_in))
    if locked and locked[-1].timeline_out > end + _EPSILON:
        end = locked[-1].timeline_out
        end_beat_for_gap: int | None = None
    else:
        end_beat_for_gap = end_beat

    filler = _Filler(inputs, rng)
    opening, placement, hook_warning = _hook_shot(inputs, grid, origin, end, locked, filler)
    if hook_warning is not None:
        warnings.append(hook_warning)
    hero_inputs = inputs
    if opening is not None and placement == "moved":
        # A moved hook is that moment's one appearance: it is not slowed again in place.
        hero_inputs = replace(
            inputs, heroes=tuple(h for h in inputs.heroes if h.segment_id != opening.segment_id)
        )

    slots: list[_Slot] = []
    opening_end = opening.timeline_out if opening is not None else 0.0
    for start, stop in _gaps(end, locked):
        start = max(start, opening_end)
        if stop - start > _EPSILON:
            slots.extend(_slots_for_gap((start, stop), grid, origin, end_beat_for_gap, inputs.pace))
    # The shot that opens the drop is filled first and asks for the most
    # energetic footage there is; then the loudest music, then the rest.
    drop_beat = grid.drop_beat
    for slot in slots:
        if drop_beat is not None and slot.beat_in == drop_beat:
            slot.energy = 1.0
    order = sorted(
        range(len(slots)),
        key=lambda i: (slots[i].beat_in != drop_beat, -round(slots[i].energy, 6), slots[i].start),
    )
    warnings.extend(_place_heroes(hero_inputs, slots, filler, drop_beat))
    for segment_id in sorted(inputs.forced):
        if not any(piece.segment.id == segment_id for piece in filler.pieces):
            warnings.append("a segment you asked to include is no longer usable footage")
            continue
        for index in order:
            if slots[index].shot is None and filler.fill(
                slots[index], only=frozenset({segment_id})
            ):
                break
        else:
            warnings.append("a segment you asked to include is shorter than any shot")

    for index in order:
        slot = slots[index]
        repeated = slot.shot is None and not filler.fill(slot) and filler.reuse(slot)
        if repeated and _REPEAT_WARNING not in warnings:
            warnings.append(_REPEAT_WARNING)

    shots = [slot.shot for slot in slots if slot.shot is not None]
    if len(shots) < len(slots):
        warnings.append("not enough usable footage to fill the edit")
        first_hole = min(slot.start for slot in slots if slot.shot is None)
        shots = [shot for shot in shots if shot.timeline_in < first_hole]
        end = max([first_hole, *(shot.timeline_out for shot in locked)])
    shots.extend(locked)
    if opening is not None:
        shots.append(opening)
    shots.sort(key=lambda shot: shot.timeline_in)
    hook_shot = next((shot for shot in shots if shot.hook), None)

    return Plan(
        shots=tuple(shots),
        duration_seconds=round(end, 6),
        target_seconds=inputs.target_seconds,
        music_start_seconds=round(zero, 6) if grid.snapped else 0.0,
        snapped=grid.snapped,
        track_sha256=grid.track_sha256,
        seed=inputs.seed,
        warnings=tuple(warnings),
        hook_placement=_placement(hook_shot, shots) if hook_shot is not None else None,
    )


def _hook_end(grid: BeatGrid, origin: int, end: float, longest: float, pace: Pace) -> int | None:
    """The beat the hook ends on: the last one at or under ``longest`` from the start."""
    beats = grid.beats
    zero = beats[origin]
    beat_period = (beats[-1] - beats[0]) / max(1, len(beats) - 1)
    shortest = max(pace.min_shot_seconds, beat_period - _EPSILON)
    fitting = [
        index
        for index in range(origin + 1, len(beats))
        if shortest - _EPSILON <= beats[index] - zero <= longest + _EPSILON
        # The edit proper keeps at least one shot after the teaser.
        and end - (beats[index] - zero) >= shortest - _EPSILON
    ]
    return fitting[-1] if fitting else None


def _hook_shot(
    inputs: PlanInputs,
    grid: BeatGrid,
    origin: int,
    end: float,
    locked: tuple[Shot, ...],
    filler: "_Filler",
) -> tuple[Shot | None, HookPlacement | None, str | None]:
    """(the opening teaser, how its footage is used, a warning). Consumes footage when moved."""
    hook = inputs.hook
    if hook is None or any(shot.hook for shot in locked):
        # No hook asked for, or the person locked the one they have: it stays as it is.
        return None, None, None
    end_index = _hook_end(grid, origin, end, min(HOOK_MAX_SECONDS, hook.duration), inputs.pace)
    if end_index is None:
        return None, None, None
    length = round(grid.beats[end_index] - grid.beats[origin], 6)
    if any(shot.timeline_in < length - _EPSILON for shot in locked):
        return None, None, LOCKED_OPENING
    placement: HookPlacement = (
        "duplicated" if inputs.target_seconds >= HOOK_DUPLICATE_MIN_TARGET else "moved"
    )
    source_in = filler.hook_window(hook, length, consume=placement == "moved")
    if source_in is None:
        return None, None, NO_ROOM_FOR_HOOK
    shot = Shot(
        id=Shot.make_id(f"hook:{hook.segment_id}", source_in),
        segment_id=hook.segment_id,
        sha256=hook.sha256,
        source_in=source_in,
        source_out=round(source_in + length, 3),
        timeline_in=0.0,
        timeline_out=length,
        beat_in=origin,
        beat_out=end_index,
        hero_score=hook.score,
        hero_reason=("opening teaser: " + "; ".join(hook.reasons))[:200],
        hook=True,
    )
    return shot, placement, None


def _placement(hook: Shot, shots: list[Shot]) -> HookPlacement:
    """What the edit did with the hook's moment: shown again in place, or only at the start."""
    start, stop = hook.footprint()
    again = any(
        not shot.hook
        and shot.sha256 == hook.sha256
        and shot.footprint()[0] < stop
        and shot.footprint()[1] > start
        for shot in shots
    )
    return "duplicated" if again else "moved"


def slow_candidates(inputs: PlanInputs) -> list[tuple[HeroMoment, str, bool]]:
    """(moment, reason, asked) in placement order: the person's asks first, then the AI's."""
    by_id = {segment.id: segment for segment in inputs.segments}
    out: list[tuple[HeroMoment, str, bool]] = []
    heroes = {hero.segment_id: hero for hero in inputs.heroes}
    for segment_id in sorted(inputs.slowmo_on):
        segment = by_id.get(segment_id)
        if segment is None or segment_id in inputs.removed:
            continue
        hero = heroes.get(segment_id) or HeroMoment(
            segment_id=segment_id,
            sha256=segment.sha256,
            peak=round((segment.start + segment.end) / 2, 3),
            score=0.0,
            rank=0,
            reasons=(ASKED_REASON,),
            signals={},
        )
        out.append((hero, ASKED_REASON, True))
    for hero in sorted(inputs.heroes, key=lambda h: (h.rank, h.segment_id)):
        if hero.segment_id in inputs.slowmo_off or hero.segment_id in inputs.slowmo_on:
            continue
        if hero.segment_id in inputs.removed or hero.segment_id not in by_id:
            continue
        out.append((hero, hero.reason, False))
    return out


def _place_heroes(
    inputs: PlanInputs, slots: list[_Slot], filler: _Filler, drop_beat: int | None
) -> list[str]:
    """Fill slots with half-speed hero shots. Returns warnings."""
    allowance = max(1, int(inputs.target_seconds // inputs.pace.slowmo_every_seconds))
    already = sum(1 for shot in inputs.locked if shot.slow)
    placed_ai = 0
    warnings: list[str] = []

    def suitable(slot: _Slot) -> bool:
        return (
            slot.shot is None
            and SLOWMO_MIN_SECONDS - _EPSILON <= slot.length <= SLOWMO_MAX_SECONDS + _EPSILON
        )

    for hero, reason, asked in slow_candidates(inputs):
        if not asked and already + placed_ai >= allowance:
            break
        order = sorted(
            (index for index in range(len(slots)) if suitable(slots[index])),
            key=lambda i: (
                slots[i].beat_in != drop_beat or drop_beat is None,
                -round(slots[i].energy, 6),
                slots[i].start,
            ),
        )
        if not any(filler.fill_slow(slots[index], hero, reason) for index in order):
            if asked and NO_ROOM_FOR_SLOWMO not in warnings:
                warnings.append(NO_ROOM_FOR_SLOWMO)
            continue
        if not asked:
            placed_ai += 1
    return warnings


def lock(plan_: Plan, shot_id: str) -> Shot:
    """The shot ``shot_id`` of ``plan_``, marked locked, for ``Decisions.locked``."""
    for shot in plan_.shots:
        if shot.id == shot_id:
            return replace(shot, locked=True)
    raise KeyError(shot_id)


__all__ = [
    "ASKED_REASON",
    "DROP_POSITION",
    "HOOK_DUPLICATE_MIN_TARGET",
    "LOUD_BAR",
    "NO_ROOM_FOR_SLOWMO",
    "SLOWMO_EVERY_SECONDS",
    "SLOWMO_MAX_SECONDS",
    "SLOWMO_MIN_SECONDS",
    "PlanInputs",
    "lock",
    "pacing_grid",
    "plan",
    "slow_candidates",
]
