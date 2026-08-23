"""The plan-leak guard, tested against the shape of the leak it missed.

`verify-01` criterion 13 was green for three weeks over 305 lines of transcribed
build plan, because it matched the guide's *formatting* rather than a leak's
*shape*. A fix is only worth anything if it fails on the real thing, so the
cases below reproduce that file's structure with invented content.

Every fixture here is ASSEMBLED AT RUNTIME rather than written out as a literal.
That is not stylistic. A literal `make verify-07` or `docs/reports/prompt-07.md`
in this file is exactly the token the guard counts, and enough of them would make
this file fail the very check it tests - correctly, and with no honest way to
exempt it. Built from a format string, the source carries the shape without
carrying the tokens, and the guard still sees a realistic input.

All names are invented. Nothing is copied from the guide.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = REPO_ROOT / "scripts" / "check_plan_leak.py"


def _load_guard() -> ModuleType:
    """Import the guard script by path - scripts/ is not an installed package."""
    spec = importlib.util.spec_from_file_location("check_plan_leak", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_plan_leak"] = module
    spec.loader.exec_module(module)
    return module


guard = _load_guard()

INVENTED_NAMES = ["Invented Alpha", "Invented Bravo", "Invented Charlie", "Invented Delta"]


def _gate(number: int) -> str:
    """The gate command for a prompt, built so the literal is not in this file."""
    return f"make verify-{number:02d}"


def _report(number: int) -> str:
    """The report path for a prompt, built for the same reason."""
    return f"docs/reports/prompt-{number:02d}.md"


def _transcribed_module() -> str:
    """The shape that walked past the old check.

    A module of keyed records, one per prompt, with the wave stored as a bare
    "Wave N" and no wave title anywhere - which is precisely why the old
    formatting-bound pattern scored zero on it.
    """
    records = "".join(
        f"""    PromptMetadata(
        id="{index:02d}",
        name="{name}",
        wave="Wave {index // 2}",
        gate_command="{_gate(index)}",
        report_file="{_report(index)}",
        estimated_timeline="{index + 1} weeks (Wave {index // 2} total)",
    ),
"""
        for index, name in enumerate(INVENTED_NAMES)
    )
    return f"PROMPTS = [\n{records}]\n"


def _session_report() -> str:
    """What a session report legitimately contains: a criteria results table.

    Four columns with a short verdict in the third, which must keep passing -
    the leading two-digit column is the only thing it shares with a plan table.
    """
    rows = "".join(
        f"| {number} | some criterion name | PASS | some detail |\n" for number in (10, 11, 12, 13)
    )
    return (
        "| # | Criterion | Result | Detail |\n|---|---|---|---|\n"
        + rows
        + f"\nRun `{_gate(1)}` and record the result in {_report(1)}.\n"
    )


TRANSCRIBED_MODULE = _transcribed_module()
SESSION_REPORT = _session_report()


def _verdict(tmp_path: Path, name: str, text: str) -> tuple[bool, dict[str, set[str]]]:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    found = guard.scan(path)
    leaked, _ = guard.verdict(found)
    return leaked, found


def test_catches_the_shape_the_old_check_missed(tmp_path: Path) -> None:
    """A keyed module with no wave titles at all is still bulk transcription."""
    leaked, found = _verdict(tmp_path, "prompts_data.py", TRANSCRIBED_MODULE)

    assert leaked is True
    # No wave title anywhere, or the fixture would pass for the OLD reason and
    # prove nothing about the new check.
    assert found.get("wave_titles", set()) == set()
    assert len(found["prompt_entries"]) == len(INVENTED_NAMES)
    assert len(found["gate_commands"]) == len(INVENTED_NAMES)


def test_a_session_reports_criteria_table_is_not_a_leak(tmp_path: Path) -> None:
    """Four-column criteria tables share the leading two-digit column."""
    leaked, _ = _verdict(tmp_path, "prompt-01.md", SESSION_REPORT)

    assert leaked is False


def test_a_single_quotation_stays_allowed(tmp_path: Path) -> None:
    """One gate command and one report path is prose, not a transcription."""
    leaked, _ = _verdict(tmp_path, "notes.md", f"Run `{_gate(4)}`, then write {_report(4)}.\n")

    assert leaked is False


def test_a_transcription_spread_thinly_across_families_is_still_caught(
    tmp_path: Path,
) -> None:
    """No single family reaches the threshold, but together they are a plan table.

    Two gate commands, two report paths and two wave-anchored estimates: every
    family sits inside the quotation tolerance, the file as a whole does not.
    """
    text = "".join(f"run {_gate(n)} -> {_report(n)}, {n + 1} weeks for Wave {n}\n" for n in (1, 2))
    leaked, found = _verdict(tmp_path, "notes.md", text)

    assert max(len(hits) for hits in found.values()) < guard.FAMILY_THRESHOLD
    assert leaked is True


@pytest.mark.parametrize("name", ["prompts_data.py", "plan.ts", "PLAN.md", "anything.txt"])
def test_the_verdict_does_not_depend_on_the_filename(tmp_path: Path, name: str) -> None:
    """Criterion 11 matches filenames; this one must match content only."""
    leaked, _ = _verdict(tmp_path, name, TRANSCRIBED_MODULE)

    assert leaked is True


def test_the_repository_itself_is_clean() -> None:
    """The live tree, scanned exactly as the gate scans it."""
    tracked = guard.tracked_files(REPO_ROOT)

    # A scan of nothing is not a pass. Without this the test would go green from
    # any working directory where `git ls-files` happened to list no files.
    scanned = [path for path in tracked if path.is_file()]
    assert len(scanned) > 50, f"expected the whole tree, scanned {len(scanned)}"

    leaks = []
    for path in scanned:
        found = guard.scan(path)
        leaked, _ = guard.verdict(found)
        if leaked:
            leaks.append((path.name, sorted(found)))

    assert leaks == []
