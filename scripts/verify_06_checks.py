#!/usr/bin/env python3
"""Measurements behind ``scripts/verify_06.sh``: one check per criterion.

Every check prints ``MEASURED: <value>`` and exits 0 (pass) or 1 (fail), with
``FAILED: <reason>`` on failure - the contract verify_03/04/05 established.
Clips and music are generated; the known speech is synthesised from sentences
we wrote (``speech_fixture.py``). The speech-to-text model and the gate voices
come from ``fetch_models.py --gate``: without them the caption criteria FAIL
with that instruction (a missing model is not one of amendment 014's skip
conditions). No absolute path is printed.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE = REPO_ROOT / "engine"
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repcut.captions import ass  # noqa: E402
from repcut.captions.styles import style  # noqa: E402
from repcut.captions.types import CaptionLine, TimedWord  # noqa: E402
from repcut.config import get_settings  # noqa: E402
from repcut.export.check import Expected, platform_problems  # noqa: E402
from repcut.export.reframe import crop_for  # noqa: E402
from repcut.export.render import ExportInputs, ShotSource, render_export  # noqa: E402
from repcut.media.ffmpeg_builder import CropBox, ExportEncode, GradeSpec  # noqa: E402
from verify_03_checks import failed, measured  # noqa: E402 - after the sys.path insert
from verify_05_checks import _pytest_check  # noqa: E402

WER_MAX = 0.15
SPEED_BUDGET_S = 300.0
SCRATCH = Path(tempfile.gettempdir()) / "repcut-gate06"


# --- 1, 2: the end-to-end run, shared --------------------------------------------


def _e2e(fresh: bool) -> tuple[int, str, Path]:
    out = SCRATCH / "e2e"
    if fresh or not (out / "e2e.mp4").is_file():
        shutil.rmtree(out, ignore_errors=True)
        completed = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "e2e_export.py"), "--out", str(out)],
            capture_output=True, text=True, timeout=1800, check=False,
        )  # fmt: skip
        lines = completed.stdout.splitlines()
        tail = next((ln for ln in reversed(lines) if ln.startswith("FAILED: ")), "")
        detail = next((ln[10:] for ln in lines if ln.startswith("MEASURED: ")), "")
        return completed.returncode, tail[8:] or detail, out
    return 0, "reused this gate run's output", out


def check_e2e() -> int:
    """1. Three clips in, a valid 9:16 MP4 out, zero manual steps, exit 0."""
    code, detail, out = _e2e(fresh=True)
    summary = out / "e2e.json"
    if summary.is_file():
        data = json.loads(summary.read_text(encoding="utf-8"))
        measured(
            f"{data['clips']} clips -> {data['edit_seconds']} s, {data['shots']} shots, "
            f"{data['caption_lines']} caption lines {data['languages']}; "
            f"stage seconds {data['stage_seconds']}"
        )
    else:
        measured(detail or "no summary written")
    if code != 0:
        failed(detail or "the end-to-end run failed")
        return 1
    return 0


def check_platform_safe() -> int:
    """2. ffprobe on the export: yuv420p, faststart, AAC 48 kHz, 1080x1920, duration +-5 %."""
    code, detail, out = _e2e(fresh=False)
    video = out / "e2e.mp4"
    if code != 0 or not video.is_file():
        measured("no exported file")
        failed(detail or "the end-to-end run produced no file")
        return 1
    target = json.loads((out / "e2e.json").read_text(encoding="utf-8"))["edit_seconds"]
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,pix_fmt,color_space,color_range,"
         "color_primaries,color_transfer,sample_rate:format=duration", "-of", "json", str(video)],
        capture_output=True, text=True, timeout=60, check=True,
    )  # fmt: skip
    problems = platform_problems(probe.stdout, video, Expected(1080, 1920, float(target)))
    data = json.loads(probe.stdout)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), {})
    measured(
        f"{v['codec_name']} {v['pix_fmt']} {v['width']}x{v['height']} {v.get('color_space')}/"
        f"{v.get('color_range')}, {a.get('codec_name')} {a.get('sample_rate')} Hz, "
        f"{float(data['format']['duration']):.2f} s of {target} s"
    )
    if problems:
        failed("; ".join(problems))
        return 1
    return 0


# --- 3, 4: captions with the real model -------------------------------------------


def _model() -> object:
    from repcut.captions import transcribe as stt

    data_dir = get_settings().data_dir
    if not stt.model_installed(data_dir):
        raise RuntimeError(stt.MODEL_MISSING)
    return stt.load_model(data_dir).recogniser


def _speech_regions(samples: np.ndarray) -> object:
    from repcut.edit.signals import speech_regions
    from repcut.edit.vad import MODEL_STORED_PATH
    from repcut.media.store import absolute

    return speech_regions(samples, absolute(get_settings().data_dir, MODEL_STORED_PATH))


def check_caption_accuracy() -> int:
    """3. Known English + German speech: word error rate under 15 %."""
    from repcut.captions.transcribe import transcribe_samples
    from speech_fixture import (
        SAMPLE_RATE,
        SENTENCES,
        FixtureUnavailableError,
        synthesize,
        word_error_rate,
    )

    data_dir = get_settings().data_dir
    try:
        recogniser = _model()
        parts = {lang: synthesize(data_dir, lang) for lang in ("en", "de")}
    except (RuntimeError, FixtureUnavailableError) as error:
        measured("not measurable here")
        failed(str(error))
        return 1
    gap = np.zeros(int(0.8 * SAMPLE_RATE), dtype=np.float32)
    rates: dict[str, float] = {}
    for lang, samples in parts.items():
        clip = np.concatenate([gap, samples, gap])
        transcript = transcribe_samples(
            recogniser, clip, sha256="0" * 64, speech=_speech_regions(clip)
        )  # type: ignore[arg-type]
        heard = " ".join(w.text for s in transcript.segments for w in s.words)
        rates[lang] = word_error_rate(SENTENCES[lang], heard)
    mixed = np.concatenate([gap, parts["en"], gap, parts["de"], gap])
    transcript = transcribe_samples(
        recogniser, mixed, sha256="0" * 64, speech=_speech_regions(mixed)
    )  # type: ignore[arg-type]
    heard = " ".join(w.text for s in transcript.segments for w in s.words)
    rates["mixed"] = word_error_rate(f"{SENTENCES['en']} {SENTENCES['de']}", heard)
    measured(", ".join(f"{k} WER {v:.1%}" for k, v in rates.items()) + f" (bound {WER_MAX:.0%})")
    if max(rates.values()) >= WER_MAX:
        failed("word error rate at or above the bound")
        return 1
    return 0


def check_music_only() -> int:
    """4. A music-only section produces zero captions."""
    import librosa
    from repcut.captions.transcribe import transcribe_samples
    from repcut.music.synthetic import generate_track

    try:
        recogniser = _model()
    except RuntimeError as error:
        measured("not measurable here")
        failed(str(error))
        return 1
    counts = {}
    for bpm in (96.0, 128.0, 150.0):
        track = generate_track(bpm=bpm, bars=24, intro_bars=4, build_bars=4, drop_bars=12)
        samples = librosa.resample(track.samples, orig_sr=track.sample_rate, target_sr=16000)
        samples = samples.astype(np.float32)
        transcript = transcribe_samples(
            recogniser, samples, sha256="0" * 64, speech=_speech_regions(samples)
        )  # type: ignore[arg-type]
        counts[f"{bpm:.0f} BPM"] = (len(transcript.segments), transcript.dropped)
    measured(
        ", ".join(
            f"{k}: {kept} kept, {dropped} dropped by the guard"
            for k, (kept, dropped) in counts.items()
        )
    )
    if any(kept for kept, _ in counts.values()):
        failed("captions were written over music")
        return 1
    return 0


# --- 5: kill at 50 %, resume ------------------------------------------------------

_RESUME_SHOTS = 8


def _resume_inputs(source: Path) -> ExportInputs:
    encode = ExportEncode(540, 960, 30, "medium", 20, "high", "4.2", 2)
    crop = crop_for(1280, 720, encode.width / encode.height)
    grade = GradeSpec(0.1, (1.02, 1.0, 0.98), 0.0, None, 0, 0.0)
    shots = tuple(
        ShotSource(
            sha256="e" * 64, source=source, source_in=0.5 * i, timeline_in=1.5 * i,
            timeline_out=1.5 * (i + 1), width=1280, height=720, has_audio=True,
            crop=CropBox(crop.width, crop.height, crop.x, crop.y), grade=grade, lut_digest=None,
        )
        for i in range(_RESUME_SHOTS)
    )  # fmt: skip
    return ExportInputs(shots, 1.5 * _RESUME_SHOTS, encode, "192k", None, (), None)


def _render_child(source: str, work: str, out: str) -> int:
    asyncio.run(render_export(_resume_inputs(Path(source)), Path(work), Path(out)))
    return 0


def check_resume_after_kill() -> int:
    """5. Kill the render at 50 %, restart: it completes without re-rendering finished shots."""
    with tempfile.TemporaryDirectory(prefix="repcut-gate06-resume-") as scratch:
        root = Path(scratch)
        source = root / "source.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
             "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000", "-t", "8",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", str(source)],
            check=True, timeout=120,
        )  # fmt: skip
        work, out = root / "work", root / "out.mp4"
        segments = work / "segments"
        child = subprocess.Popen(
            [sys.executable, __file__, "_render", str(source), str(work), str(out)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline and child.poll() is None:
            done = (
                [p for p in segments.glob("*.mkv") if not p.name.startswith(".")]
                if segments.exists()
                else []
            )
            if len(done) >= _RESUME_SHOTS // 2:
                break
            time.sleep(0.05)
        if child.poll() is not None:
            measured("the render finished before it could be killed")
            failed("could not kill the render mid-way; nothing was tested")
            return 1
        child.kill()
        child.wait()
        survived = {
            p.name: p.stat().st_mtime_ns
            for p in segments.glob("*.mkv")
            if not p.name.startswith(".")
        }
        partials = [p.name for p in segments.glob(".*")]
        result = asyncio.run(render_export(_resume_inputs(source), work, out))
        unchanged = all(
            (segments / name).stat().st_mtime_ns == mtime for name, mtime in survived.items()
        )
        measured(
            f"killed with {len(survived)}/{result.segments_total} shots finished "
            f"({len(partials)} partial file(s) left, never read); restart rendered "
            f"{result.segments_rendered}, reused {result.segments_reused}; "
            "finished files untouched: "
            f"{unchanged}; problems: {result.problems or 'none'}"
        )
        ok = (
            0 < len(survived) < result.segments_total
            and result.segments_reused == len(survived)
            and result.segments_rendered == result.segments_total - len(survived)
            and unchanged
            and not result.problems
        )
        if not ok:
            failed("the restart did not resume from the finished shots")
            return 1
    return 0


# --- 6: the speed budget ----------------------------------------------------------


def check_speed_budget() -> int:
    """6. 60 s of 1080p, graded with a LUT and captioned, exported as a Reel in under 5 minutes."""
    from repcut.grading.packs import PackLibrary, ensure_lut

    with tempfile.TemporaryDirectory(prefix="repcut-gate06-speed-") as scratch:
        root = Path(scratch)
        source = root / "src.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30",
             "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000", "-t", "62",
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", str(source)],
            check=True, timeout=600,
        )  # fmt: skip
        theme = PackLibrary().load("gym").theme("moody")
        lut = ensure_lut(root / "luts", "gym", theme)
        grade = GradeSpec(0.2, (1.03, 1.0, 0.97), 0.2, lut, 80, theme.finish.grain)
        crop = crop_for(1920, 1080, 9 / 16)
        shots = tuple(
            ShotSource(
                sha256="f" * 64, source=source, source_in=(i * 7) % 50, timeline_in=2.0 * i,
                timeline_out=2.0 * (i + 1), width=1920, height=1080, has_audio=True,
                crop=CropBox(crop.width, crop.height, crop.x, crop.y), grade=grade,
                lut_digest="moody",
            )
            for i in range(30)
        )  # fmt: skip
        words = tuple(
            TimedWord(f"w{i}", 0.4 * i + 0.1, 0.4 * i + 0.4, "rep", "rep", 0.9) for i in range(150)
        )
        events = tuple(
            ass.events(
                [CaptionLine("l", 0.1, 60.0, words)], style("punch"), [i * 0.5 for i in range(120)]
            )
        )
        encode = ExportEncode(1080, 1920, 30, "medium", 18, "high", "4.2", 2)
        started = time.monotonic()
        result = asyncio.run(
            render_export(
                ExportInputs(shots, 60.0, encode, "192k", None, events, style("punch")),
                root / "w",
                root / "o.mp4",
            )
        )
        elapsed = time.monotonic() - started
    measured(
        f"{elapsed:.0f} s for 60 s (budget {SPEED_BUDGET_S:.0f} s); stage ms {result.timings_ms}"
    )
    if result.problems or elapsed >= SPEED_BUDGET_S:
        failed("over the render budget" if not result.problems else "; ".join(result.problems))
        return 1
    return 0


# --- 7-9: suites --------------------------------------------------------------------


def check_escaping() -> int:
    """7. Caption text is escaped at both sinks: the subtitle filter (pixels) and the DOM."""
    code = _pytest_check(
        "subtitle sink",
        "tests/test_captions.py::test_escaping_closes_every_markup_door",
        "tests/test_captions.py::test_hostile_caption_text_renders_literally_through_ffmpeg",
        "tests/test_export.py::test_the_segment_builder_states_every_platform_property",
    )
    if code != 0:
        return 1
    npx = shutil.which("npx")
    if npx is None:
        measured("no npx")
        failed("node is not installed; the DOM half cannot run")
        return 1
    completed = subprocess.run(
        [npx, "vitest", "run", "components/captions/CaptionPanel.test.tsx"],
        cwd=REPO_ROOT / "ui", capture_output=True, text=True, timeout=600, check=False,
        env={**os.environ, "CI": "1"},
    )  # fmt: skip
    summary = next(
        (ln.strip() for ln in completed.stdout.splitlines() if "Tests" in ln and "passed" in ln),
        "no summary",
    )
    measured(f"DOM: {summary}")
    if completed.returncode != 0:
        failed("the caption editor rendered caption text as markup, or its tests failed")
        return 1
    return 0


def check_api_and_taste() -> int:
    """8. Caption and export changes are named, validated and logged as taste events."""
    return _pytest_check(
        "captions + export",
        "tests/test_captions.py",
        "tests/test_export.py",
        "tests/test_captions_export_api.py",
    )


def check_migration() -> int:
    """9. Migration 0005 round-trips and matches the models."""
    return _pytest_check("migrations", "tests/test_migrations.py", "tests/test_db_models.py")


CHECKS: dict[str, Callable[[], int]] = {
    "e2e": check_e2e,
    "platform-safe": check_platform_safe,
    "caption-accuracy": check_caption_accuracy,
    "music-only": check_music_only,
    "resume-after-kill": check_resume_after_kill,
    "speed-budget": check_speed_budget,
    "escaping": check_escaping,
    "api-and-taste": check_api_and_taste,
    "migration": check_migration,
}


def main() -> int:
    if len(sys.argv) == 5 and sys.argv[1] == "_render":
        return _render_child(*sys.argv[2:])
    if len(sys.argv) != 2 or sys.argv[1] not in CHECKS:
        print(f"usage: {Path(__file__).name} <{'|'.join(CHECKS)}>", file=sys.stderr)
        return 1
    if shutil.which("ffmpeg") is None:
        measured("no ffmpeg")
        failed("ffmpeg is not on PATH")
        return 1
    SCRATCH.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    code = CHECKS[sys.argv[1]]()
    print(f"ELAPSED: {time.monotonic() - started:.1f}s")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
