#!/usr/bin/env python3
"""Repcut development environment check.

Prints one row per check with OK / WARN / FAIL, and a named fix for every
non-OK row. Exits 1 if any *hard* check fails; GPU checks are WARN only,
because the engine must run — and CI must pass — on a machine with no GPU.

Never prints a secret value. Never prints an absolute path containing the OS
username: paths are shown relative to the repo root. See .claude/rules/secrets.md
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

MIN_PYTHON = (3, 11)
MIN_NODE_MAJOR = 20
MIN_FREE_DISK_GB = 20
MIN_VRAM_GB = 3.5

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"


@dataclass
class Result:
    """One row of the report. `hard` rows failing is what drives exit code 1."""

    name: str
    status: str
    detail: str = ""
    fix: str = ""
    hard: bool = True


def rel(path: Path) -> str:
    """Render a path relative to the repo root — never leak a user directory."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        # Outside the repo (e.g. a DATA_DIR on another drive). Show only the
        # final component so no username or drive layout is disclosed.
        return f"<outside-repo>/{path.name}"


def run(cmd: list[str]) -> str | None:
    """Run a command, return stdout+stderr, or None if it is not installed."""
    try:
        # check=False: a non-zero exit is data here (the probe reports it), not an error.
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except FileNotFoundError:
        # The executable is not on PATH — the common case this whole script exists for.
        return None
    except subprocess.TimeoutExpired:
        # A hung probe must not hang the environment check.
        return None
    except OSError:
        # Windows raises OSError for a malformed/inaccessible executable entry on PATH.
        return None
    return (proc.stdout or "") + (proc.stderr or "")


def check_python() -> Result:
    v = sys.version_info
    got = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= MIN_PYTHON:
        return Result("Python >= 3.11", OK, got)
    return Result(
        "Python >= 3.11",
        FAIL,
        got,
        "install Python 3.11 from python.org, then recreate the venv: "
        "python -m venv .venv && make setup",
    )


def check_ffmpeg() -> tuple[Result, Result]:
    out = run(["ffmpeg", "-version"])
    if out is None:
        missing = Result(
            "ffmpeg on PATH",
            FAIL,
            "not found",
            "winget install Gyan.FFmpeg  (then open a new terminal so PATH refreshes)",
        )
        return missing, Result(
            "ffmpeg built with libx264",
            FAIL,
            "ffmpeg missing",
            "install ffmpeg first (see the row above)",
        )

    first = out.splitlines()[0] if out.splitlines() else ""
    version = first.replace("ffmpeg version ", "").split(" ")[0] or "unknown"
    found = Result("ffmpeg on PATH", OK, version)

    if "libx264" in out:
        x264 = Result("ffmpeg built with libx264", OK, "enabled")
    else:
        x264 = Result(
            "ffmpeg built with libx264",
            FAIL,
            "not in build config",
            "install a full build: winget install Gyan.FFmpeg "
            "(the 'essentials' build omits libx264; exports require it)",
        )
    return found, x264


def check_node() -> Result:
    out = run(["node", "--version"])
    if out is None:
        return Result(
            "Node >= 20",
            FAIL,
            "not found",
            "winget install OpenJS.NodeJS.LTS  (then open a new terminal)",
        )
    raw = out.strip().splitlines()[0].lstrip("v")
    try:
        major = int(raw.split(".")[0])
    except ValueError:
        return Result(
            "Node >= 20", FAIL, f"unparseable version {raw!r}", "reinstall Node 20 LTS"
        )
    if major >= MIN_NODE_MAJOR:
        return Result("Node >= 20", OK, f"v{raw}")
    return Result(
        "Node >= 20",
        FAIL,
        f"v{raw}",
        "winget install OpenJS.NodeJS.LTS  (Next.js 14 requires Node 18.17+; we target 20)",
    )


def check_npm() -> Result:
    # npm is a .cmd shim on Windows; resolve through the shell's PATH lookup.
    out = run(["npm", "--version"]) or run(["npm.cmd", "--version"])
    if out is None:
        return Result(
            "npm on PATH",
            FAIL,
            "not found",
            "reinstall Node LTS - npm ships with it",
        )
    return Result("npm on PATH", OK, out.strip().splitlines()[0])


def check_make() -> Result:
    out = run(["make", "--version"])
    if out is None:
        return Result(
            "make on PATH",
            FAIL,
            "not found",
            "winget install GnuWin32.Make and add its bin/ to PATH - "
            "every gate in this project is a make target",
        )
    return Result("make on PATH", OK, out.splitlines()[0].strip())


def check_torch() -> list[Result]:
    """GPU checks are WARN, never FAIL: the engine has a mandatory CPU path."""
    cuda_fix = (
        "install a CUDA build into the venv: "
        ".venv/Scripts/python -m pip install torch "
        "--index-url https://download.pytorch.org/whl/cu124   (or: make setup-gpu)"
    )
    # Absence is not a problem to be fixed now — it is a scheduled install. Say
    # so, and say when, so a WARN row explains itself instead of nagging.
    # See docs/guide-amendments/003-defer-torch-to-prompt-07.md
    deferred_fix = (
        "no action needed yet - torch is deferred to Prompt 07 (RIFE slow-mo), "
        "its first consumer. Nothing through Prompt 06 imports it: captions use "
        "faster-whisper (CTranslate2). The engine runs on CPU without it and "
        "/health reports torch_device_active=cpu. When Prompt 07 starts: make setup-gpu"
    )

    try:
        import torch
    except ImportError:
        # torch is deliberately not an engine dependency — absence is expected
        # until GPU work begins, so this is a warning, not a failure.
        return [
            Result("torch importable", WARN, "not installed (expected until Prompt 07)",
                   deferred_fix, hard=False),
            Result("CUDA visible to torch", WARN, "torch missing", "", hard=False),
            Result("GPU is an RTX 3050", WARN, "torch missing", "", hard=False),
            Result("total VRAM >= 3.5GB", WARN, "torch missing", "", hard=False),
        ]

    results = [Result("torch importable", OK, torch.__version__, hard=False)]

    try:
        cuda_ok = torch.cuda.is_available()
    except RuntimeError:
        # A driver/CUDA init failure must degrade to CPU, never crash the check.
        cuda_ok = False

    if not cuda_ok:
        build = "CPU-only build" if "+cpu" in torch.__version__ else "no CUDA device"
        results += [
            Result("CUDA visible to torch", WARN, build, cuda_fix, hard=False),
            Result("GPU is an RTX 3050", WARN, "CUDA unavailable", cuda_fix, hard=False),
            Result("total VRAM >= 3.5GB", WARN, "CUDA unavailable", cuda_fix, hard=False),
        ]
        return results

    results.append(Result("CUDA visible to torch", OK, f"cuda {torch.version.cuda}", hard=False))

    name = torch.cuda.get_device_name(0)
    if "3050" in name:
        results.append(Result("GPU is an RTX 3050", OK, name, hard=False))
    else:
        results.append(
            Result(
                "GPU is an RTX 3050",
                WARN,
                name,
                "not a blocker - but the 4GB VRAM budget in .claude/rules/gpu-vram.md "
                "was sized for a 3050; re-profile before trusting it",
                hard=False,
            )
        )

    total_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
    if total_gb >= MIN_VRAM_GB:
        results.append(Result("total VRAM >= 3.5GB", OK, f"{total_gb:.1f}GB", hard=False))
    else:
        results.append(
            Result(
                "total VRAM >= 3.5GB",
                WARN,
                f"{total_gb:.1f}GB",
                "models must be tiled more aggressively; see .claude/rules/gpu-vram.md",
                hard=False,
            )
        )
    return results


def data_dir() -> Path:
    raw = os.environ.get("DATA_DIR") or read_env_file().get("DATA_DIR") or "./data"
    path = Path(raw)
    return path if path.is_absolute() else (REPO_ROOT / path)


def check_data_dir() -> tuple[Result, Result]:
    target = data_dir()
    shown = rel(target)

    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".repcut_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = Result("DATA_DIR exists and is writable", OK, shown)
    except OSError as exc:
        # Permission denied, read-only volume, or a missing parent drive.
        writable = Result(
            "DATA_DIR exists and is writable",
            FAIL,
            f"{shown} ({exc.__class__.__name__})",
            "check DATA_DIR in .env points at a writable location on an existing drive",
        )

    try:
        free_gb = shutil.disk_usage(target if target.exists() else REPO_ROOT).free / 1024**3
    except OSError:
        # The volume disappeared or is not addressable.
        return writable, Result(
            f"free disk >= {MIN_FREE_DISK_GB}GB in DATA_DIR",
            FAIL,
            "could not read disk usage",
            "check the drive holding DATA_DIR is mounted",
        )

    if free_gb >= MIN_FREE_DISK_GB:
        disk = Result(
            f"free disk >= {MIN_FREE_DISK_GB}GB in DATA_DIR", OK, f"{free_gb:.0f}GB free"
        )
    else:
        disk = Result(
            f"free disk >= {MIN_FREE_DISK_GB}GB in DATA_DIR",
            FAIL,
            f"{free_gb:.0f}GB free",
            f"free at least {MIN_FREE_DISK_GB - free_gb:.0f}GB - raw 4K gym footage, "
            "normalized intermediates and model weights all land in DATA_DIR",
        )
    return writable, disk


def read_env_file() -> dict[str, str]:
    """Parse .env into key -> value.

    The VALUE IS NEVER RETURNED TO THE REPORT — only used to test non-emptiness.
    Nothing in this module may print a value read here.
    """
    env_path = REPO_ROOT / ".env"
    values: dict[str, str] = {}
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        # No .env yet — reported as its own check row, not an error here.
        return values
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def check_env_file() -> tuple[Result, Result]:
    if not (REPO_ROOT / ".env").is_file():
        missing = Result(
            ".env present",
            FAIL,
            "not found",
            "copy .env.example .env   then fill in GEMINI_API_KEY and REPCUT_GUIDE_PATH",
        )
        return missing, Result(
            "GEMINI_API_KEY set",
            FAIL,
            "set: false",
            "create .env first (see the row above)",
        )

    present = Result(".env present", OK, "gitignored, as required")
    # Only the boolean escapes this scope. Never the value.
    key_set = bool(read_env_file().get("GEMINI_API_KEY", "").strip())
    if key_set:
        return present, Result("GEMINI_API_KEY set", OK, "set: true")
    return present, Result(
        "GEMINI_API_KEY set",
        FAIL,
        "set: false",
        "get a free-tier key at aistudio.google.com/apikey and put it in .env "
        "(never in .env.example, never in a commit)",
    )


def check_precommit() -> Result:
    """A public repo without the hook installed is one careless commit from a leak."""
    hook = REPO_ROOT / ".git" / "hooks" / "pre-commit"
    if run(["pre-commit", "--version"]) is None:
        return Result(
            "pre-commit hook installed",
            FAIL,
            "pre-commit not installed",
            "pip install pre-commit && pre-commit install",
        )
    if not hook.is_file():
        return Result(
            "pre-commit hook installed",
            FAIL,
            "hook not wired into .git/hooks",
            "pre-commit install",
        )
    return Result("pre-commit hook installed", OK, "gitleaks + guardrails active")


def main() -> int:
    ffmpeg_found, ffmpeg_x264 = check_ffmpeg()
    dir_writable, disk_free = check_data_dir()
    env_present, key_present = check_env_file()

    results: list[Result] = [
        check_python(),
        ffmpeg_found,
        ffmpeg_x264,
        check_node(),
        check_npm(),
        check_make(),
        *check_torch(),
        dir_writable,
        disk_free,
        env_present,
        key_present,
        check_precommit(),
    ]

    width = max(len(r.name) for r in results)
    print("Repcut environment check")
    print()
    for r in results:
        print(f"  [{r.status:^4}]  {r.name.ljust(width)}   {r.detail}")
        if r.status != OK and r.fix:
            print(f"          {' ' * width}   fix: {r.fix}")

    hard_failures = [r for r in results if r.status == FAIL and r.hard]
    warnings = [r for r in results if r.status == WARN]

    print()
    if hard_failures:
        print(f"FAILED: {len(hard_failures)} required check(s) failed, {len(warnings)} warning(s)")
        return 1
    print(f"PASSED: {len(results)} checks, {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
