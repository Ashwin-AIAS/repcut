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
import shutil
import subprocess
import sys
from functools import partial
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
    #
    # Resolved against REPO_ROOT, because `tracked_files` returns paths relative
    # to it: testing `is_file()` on the bare relative path silently drops every
    # file whenever pytest runs from anywhere but the repo root, and a shorter
    # list is exactly the failure this assertion exists to catch.
    scanned = [REPO_ROOT / path for path in tracked]
    scanned = [path for path in scanned if path.is_file()]
    assert len(scanned) > 50, f"expected the whole tree, scanned {len(scanned)}"

    leaks = []
    for path in scanned:
        found = guard.scan(path)
        leaked, _ = guard.verdict(found)
        if leaked:
            leaks.append((path.name, sorted(found)))

    assert leaks == []


def test_padding_a_file_does_not_hide_a_transcription(tmp_path: Path) -> None:
    """Size is not a way past the check.

    The first version skipped any file over 2MB, so a transcription with enough
    padding scored zero hits and the gate printed PASS. The scan now reads in
    bounded chunks instead, and the padding here is deliberately larger than
    that old limit.
    """
    padding = "# filler\n" * 300_000
    leaked, found = _verdict(tmp_path, "padded.py", padding + TRANSCRIBED_MODULE)

    assert (tmp_path / "padded.py").stat().st_size > 2_000_000
    assert leaked is True
    assert len(found["prompt_entries"]) == len(INVENTED_NAMES)


def test_a_transcription_split_across_a_chunk_boundary_is_still_seen(tmp_path: Path) -> None:
    """The overlap carried between chunks covers a record on the seam."""
    head = "# filler\n" * ((guard.CHUNK_CHARS // 9) - 2)
    leaked, found = _verdict(tmp_path, "seam.py", head + TRANSCRIBED_MODULE)

    assert leaked is True
    assert len(found["prompt_entries"]) == len(INVENTED_NAMES)


def test_wave_titles_are_matched_by_shape_not_by_value(tmp_path: Path) -> None:
    """Invented titles must trip the family, or it is matching the guide's values.

    The point of the rewrite: the six real wave titles are not written in the
    repo, so the family matches "a wave number bound to a title-cased phrase".
    A transcription that renamed the waves used to score zero.
    """
    text = "".join(f"Wave {n} - Invented {name}\n" for n, name in enumerate(INVENTED_NAMES))
    leaked, found = _verdict(tmp_path, "waves.md", text)

    assert len(found["wave_titles"]) == len(INVENTED_NAMES)
    assert leaked is True


def test_prose_about_waves_is_not_a_leak(tmp_path: Path) -> None:
    """The shape pattern must not fire on ordinary sentences mentioning a wave."""
    text = (
        "Wave 1 was the scaffold. In wave 2 we add the engine, and wave 3 is\n"
        "deferred until later. Wave 4 covers hardening.\n"
    )
    leaked, found = _verdict(tmp_path, "notes.md", text)

    assert leaked is False
    assert len(found.get("wave_titles", set())) < guard.FAMILY_THRESHOLD


def _posix_bash() -> str | None:
    """A POSIX bash, or None if the only one here is WSL's.

    On Windows `bash` resolves to System32\bash.exe - the WSL launcher - before
    Git Bash, and running a repo script through it produces a shell that half
    works against Windows paths. A test that silently ran there would report on
    something other than the hook.
    """
    found = shutil.which("bash")
    if found is None:
        return None
    return None if "system32" in found.replace("\\", "/").lower() else found


@pytest.mark.skipif(_posix_bash() is None, reason="no POSIX bash on this machine")
def test_the_hook_scans_staged_content_not_the_working_tree(tmp_path: Path) -> None:
    """Staging a transcription and then cleaning the file must still be blocked.

    `git commit` commits the index. The hook used to hand the checker a path and
    let it read the working tree, so an unstaged edit that cleaned the file
    afterwards left the hook inspecting content the commit would not contain.
    """
    bash = _posix_bash()
    assert bash is not None
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(GUARD_PATH, repo / "scripts" / "check_plan_leak.py")
    shutil.copy(REPO_ROOT / "scripts" / "precommit_guard.sh", repo / "scripts")

    run = partial(subprocess.run, cwd=repo, capture_output=True, text=True, check=True)
    run(["git", "init", "-q"])
    run(["git", "config", "user.email", "test@example.invalid"])
    run(["git", "config", "user.name", "test"])

    leaked_file = repo / "notes.md"
    leaked_file.write_text(TRANSCRIBED_MODULE, encoding="utf-8")
    run(["git", "add", "notes.md"])
    # The leak is now in the index. Clean the working tree only - the commit
    # would still carry the transcription.
    leaked_file.write_text("nothing to see here\n", encoding="utf-8")

    result = subprocess.run(
        [bash, "scripts/precommit_guard.sh", "notes.md"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "BUILD PLAN TRANSCRIBED" in result.stdout
