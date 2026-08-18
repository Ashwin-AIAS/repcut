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

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class ShellNotFoundError(RuntimeError):
    """No POSIX shell that can run `scripts/dev.sh` against this machine's ports."""


def _is_wsl_launcher(path: str) -> bool:
    """True for ``System32\bash.exe``, whatever case and separators it arrives in."""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
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
    raise ShellNotFoundError(
        "no POSIX shell found; install Git Bash or set REPCUT_BASH"
    )


def main(argv: list[str]) -> int:
    """Run ``argv[0]`` as a shell script, forwarding the rest as its arguments.

    The Makefile's entry point. Exit status is the script's, so `make` still
    fails when the script does.
    """
    if not argv:
        print("usage: posix_shell.py <script.sh> [args...]", file=sys.stderr)
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
    return subprocess.call([shell, *argv], cwd=str(REPO_ROOT))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
