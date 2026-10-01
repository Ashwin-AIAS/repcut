r"""Resolve the POSIX shell that can actually see this machine's ports, and run
a script in it.

This exists because of one Windows fact with an expensive consequence.
``CreateProcess`` searches ``System32`` before ``PATH``, and
``C:\Windows\System32\bash.exe`` is **WSL's launcher**. So a bare ``bash`` -
from a Makefile recipe, from ``subprocess``, from PowerShell - does not run Git
Bash. It runs a Linux VM.

The scripts still appear to work, because WSL's binfmt interop happily executes
``.venv/Scripts/python.exe`` and ``npm`` as *Windows* processes: the servers
start, on the right ports, on the host. What does not work is every observation
the script makes about them. WSL2 has its own network namespace, so
``/dev/tcp/127.0.0.1/8000`` cannot reach a listener on the Windows host, and
``lsof``/``ss`` enumerate the VM. ``uname -s`` reports ``Linux``, so the Windows
branches - the ones that call ``taskkill`` - are never taken.

`make dev` therefore timed out waiting ninety seconds for an engine that had
already logged "Application startup complete", then failed to kill it, then
declared the still-occupied ports free on the next run. Three symptoms, one
cause, none of them visible from inside the script.

`scripts/dev_stack.py` had the correct resolver for its own subprocesses, so the
gate spawned Git Bash and passed while the Makefile spawned WSL and broke. The
resolver lives here now so there is exactly one of it, and so the Makefile can
reach it without importing the gate.

Stdlib only, and deliberately conservative about syntax: `make setup` runs
through this module with whatever ``python`` is on PATH, before a virtualenv
exists.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import FrameType

REPO_ROOT = Path(__file__).resolve().parents[1]

# `make dev`'s mode, and only its: a Ctrl-C is how that script is meant to end.
CTRL_C_STOPS = "--ctrl-c-stops"
# The file this wrapper creates the moment it sees a Ctrl-C, named to the script
# in this variable. `scripts/dev.sh` reads it to tell a child that died *of* the
# Ctrl-C (a console Ctrl-C reaches `next dev` directly, and it exits before the
# forwarded SIGINT lands) from a child that crashed on its own.
STOP_FILE_VARIABLE = "REPCUT_DEV_STOP_FILE"
_POLL_S = 0.25
# Records the shell's own MSYS pid, then becomes the script: `$$` survives exec.
_RECORD_PID = 'echo $$ > "$1"; shift; exec "$0" "$@"'


class ShellNotFoundError(RuntimeError):
    """No POSIX shell that can run `scripts/dev.sh` against this machine's ports."""


def _is_wsl_launcher(path: str) -> bool:
    """True for ``System32\bash.exe``, whatever case and separators it arrives in."""
    # "SystemRoot" is the real Windows variable's actual spelling (what a
    # native process sees when it enumerates its own environment), kept
    # verbatim rather than the all-caps style ruff suggests. It also has to
    # stay byte-for-byte what scripts/verify_02_checks.py's shell-resolution
    # test sets, which fakes this exact lookup.
    system_root = os.environ.get("SystemRoot", r"C:\Windows")  # noqa: SIM112
    system32 = str(Path(system_root) / "System32").lower()
    return str(Path(path)).lower().startswith(system32)


def bash_executable() -> str:
    """An absolute path to a POSIX shell, never the bare name ``bash``.

    ``REPCUT_BASH`` overrides, for a shell installed somewhere unusual. Otherwise
    ``PATH`` is consulted (which finds Git Bash when Git's ``usr/bin`` is on it,
    as it is inside Git Bash itself), the WSL launcher is rejected outright, and
    Git's two standard locations are the fallback - which is the case that
    matters, because a PowerShell ``PATH`` has System32 on it and Git's
    ``usr/bin`` usually not.
    """
    configured = os.environ.get("REPCUT_BASH", "").strip()
    if configured and Path(configured).is_file():
        return configured

    found = shutil.which("bash")
    if found is not None and not _is_wsl_launcher(found):
        return found

    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    raise ShellNotFoundError("no POSIX shell found; install Git Bash or set REPCUT_BASH")


def run_passthrough(shell: str, argv: list[str]) -> int:
    """Run the script and report its exit status - always, whatever reaches this process."""
    with subprocess.Popen([shell, *argv], cwd=str(REPO_ROOT)) as child:
        while True:
            try:
                return child.wait()
            except KeyboardInterrupt:
                # Named: a Ctrl-C reaches every process on the console, this one
                # included. The script owns the reaction, so keep waiting, as a
                # shell waits on a foreground job, and report what it returns.
                # Never a traceback (open issue 7, docs/reports/prompt-02.md), and
                # never an invented 130: a Ctrl-C that verify-03's criterion 16
                # sends to `make dev` also lands here while this process wraps
                # verify_03.sh, and returning 130 turned the gate's own finished
                # exit 1 (or 0) into 130 (verify-03 criterion 16b).
                continue


def _forward_sigint(shell: str, pidfile: Path) -> None:
    """SIGINT to the script's shell through MSYS - the one delivery its INT trap sees.

    A console Ctrl-C reaches native processes, this one included, but never Git
    Bash's INT trap: measured in a real PowerShell console, where `make dev` then
    ended only because its children died, through dev.sh's crash path.
    """
    try:
        msys_pid = pidfile.read_text(encoding="utf-8").strip()
    except OSError:
        return  # named: the script has not recorded its pid, so it is not running yet
    if not msys_pid.isdigit():
        return
    subprocess.run(
        [shell, "-c", f"kill -INT {msys_pid}"], capture_output=True, check=False, timeout=30
    )


def run_ctrl_c_stops(shell: str, argv: list[str]) -> int:
    """Run a script whose documented stop is Ctrl-C: forward it, wait, report 130.

    130 only once the script has exited - its teardown is the script's to finish,
    and `make dev` returning is a person's cue that the ports are theirs again. A
    handler rather than KeyboardInterrupt, so a second Ctrl-C mid-forward cannot
    surface as a traceback. On POSIX the terminal already signals the script's
    process group, so nothing is forwarded: a second SIGINT could re-enter the
    trap mid-teardown.

    The stop file is written first, inside the handler, before anything is
    forwarded: the forward is a whole bash process away, and in that gap the
    script can already be looking at a child the same Ctrl-C killed.
    """
    interrupted = threading.Event()

    handle, name = tempfile.mkstemp(prefix="repcut-dev-", suffix=".pid")
    os.close(handle)
    pidfile = Path(name)
    stop_file = pidfile.with_suffix(".stop")

    def _on_sigint(_signum: int, _frame: FrameType | None) -> None:
        interrupted.set()
        # Named: the temp directory refused the write. The forwarded SIGINT
        # still stops the script; only the crash/stop distinction is lost.
        with contextlib.suppress(OSError):
            stop_file.touch()

    environment = dict(os.environ)
    environment[STOP_FILE_VARIABLE] = stop_file.as_posix()
    previous = signal.signal(signal.SIGINT, _on_sigint)
    try:
        command = [shell, "-c", _RECORD_PID, shell, pidfile.as_posix(), *argv]
        with subprocess.Popen(command, cwd=str(REPO_ROOT), env=environment) as child:
            forwarded = False
            while True:
                try:
                    code = child.wait(timeout=_POLL_S)
                    break
                except subprocess.TimeoutExpired:
                    pass  # named: the poll interval, so the flag below is seen
                if interrupted.is_set() and not forwarded:
                    forwarded = True
                    if os.name == "nt":
                        _forward_sigint(shell, pidfile)
    finally:
        signal.signal(signal.SIGINT, previous)
        pidfile.unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)
    return 130 if interrupted.is_set() else code


def main(argv: list[str]) -> int:
    """Run ``argv[0]`` as a shell script, forwarding the rest as its arguments.

    The Makefile's entry point. Exit status is the script's - always, so `make`
    fails exactly when the script does - except under ``--ctrl-c-stops``, which
    only `make dev` passes: there a Ctrl-C is the stop a person asked for, and
    ends in 130 once the script has torn down (`run_ctrl_c_stops`).
    """
    ctrl_c_stops = bool(argv) and argv[0] == CTRL_C_STOPS
    if ctrl_c_stops:
        argv = argv[1:]
    if not argv:
        print(f"usage: posix_shell.py [{CTRL_C_STOPS}] <script.sh> [args...]", file=sys.stderr)
        return 2
    try:
        shell = bash_executable()
    except ShellNotFoundError as exc:
        # Named, because the alternative is make reporting "Error 127" for a
        # missing shell and the reader assuming the script is at fault.
        print(f"[posix-shell] {exc}", file=sys.stderr)
        print(
            "[posix-shell]   fix: install Git for Windows, or set REPCUT_BASH to a bash.exe",
            file=sys.stderr,
        )
        return 127
    if ctrl_c_stops:
        return run_ctrl_c_stops(shell, argv)
    return run_passthrough(shell, argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
