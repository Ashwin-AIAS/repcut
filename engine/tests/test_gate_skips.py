"""The skip policy every verify gate shares (amendment 014), exercised for real.

`scripts/gate_skip.sh` is sourced into a stub gate and run by a POSIX bash, and
the condition it names is decided by the real `scripts/gate_conditions.py`.
NO_GUIDE is the condition these tests steer, because it can be made to hold or
not honestly - by pointing REPCUT_GUIDE_PATH at a missing or an existing file -
without any test-only override, which would be the loophole the policy forbids.
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


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


posix_shell = _load("posix_shell")

# A gate reduced to what gate_skip.sh needs: the three verdict printers, the
# counters, a no-op scrub and $PY. Then one skip, then the counts.
_STUB_GATE = """
PY="$1"; shift
pass=0; fail=0; skip=0
ok()      { pass=$((pass+1)); echo "PASS|$1|$2"; }
no()      { fail=$((fail+1)); echo "FAIL|$1|$2"; }
skipped() { skip=$((skip+1)); echo "SKIP|$1|$2"; }
scrub()   { cat; }
. scripts/gate_skip.sh
gate_skip "X  some criterion" "$1"
echo "COUNTS|$pass|$fail|$skip|$(gate_summary_note)"
"""


def _gate_skip(reason: str, *, guide: Path, strict: bool) -> tuple[str, str]:
    """Run one skip through the real policy; return (verdict, summary note)."""
    try:
        bash = posix_shell.bash_executable()
    except posix_shell.ShellNotFoundError:
        pytest.fail("no POSIX bash - the gates cannot run here either")
    env = {k: v for k, v in os.environ.items() if k != "REPCUT_GATE_STRICT"}
    env["REPCUT_GUIDE_PATH"] = str(guide)
    if strict:
        env["REPCUT_GATE_STRICT"] = "1"
    completed = subprocess.run(
        [bash, "-c", _STUB_GATE, "stub-gate", sys.executable, reason],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    lines = completed.stdout.splitlines()
    verdict = next(line.split("|")[0] for line in lines if not line.startswith("COUNTS|"))
    counts = next(line for line in lines if line.startswith("COUNTS|"))
    return verdict, counts


@pytest.fixture
def no_guide(tmp_path: Path) -> Path:
    return tmp_path / "there-is-no-guide-here.md"


@pytest.fixture
def a_guide(tmp_path: Path) -> Path:
    guide = tmp_path / "guide.md"
    guide.write_text("a stand-in: only its existence is read here\n", encoding="utf-8")
    return guide


@pytest.mark.parametrize(
    "reason",
    [
        "REPCUT_SLOW=0",  # verify-02 #13's old opt-out
        "needs 5GB free, has 2.1GB",
        "tag prompt-03-done is not in this clone",
        "NO_DISK the space ran out",  # condition-shaped, not a condition
        "",
    ],
)
def test_a_skip_for_anything_but_the_three_conditions_fails(reason: str, no_guide: Path) -> None:
    verdict, counts = _gate_skip(reason, guide=no_guide, strict=False)

    assert verdict == "FAIL"
    assert counts.startswith("COUNTS|0|1|0|")


def test_a_detected_condition_skips_in_normal_mode(no_guide: Path) -> None:
    verdict, counts = _gate_skip("NO_GUIDE nothing to match against", guide=no_guide, strict=False)

    assert verdict == "SKIP"
    assert counts == "COUNTS|0|0|1| (1 skipped, conditions printed above)"


def test_a_detected_condition_fails_in_strict_mode(no_guide: Path) -> None:
    verdict, counts = _gate_skip("NO_GUIDE nothing to match against", guide=no_guide, strict=True)

    assert verdict == "FAIL"
    assert counts == "COUNTS|0|1|0| [strict]"


def test_a_condition_the_gate_finds_false_fails(a_guide: Path) -> None:
    """Printing the right word is not enough: the gate checks it independently."""
    verdict, _ = _gate_skip("NO_GUIDE says the check", guide=a_guide, strict=False)

    assert verdict == "FAIL"


def test_every_skip_in_every_gate_goes_through_the_policy() -> None:
    """One choke point: no gate prints a SKIP except through gate_skip."""
    for gate in sorted(SCRIPTS.glob("verify_*.sh")):
        text = gate.read_text(encoding="utf-8")
        direct = [
            line.strip()
            for line in text.splitlines()
            if re.search(r"(^|[;&|(]\s*|\)\s*)skipped\s+\"", line.strip())
        ]
        assert not direct, f"{gate.name} records a skip directly: {direct}"
        if "skipped()" in text:
            assert ". scripts/gate_skip.sh" in text, f"{gate.name} defines skipped() unsourced"


def test_a_python_check_must_name_its_condition() -> None:
    """The checks' helper takes the condition first, so a bare reason cannot be written."""
    source = (SCRIPTS / "verify_03_checks.py").read_text(encoding="utf-8")
    calls = re.findall(r"\bskipped\(\s*\"([A-Z_]+)\"", source)
    total = len(re.findall(r"\bskipped\(", source)) - 1  # minus the definition

    assert calls and len(calls) == total
    assert set(calls) <= {"NO_CONSOLE", "NO_GUIDE", "NO_GPU"}
