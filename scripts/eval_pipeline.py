#!/usr/bin/env python3
"""Repcut's product claims, measured on real footage. ``make eval``.

Each claim the UI makes is one gate here, and each gate exits the run 1 when
its metric misses, naming the metric. Gates measure the real-footage fixtures
(``scripts/eval_fixtures.py``); a gate whose fixture, label or model is not on
this machine reports BOUND - never PASS - and the run exits 3 (``--strict``:
1). Synthetic media never stands in for a claim. ``--harness-check`` is the one
exception and says so: it proves the beat gate itself fails on a deliberately
broken render, using generated clips, and measures no claim.

    python scripts/eval_pipeline.py [--gates a,b] [--strict] [--json FILE]
    python scripts/eval_pipeline.py --harness-check

Gemini is never called (no key reaches any gate). Results go to
``$DATA_DIR/eval/eval-results.json``; ``scripts/eval_report.py`` turns them into
``docs/reports/eval-latest.md``. No path or file name is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_fixtures as fx  # noqa: E402
from eval_metrics import (  # noqa: E402
    BOUND,
    FAIL,
    PASS,
    GateResult,
    audio_lag,
    beat_errors,
    exit_code,
    frame_differences,
    hue_drift_degrees,
    locate_cuts,
    middle_third_fraction,
    onset_envelope,
    revert_rate,
    to_json,
    within_one,
    word_error_rate,
)

# The bounds. Each is the guide's number for its claim (amendment 025 lists them).
WAVE1_SECONDS_MAX = 15 * 60
BEAT_TOLERANCE_S = 0.040
WER_MAX = 0.15
REVERT_RATE_MIN = 1.0
REP_TOLERANCE = 1
SKIN_HUE_TOLERANCE_DEGREES = 4.0  # the same bound as verify-04 criterion 9a
MIDDLE_THIRD_MIN = 0.95  # the same bound as verify-10 criterion 2
HARNESS_DELAY_MS = 100  # the deliberate break: music 100 ms late against the cuts
AUDIO_RATE = 22050
HOP = 256

E2E = Path(__file__).resolve().parent / "e2e_export.py"


def _no_key() -> None:
    """No gate may reach Gemini: blank the key for this process and its children."""
    os.environ["GEMINI_API_KEY"] = ""


def _data_dir() -> Path:
    from repcut.config import get_settings

    return get_settings().data_dir


def _bound(gate: str, claim: str, metric: str, bound: str, why: str) -> GateResult:
    return GateResult(gate, claim, BOUND, metric, "not measured", bound, why)


# --------------------------------------------------------------- decoding
def _grey_frames(video: Path, size: int = 96) -> tuple[np.ndarray, float]:
    rate = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate",
            "-of",
            "csv=p=0",
            str(video),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout.strip()
    num, _, den = rate.partition("/")
    fps = float(num) / float(den or 1)
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(video),
            "-vf",
            f"scale={size}:{size},format=gray",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        timeout=600,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, size, size), fps


def _mono(path: Path, rate: int = AUDIO_RATE) -> np.ndarray:
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            # Pad from t=0, so sound that starts late is heard late: a raw
            # decode otherwise begins at the first sample, wherever it sits.
            "-af",
            "aresample=async=1:first_pts=0",
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        timeout=600,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def delay_audio(video: Path, out: Path, milliseconds: int) -> Path:
    """The deliberate break: the same render with its sound ``milliseconds`` late."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(video),
            "-itsoffset",
            f"{milliseconds / 1000:.3f}",
            "-i",
            str(video),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            str(out),
        ],
        check=True,
        timeout=300,
    )
    return out


# --------------------------------------------------------------- beat sync
def measure_beats(video: Path, music: Path, summary: dict[str, object]) -> GateResult:
    """Every visible cut in the RENDER within 40 ms of a beat of the music the
    render actually plays: cuts read from the frames, beats from where the
    track lands in the rendered sound (cross-correlated), not from the plan."""
    edit = summary["edit"]
    track = summary["track"]
    if not isinstance(edit, dict) or not isinstance(track, dict):
        raise TypeError("the e2e summary carries no edit or track")
    shots = sorted(edit["shots"], key=lambda s: s["timeline_in"])
    cuts: list[float] = []
    for previous, shot in itertools.pairwise(shots):
        continuous = (
            previous["sha256"] == shot["sha256"]
            and abs(previous["source_out"] - shot["source_in"]) < 0.02
        )
        if not continuous:  # a split inside one continuous take shows no cut
            cuts.append(float(shot["timeline_in"]))
    claim = "100% of cuts within 40 ms of the beat, in the rendered file"
    if edit.get("music") == "off" or not cuts:
        return GateResult(
            "beat-accuracy",
            claim,
            FAIL,
            "cuts on the beat",
            "no music or no cuts",
            "100%",
            "the render has nothing to measure",
        )
    frames, fps = _grey_frames(video)
    located = locate_cuts(frame_differences(frames), fps, cuts)
    rendered = onset_envelope(_mono(video), AUDIO_RATE, HOP)
    start = float(edit.get("music_start_seconds") or 0.0)
    reference_audio = _mono(music)[int(start * AUDIO_RATE) :]
    reference = onset_envelope(reference_audio, AUDIO_RATE, HOP)
    period = 60.0 / float(track["bpm"])
    # A beat grid repeats every period, so an offset is only knowable modulo
    # it: search under half a period, where the answer is unique.
    lag = audio_lag(rendered, reference, AUDIO_RATE / HOP, max_lag=0.45 * period)
    first = float(track["first_beat_seconds"]) - start
    duration = len(frames) / fps
    k0 = int(np.floor(-first / period))
    beats = [first + k * period + lag for k in range(k0, k0 + int(duration / period) + 3)]
    visible = [c for c in located if c is not None]
    errors = beat_errors(visible, beats)
    hits = sum(1 for e in errors if e <= BEAT_TOLERANCE_S)
    worst = max(errors) if errors else float("inf")
    missing = len(located) - len(visible)
    value = (
        f"{hits}/{len(located)} within 40 ms "
        f"(worst {worst * 1000:.0f} ms, music lag {lag * 1000:+.0f} ms)"
    )
    status = PASS if hits == len(located) and missing == 0 else FAIL
    detail = f"{missing} planned cut(s) not visible in the frames" if missing else ""
    return GateResult("beat-accuracy", claim, status, "cuts on the beat", value, "100%", detail)


def _run_e2e(
    clips: Sequence[Path] | None, out: Path, captions: bool
) -> tuple[int, float, dict[str, object]]:
    argv = [sys.executable, str(E2E), "--out", str(out), "--seconds", "20"]
    if clips:
        argv += ["--clips", *map(str, clips)]
    if not captions:
        argv.append("--no-captions")
    began = time.perf_counter()
    done = subprocess.run(
        argv, cwd=REPO_ROOT, capture_output=True, text=True, timeout=3600, check=False
    )
    seconds = time.perf_counter() - began
    summary_file = out / "e2e.json"
    summary = json.loads(summary_file.read_text(encoding="utf-8")) if summary_file.is_file() else {}
    if done.returncode != 0:
        reason = next((ln[8:] for ln in done.stdout.splitlines() if ln.startswith("FAILED: ")), "")
        summary["failure"] = (
            reason or (done.stderr.strip().splitlines() or ["e2e failed"])[-1][:200]
        )
    return done.returncode, seconds, summary


def gate_wave1_and_beats(scratch: Path) -> list[GateResult]:
    """Wave-1 time (3 real clips in, a captioned 9:16 export out, < 15 min) and,
    from that same render, the beat check."""
    from repcut.captions import transcribe as stt

    claim_t = "3 real clips to an exported reel in under 15 minutes"
    claim_b = "100% of cuts within 40 ms of the beat, in the rendered file"
    session = [p for _, p in fx.by_role("session")]
    why = fx.missing_roles().get("session")
    if why or len(session) < 3:
        return [
            _bound(
                "wave1-time", claim_t, "session to export", "< 900 s", f"session fixtures: {why}"
            ),
            _bound(
                "beat-accuracy", claim_b, "cuts on the beat", "100%", f"session fixtures: {why}"
            ),
        ]
    if not stt.model_installed(_data_dir()):
        why = "the speech-to-text model is not installed (python scripts/fetch_models.py)"
        return [
            _bound("wave1-time", claim_t, "session to export", "< 900 s", why),
            _bound("beat-accuracy", claim_b, "cuts on the beat", "100%", why),
        ]
    out = scratch / "wave1"
    code, seconds, summary = _run_e2e(session[:3], out, captions=True)
    if code != 0:
        reason = str(summary.get("failure", "the export did not finish"))
        return [
            GateResult(
                "wave1-time",
                claim_t,
                FAIL,
                "session to export",
                f"{seconds:.0f} s",
                "< 900 s",
                reason,
            ),
            GateResult(
                "beat-accuracy", claim_b, FAIL, "cuts on the beat", "no render", "100%", reason
            ),
        ]
    status = PASS if seconds < WAVE1_SECONDS_MAX else FAIL
    stages = summary.get("stage_seconds", {})
    timing = GateResult(
        "wave1-time",
        claim_t,
        status,
        "session to export",
        f"{seconds:.0f} s",
        "< 900 s",
        f"stages {json.dumps(stages)}; {platform.machine()}",
    )
    return [timing, measure_beats(out / "e2e.mp4", out / "music.wav", summary)]


def harness_check() -> int:
    """The beat gate must PASS a clean render and FAIL the same render with its
    music 100 ms late. Generated clips: this proves the gate, not the claim."""
    _no_key()
    with tempfile.TemporaryDirectory(prefix="repcut-eval-harness-") as scratch:
        out = Path(scratch) / "harness"
        code, seconds, summary = _run_e2e(None, out, captions=False)
        if code != 0:
            print(f"MEASURED: generated render failed after {seconds:.0f} s")
            print(f"FAILED: {summary.get('failure', 'e2e failed')}")
            return 1
        clean = measure_beats(out / "e2e.mp4", out / "music.wav", summary)
        broken_file = delay_audio(out / "e2e.mp4", Path(scratch) / "late.mp4", HARNESS_DELAY_MS)
        broken = measure_beats(broken_file, out / "music.wav", summary)
    print(
        f"MEASURED: clean render {clean.status} ({clean.value}); music +{HARNESS_DELAY_MS} ms "
        f"{broken.status} ({broken.value})"
    )
    if clean.status != PASS:
        print(f"FAILED: the beat gate fails a correct render: {clean.detail or clean.value}")
        return 1
    if broken.status != FAIL:
        print("FAILED: the beat gate passed a render whose music is 100 ms late")
        return 1
    return 0


# --------------------------------------------------------------- captions
def gate_caption_wer(_scratch: Path) -> list[GateResult]:
    from repcut.captions import transcribe as stt

    claim = "captions under 15% word error rate on labelled real speech"
    why = fx.missing_roles().get("known_audio")
    if why:
        return [
            _bound("caption-wer", claim, "word error rate", "< 15%", f"known-audio fixture: {why}")
        ]
    data_dir = _data_dir()
    if not stt.model_installed(data_dir):
        return [_bound("caption-wer", claim, "word error rate", "< 15%", stt.MODEL_MISSING)]
    from repcut.captions.transcribe import SAMPLE_RATE, transcribe_samples
    from repcut.edit.signals import speech_regions
    from repcut.edit.vad import MODEL_STORED_PATH
    from repcut.media.store import absolute

    recogniser = stt.load_model(data_dir).recogniser
    rates: list[str] = []
    worst = 0.0
    for fixture, path in fx.by_role("known_audio"):
        samples = _mono(path, SAMPLE_RATE)
        regions = speech_regions(samples, absolute(data_dir, MODEL_STORED_PATH))
        transcript = transcribe_samples(recogniser, samples, sha256=fixture.sha256, speech=regions)  # type: ignore[arg-type]
        heard = " ".join(w.text for s in transcript.segments for w in s.words)
        rate = word_error_rate(str(fixture.label["transcript"]), heard)
        worst = max(worst, rate)
        rates.append(f"{fixture.label.get('language', '?')} {rate:.1%}")
    status = PASS if worst < WER_MAX else FAIL
    return [
        GateResult(
            "caption-wer",
            claim,
            status,
            "word error rate",
            f"worst {worst:.1%}",
            "< 15%",
            ", ".join(rates),
        )
    ]


# --------------------------------------------------------------- slow motion
async def _proxy(path: Path, out: Path) -> Path:
    """The engine's own SDR proxy of a fixture: frames measured as Repcut sees them."""
    from repcut.media.ffmpeg_builder import build_probe, build_proxy, render, run
    from repcut.media.metadata import parse_color_properties, parse_probe

    document = json.loads(await run(build_probe(path)))
    props, colour = parse_probe(document), parse_color_properties(document)
    await render(
        build_proxy(
            path,
            out,
            display_width=props.display_width,
            display_height=props.display_height,
            color_primaries=colour.color_primaries,
            color_transfer=colour.color_transfer,
            color_space=colour.color_space,
            color_range=colour.color_range,
            duration_seconds=props.duration_seconds,
        ),
        dry_run_first=False,
    )
    return out


def gate_slowmo_revert(scratch: Path) -> list[GateResult]:
    claim = "slow motion falls back to normal speed on every artifact-prone span"
    why = fx.missing_roles().get("barbell_artifact")
    if why:
        return [_bound("slowmo-revert", claim, "revert rate", "100%", f"barbell fixtures: {why}")]
    import verify_07_checks as v7
    from slowmo_fixture import FPS as SLOW_FPS

    missing = v7._model_ready()
    if missing:
        return [_bound("slowmo-revert", claim, "revert rate", "100%", missing)]
    import dataclasses

    from repcut.export.reframe import crop_for
    from repcut.export.render import ExportInputs, ShotSource, render_export
    from repcut.media.ffmpeg_builder import CropBox, ExportEncode, GradeSpec
    from repcut.slowmo.runtime import InterpolationSetup

    accepted: list[bool] = []
    notes: list[str] = []
    for fixture, path in fx.by_role("barbell_artifact"):
        proxy = asyncio.run(_proxy(path, scratch / f"slow-{fixture.index}.mp4"))
        frames, fps = _grey_frames(proxy, 16)
        length = len(frames) / fps
        width, height = (
            int(v)
            for v in subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "csv=p=0",
                    str(proxy),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
            .stdout.strip()
            .split(",")
        )
        crop = crop_for(width, height, 9 / 16)
        shot = ShotSource(
            sha256=fixture.sha256,
            source=proxy,
            source_in=length / 4,
            timeline_in=0.0,
            timeline_out=length,
            width=width,
            height=height,
            has_audio=False,
            crop=CropBox(crop.width, crop.height, crop.x, crop.y),
            grade=GradeSpec(0.0, (1.0, 1.0, 1.0), 0.0, None, 0, 0.0),
            lut_digest=None,
            color_primaries="bt709",
            color_transfer="bt709",
            color_space="bt709",
            color_range="tv",
        )
        inputs = ExportInputs(
            shots=(dataclasses.replace(shot, speed=0.5, fallback_in=0.0),),
            duration_seconds=length,
            encode=ExportEncode(v7.REEL[0], v7.REEL[1], SLOW_FPS, "veryfast", 18, "high", "4.2", 2),
            audio_bitrate="192k",
            music=None,
            captions=(),
            style=None,
            interpolation=InterpolationSetup(weights=v7._weights()),
        )
        result = asyncio.run(
            render_export(
                inputs, scratch / f"work-{fixture.index}", scratch / f"slow-{fixture.index}-out.mp4"
            )
        )
        decision = result.slowmo[0] if result.slowmo else {}
        accepted.append(decision.get("accepted") is not False)
        notes.append(f"{fixture.sha256[:8]}: {decision.get('played')} ({decision.get('reason')})")
    rate = revert_rate(accepted)
    status = PASS if rate >= REVERT_RATE_MIN else FAIL
    return [
        GateResult(
            "slowmo-revert",
            claim,
            status,
            "revert rate",
            f"{rate:.0%} of {len(accepted)}",
            "100%",
            "; ".join(notes),
        )
    ]


# --------------------------------------------------------------- reps
def gate_rep_count(scratch: Path) -> list[GateResult]:
    claim = "rep count within one of the human count"
    why = fx.missing_roles().get("reps")
    if why:
        return [_bound("rep-count", claim, "reps within one", "3 of 3", f"rep fixtures: {why}")]
    from repcut.gym.reps import PoseEstimator, PoseUnavailableError, count_reps, segment_keypoints
    from repcut.gym.weights import POSE
    from repcut.media.store import absolute

    try:
        estimator = PoseEstimator(absolute(_data_dir(), POSE.stored_path), "auto")
    except PoseUnavailableError as error:
        return [_bound("rep-count", claim, "reps within one", "3 of 3", str(error))]
    counted: list[int | None] = []
    truth: list[int] = []
    with estimator:
        for fixture, path in fx.by_role("reps"):
            proxy = asyncio.run(_proxy(path, scratch / f"reps-{fixture.index}.mp4"))
            length = len(_grey_frames(proxy, 16)[0]) / 30.0
            result = count_reps(segment_keypoints(proxy, 0.0, length, estimator))
            counted.append(result.reps)
            truth.append(int(fixture.label["reps"]))  # type: ignore[call-overload]
    ok = within_one(counted, truth)
    status = PASS if all(ok) else FAIL
    pairs = ", ".join(f"{c} vs {t}" for c, t in zip(counted, truth, strict=True))
    return [
        GateResult(
            "rep-count",
            claim,
            status,
            "reps within one",
            f"{sum(ok)} of {len(ok)}",
            f"{len(ok)} of {len(ok)}",
            pairs,
        )
    ]


# --------------------------------------------------------------- skin
def gate_skin_tone(scratch: Path) -> list[GateResult]:
    claim = "every theme keeps skin hue within 4 degrees on real footage"
    why = fx.missing_roles().get("skin")
    if why:
        return [
            _bound("skin-tone", claim, "worst skin hue drift", "<= 4.0 deg", f"skin labels: {why}")
        ]
    from repcut.grading.packs import PackLibrary, ensure_lut
    from repcut.media import ffmpeg_builder as builder

    def still(proxy: Path, out: Path, lut: Path | None, intensity: int, at: float) -> np.ndarray:
        grade = builder.GradeSpec(0.0, (1.0, 1.0, 1.0), 0.0, lut, intensity, 0.0)
        asyncio.run(
            builder.render(
                builder.build_graded_still(proxy, out, timestamp_seconds=at, grade=grade),
                dry_run_first=False,
            )
        )
        import cv2

        image = cv2.imread(str(out))
        if image is None:
            raise RuntimeError("the graded still could not be read")
        return np.asarray(image[:, :, ::-1], dtype=np.float64)

    pack = PackLibrary().load("gym")
    worst, notes = 0.0, []
    for fixture, path in fx.by_role("skin"):
        proxy = asyncio.run(_proxy(path, scratch / f"skin-{fixture.index}.mp4"))
        at = float(fixture.label["time"]) - fixture.start  # type: ignore[arg-type]
        x, y, w, h = (float(v) for v in fixture.label["box"])  # type: ignore[union-attr]
        before = still(proxy, scratch / "b.png", None, 0, at)
        rows = slice(int(y * before.shape[0]), int((y + h) * before.shape[0]))
        cols = slice(int(x * before.shape[1]), int((x + w) * before.shape[1]))
        base = before[rows, cols].reshape(-1, 3).mean(axis=0)
        for theme in pack.themes:  # type: ignore[attr-defined]
            lut = ensure_lut(scratch / "luts", "gym", theme)
            after = still(proxy, scratch / "a.png", lut, 100, at)
            drift = hue_drift_degrees(base, after[rows, cols].reshape(-1, 3).mean(axis=0))
            if drift > worst:
                worst, notes = drift, [f"{fixture.sha256[:8]} under {theme.id}"]
    status = PASS if worst <= SKIN_HUE_TOLERANCE_DEGREES else FAIL
    return [
        GateResult(
            "skin-tone",
            claim,
            status,
            "worst skin hue drift",
            f"{worst:.2f} deg",
            "<= 4.0 deg",
            "; ".join(notes),
        )
    ]


# --------------------------------------------------------------- reframe
def gate_reframe(scratch: Path) -> list[GateResult]:
    claim = "a walking subject stays in the middle third of a 9:16 crop"
    why = fx.missing_roles().get("walking_subject")
    if why:
        return [
            _bound(
                "reframe", claim, "frames in the middle third", ">= 95%", f"walking fixture: {why}"
            )
        ]
    from repcut.export.reframe import crop_path
    from repcut.export.subjects import DetectorUnavailableError, open_person_detector, track
    from repcut.gym.weights import DETECT
    from repcut.media.store import absolute

    try:
        detector = open_person_detector(absolute(_data_dir(), DETECT.stored_path))
    except DetectorUnavailableError as error:
        return [_bound("reframe", claim, "frames in the middle third", ">= 95%", str(error))]
    worst = 1.0
    for fixture, path in fx.by_role("walking_subject"):
        proxy = asyncio.run(_proxy(path, scratch / f"walk-{fixture.index}.mp4"))
        frames, fps = _grey_frames(proxy, 16)
        found = track(proxy, detector)
        width, height = (
            int(v)
            for v in subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "csv=p=0",
                    str(proxy),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
            .stdout.strip()
            .split(",")
        )
        samples = [(s.t, s.cx) for s in found.samples]
        path_ = crop_path(
            samples,
            start=0.0,
            frames=len(frames),
            fps=fps,
            width=width,
            height=height,
            aspect=9 / 16,
        )
        subject = [s.cx for s in found.samples]
        crop_x = [path_.x_at(round(s.t * fps)) for s in found.samples]
        worst = min(worst, middle_third_fraction(subject, crop_x, path_.width))
    status = PASS if worst >= MIDDLE_THIRD_MIN else FAIL
    return [
        GateResult("reframe", claim, status, "frames in the middle third", f"{worst:.1%}", ">= 95%")
    ]


GATES: dict[str, Callable[[Path], list[GateResult]]] = {
    "wave1": gate_wave1_and_beats,
    "caption-wer": gate_caption_wer,
    "slowmo-revert": gate_slowmo_revert,
    "rep-count": gate_rep_count,
    "skin-tone": gate_skin_tone,
    "reframe": gate_reframe,
}


def run_gates(names: Sequence[str]) -> list[GateResult]:
    _no_key()
    results: list[GateResult] = []
    with tempfile.TemporaryDirectory(prefix="repcut-eval-") as scratch:
        for name in names:
            folder = Path(scratch) / name
            folder.mkdir()
            try:
                results += GATES[name](folder)
            except Exception as error:
                results.append(
                    GateResult(
                        name,
                        name,
                        FAIL,
                        "gate ran",
                        "crashed",
                        "ran",
                        type(error).__name__ + ": " + str(error)[:160],
                    )
                )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gates", default=",".join(GATES))
    parser.add_argument("--strict", action="store_true", help="a BOUND gate fails the run")
    parser.add_argument(
        "--json", type=Path, help="results file (default $DATA_DIR/eval/eval-results.json)"
    )
    parser.add_argument("--harness-check", action="store_true")
    args = parser.parse_args(argv)
    if args.harness_check:
        return harness_check()
    names = [n for n in args.gates.split(",") if n]
    unknown = [n for n in names if n not in GATES]
    if unknown:
        print(f"FAILED: unknown gate(s): {', '.join(unknown)}")
        return 1
    results = run_gates(names)
    for result in results:
        print(result.line())
    code = exit_code(results, strict=args.strict or os.environ.get("REPCUT_GATE_STRICT") == "1")
    meta = {
        "when": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "machine": f"{platform.system()} {platform.machine()}",
        "commit": subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip(),
        "exit": str(code),
    }
    target = args.json or (_data_dir() / "eval" / "eval-results.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_json(results, meta), encoding="utf-8")
    counts = {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, BOUND)}
    print(f"MEASURED: {counts[PASS]} pass, {counts[FAIL]} fail, {counts[BOUND]} bound")
    failing = [f"{r.gate} ({r.metric} = {r.value})" for r in results if r.status == FAIL]
    if failing:
        print("FAILED: " + "; ".join(failing))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
