#!/usr/bin/env python3
"""Measurements behind ``scripts/verify_05.sh``: one check per criterion.

Every check prints ``MEASURED: <value>`` and exits 0 (pass) or 1 (fail), with
``FAILED: <reason>`` on failure - the contract verify_03/04 established. Music
is generated (``repcut.music.synthetic``); nothing is downloaded but the pinned
speech model, by ``fetch_models.py``. No absolute path is printed.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import shutil
import subprocess
import sys
import time
import wave
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE = REPO_ROOT / "engine"
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repcut.edit import ducking  # noqa: E402
from repcut.edit.planner import PlanInputs, lock, plan  # noqa: E402
from repcut.edit.types import BeatGrid, Pace, Plan, Segment, Shot  # noqa: E402
from repcut.music.analysis import TrackAnalysis, analyse  # noqa: E402
from repcut.music.synthetic import SyntheticTrack, generate_track, to_wav_bytes  # noqa: E402
from verify_03_checks import failed, measured  # noqa: E402 - after the sys.path insert

SNAP = 0.040
TARGETS = (15.0, 30.0, 60.0)
RESYNC_BUDGET_S = 3.0
DUCK_MIN_DB = 8.0


# --- fixtures ------------------------------------------------------------------


def _track(bpm: float, first_beat: float = 0.25) -> tuple[SyntheticTrack, TrackAnalysis]:
    track = generate_track(
        bpm=bpm, bars=48, intro_bars=8, build_bars=8, drop_bars=16, first_beat_seconds=first_beat
    )
    return track, analyse(track.samples, track.sample_rate)


def _segments(seed: int = 3, clips: int = 4, per_clip: int = 6) -> tuple[Segment, ...]:
    rng = random.Random(seed)  # noqa: S311 - fixture data, not security
    out = []
    for clip in range(clips):
        sha = f"{clip:x}" * 64
        position = 0.0
        for _ in range(per_clip):
            length = rng.uniform(1.5, 9.0)
            out.append(
                Segment(
                    Segment.make_id(sha, position, position + length), sha, position,
                    position + length, quality=rng.random(), energy=rng.random(),
                )
            )  # fmt: skip
            position += length + rng.uniform(0.5, 2.0)
    return tuple(out)


def _errors(result: Plan, times: list[float], cuts: tuple[float, ...]) -> float:
    beats = [t - result.music_start_seconds for t in times]
    return max((min(abs(c - b) for b in beats) for c in cuts), default=0.0)


def _pytest(*nodes: str) -> tuple[int, str]:
    """Run pytest nodes from engine/, returning (exit code, summary line)."""
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *nodes],
        cwd=ENGINE,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    lines = [
        line for line in completed.stdout.splitlines() if re.search(r"\d+ (passed|failed)", line)
    ]
    return completed.returncode, (lines[-1] if lines else "no summary")


# --- checks --------------------------------------------------------------------


def check_planner_on_beat() -> int:
    """1. Cuts on beats (+-40 ms), duration +-5 %, min shot, deterministic - three durations."""
    track, analysis = _track(128.0)
    grid = BeatGrid.from_analysis(analysis, "a" * 64)
    truth = [track.first_beat_seconds + i * 60.0 / track.bpm for i in range(48 * 4)]
    pace = Pace()
    rows, problems = [], []
    for target in TARGETS:
        inputs = PlanInputs(_segments(), grid, target, pace, seed=7)
        first, second = plan(inputs), plan(inputs)
        grid_err = _errors(first, list(grid.beats), first.cuts)
        truth_err = _errors(first, truth, first.cuts)
        drift = abs(first.duration_seconds - target) / target
        shortest = min(shot.duration for shot in first.shots)
        same = first.to_json() == second.to_json()
        other_seed = plan(replace(inputs, seed=8)).to_json() != first.to_json()
        rows.append(
            f"{target:.0f}s: grid {grid_err * 1000:.1f} ms, truth {truth_err * 1000:.1f} ms, "
            f"dur {drift * 100:.1f}%, min {shortest:.2f}s, same {same}"
        )
        if grid_err > SNAP or truth_err > SNAP:
            problems.append(f"{target:.0f}s cut off the beat")
        if drift > 0.05:
            problems.append(f"{target:.0f}s duration off by {drift * 100:.1f}%")
        if shortest < pace.min_shot_seconds - 1e-6:
            problems.append(f"{target:.0f}s has a {shortest:.2f}s shot")
        if not same or not other_seed:
            problems.append(f"{target:.0f}s is not deterministic by seed")
    measured("; ".join(rows))
    if problems:
        failed(", ".join(problems))
        return 1
    return 0


def check_track_change_resync() -> int:
    """2. New track: 100 % of unlocked cuts re-snap; locked shots byte-identical."""
    _, first_analysis = _track(128.0)
    _, other_analysis = _track(100.0, first_beat=0.6)
    grid = BeatGrid.from_analysis(first_analysis, "a" * 64)
    other = BeatGrid.from_analysis(other_analysis, "b" * 64)
    before = plan(PlanInputs(_segments(), grid, 30.0, Pace(), seed=7))
    locked = (lock(before, before.shots[2].id), lock(before, before.shots[5].id))
    after = plan(PlanInputs(_segments(), other, 30.0, Pace(), seed=7, locked=locked))
    after_json = [json.dumps(s.to_json(), sort_keys=True) for s in after.shots]
    identical = sum(json.dumps(s.to_json(), sort_keys=True) in after_json for s in locked)
    edges = {e for s in locked for e in (s.timeline_in, s.timeline_out)}
    unlocked = [c for c in after.cuts if c not in edges]
    on_beat = [c for c in unlocked if _errors(after, list(other.beats), (c,)) <= SNAP]
    measured(
        f"locked byte-identical {identical}/{len(locked)}; unlocked cuts on the new grid "
        f"{len(on_beat)}/{len(unlocked)}"
    )
    if identical != len(locked) or not unlocked or len(on_beat) != len(unlocked):
        failed("a locked shot changed or an unlocked cut stayed off the new grid")
        return 1
    return 0


def check_drop_alignment() -> int:
    """3. The drop opens on a cut within one bar, with top-quartile-energy footage."""
    _, analysis = _track(128.0)
    grid = BeatGrid.from_analysis(analysis, "a" * 64)
    energies = sorted(s.energy for s in _segments())
    rows, bad = [], []
    for target in TARGETS:
        result = plan(PlanInputs(_segments(), grid, target, Pace(), seed=7))
        assert analysis.drop_seconds is not None
        drop = analysis.drop_seconds - result.music_start_seconds
        opening = min(result.shots, key=lambda s: abs(s.timeline_in - drop))
        offset = abs(opening.timeline_in - drop)
        energy = next(s.energy for s in _segments() if s.id == opening.segment_id)
        percentile = energies.index(energy) / (len(energies) - 1)
        rows.append(
            f"{target:.0f}s: cut {offset * 1000:.0f} ms from drop, energy pct {percentile:.2f}"
        )
        if offset > 4 * 60.0 / analysis.bpm or percentile < 0.75:
            bad.append(f"{target:.0f}s")
    measured("; ".join(rows))
    if bad:
        failed(f"drop not aligned or not on top footage at {', '.join(bad)}")
        return 1
    return 0


def check_resync_budget() -> int:
    """4. A full re-plan of a large session, with locks, under 3 s."""
    from repcut.edit.resync import Override, apply, timed_replan
    from repcut.edit.types import Decisions

    _, analysis = _track(128.0)
    grid = BeatGrid.from_analysis(analysis, "a" * 64)
    segments = _segments(clips=20, per_clip=15)
    decisions = Decisions(target_seconds=60.0, seed=3)
    first = timed_replan(segments, grid, Pace(), decisions)
    decisions = apply(decisions, first.plan, Override("cut_locked", shot_id=first.plan.shots[3].id))
    decisions = apply(
        decisions, first.plan, Override("cut_removed", shot_id=first.plan.shots[6].id)
    )
    worst = max(timed_replan(segments, grid, Pace(), decisions).seconds for _ in range(5))
    measured(f"{len(segments)} segments, 60 s edit: worst re-plan {worst * 1000:.1f} ms of 3000")
    if worst >= RESYNC_BUDGET_S:
        failed("re-plan over budget")
        return 1
    return 0


def _rms_db(path: Path, start: float, end: float) -> float:
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        frames = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    samples = frames.reshape(-1, 2).astype(float)[int(start * rate) : int(end * rate)]
    return float(20 * np.log10(np.sqrt(np.mean(samples**2))))


def check_ducking() -> int:
    """5. Speech segments show >= 8 dB music reduction in rendered audio."""
    from repcut.edit.vad import SpeechRegion
    from repcut.media.ffmpeg_builder import build_music_stem, run

    sha = "f" * 64
    shots = (
        Shot("a", "s1", sha, 10.0, 20.0, 0.0, 10.0, 0, 20),
        Shot("b", "s2", sha, 30.0, 40.0, 10.0, 20.0, 20, 40),
    )
    edit = Plan(shots, 20.0, 20.0, 5.0, True, "x" * 64, 1)
    speech = {sha: (SpeechRegion(12.0, 14.0), SpeechRegion(33.0, 36.5))}
    regions = ducking.timeline_speech(edit, speech)
    ramps = ducking.envelope(edit, regions, {"s1": 0.2, "s2": 0.2})
    track = generate_track(bpm=128, bars=40, intro_bars=2, build_bars=2, drop_bars=30)
    with TemporaryDirectory(prefix="repcut-gate05-") as scratch:
        root = Path(scratch)
        music = root / "music.wav"
        music.write_bytes(to_wav_bytes(track.samples, track.sample_rate))

        async def render(name: str, expression: str) -> Path:
            out = root / name
            await run(
                build_music_stem(
                    music, out, start_seconds=5.0, duration_seconds=20.0,
                    gain_db_expression=expression,
                )
            )  # fmt: skip
            return out

        ducked = asyncio.run(render("ducked.wav", ducking.gain_expression(ramps)))
        plain = asyncio.run(render("plain.wav", "0"))
        reductions = [_rms_db(plain, a, b) - _rms_db(ducked, a, b) for a, b in regions]
        base = [_rms_db(ducked, 6.0, 9.0) - _rms_db(ducked, a, b) for a, b in regions]
    measured(
        "under speech vs undocked: "
        + ", ".join(f"{r:.1f} dB" for r in reductions)
        + "; vs the music's own base: "
        + ", ".join(f"{r:.1f} dB" for r in base)
    )
    if not regions or min(reductions + base) < DUCK_MIN_DB:
        failed("music was not at least 8 dB lower under speech")
        return 1
    return 0


def check_speech_model() -> int:
    """6. The pinned speech model is present and verified; no speech in music, noise or tone."""
    from repcut.config import get_settings
    from repcut.edit.vad import MODEL_STORED_PATH, VadUnavailableError, detect_speech
    from repcut.media.store import absolute

    fetched = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "fetch_models.py")],
        capture_output=True, text=True, timeout=300, check=False,
    )  # fmt: skip
    if fetched.returncode != 0:
        measured(fetched.stdout.strip()[-160:] or "fetch failed")
        failed("the speech model could not be fetched and verified")
        return 1
    model = absolute(get_settings().data_dir, MODEL_STORED_PATH)
    import librosa

    track = generate_track(bpm=128.0)
    music = librosa.resample(track.samples, orig_sr=track.sample_rate, target_sr=16000)
    rng = np.random.default_rng(1)
    samples = {
        "music": music.astype(np.float32),
        "noise": (rng.standard_normal(16000 * 8) * 0.1).astype(np.float32),
        "tone": (0.5 * np.sin(2 * np.pi * 440 * np.arange(16000 * 8) / 16000)).astype(np.float32),
    }
    try:
        found = {name: detect_speech(audio, model) for name, audio in samples.items()}
    except VadUnavailableError as error:
        measured("model unusable")
        failed(str(error))
        return 1
    measured(", ".join(f"{name}: {len(regions)} speech regions" for name, regions in found.items()))
    if any(found.values()):
        failed("speech was detected where there is none")
        return 1
    return 0


def _pytest_check(label: str, *nodes: str) -> int:
    code, summary = _pytest(*nodes)
    measured(f"{label}: {summary}")
    if code != 0:
        failed(f"{label} failed")
        return 1
    return 0


def check_empty_library() -> int:
    """7. Empty music library -> a valid silent-mode edit through the real engine, exit 0."""
    return _pytest_check(
        "empty library", "tests/test_edit_api.py::test_an_empty_library_still_makes_an_edit"
    )


def check_overrides_logged() -> int:
    """8. Every override re-syncs and writes one taste event; the decisions survive a reload."""
    return _pytest_check(
        "overrides",
        "tests/test_edit_api.py::test_an_edit_cuts_on_the_beat_and_every_override_resyncs_and_is_logged",
        "tests/test_edit_api.py::test_bad_overrides_are_named_errors",
        "tests/test_planner.py",
    )


def check_music_security() -> int:
    """9. Tracks decode only through the validated source; no path from a track's name."""
    code, summary = _pytest(
        "tests/test_edit_api.py::test_the_library_is_scanned_safely_and_analysed_once",
        "tests/test_ducking.py::test_a_track_that_names_a_protocol_is_never_decoded",
        "tests/test_ducking.py::test_a_gain_expression_outside_the_allow_list_is_refused",
    )
    # Static half: nothing in music/ or edit/ opens audio except through the
    # builder, and no path is joined from a track's display name.
    forbidden = (
        r"librosa\.load\(",
        r"soundfile\.",
        r"audioread\.",
        r"Path\([^)]*display_name",
        r"/\s*[\w.]*display_name",
    )
    offenders = []
    sources = [
        *(ENGINE / "repcut" / "music").glob("*.py"),
        *(ENGINE / "repcut" / "edit").glob("*.py"),
    ]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        offenders += [f"{path.name}: {p}" for p in forbidden if re.search(p, text)]
    measured(f"{summary}; static offenders {len(offenders)}")
    if code != 0 or offenders:
        failed(f"tests exit {code}; offenders {offenders}")
        return 1
    return 0


def check_migration() -> int:
    """10. Migration 0004 round-trips and matches the models."""
    return _pytest_check("migrations", "tests/test_migrations.py", "tests/test_db_models.py")


_EDIT_IN_THE_BROWSER = """
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = async (fn, ms) => {
    const end = Date.now() + ms;
    while (Date.now() < end) { const v = fn(); if (v) return v; await sleep(200); }
    return null;
  };
  const layout = () => [...document.querySelectorAll("[data-shot-id]")].map((el) => ({
    // One shot's id, lock state and place on the timeline.
    id: el.getAttribute("data-shot-id"),
    locked: el.getAttribute("data-locked") === "true",
    left: el.style.left, width: el.style.width,
  }));
  const shots = () => document.querySelectorAll("[data-shot-id]").length;
  const ready = await until(() => shots() >= 3, 60000);
  if (!ready) return {error: "no cut list rendered"};
  const suggestedBefore = document.body.innerText.includes("AI suggested");
  const lockSelector = 'button[aria-label="Lock cut 2"]';
  const lockButton = await until(() => document.querySelector(lockSelector), 5000);
  if (!lockButton) return {error: "no lock control"};
  lockButton.click();
  const lockedNow = await until(() => layout().find((s) => s.locked), 15000);
  if (!lockedNow) return {error: "locking did not show"};
  const before = layout();
  const other = [...document.querySelectorAll('button[role="radio"][data-track]')].find(
    (r) => r.getAttribute("aria-checked") !== "true");
  if (!other) return {error: "no other track to pick"};
  other.click();
  const moved = await until(() => {
    const now = layout();
    const free = (list) => JSON.stringify(list.filter((s) => !s.locked));
    return free(now) !== free(before) ? now : null;
  }, 20000);
  if (!moved) return {error: "the unlocked cuts did not re-sync"};
  const keptLock = moved.find((s) => s.id === lockedNow.id);
  const removeButton = [...document.querySelectorAll('button[aria-label^="Remove cut"]')].find(
    (b) => !b.closest('[data-locked="true"]'));
  const beforeRemove = layout();
  removeButton.click();
  const afterRemove = await until(() => {
    const now = layout();
    return JSON.stringify(now) !== JSON.stringify(beforeRemove) ? now : null;
  }, 20000);
  return {
    suggestedBefore,
    yourChoice: document.body.innerText.includes("Your choice"),
    unlockedMoved: true,
    lockKept: !!keptLock && keptLock.left === lockedNow.left && keptLock.width === lockedNow.width,
    removed: !!afterRemove,
    cuts: (afterRemove || moved).length,
  };
})()
"""


def check_someone_can_edit() -> int:
    """11. In a real browser on `make dev`: lock a cut, swap the track, remove a cut."""
    import dev_stack
    import verify_02_checks as v2
    from cdp_browser import BrowserNotFoundError, inspect_page

    with (
        dev_stack.DevStack(gemini_enabled=False) as stack,
        TemporaryDirectory(prefix="repcut-gate05-", ignore_cleanup_errors=True) as scratch,
    ):
        music = stack.data_dir / "music"
        music.mkdir(parents=True, exist_ok=True)
        for name, bpm in (("gate fast.wav", 138.0), ("gate slow.wav", 98.0)):
            track = generate_track(bpm=bpm, bars=48, intro_bars=4, build_bars=4, drop_bars=24)
            (music / name).write_bytes(to_wav_bytes(track.samples, track.sample_rate))
        stack.start()
        if not stack.wait_ready():
            measured("stack did not start")
            failed("`make dev` never reached both ports; see the launcher output")
            return 1
        status, project = stack.engine_request("POST", "/projects", {"name": "gate-05 edit"})
        if status != 201 or not isinstance(project, dict):
            measured(f"POST /projects -> HTTP {status}")
            failed("could not create a project against the running stack")
            return 1
        project_id = str(project["id"])
        for index in range(3):
            clip = v2.make_clip(Path(scratch) / f"edit{index}.mp4", seconds=12.0 + index)
            if not v2.upload(stack.engine_port, project_id, clip).get("sha256"):
                measured("upload failed")
                failed("a fixture did not upload")
                return 1
        stack.engine_request("POST", "/music/scan")
        deadline = time.monotonic() + v2.INGEST_TIMEOUT_S
        edit: object = None
        while time.monotonic() < deadline:
            stack.engine_request("POST", f"/projects/{project_id}/edit/signals")
            _, edit = stack.engine_request("GET", f"/projects/{project_id}/edit")
            if isinstance(edit, dict) and not edit.get("clips_pending") and edit.get("snapped"):
                break
            time.sleep(2.0)
        if not (isinstance(edit, dict) and edit.get("snapped")):
            measured("edit never snapped to a track")
            failed("the edit did not become ready (signals or music scan)")
            return 1
        try:
            report = asyncio.run(
                inspect_page(
                    f"http://localhost:{stack.ui_port}/projects/{project_id}",
                    observe_seconds=6.0,
                    evaluate=_EDIT_IN_THE_BROWSER,
                    evaluate_budget_s=120.0,
                )
            )
        except BrowserNotFoundError as error:
            measured("no browser")
            failed(f"cannot use the edit screen without a browser: {error}")
            return 1
    result = report.evaluated if isinstance(report.evaluated, dict) else {"error": "no result"}
    measured(json.dumps(result))
    if "error" in result:
        failed(str(result["error"]))
        return 1
    ok = all(
        result.get(k)
        for k in ("suggestedBefore", "yourChoice", "unlockedMoved", "lockKept", "removed")
    )
    if not ok:
        failed("a control did not do what it says")
        return 1
    return 0


CHECKS: dict[str, Callable[[], int]] = {
    "planner-on-beat": check_planner_on_beat,
    "track-change-resync": check_track_change_resync,
    "drop-alignment": check_drop_alignment,
    "resync-budget": check_resync_budget,
    "ducking": check_ducking,
    "speech-model": check_speech_model,
    "empty-library": check_empty_library,
    "overrides-logged": check_overrides_logged,
    "music-security": check_music_security,
    "migration": check_migration,
    "someone-can-edit": check_someone_can_edit,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in CHECKS:
        print(f"usage: {Path(__file__).name} <{'|'.join(CHECKS)}>", file=sys.stderr)
        # 1, not 2: the gate reads 2 as SKIP, and an unknown name is a broken gate.
        return 1
    if shutil.which("ffmpeg") is None:
        measured("no ffmpeg")
        failed("ffmpeg is not on PATH")
        return 1
    started = time.monotonic()
    code = CHECKS[sys.argv[1]]()
    print(f"ELAPSED: {time.monotonic() - started:.1f}s")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
