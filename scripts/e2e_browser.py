#!/usr/bin/env python3
"""The happy path through the real UI: theme, edit, copilot off, export.

Runs against ``make dev`` (``dev_stack.DevStack``: the real launcher, a scratch
``$DATA_DIR``, Gemini off - mocked by absence, no key reaches it) and drives the
installed Chrome or Edge over the DevTools protocol (``cdp_browser.py``, the
repo's stand-in for Playwright, amendment 012). Three generated clips go in
through the engine's own upload route (the browser cannot hand a headless file
picker a file without a second driver); everything after that is clicks:

    pick another theme -> the graded still changes
    lock cut 2         -> the timeline shows it locked
    the copilot panel  -> input disabled with a reason (no key: P5, offline)
    Export             -> "Last export" with a Download link, no platform problem

Prints ``MEASURED:`` JSON; exit 0 only if every step did what it says.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repcut.music.synthetic import generate_track, to_wav_bytes  # noqa: E402

HAPPY_PATH = """
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = async (fn, ms) => {
    const end = Date.now() + ms;
    while (Date.now() < end) { const v = fn(); if (v) return v; await sleep(250); }
    return null;
  };
  const out = {};
  // 1. theme: another theme in the Theme radiogroup changes the graded still.
  const still = () => {
    const img = document.querySelector('img[data-graded-current="true"]');
    return img && img.complete && img.naturalWidth > 0 ? img : null;
  };
  const first = await until(still, 60000);
  if (!first) return {error: "no graded still rendered", ...out};
  const themes = document.querySelector('[role="radiogroup"][aria-label="Theme"]');
  const pick = themes && [...themes.querySelectorAll('[role="radio"]')].find(
    (r) => r.getAttribute("aria-checked") !== "true");
  if (!pick) return {error: "no other theme to pick", ...out};
  const src = first.src;
  pick.click();
  const changed = () => { const i = still(); return i && i.src !== src; };
  out.themeChanged = !!(await until(changed, 30000));
  // 2. edit: lock cut 2 and see it locked on the timeline.
  const ready = await until(() => document.querySelectorAll("[data-shot-id]").length >= 2, 60000);
  if (!ready) return {error: "no cut list rendered", ...out};
  const lock = await until(() => document.querySelector('button[aria-label="Lock cut 2"]'), 10000);
  if (!lock) return {error: "no lock control", ...out};
  lock.click();
  out.cutLocked = !!(await until(() => document.querySelector('[data-locked="true"]'), 15000));
  // 3. copilot with no key: the input is disabled and a reason is shown.
  const input = await until(() => document.getElementById("copilot-input"), 10000);
  out.copilotOff = !!input && input.disabled
    && /off|key|offline|unavailable/i.test(input.closest("section, div").innerText);
  // 4. export: the Export button, then "Last export" with a Download link.
  const button = [...document.querySelectorAll("button")].find(
    (b) => b.innerText.trim() === "Export" && !b.disabled);
  if (!button) return {error: "no Export button", ...out};
  button.click();
  const done = await until(() => {
    const last = document.querySelector('section[aria-label="Last export"]');
    return last && last.querySelector("a[download]") ? last : null;
  }, 600000);
  out.exported = !!done;
  out.platformSafe = !!done
    && !document.querySelector('ul[aria-label="Why this file is not safe to post"]');
  out.failed = /The export failed/.test(document.body.innerText);
  return out;
})()
"""

STEPS = ("themeChanged", "cutLocked", "copilotOff", "exported", "platformSafe")


def main() -> int:
    import dev_stack
    import verify_02_checks as v2
    from cdp_browser import BrowserNotFoundError, inspect_page

    began = time.monotonic()
    with (
        dev_stack.DevStack(gemini_enabled=False) as stack,
        TemporaryDirectory(prefix="repcut-e2e-ui-", ignore_cleanup_errors=True) as scratch,
    ):
        music = stack.data_dir / "music"
        music.mkdir(parents=True, exist_ok=True)
        track = generate_track(bpm=120.0, bars=48, intro_bars=4, build_bars=4, drop_bars=24)
        (music / "e2e ui 120 bpm.wav").write_bytes(to_wav_bytes(track.samples, track.sample_rate))
        stack.start()
        if not stack.wait_ready():
            print("MEASURED: stack did not start")
            print("FAILED: `make dev` never reached both ports")
            return 1
        status, project = stack.engine_request("POST", "/projects", {"name": "e2e ui"})
        if status != 201 or not isinstance(project, dict):
            print(f"MEASURED: POST /projects -> HTTP {status}")
            print("FAILED: could not create a project against the running stack")
            return 1
        project_id = str(project["id"])
        for index in range(3):
            clip = v2.make_clip(Path(scratch) / f"ui{index}.mp4", seconds=10.0 + index)
            if not v2.upload(stack.engine_port, project_id, clip).get("sha256"):
                print("MEASURED: upload failed")
                print("FAILED: a clip did not upload")
                return 1
        stack.engine_request("POST", "/music/scan")
        deadline = time.monotonic() + v2.INGEST_TIMEOUT_S
        while time.monotonic() < deadline:
            stack.engine_request("POST", f"/projects/{project_id}/edit/signals")
            _, edit = stack.engine_request("GET", f"/projects/{project_id}/edit")
            if isinstance(edit, dict) and not edit.get("clips_pending") and edit.get("snapped"):
                break
            time.sleep(2.0)
        else:
            print("MEASURED: the edit never became ready")
            print("FAILED: ingest, signals or the music scan did not finish")
            return 1
        try:
            report = asyncio.run(
                inspect_page(
                    f"http://localhost:{stack.ui_port}/projects/{project_id}",
                    observe_seconds=6.0,
                    evaluate=HAPPY_PATH,
                    evaluate_budget_s=720.0,
                )
            )
        except BrowserNotFoundError as error:
            print("MEASURED: no browser")
            print(f"FAILED: the happy path needs Chrome or Edge: {error}")
            return 1
    result = report.evaluated if isinstance(report.evaluated, dict) else {"error": "no result"}
    result["seconds"] = round(time.monotonic() - began)
    print("MEASURED: " + json.dumps(result))
    if "error" in result:
        print(f"FAILED: {result['error']}")
        return 1
    missing = [step for step in STEPS if not result.get(step)]
    if missing or result.get("failed"):
        print("FAILED: steps that did not do what they say: " + ", ".join(missing or ["export"]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
