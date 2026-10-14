"""[HUMAN] criteria and the two gate modes (amendment 016), exercised for real.

`scripts/gate_skip.sh` and `scripts/gate_human.sh` are sourced into a stub gate
run by a POSIX bash. The stub holds one automated criterion that passes and one
[HUMAN] criterion read from a checklist written here, so the only thing that
decides its exit code is the shared policy every real gate uses.

The contract: an unsigned box makes the gate (verify-NN) exit non-zero and its
--auto twin exit zero; --auto never prints an unsigned box as a PASS; no
environment variable can turn the gate into --auto; and every manual checklist
in docs/manual-checks/ is read by its own gate through gate_human, so deleting
a [HUMAN] criterion from a gate fails a test here.
"""

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
MANUAL_CHECKS = REPO_ROOT / "docs" / "manual-checks"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


posix_shell = _load("posix_shell")

# $1 = checklist, $2 = section ("" for the whole file), $3 = stop-bound rc,
# the rest = the gate's own arguments.
_STUB_GATE = """
PY="python"
MANUAL="$1"; SECTION="$2"; BOUND_RC="$3"; shift 3
pass=0; fail=0; skip=0
ok()      { pass=$((pass+1)); echo "PASS|$1|$2"; }
no()      { fail=$((fail+1)); echo "FAIL|$1|$2"; }
skipped() { skip=$((skip+1)); echo "SKIP|$1|$2"; }
scrub()   { cat; }
. scripts/gate_skip.sh
. scripts/gate_human.sh
gate_mode "$@"
ok "1  an automated criterion" "(measured)"
[ "$BOUND_RC" != none ] && gate_stop_bound "2  operator-bound" "$BOUND_RC" "(error 33.4)"
gate_human "3  [HUMAN] boxes" "$MANUAL" $SECTION
gate_finish verify-stub
"""

_UNSIGNED = "# check\n\n- [x] one thing\n- [ ] the thing only Ashwin can see\n"
_SIGNED = "# check\n\n- [x] one thing\n- [X] the thing only Ashwin can see\n"
_SECTIONED = (
    "# check\n\n### Phase A - first\n\n- [x] a\n- [x] b\n\n### Phase B - later\n\n- [ ] c\n"
)


def _run(
    checklist: Path,
    *args: str,
    section: str = "",
    bound_rc: str = "none",
    env_extra: dict[str, str] | None = None,
) -> tuple[int, str]:
    try:
        bash = posix_shell.bash_executable()
    except posix_shell.ShellNotFoundError:
        pytest.fail("no POSIX bash - the gates cannot run here either")
    env = {k: v for k, v in os.environ.items() if not k.startswith("REPCUT_GATE")}
    env.update(env_extra or {})
    sec = f'"{section}"' if section else ""
    script = _STUB_GATE.replace("$SECTION", sec)
    completed = subprocess.run(
        [bash, "-c", script, "verify-stub", checklist.as_posix(), section, bound_rc, *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return completed.returncode, completed.stdout


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "manual.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_an_unsigned_box_fails_the_gate_and_not_its_auto_twin(tmp_path: Path) -> None:
    checklist = _write(tmp_path, _UNSIGNED)
    gate_rc, gate_out = _run(checklist)
    auto_rc, auto_out = _run(checklist, "--auto")
    assert gate_rc != 0, gate_out
    assert "FAIL|3  [HUMAN] boxes|(1 unticked, 1 ticked)" in gate_out
    assert auto_rc == 0, auto_out
    assert "[HUMAN] 3  [HUMAN] boxes" in auto_out
    assert "PASS|3" not in auto_out, "an unsigned box printed as a PASS"
    assert "1 [HUMAN] pending, so verify-stub itself is still red" in auto_out


def test_a_signed_box_passes_in_both_modes(tmp_path: Path) -> None:
    checklist = _write(tmp_path, _SIGNED)
    for args in ((), ("--auto",)):
        rc, out = _run(checklist, *args)
        assert rc == 0, out
        assert "PASS|3  [HUMAN] boxes|(2 of 2 ticked)" in out


def test_a_missing_checklist_fails_in_both_modes(tmp_path: Path) -> None:
    for args in ((), ("--auto",)):
        rc, out = _run(tmp_path / "absent.md", *args)
        assert rc != 0, out
        assert "does not exist" in out


def test_no_environment_variable_turns_the_gate_into_auto(tmp_path: Path) -> None:
    checklist = _write(tmp_path, _UNSIGNED)
    for name in ("REPCUT_GATE_AUTO", "GATE_AUTO", "REPCUT_AUTO"):
        rc, out = _run(checklist, env_extra={name: "1"})
        assert rc != 0, f"{name}=1 made the gate pass:\n{out}"


def test_an_unknown_argument_is_refused(tmp_path: Path) -> None:
    checklist = _write(tmp_path, _SIGNED)
    for args in (("--human-ok",), ("--auto", "--auto"), ("auto",)):
        rc, out = _run(checklist, *args)
        assert rc != 0, f"{args} accepted:\n{out}"
        assert "usage:" in out


def test_a_section_counts_only_its_own_boxes(tmp_path: Path) -> None:
    checklist = _write(tmp_path, _SECTIONED)
    rc, out = _run(checklist, section="Phase A")
    assert rc == 0 and "(2 of 2 ticked)" in out, out
    rc, out = _run(checklist, section="Phase B")
    assert rc != 0, out


def test_a_stop_bound_failure_is_pending_only_in_auto(tmp_path: Path) -> None:
    checklist = _write(tmp_path, _SIGNED)
    gate_rc, gate_out = _run(checklist, bound_rc="3")
    auto_rc, auto_out = _run(checklist, "--auto", bound_rc="3")
    assert gate_rc != 0 and "FAIL|2  operator-bound" in gate_out
    assert auto_rc == 0 and "[HUMAN] 2  operator-bound" in auto_out
    assert "PASS|2" not in auto_out


def test_any_other_failure_of_a_stop_bound_check_fails_in_auto(tmp_path: Path) -> None:
    checklist = _write(tmp_path, _SIGNED)
    for rc_value in ("1", "2", "130"):
        rc, out = _run(checklist, "--auto", bound_rc=rc_value)
        assert rc != 0 and "FAIL|2  operator-bound" in out, (rc_value, out)


def _gates() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8") for path in sorted(SCRIPTS.glob("verify_*.sh"))
    }


def test_every_manual_checklist_is_read_by_its_gate_through_gate_human() -> None:
    """Mutation guard: delete a [HUMAN] criterion from a gate and this fails."""
    checklists = sorted(MANUAL_CHECKS.glob("prompt-[0-9][0-9].md"))
    assert checklists, "no manual checklists found - the guard would guard nothing"
    gates = _gates()
    for checklist in checklists:
        number = checklist.stem.split("-")[1]
        gate = f"verify_{number}.sh"
        assert gate in gates, f"{checklist.name} has no gate {gate}"
        rel = checklist.relative_to(REPO_ROOT).as_posix()
        label = r'^\s*gate_human\s+"[^"]*\[HUMAN\][^"]*"\s+"'
        call = re.compile(label + re.escape(rel) + '"', re.M)
        assert call.search(gates[gate]), f"{gate} no longer reads {rel} through gate_human"


def test_every_section_of_a_checklist_is_read_by_some_gate_human_call() -> None:
    """Mutation guard: a gate reading only its Phase A boxes leaves Phase B unsigned-but-green.

    Each ``### `` section holding a box must be covered by a ``gate_human``
    call on that file, either for the whole file or for that section.
    """
    gates = _gates()
    for checklist in sorted(MANUAL_CHECKS.glob("prompt-[0-9][0-9].md")):
        gate = gates[f"verify_{checklist.stem.split('-')[1]}.sh"]
        rel = re.escape(checklist.relative_to(REPO_ROOT).as_posix())
        head = r'^\s*gate_human\s+"[^"]*"\s+"' + rel + '"'
        whole = re.search(head + r"\s*$", gate, re.M)
        if whole:
            continue
        # gate_human's awk selects a section whose heading starts with the argument.
        read = re.findall(head + r'\s+"([^"]+)"', gate, re.M)
        text = checklist.read_text(encoding="utf-8")
        for section in re.findall(r"^### (.+?)\s*$", text, re.M):
            body = text.split(f"### {section}", 1)[1].split("\n### ", 1)[0]
            if not re.search(r"^\s*-\s*\[[ xX]\]", body, re.M):
                continue
            assert any(section.startswith(arg) for arg in read), (
                f"{checklist.name} section {section!r} is read by no gate_human call"
            )


def test_no_gate_counts_checkboxes_itself() -> None:
    """A gate that counts boxes by hand would bypass both modes' rules."""
    for name, text in _gates().items():
        assert r"\[[[:space:]]\]" not in text, f"{name} counts checkboxes outside gate_human"


def test_a_gate_with_a_human_criterion_takes_its_mode_and_passes_it_on() -> None:
    for name, text in _gates().items():
        if "gate_human " not in text:
            continue
        assert ". scripts/gate_human.sh" in text, name
        assert 'gate_mode "$@"' in text, name
        assert "gate_finish " in text, name
        for nested in re.findall(r"scripts/verify_0[2-9]\.sh[^\n]*", text):
            if nested.startswith("scripts/verify_0") and "2>&1" in nested:
                assert "$(gate_mode_arg)" in nested, f"{name} nests without its mode: {nested}"
