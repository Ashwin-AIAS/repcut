#!/usr/bin/env python3
"""The only environment conditions a gate criterion may SKIP for (amendment 014).

A criterion that cannot run here says which condition stopped it; the gate then
asks this module, independently, whether that condition actually holds. A skip
for anything else, or for a condition that does not hold, is a FAIL. So a check
cannot excuse itself by printing the right word, and nothing here can be forced
true from outside: there is deliberately no override flag or variable. An
override anyone can set is a loophole (Ashwin, 2026-09-28).

    NO_CONSOLE  no console attached, so a real Ctrl-C cannot be delivered
    NO_GUIDE    REPCUT_GUIDE_PATH unset or not a readable file (amendment 006)
    NO_GPU      no NVIDIA GPU visible to the driver (gpu-vram.md)

Usage:
    python scripts/gate_conditions.py CONDITION

Exit codes:
    0  the condition holds here (evidence on stdout)
    1  it does not hold (evidence on stdout)
    2  not a condition this module knows; the gate reads anything but 0 as
       "does not hold", so this also ends in FAIL
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_plan_titles  # after the sys.path insert it depends on


def no_console() -> tuple[bool, str]:
    """Whether this process lacks a console to receive a Ctrl-C."""
    if sys.platform == "win32":
        import ctypes

        window = ctypes.windll.kernel32.GetConsoleWindow()  # type: ignore[attr-defined]
        return not window, f"GetConsoleWindow() == {window or 0}"
    try:
        tty = os.open("/dev/tty", os.O_RDONLY)
    except OSError as error:
        # Named: no controlling terminal is exactly the condition asked about.
        return True, f"/dev/tty: {error.strerror}"
    os.close(tty)
    return False, "/dev/tty opens"


def no_guide() -> tuple[bool, str]:
    """Whether the private build guide is out of reach, read the way the title guard reads it."""
    guide = check_plan_titles.guide_path()
    if guide is None:
        return True, "REPCUT_GUIDE_PATH is not set (environment or .env)"
    if not guide.is_file():
        return True, "REPCUT_GUIDE_PATH does not point at a readable file"
    return False, "the guide resolves to a readable file"


def no_gpu() -> tuple[bool, str]:
    """Whether the NVIDIA driver reports no GPU."""
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return True, "nvidia-smi is not on PATH"
    try:
        listed = subprocess.run([smi, "-L"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        # Named: a driver that cannot be asked cannot report a GPU either.
        return True, f"nvidia-smi -L did not answer: {type(error).__name__}"
    gpus = [line for line in listed.stdout.splitlines() if line.startswith("GPU ")]
    if listed.returncode != 0 or not gpus:
        return True, f"nvidia-smi -L exit {listed.returncode}, {len(gpus)} GPU(s)"
    return False, f"{len(gpus)} GPU(s) listed"


CONDITIONS: dict[str, Callable[[], tuple[bool, str]]] = {
    "NO_CONSOLE": no_console,
    "NO_GUIDE": no_guide,
    "NO_GPU": no_gpu,
}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in CONDITIONS:
        asked = " ".join(argv[1:]) or "(none)"
        print(f"not a skip condition: {asked}; known: {', '.join(CONDITIONS)}")
        return 2
    holds, evidence = CONDITIONS[argv[1]]()
    print(evidence)
    return 0 if holds else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
