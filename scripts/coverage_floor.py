#!/usr/bin/env python3
"""Fail when a core engine package's line coverage drops under its floor.

    python scripts/coverage_floor.py [--data FILE]

Reads the coverage data ``make test`` leaves (``engine/.coverage`` unless
``--data`` or ``$COVERAGE_FILE`` says otherwise) and checks each package
below against its floor. Prints ``MEASURED:`` with every package's figure and
``FAILED:`` naming each package under its floor; exit 1 then, 0 otherwise.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# The packages whose decisions the product's claims rest on, and their floors.
FLOORS = {"edit": 80.0, "grading": 80.0, "analysis": 80.0}


def package_coverage(data_file: Path) -> dict[str, tuple[int, int]]:
    """package -> (statements, missed), from a coverage data file."""
    from coverage import Coverage

    cov = Coverage(data_file=str(data_file))
    cov.load()
    totals = {name: [0, 0] for name in FLOORS}
    for measured in cov.get_data().measured_files():
        parts = Path(measured).parts
        if "repcut" not in parts:
            continue
        # The package directory is the LAST "repcut" (the checkout may be named so too).
        tail = parts[len(parts) - parts[::-1].index("repcut") :]
        if not tail or tail[0] not in FLOORS:
            continue
        _, statements, _, missing, _ = cov.analysis2(measured)
        totals[tail[0]][0] += len(statements)
        totals[tail[0]][1] += len(missing)
    return {name: (s, m) for name, (s, m) in totals.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path)
    args = parser.parse_args(argv)
    data = args.data or Path(os.environ.get("COVERAGE_FILE") or REPO_ROOT / "engine" / ".coverage")
    if not data.is_file():
        print("MEASURED: no coverage data")
        print("FAILED: run `make test` first (it writes the coverage data)")
        return 1
    figures, problems = [], []
    for name, (statements, missed) in package_coverage(data).items():
        if statements == 0:
            problems.append(f"{name}: no lines measured")
            continue
        percent = 100.0 * (statements - missed) / statements
        figures.append(f"{name} {percent:.1f}%")
        if percent < FLOORS[name]:
            problems.append(f"{name} {percent:.1f}% under its {FLOORS[name]:.0f}% floor")
    print("MEASURED: " + ", ".join(figures))
    if problems:
        print("FAILED: " + "; ".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
