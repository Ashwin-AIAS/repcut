""" "What Repcut did": the processing summary, read from the edit's own record.

The panel's whole point is that it is true (P4: transparency as a feature), so
nothing here is a template filled with a guess. ``summarise`` reads one JSON
document - the *edit record*, built by ``review.service`` from the stored plan,
decisions, grades, captions and the dead space the edit skipped - and every
claim it makes carries **facts**: JSON pointers into that same record with the
value found there. A claim's number is the number of its facts. ``verify``
re-reads every pointer, so a claim that the record does not back is a failure,
not a sentence on screen (the gate runs it on every summary it builds).

Pure: a mapping in, claims out. No database, no clock.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

# Below these, a correction is too small to be worth a line.
WHITE_BALANCE_NOTE_K: Final = 50.0
EXPOSURE_NOTE_STOPS: Final = 0.05


@dataclass(frozen=True, slots=True)
class Fact:
    """A JSON pointer (RFC 6901) into the edit record, and the value it holds."""

    pointer: str
    value: Any

    def to_json(self) -> dict[str, Any]:
        return {"pointer": self.pointer, "value": self.value}


@dataclass(frozen=True, slots=True)
class Claim:
    id: str
    text: str
    facts: tuple[Fact, ...]
    # The claim's count, when it states one: it must equal the facts behind it.
    count: int | None = None
    # Facts the claim also rests on that are not what it counts (the music was on).
    context: tuple[Fact, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "count": self.count,
            "facts": [fact.to_json() for fact in (*self.context, *self.facts)],
        }


class PointerError(LookupError):
    """A pointer that names nothing in the record."""


def resolve(record: object, pointer: str) -> object:
    """The value at ``pointer`` in ``record`` (RFC 6901). Raises ``PointerError``."""
    if pointer == "":
        return record
    if not pointer.startswith("/"):
        raise PointerError(pointer)
    node: object = record
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, Mapping):
            if token not in node:
                raise PointerError(pointer)
            node = node[token]
        elif isinstance(node, list):
            if not token.isdigit() or int(token) >= len(node):
                raise PointerError(pointer)
            node = node[int(token)]
        else:
            raise PointerError(pointer)
    return node


def verify(record: Mapping[str, Any], claims: Sequence[Claim]) -> list[str]:
    """Every way ``claims`` are not backed by ``record``. Empty when all are true."""
    problems: list[str] = []
    for claim in claims:
        if not claim.facts:
            problems.append(f"{claim.id}: no fact behind it")
        if claim.count is not None and claim.count != len(claim.facts):
            problems.append(f"{claim.id}: says {claim.count}, backed by {len(claim.facts)}")
        if claim.count is not None and str(claim.count) not in claim.text:
            problems.append(f"{claim.id}: its number is not the one it states")
        for fact in (*claim.context, *claim.facts):
            try:
                found = resolve(record, fact.pointer)
            except PointerError:
                problems.append(f"{claim.id}: {fact.pointer} is not in the record")
                continue
            if found != fact.value:
                problems.append(f"{claim.id}: {fact.pointer} is {found!r}, not {fact.value!r}")
    return problems


def clock(seconds: float) -> str:
    """0:12 style, for a time on the edit's timeline."""
    whole = max(0, round(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _clip(record: Mapping[str, Any], sha256: str) -> str:
    names = record.get("clip_names", {})
    return str(names.get(sha256, "a clip")) if isinstance(names, Mapping) else "a clip"


def _hook(record: Mapping[str, Any]) -> list[Claim]:
    shots = record["plan"]["shots"]
    for index, shot in enumerate(shots):
        if not shot.get("hook"):
            continue
        base = f"/plan/shots/{index}"
        length = shot["timeline_out"] - shot["timeline_in"]
        placement = record["plan"].get("hook_placement")
        tail = {
            "duplicated": "; it plays again later in the edit",
            "moved": "; moved there, so it plays once",
        }.get(str(placement), "")
        facts = [
            Fact(f"{base}/hook", True),
            Fact(f"{base}/timeline_in", shot["timeline_in"]),
            Fact(f"{base}/timeline_out", shot["timeline_out"]),
        ]
        if placement is not None:
            facts.append(Fact("/plan/hook_placement", placement))
        return [
            Claim(
                id="hook",
                text=f"Opens on a {length:.1f} s teaser of the best moment "
                f"({_clip(record, shot['sha256'])}){tail}",
                facts=tuple(facts),
            )
        ]
    return []


def _cuts(record: Mapping[str, Any]) -> list[Claim]:
    plan = record["plan"]
    shots = plan["shots"]
    on_grid = [
        Fact(f"/plan/shots/{i}/beat_in", shot["beat_in"])
        for i, shot in enumerate(shots)
        if i > 0 and shot.get("beat_in") is not None
    ]
    if not on_grid:
        return []
    where = "on the beat of the music" if plan["snapped"] else "at the theme's pace (no music)"
    return [
        Claim(
            id="cuts",
            text=f"{_plural(len(on_grid), 'cut', 'cuts')} {where}",
            facts=tuple(on_grid),
            count=len(on_grid),
            context=(Fact("/plan/snapped", plan["snapped"]),),
        )
    ]


def _music(record: Mapping[str, Any]) -> list[Claim]:
    music = record.get("music")
    if not isinstance(music, Mapping) or music.get("display_name") is None:
        return []
    return [
        Claim(
            id="music",
            text=f"Cut to {music['display_name']} ({music['bpm']:.0f} BPM)",
            facts=(
                Fact("/music/display_name", music["display_name"]),
                Fact("/music/bpm", music["bpm"]),
                Fact("/plan/track_sha256", music["sha256"]),
            ),
        )
    ]


def _slow(record: Mapping[str, Any]) -> list[Claim]:
    claims = []
    for index, shot in enumerate(record["plan"]["shots"]):
        if shot.get("speed", 1.0) >= 1.0:
            continue
        base = f"/plan/shots/{index}"
        claims.append(
            Claim(
                id=f"slowmo-{index}",
                text=f"1 slow-motion shot at {clock(shot['timeline_in'])}, half speed from "
                "your real frames (normal speed if the in-between frames fail the check)",
                facts=(Fact(f"{base}/speed", shot["speed"]),),
                count=1,
                context=(Fact(f"{base}/timeline_in", shot["timeline_in"]),),
            )
        )
    return claims


def _grading(record: Mapping[str, Any]) -> list[Claim]:
    claims: list[Claim] = []
    for c, clip in enumerate(record.get("grading", [])):
        scenes = clip.get("scenes", [])
        name = _clip(record, clip["sha256"])
        balanced = [
            (s, scene) for s, scene in enumerate(scenes)
            if abs(scene["temperature_shift_k"]) >= WHITE_BALANCE_NOTE_K
        ]  # fmt: skip
        if balanced:
            s, scene = max(
                balanced, key=lambda item: (abs(item[1]["temperature_shift_k"]), -item[0])
            )
            shift = scene["temperature_shift_k"]
            claims.append(
                Claim(
                    id=f"white-balance-{c}",
                    text=f"Corrected white balance {shift:+.0f}K on {name}"
                    + (f" (the largest of {len(balanced)} scenes)" if len(balanced) > 1 else ""),
                    facts=(Fact(f"/grading/{c}/scenes/{s}/temperature_shift_k", shift),),
                )
            )
        lit = [
            (s, scene) for s, scene in enumerate(scenes)
            if abs(scene["exposure_stops"]) >= EXPOSURE_NOTE_STOPS
        ]  # fmt: skip
        if lit:
            s, scene = max(lit, key=lambda item: (abs(item[1]["exposure_stops"]), -item[0]))
            stops = scene["exposure_stops"]
            verb = "Brightened" if stops > 0 else "Darkened"
            claims.append(
                Claim(
                    id=f"exposure-{c}",
                    text=f"{verb} {name} by {abs(stops):.2f} stops",
                    facts=(Fact(f"/grading/{c}/scenes/{s}/exposure_stops", stops),),
                )
            )
        looks = {(scene["theme_id"], scene["intensity"]) for scene in scenes}
        if len(looks) == 1 and scenes:
            theme_id, intensity = next(iter(looks))
            claims.append(
                Claim(
                    id=f"look-{c}",
                    text=f"Graded {name} with the {scenes[0]['theme_name']} look at {intensity}%",
                    facts=(
                        Fact(f"/grading/{c}/scenes/0/theme_id", theme_id),
                        Fact(f"/grading/{c}/scenes/0/theme_name", scenes[0]["theme_name"]),
                        Fact(f"/grading/{c}/scenes/0/intensity", intensity),
                    ),
                )
            )
    return claims


def _dead(record: Mapping[str, Any]) -> list[Claim]:
    stretches = record.get("dead_space", [])
    if not stretches:
        return []
    facts = tuple(Fact(f"/dead_space/{i}/start", s["start"]) for i, s in enumerate(stretches))
    seconds = sum(s["end"] - s["start"] for s in stretches)
    return [
        Claim(
            id="dead-space",
            text=f"{_plural(len(facts), 'silent, still stretch', 'silent, still stretches')} "
            f"left out ({seconds:.1f} s with no movement and no speech)",
            facts=facts,
            count=len(facts),
        )
    ]


def _captions(record: Mapping[str, Any]) -> list[Claim]:
    captions = record.get("captions")
    if not isinstance(captions, Mapping):
        return []
    if not captions["enabled"]:
        return [
            Claim(
                id="captions",
                text="Captions are off",
                facts=(Fact("/captions/enabled", False),),
            )
        ]
    lines = captions["lines"]
    if not lines:
        return []
    facts = tuple(Fact(f"/captions/lines/{i}/start", line["start"]) for i, line in enumerate(lines))
    return [
        Claim(
            id="captions",
            text=f"{_plural(len(facts), 'caption line', 'caption lines')} in the "
            f"{captions['style_name']} style, transcribed on this machine",
            facts=facts,
            count=len(facts),
            context=(
                Fact("/captions/enabled", True),
                Fact("/captions/style_name", captions["style_name"]),
            ),
        )
    ]


def _yours(record: Mapping[str, Any]) -> list[Claim]:
    decisions = record["decisions"]
    claims = []
    locked = decisions.get("locked", [])
    if locked:
        facts = tuple(Fact(f"/decisions/locked/{i}/id", s["id"]) for i, s in enumerate(locked))
        claims.append(
            Claim(
                id="locked",
                text=f"{_plural(len(facts), 'cut', 'cuts')} you locked, kept exactly as you set",
                facts=facts,
                count=len(facts),
            )
        )
    removed = decisions.get("removed_ranges", [])
    if removed:
        facts = tuple(
            Fact(f"/decisions/removed_ranges/{i}/start", r["start"]) for i, r in enumerate(removed)
        )
        claims.append(
            Claim(
                id="removed",
                text=f"{_plural(len(facts), 'cut', 'cuts')} you removed, left out",
                facts=facts,
                count=len(facts),
            )
        )
    return claims


def summarise(record: Mapping[str, Any]) -> list[Claim]:
    """Every claim the panel shows, in reading order. Each is backed by facts in ``record``."""
    return [
        *_hook(record),
        *_cuts(record),
        *_music(record),
        *_slow(record),
        *_grading(record),
        *_dead(record),
        *_captions(record),
        *_yours(record),
    ]


__all__ = [
    "Claim",
    "Fact",
    "PointerError",
    "clock",
    "resolve",
    "summarise",
    "verify",
]
