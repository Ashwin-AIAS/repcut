#!/usr/bin/env python3
"""Turn ``make eval``'s results into one markdown scorecard.

    python scripts/eval_report.py [--results FILE] [--out docs/reports/eval-latest.md]

Reads ``$DATA_DIR/eval/eval-results.json`` (written by
``scripts/eval_pipeline.py``) and writes the scorecard: one row per product
claim, its metric, the measured value, the bound and the verdict. A BOUND row
says what is missing; it is never shown as a pass. The file is committed, so
it is scrubbed: no path, no file name, nothing but the numbers and the names of
the gates. Exit 1 if the results are missing or any gate failed, 3 if any is
bound, 0 when every claim passed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_metrics import BOUND, FAIL, PASS, GateResult, exit_code  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "docs" / "reports" / "eval-latest.md"
# Anything path-shaped is cut before it can reach a committed file.
_PATHISH = re.compile(r"([A-Za-z]:[\\/]|/(home|Users|sessions|tmp|mnt)/)[^\s,;)]*")
_MARK = {PASS: "PASS", FAIL: "**FAIL**", BOUND: "BOUND (not measured)"}


def _scrub(text: str) -> str:
    return _PATHISH.sub("<path>", text).replace("|", "/")


def render(results: list[GateResult], meta: dict[str, str]) -> str:
    counts = {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, BOUND)}
    lines = [
        "# Eval scorecard (latest run)",
        "",
        f"Run: {meta.get('when', '?')} on {meta.get('machine', '?')} at commit "
        f"`{meta.get('commit', '?')}`. Written by `scripts/eval_report.py` from "
        "`make eval`; regenerate, never hand-edit.",
        "",
        f"**{counts[PASS]} pass, {counts[FAIL]} fail, {counts[BOUND]} bound.** "
        "BOUND means the real-footage fixture, its label or a model is not on the "
        "machine that ran it: not measured, and never a pass. Synthetic clips never "
        "stand in for a claim.",
        "",
        "| Gate | Claim | Metric | Measured | Bound | Verdict | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        cells = (
            r.gate,
            r.claim,
            r.metric,
            r.value,
            r.bound,
            _MARK.get(r.status, r.status),
            r.detail,
        )
        lines.append("| " + " | ".join(_scrub(c) for c in cells) + " |")
    lines += [
        "",
        "How to fill a BOUND row: `docs/manual-checks/VERIFY-LATER.md`, item 11.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    source = args.results
    if source is None:
        data_dir = Path(os.environ.get("DATA_DIR") or (REPO_ROOT / "data"))
        source = data_dir / "eval" / "eval-results.json"
    if not source.is_file():
        print("FAILED: no eval results - run `make eval` first")
        return 1
    document = json.loads(source.read_text(encoding="utf-8"))
    results = [GateResult(**g) for g in document.get("gates", [])]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(results, document.get("meta", {})), encoding="utf-8")
    code = exit_code(results)
    print(f"MEASURED: scorecard of {len(results)} gates written (exit {code})")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
