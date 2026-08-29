#!/usr/bin/env python3
"""Detect a verbatim guide prompt or wave TITLE in a tracked file.

Why this exists alongside ``check_plan_leak.py``
------------------------------------------------
``check_plan_leak.py`` (verify-01 criterion 13) scores BULK transcription: one
signal family reaching three distinct hits, or six spread across families. That
is the right shape for a transcribed table or a keyed module, and it is
deliberately blind to a single title sitting in prose - a lone title matches no
family at all, because ``prompt_entries`` wants an ``id=``/``name=`` record and
``prompt_rows`` wants a five-column table.

Amendment 006 forbids both halves: the bulk deliverables AND the titles. Only
the bulk half had a gate. Four files passed criterion 13 while carrying a
verbatim title - a kick-off doc's H1, a context file, an amendment aside, and an
agent description. This check is the missing complement: exact,
case-insensitive, whitespace-normalised phrase matching. One hit fails.

The titles are NEVER stored in this file
----------------------------------------
A fixed list of the guide's titles in a tracked file would BE the leak this
check exists to prevent - the check would become the violation. They are read at
runtime from ``REPCUT_GUIDE_PATH``, exactly as the dashboard reads the plan.

When the guide is unreachable the exit code is 2 and the reason is named. The
caller must render that as SKIP, never as PASS: a green here without the guide
would be a gate reporting success for a check it never ran, which is the precise
failure this whole amendment keeps rediscovering.

Printing a hit prints the title. That is safe and it is the point: the check only
runs where the guide resolves, which is a private machine, and a matched title is
by definition already sitting in a tracked file at that moment. It does not run
in CI, so a title never reaches a public log through here.

Usage:
    python scripts/check_plan_titles.py             # every tracked file
    python scripts/check_plan_titles.py FILE...     # named files (negative control)

Exit codes:
    0  no guide title found in any scanned file
    1  a guide title appears in a tracked file
    2  the guide could not be read; nothing was scanned (reason on stdout)
"""

import os
import re
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 2_000_000

# A title shorter than this is too generic to attribute to the guide - it would
# fire on ordinary prose and make the gate useless.
MIN_TITLE = 8

# "PROMPT 03 - Analysis Engine", "Prompt 3: Something", "WAVE 2 - Foundation".
# The title is whatever follows the separator up to end of line or a table pipe.
_PROMPT_TITLE = re.compile(r"(?:PROMPT|Prompt)\s*\d{1,2}\s*[\u2014\u2013:\-]\s*([^\n|]{4,80})")
_WAVE_TITLE = re.compile(r"(?:WAVE|Wave)\s*\d\s*[\u2014\u2013:\-]\s*([^\n|]{3,60})")


def _flat(text: str) -> str:
    """Whitespace-normalised, case-folded - so a line wrap cannot hide a title."""
    return re.sub(r"\s+", " ", text).casefold()


def guide_path() -> Path | None:
    """REPCUT_GUIDE_PATH from the environment, falling back to .env.

    The verify script does not export the engine's settings, and this runs as a
    plain subprocess, so reading .env directly is the only way to see the value
    a developer actually configured.
    """
    raw = os.environ.get("REPCUT_GUIDE_PATH", "").strip()
    if not raw:
        env_file = Path(".env")
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                key, sep, value = line.partition("=")
                if sep and key.strip() == "REPCUT_GUIDE_PATH":
                    raw = value.strip().strip("\"'")
                    break
    if not raw:
        return None
    return Path(raw).expanduser()


def guide_titles(guide: Path) -> set[str]:
    """Every prompt and wave title the guide binds to a number."""
    text = guide.read_text(encoding="utf-8", errors="replace")
    titles: set[str] = set()
    for pattern in (_PROMPT_TITLE, _WAVE_TITLE):
        for match in pattern.finditer(text):
            title = match.group(1).strip().rstrip(".*_ ")
            if len(title) >= MIN_TITLE:
                titles.add(title)
    return titles


def tracked_files(root: Path | None = None) -> list[Path]:
    """Every file git tracks, NUL-separated so paths with spaces survive."""
    # Fixed argv, no shell, no user input; git is resolved from PATH by design.
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


def scan(path: Path, titles: dict[str, str]) -> list[str]:
    """The guide titles present in one file, empty if unreadable.

    ``titles`` maps normalised form -> original, so the report can print the
    title as the guide writes it rather than as this scan folded it.
    """
    try:
        if path.stat().st_size > MAX_BYTES:
            return []
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Named: binary content, a broken symlink, or an unreadable path. None
        # of those can be a prose transcription of a title.
        return []

    flat = _flat(text)
    return sorted(original for norm, original in titles.items() if norm in flat)


def main(argv: list[str]) -> int:
    """Scan and report. Exit 1 if any scanned file carries a guide title."""
    guide = guide_path()
    if guide is None:
        print("SKIP: REPCUT_GUIDE_PATH is not set; no titles to match against")
        return 2
    if not guide.is_file():
        print("SKIP: REPCUT_GUIDE_PATH does not point at a readable file")
        return 2

    titles = guide_titles(guide)
    if not titles:
        print("SKIP: no prompt or wave titles could be parsed from the guide")
        return 2
    lookup = {_flat(t): t for t in titles}

    targets = [Path(a) for a in argv[1:]] if len(argv) > 1 else tracked_files()

    leaks: list[tuple[Path, list[str]]] = []
    scanned = 0
    for path in targets:
        if not path.is_file():
            continue
        scanned += 1
        found = scan(path, lookup)
        if found:
            leaks.append((path, found))

    if not leaks:
        print(f"clean: {scanned} files scanned against {len(titles)} guide titles")
        return 0

    print(f"BUILD PLAN TITLE in {len(leaks)} file(s), matched against the guide:")
    for path, found in leaks:
        for title in found:
            print(f"  {path.as_posix()}: {title}")
    print()
    print("  A prompt title is the plan (amendment 006). Keep the number, drop")
    print("  the title, or read it at runtime from REPCUT_GUIDE_PATH.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
