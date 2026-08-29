#!/usr/bin/env python3
"""Detect the build plan transcribed into tracked files.

Repcut is a public repository; the build plan is a private document. Criterion
11 of ``verify-01`` matches FILENAMES, which a source module named something
ordinary walks straight past. This matches CONTENT.

Why it is written this way
--------------------------
The first version of this check looked for wave titles in the guide's own
formatting (``Wave 2 - <title>``, matched against an alternation of the real
title values). ``engine/repcut/prompts_data.py`` stored
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

# Anchors the default scan. Resolved from this file, never from the cwd: `git
# ls-files` run inside a subdirectory lists that subtree only, and a scan that
# covers nothing exits 0 and reads as a pass.
REPO_ROOT = Path(__file__).resolve().parent.parent

# Large text files are read in bounded chunks, never skipped. A size-based skip
# was a bypass with a one-line recipe: pad a transcription past the limit and
# the scan returns no hits at all. OVERLAP_CHARS is carried across the boundary
# so a match spanning two chunks is still seen; the longest pattern here spans
# a few hundred characters, far inside that.
CHUNK_CHARS = 1_000_000
OVERLAP_CHARS = 4_096

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

# A wave number bound to a title-cased phrase on the same line, in any
# formatting: `Wave 2 - Some Title`, `wave="Wave 2", title="Some Title"`,
# `| Wave 2 | Some Title |`.
#
# The six real wave titles are deliberately NOT written here. An alternation of
# the guide's own title values would put them in a tracked file, which is the
# thing this guard exists to prevent - amendment 006 forbids the plan "as data,
# in a fixture, in a docstring example, as prose in a comment" - and a guard
# that carries the content it detects has not fixed anything. Matching the
# SHAPE also catches a transcription that renames the waves, which an
# alternation of fixed values never could.
#
# Measured over all 221 tracked files before shipping: two files hit this once
# each, and one distinct hit reaches neither FAMILY_THRESHOLD nor the >=2 a
# family needs to count toward COMBINED_THRESHOLD. Matching forward only
# (number, then title) is what keeps it that quiet - also matching title-then-
# number added four more files and caught nothing the samples did not already.
_WAVE_TITLE_SHAPE = r"(?P<title>[A-Z][a-z]{2,}(?:[ \t]+[A-Z][a-z]+){0,3})"

FAMILIES: list[Family] = [
    Family(
        "wave_titles",
        re.compile(r"Wave[ \t]*[0-5][^\n]{0,24}?\b" + _WAVE_TITLE_SHAPE),
        group="title",
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
            # The en-dash and em-dash here are deliberate, not typos: the
            # guide's own range formatting for a span of weeks uses them, and
            # a plain hyphen-only pattern would miss a transcription that kept
            # the guide's exact punctuation. noqa: RUF001 x2 below.
            r"\d+\s*[—–-]?\s*\d*\s*weeks?[^\n]{0,30}?(?:Wave|Prompt)\s*\d"  # noqa: RUF001
            r"|(?:Wave|Prompt)\s*\d[^\n]{0,30}?\d+\s*[—–-]?\s*\d*\s*weeks?",  # noqa: RUF001
            re.IGNORECASE,
        ),
    ),
    # A transcribed index of session reports.
    Family("report_paths", re.compile(r"docs/reports/prompt-(?P<n>\d{2})\.md"), group="n"),
]


def tracked_files(root: Path | None = None) -> list[Path]:
    """Every file git tracks, NUL-separated so paths with spaces survive.

    ``root`` anchors the listing; the returned paths are relative to it. Without
    it, running from a subdirectory lists that subtree only - which would let a
    caller scan nothing and read the empty result as a pass. ``main`` therefore
    always passes ``REPO_ROOT``.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True,
        check=False,
        cwd=root,
    )
    if result.returncode != 0:
        return []
    names = result.stdout.decode("utf-8", errors="replace").split("\0")
    return [Path(name) for name in names if name]


def scan(path: Path) -> dict[str, set[str]]:
    """Return the distinct hits per family for one file, empty if unreadable.

    Read in overlapping chunks so that size is never a way past the check. Hits
    are sets of distinct values, so the overlap re-scanning a few kilobytes
    cannot inflate a count.
    """
    found: dict[str, set[str]] = {}
    try:
        with path.open(encoding="utf-8") as handle:
            carry = ""
            while True:
                chunk = handle.read(CHUNK_CHARS)
                if not chunk:
                    break
                text = carry + chunk
                for family in FAMILIES:
                    hits = family.hits(text)
                    if hits:
                        found.setdefault(family.name, set()).update(hits)
                # Prefer to start the next window at a line boundary:
                # `prompt_rows` is MULTILINE-anchored, and a window opening
                # mid-line lets `^` match there and invent a row.
                #
                # But only when the window HAS a boundary. Dropping a
                # newline-free tail would carry nothing across the seam, so
                # a single-line file padded to split one id/name record
                # over the boundary would match in neither chunk - a
                # bypass, and the same class of hole as the size skip this
                # replaced. Keeping the raw tail can at worst invent a row
                # that is not there; a guard has to fail that way round.
                tail = text[-OVERLAP_CHARS:]
                _, newline, rest = tail.partition("\n")
                carry = rest if newline else tail
    except (OSError, UnicodeDecodeError):
        # Named: binary content, a broken symlink, or a path this user cannot
        # read. None of those can be a prose transcription of the plan.
        return {}

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
    # (name to report, file to read). The default scan is anchored on REPO_ROOT
    # so it covers the whole repository from any working directory, and reports
    # repo-relative names - an absolute path here would carry the OS username
    # into the gate output (`.claude/rules/secrets.md`).
    targets: list[tuple[Path, Path]] = (
        [(Path(a), Path(a)) for a in argv[1:]]
        if len(argv) > 1
        else [(name, REPO_ROOT / name) for name in tracked_files(REPO_ROOT)]
    )

    leaks: list[tuple[Path, dict[str, set[str]]]] = []
    for name, path in targets:
        if not path.is_file():
            continue
        found = scan(path)
        leaked, _ = verdict(found)
        if leaked:
            leaks.append((name, found))

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
