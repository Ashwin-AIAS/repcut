#!/usr/bin/env python3
"""Detect the build plan transcribed into tracked files.

Repcut is a public repository; the build plan is a private document. Criterion
11 of ``verify-01`` matches FILENAMES, which a source module named something
ordinary walks straight past. This matches CONTENT.

Why it is written this way
--------------------------
The first version of this check looked for wave titles in the guide's own
formatting (``Wave 2 - Magic Core``). ``engine/repcut/prompts_data.py`` stored
the wave as ``"Wave 0"`` with no title at all, so the pattern matched zero times
and the gate stayed green over 305 lines of transcribed plan for three weeks.
The guard was
written against the shape of the SOURCE DOCUMENT rather than the shape of a
LEAK, and a leak does not have to preserve formatting - a transcription is free
to paraphrase, restructure and split fields apart.

So this looks for several independent signals of bulk transcription, each of
which survives reformatting, and treats their combination as the verdict. One or
two hits is a quotation and stays allowed; bulk is what matters.

Usage:
    python scripts/check_plan_leak.py              # every tracked file
    python scripts/check_plan_leak.py FILE...      # named files (negative control)
"""

import re
import subprocess
import sys
from pathlib import Path

# Files above this size are almost certainly not hand-written source; reading
# them whole would also make the gate slow.
MAX_BYTES = 2_000_000

# A single family reaching this many distinct hits is bulk on its own.
FAMILY_THRESHOLD = 3
# Or a transcription spread thinly across families, so that no single one
# reaches the threshold above. Set to 6 rather than 4 deliberately: a file that
# quotes two gate commands and the two matching report paths is a session
# report, not a leak, and that shape must keep passing.
COMBINED_THRESHOLD = 6


class Family:
    """One independent signal of transcription."""

    def __init__(self, name: str, pattern: re.Pattern[str], group: int | str = 0) -> None:
        self.name = name
        self.pattern = pattern
        self.group = group

    def hits(self, text: str) -> set[str]:
        """Distinct, case-folded matches of this signal in one file."""
        found: set[str] = set()
        for match in self.pattern.finditer(text):
            value = match.group(self.group)
            if value:
                found.add(value.strip().casefold())
        return found


# NOTE ON SELF-MATCHING: every pattern below is written so that this file - which
# contains the patterns as literal source text - does not trip its own check. The
# alternations are preceded by regex syntax (``\s*``, ``[0-5]``) that cannot
# appear in real transcribed prose, so the literal never matches the pattern.
# `make verify-01` on this branch proves it: this script is a tracked file and is
# scanned like any other.

_WAVE_TITLE = r"(?:Foundation|Magic Core|Differentiators|Moats|Hardening|Public)"

FAMILIES: list[Family] = [
    # A wave number and its title on one line, in any formatting: `Wave 2 - Magic
    # Core`, `wave=2, title="Magic Core"`, `| 2 | Magic Core |`.
    Family(
        "wave_titles",
        re.compile(
            r"Wave\s*[0-5][^\n]{0,24}?" + _WAVE_TITLE + r"|" + _WAVE_TITLE + r"[^\n]{0,24}?Wave\s*[0-5]",
            re.IGNORECASE,
        ),
    ),
    # A prompt id bound to a human title, as a keyed record.
    Family(
        "prompt_entries",
        re.compile(
            # [\s\S] rather than [^\n]: the fields of a transcribed record are
            # usually on separate lines, which is exactly what the first version
            # of this pattern failed to cross.
            r"""\bid\s*[=:]\s*["']?(?P<id>\d{2})["']?[\s\S]{0,200}?\bname\s*[=:]\s*["'][^"']{4,80}["']""",
            re.IGNORECASE,
        ),
        group="id",
    ),
    # A prompt id bound to a human title, as a markdown table row.
    #
    # Five columns with a long third cell, which is the plan table's shape. A
    # session report's criteria table (`| 13 | criterion | PASS | detail |`) is
    # four columns with a short verdict in the third and must not match - that
    # was a real false positive while this pattern only counted pipes.
    Family(
        "prompt_rows",
        re.compile(
            r"^\|\s*(?P<id>\d{2})\s*\|\s*[^|\n]{4,80}\|\s*[^|\n]{20,}\|[^|\n]*\|[^|\n]*\|",
            re.MULTILINE,
        ),
        group="id",
    ),
    # A transcribed list of gate commands. The Makefile declares targets as
    # `verify-07:` and CLAUDE.md writes `make verify-XX`; neither matches.
    Family("gate_commands", re.compile(r"\bmake\s+verify-(?P<n>\d{2})\b"), group="n"),
    # A calendar estimate carrying plan context. Bare "2 weeks" in prose is not
    # a leak; "6-8 weeks (Wave 1 total)" is a row of the timeline table.
    Family(
        "timelines",
        re.compile(
            r"\d+\s*[—–-]?\s*\d*\s*weeks?[^\n]{0,30}?(?:Wave|Prompt)\s*\d"
            r"|(?:Wave|Prompt)\s*\d[^\n]{0,30}?\d+\s*[—–-]?\s*\d*\s*weeks?",
            re.IGNORECASE,
        ),
    ),
    # A transcribed index of session reports.
    Family("report_paths", re.compile(r"docs/reports/prompt-(?P<n>\d{2})\.md"), group="n"),
]


def tracked_files(root: Path | None = None) -> list[Path]:
    """Every file git tracks, NUL-separated so paths with spaces survive.

    ``root`` anchors both the listing and the returned paths. Without it, running
    from a subdirectory lists that subtree only - which would let a caller scan
    nothing and read the empty result as a pass.
    """
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
        ["git", "ls-files", "-z"],  # noqa: S607 - git is resolved from PATH by design
        capture_output=True,
        check=False,
        cwd=root,
    )
    if result.returncode != 0:
        return []
    names = result.stdout.decode("utf-8", errors="replace").split("\0")
    return [Path(name) for name in names if name]


def scan(path: Path) -> dict[str, set[str]]:
    """Return the distinct hits per family for one file, empty if unreadable."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return {}
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Named: binary content, a broken symlink, or a path this user cannot
        # read. None of those can be a prose transcription of the plan.
        return {}

    found: dict[str, set[str]] = {}
    for family in FAMILIES:
        hits = family.hits(text)
        if hits:
            found[family.name] = hits
    return found


def verdict(found: dict[str, set[str]]) -> tuple[bool, int]:
    """Whether this file reads as bulk transcription, and its strongest count.

    Only families with more than one distinct hit count toward the combined
    score. A single hit is weak evidence on its own - a test that asserts one
    derived gate command and one derived report path lights up three families
    with one hit each, and that is a quotation, not a transcription.
    """
    if not found:
        return False, 0
    biggest = max(len(hits) for hits in found.values())
    repeated = [len(hits) for hits in found.values() if len(hits) >= 2]
    leaked = biggest >= FAMILY_THRESHOLD or sum(repeated) >= COMBINED_THRESHOLD
    return leaked, biggest


def main(argv: list[str]) -> int:
    """Scan and report. Exit 1 if any tracked file transcribes the plan."""
    targets = [Path(a) for a in argv[1:]] if len(argv) > 1 else tracked_files()

    leaks: list[tuple[Path, dict[str, set[str]]]] = []
    for path in targets:
        if not path.is_file():
            continue
        found = scan(path)
        leaked, _ = verdict(found)
        if leaked:
            leaks.append((path, found))

    if not leaks:
        print(f"clean: {len(targets)} files scanned, no build plan transcription")
        return 0

    print(f"BUILD PLAN TRANSCRIBED into {len(leaks)} tracked file(s):")
    for path, found in leaks:
        detail = ", ".join(f"{name}={len(hits)}" for name, hits in sorted(found.items()))
        print(f"  {path.as_posix()}  [{detail}]")
    print()
    print("  The build plan is private and must not live in this public repo.")
    print("  Read it at runtime from REPCUT_GUIDE_PATH instead.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
