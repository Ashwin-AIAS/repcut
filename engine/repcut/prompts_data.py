"""Build-plan prompt metadata: the shape of an entry, and a parser for the guide.

The build plan is **not** in this repository and must never be. Repcut is a
public repo; the guide is a private document whose local path comes from
``REPCUT_GUIDE_PATH``. This module carries the *mechanism* - the models and the
parser, which are ours to publish - and none of the *content*, which is not.

A clone without the guide gets a working engine and a dashboard that reports the
plan as unavailable. That is the correct outcome, not a bug.

See ``docs/guide-amendments/006-plan-not-in-public-repo.md``.
"""

import re
from pathlib import Path

from pydantic import BaseModel, Field

from repcut.config import get_settings

_EM_DASH = "—"


class GuideUnavailableError(RuntimeError):
    """The build plan could not be read.

    ``reason`` is a fixed, UI-safe sentence. It never contains the guide path: a
    path on this machine carries the OS username (``.claude/rules/secrets.md``).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PromptMetadata(BaseModel):
    """Static metadata defining a build plan prompt."""

    id: str = Field(description="Two-digit prompt identifier")
    name: str = Field(description="Short human-readable title")
    wave: str = Field(description="Wave title the prompt belongs to")
    wave_number: int = Field(description="Numeric wave identifier")
    summary: str = Field(description="One-line summary of what gets built")
    deliverables: list[str] = Field(description="List of key deliverables")
    human_review: bool = Field(description="Whether a human taste/gate review is required")
    human_review_type: str | None = Field(
        default=None, description="Details of human review checkpoint"
    )
    key_tech: list[str] = Field(description="Key technologies used")
    gate_command: str = Field(description="Make command to run verification gate")
    report_file: str = Field(description="Relative path to session report doc")
    estimated_timeline: str = Field(description="Realistic calendar time estimate")


class WaveDefinition(BaseModel):
    """A build wave, as the guide defines it."""

    number: int
    title: str
    prompt_ids: list[str]
    estimated_timeline: str


# --- Structure, not content -------------------------------------------------
#
# Every pattern below describes the guide's *layout* - table shapes and heading
# formats. None of them encodes a prompt title, a summary, a deliverable or a
# timeline, which is the whole point: the mechanism is ours to publish and the
# content is not. The dash class covers em, en and hyphen because the guide uses
# all three as separators.

_DASH = "[\u2014\u2013-]"

# `| 0 - Foundation | ... | 01 |` - the wave plan table.
_WAVE_ROW = re.compile(
    r"^\|\s*(?P<number>\d)\s*" + _DASH + r"\s*(?P<title>[^|]+?)\s*\|"
    r"[^|]*\|\s*(?P<prompts>[^|]+?)\s*\|\s*$",
    re.MULTILINE,
)

# `| Wave 0 | 01 | 1 week |` - the calendar-time table.
_TIMELINE_ROW = re.compile(
    r"^\|\s*Wave\s+(?P<number>\d)\s*\|[^|]*\|\s*(?P<timeline>[^|]+?)\s*\|\s*$",
    re.MULTILINE,
)

# `| 01 | Name | What gets built | review | tech |` - the build plan table.
_PLAN_ROW = re.compile(
    r"^\|\s*(?P<id>\d{2})\s*\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<summary>[^|]+?)\s*\|"
    r"\s*(?P<review>[^|]+?)\s*\|\s*(?P<tech>[^|]+?)\s*\|\s*$",
    re.MULTILINE,
)

# `## PROMPT 04 - Title`, optionally flagged as a human review checkpoint.
_PROMPT_HEADING = re.compile(
    r"^##\s+PROMPT\s+(?P<id>\d{2})\s*" + _DASH + r"\s*(?P<title>.+?)\s*$",
    re.MULTILINE,
)

_DELIVERABLES_HEADING = re.compile(r"^###\s+Deliverables\s*$", re.MULTILINE)
_NEXT_HEADING = re.compile(r"^#{2,3}\s+\S", re.MULTILINE)
_NUMBERED_ITEM = re.compile(r"^\s*\d+\.\s+(?P<text>.+?)\s*$")
_PROMPT_RANGE = re.compile(r"(\d{2})(?:\s*" + _DASH + r"\s*(\d{2}))?")
_TECH_SEPARATOR = re.compile(r"\s*(?:·|\|)\s*")


def _strip_emphasis(cell: str) -> str:
    """Drop markdown emphasis so a table cell reads as plain prose.

    Underscores are deliberately left alone. Stripping them turns a snake_case
    identifier in the prose - `taste_events` - into `tasteevents`.
    """
    return re.sub(r"[*`]+", "", cell).strip()


def _parse_waves(text: str) -> dict[int, WaveDefinition]:
    """Recover the wave definitions from the guide's two wave tables."""
    timelines: dict[int, str] = {
        int(m.group("number")): _strip_emphasis(m.group("timeline"))
        for m in _TIMELINE_ROW.finditer(text)
    }

    waves: dict[int, WaveDefinition] = {}
    for match in _WAVE_ROW.finditer(text):
        number = int(match.group("number"))
        if number in waves:
            continue
        span = _PROMPT_RANGE.search(match.group("prompts"))
        ids: list[str] = []
        if span is not None:
            first = int(span.group(1))
            last = int(span.group(2)) if span.group(2) else first
            ids = [f"{n:02d}" for n in range(first, last + 1)]
        title = _strip_emphasis(match.group("title"))
        waves[number] = WaveDefinition(
            number=number,
            title=f"Wave {number} {_EM_DASH} {title}",
            prompt_ids=ids,
            estimated_timeline=timelines.get(number, "not stated"),
        )
    return waves


def _parse_deliverables(text: str) -> dict[str, list[str]]:
    """Collect the numbered deliverables under each prompt's own section."""
    per_prompt: dict[str, list[str]] = {}
    headings = list(_PROMPT_HEADING.finditer(text))

    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[heading.end() : end]

        marker = _DELIVERABLES_HEADING.search(section)
        if marker is None:
            continue
        rest = section[marker.end() :]
        stop = _NEXT_HEADING.search(rest)
        block = rest[: stop.start()] if stop else rest

        items: list[str] = []
        for line in block.splitlines():
            item = _NUMBERED_ITEM.match(line)
            if item is not None:
                items.append(_strip_emphasis(item.group("text")))
            elif items and line.strip() and not line.lstrip().startswith("#"):
                # A wrapped continuation of the previous numbered item.
                items[-1] = f"{items[-1]} {line.strip()}"
        per_prompt[heading.group("id")] = items

    return per_prompt


def parse_guide(text: str) -> list[PromptMetadata]:
    """Recover every prompt entry the guide defines, in plan order.

    Raises ``GuideUnavailableError`` when the document does not look like the
    build plan - an empty or wrong file is a named failure, never a silently
    empty dashboard.
    """
    waves = _parse_waves(text)
    deliverables = _parse_deliverables(text)

    wave_of: dict[str, int] = {}
    for wave in waves.values():
        for prompt_id in wave.prompt_ids:
            wave_of[prompt_id] = wave.number

    prompts: list[PromptMetadata] = []
    seen: set[str] = set()
    for row in _PLAN_ROW.finditer(text):
        prompt_id = row.group("id")
        if prompt_id in seen:
            continue
        seen.add(prompt_id)

        review = _strip_emphasis(row.group("review"))
        wave_number = wave_of.get(prompt_id, -1)
        owning_wave = waves.get(wave_number)
        tech = _strip_emphasis(row.group("tech"))

        prompts.append(
            PromptMetadata(
                id=prompt_id,
                name=_strip_emphasis(row.group("name")),
                wave=owning_wave.title if owning_wave else "Unassigned",
                wave_number=wave_number,
                summary=_strip_emphasis(row.group("summary")),
                deliverables=deliverables.get(prompt_id, []),
                human_review="yes" in review.lower(),
                human_review_type=review or None,
                key_tech=[part for part in _TECH_SEPARATOR.split(tech) if part],
                gate_command=f"make verify-{prompt_id}",
                report_file=f"docs/reports/prompt-{prompt_id}.md",
                estimated_timeline=(
                    owning_wave.estimated_timeline if owning_wave else "not stated"
                ),
            )
        )

    if not prompts:
        raise GuideUnavailableError(
            "The build plan document was found but no prompt entries could be "
            "read from it. Check that REPCUT_GUIDE_PATH points at the plan."
        )

    prompts.sort(key=lambda prompt: prompt.id)
    return prompts


def load_prompts_metadata(guide_path: Path | None = None) -> list[PromptMetadata]:
    """Read and parse the build plan from disk.

    Blocking file I/O - callers on the event loop run it via ``asyncio.to_thread``.
    """
    if guide_path is None:
        guide_path = get_settings().repcut_guide_path

    if guide_path is None:
        raise GuideUnavailableError(
            "The build plan is not available on this machine. It is a private "
            "document and is deliberately not part of this repository; set "
            "REPCUT_GUIDE_PATH in .env to point at your local copy."
        )

    try:
        text = guide_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Named: REPCUT_GUIDE_PATH is set but points at nothing on this machine.
        raise GuideUnavailableError(
            "REPCUT_GUIDE_PATH is set but no file exists there. The build plan "
            "lives outside this repository; check the path in .env."
        ) from None
    except PermissionError:
        # Named: the file exists but this user cannot read it.
        raise GuideUnavailableError(
            "The build plan file exists but could not be opened for reading. Check its permissions."
        ) from None
    except (OSError, UnicodeDecodeError):
        # Named: a directory, an unreadable drive, or a non-UTF-8 document - a
        # PDF export rather than the markdown guide, most likely.
        raise GuideUnavailableError(
            "The build plan file could not be read as UTF-8 text. The parser "
            "expects the markdown guide, not a PDF export."
        ) from None

    return parse_guide(text)
