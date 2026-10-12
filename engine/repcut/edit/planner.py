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

With no music (``grid`` None) the plan cuts on a pacing grid instead - the
theme's tempo, no beat snap - so an empty library still produces an edit.
"""

import math
import random
from dataclasses import dataclass, field, replace

from repcut.edit.types import BeatGrid, Pace, Plan, Segment, Shot

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
    for shot in inputs.locked:
        next_pieces: list[_Piece] = []
        for piece in pieces:
            if piece.segment.sha256 == shot.sha256:
                next_pieces.extend(_subtract(piece, shot.source_in, shot.source_out, minimum))
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
                if s.id not in self.inputs.removed and s.duration >= slot.length
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

    slots: list[_Slot] = []
    for gap in _gaps(end, locked):
        slots.extend(_slots_for_gap(gap, grid, origin, end_beat_for_gap, inputs.pace))

    filler = _Filler(inputs, rng)
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
    shots.sort(key=lambda shot: shot.timeline_in)

    return Plan(
        shots=tuple(shots),
        duration_seconds=round(end, 6),
        target_seconds=inputs.target_seconds,
        music_start_seconds=round(zero, 6) if grid.snapped else 0.0,
        snapped=grid.snapped,
        track_sha256=grid.track_sha256,
        seed=inputs.seed,
        warnings=tuple(warnings),
    )


def lock(plan_: Plan, shot_id: str) -> Shot:
    """The shot ``shot_id`` of ``plan_``, marked locked, for ``Decisions.locked``."""
    for shot in plan_.shots:
        if shot.id == shot_id:
            return replace(shot, locked=True)
    raise KeyError(shot_id)


__all__ = ["DROP_POSITION", "LOUD_BAR", "PlanInputs", "lock", "pacing_grid", "plan"]
