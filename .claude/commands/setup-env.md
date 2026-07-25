---
description: Run the environment check and diagnose every failure with a named fix
allowed-tools: Read, Write, Edit, Grep, Glob, Bash
---

Verify the development environment.

**Before Prompt 01:** `scripts/check_env.py` does not exist yet — it is a
Prompt 01 deliverable. Until then, run the checks below inline with `Bash` and
report the same table. Do not create the script early; Prompt 01 owns it.

1. Run `python scripts/check_env.py` (or the equivalent checks inline). It verifies:
   - Python ≥ 3.11
   - ffmpeg present, with libx264, and ffprobe available
   - CUDA visible to PyTorch, RTX 3050 detected, free VRAM reported
   - ≥ 20GB free disk in `DATA_DIR`
   - Node version and package manager present
   - `.env` exists and every required key is **set** (report `set: true/false`
     only — never print a value)

2. For each failure, give the **named fix**, not a generic message. "ffmpeg not
   found" is useless; "install ffmpeg and ensure it is on PATH — verify with
   `ffmpeg -version`, must list `--enable-libx264`" is a fix.

3. If CUDA is unavailable: this is **not fatal**. The engine must boot on CPU
   with the fallback flagged in `/health`. Report it as a warning and confirm
   the CPU path works. A build that only runs on one machine is a broken build.

4. If `.env` is missing: `cp .env.example .env`, then tell Ashwin which keys he
   must fill in himself. **Never** generate, guess, or paste a key value. Never
   read a key from anywhere and echo it.

5. Confirm `make verify-00` is green — the harness gate — and that
   `pre-commit install` has been run — the local gitleaks hook is the
   first line of defence on a public repo.

Exit non-zero if any hard requirement fails.
