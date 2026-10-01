"""`make dev`'s Ctrl-C wrapper tells the launcher a Ctrl-C happened, before it forwards one.

On Windows a console Ctrl-C reaches every process on the console: `next dev`
dies of it at once, and `scripts/dev.sh` hears about it only when
`scripts/posix_shell.py --ctrl-c-stops` forwards a SIGINT a moment later. In
that gap the launcher found its UI dead and reported a crash (prompt-04
review). The wrapper now writes a stop file first; `dev.sh` reads it. This file
proves the wrapper half; the launcher half runs against a real stack in
verify-02's criterion 19, phase "ctrl-c-then-child-exit".
"""

import importlib.util
import os
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

# Stands in for dev.sh: ignores the forwarded SIGINT, so the only way it can
# learn of the Ctrl-C is the stop file - which is the window under test.
_WAITS_FOR_STOP_FILE = """
trap '' INT
tick=0
while [ "$tick" -lt 200 ]; do
  [ -n "${REPCUT_DEV_STOP_FILE:-}" ] && [ -e "$REPCUT_DEV_STOP_FILE" ] && { echo SAW_STOP; exit 0; }
  sleep 0.1
  tick=$((tick + 1))
done
echo NO_STOP
exit 3
"""

# Runs the wrapper in its own interpreter, so the SIGINT it raises at itself -
# what a console Ctrl-C delivers to it - never reaches pytest.
_DRIVER = """
import signal, sys, threading
sys.path.insert(0, sys.argv[1])
import posix_shell
threading.Timer(1.5, signal.raise_signal, [signal.SIGINT]).start()
sys.exit(posix_shell.run_ctrl_c_stops(sys.argv[2], [sys.argv[3]]))
"""


def test_a_ctrl_c_is_written_down_for_the_launcher_before_anything_else(tmp_path: Path) -> None:
    try:
        bash = posix_shell.bash_executable()
    except posix_shell.ShellNotFoundError:
        pytest.fail("no POSIX bash - `make dev` cannot run here either")
    script = tmp_path / "waits_for_stop_file.sh"
    script.write_bytes(_WAITS_FOR_STOP_FILE.encode("utf-8"))  # LF: bash reads CR as data

    completed = subprocess.run(
        [sys.executable, "-c", _DRIVER, str(SCRIPTS), bash, script.as_posix()],
        env={k: v for k, v in os.environ.items() if k != posix_shell.STOP_FILE_VARIABLE},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert "SAW_STOP" in completed.stdout, completed.stdout + completed.stderr
    assert completed.returncode == 130, "a Ctrl-C stop still reports 130"
