"""Trust: before/after show the same instant; the summary says only what is true."""

import asyncio
import copy
import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from test_hook import _plan

from repcut.grading import apply as grading
from repcut.grading.corrector import Correction
from repcut.grading.packs import PackLibrary
from repcut.media import ffmpeg_builder as builder
from repcut.media.artifacts import PROXY_RECIPE
from repcut.review.compare import FRAME_CENTRE, comparison, first_frame
from repcut.review.summary import Claim, PointerError, clock, resolve, summarise, verify

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


# --- the summary ----------------------------------------------------------------------


def _record(target: float = 30.0, seed: int = 7) -> dict[str, Any]:
    result = _plan(target, seed=seed)
    sha = result.shots[0].sha256
    return {
        "plan": result.to_json(),
        "decisions": {"locked": [], "removed_ranges": []},
        "theme": {"id": "moody", "name": "Moody"},
        "clip_names": {sha: "deadlift.mp4"},
        "music": {"sha256": result.track_sha256, "display_name": "fast drop", "bpm": 128.0},
        "grading": [
            {
                "sha256": sha,
                "scenes": [
                    {
                        "key": "s1",
                        "theme_id": "moody",
                        "theme_name": "Moody",
                        "intensity": 80,
                        "temperature_shift_k": 400.0,
                        "exposure_stops": 0.0,
                    },
                    {
                        "key": "s2",
                        "theme_id": "moody",
                        "theme_name": "Moody",
                        "intensity": 80,
                        "temperature_shift_k": 120.0,
                        "exposure_stops": 0.25,
                    },
                ],
            }
        ],
        "dead_space": [
            {"sha256": sha, "start": 4.0, "end": 6.5},
            {"sha256": sha, "start": 20.0, "end": 21.5},
        ],
        "captions": {
            "enabled": True,
            "style_id": "punch",
            "style_name": "Punch",
            "lines": [{"id": "a", "start": 1.0, "end": 2.0}],
        },
    }


@pytest.mark.parametrize("target", [15.0, 30.0, 60.0])
@pytest.mark.parametrize("seed", [1, 7, 23])
def test_every_claim_exists_in_the_edit_record(target: float, seed: int) -> None:
    record = _record(target, seed)
    claims = summarise(record)
    assert claims
    assert verify(record, claims) == []
    ids = {c.id for c in claims}
    assert {"hook", "cuts", "music", "dead-space", "captions"} <= ids
    assert any(i.startswith("white-balance") for i in ids)
    assert any(i.startswith("slowmo") for i in ids)
    for claim in claims:
        if claim.count is not None:
            assert claim.count == len(claim.facts)
            assert str(claim.count) in claim.text


def test_the_numbers_come_from_the_record_not_a_template() -> None:
    record = _record()
    texts = " | ".join(c.text for c in summarise(record))
    assert "+400K" in texts and "2 silent, still stretches" in texts and "4.0 s" in texts
    record["grading"][0]["scenes"][0]["temperature_shift_k"] = -250.0
    record["dead_space"].pop()
    texts = " | ".join(c.text for c in summarise(record))
    assert "-250K" in texts and "+400K" not in texts
    assert "1 silent, still stretch " in texts
    beat_cuts = sum(1 for s in record["plan"]["shots"][1:] if s["beat_in"] is not None)
    assert f"{beat_cuts} cuts on the beat" in texts
    slow = [s for s in record["plan"]["shots"] if s["speed"] < 1]
    assert texts.count("slow-motion shot at") == len(slow)
    assert clock(slow[0]["timeline_in"]) in texts


def test_a_claim_about_an_old_record_fails_against_the_new_one() -> None:
    record = _record()
    claims = summarise(record)
    changed = copy.deepcopy(record)
    slow_index = next(i for i, s in enumerate(changed["plan"]["shots"]) if s["speed"] < 1)
    changed["plan"]["shots"][slow_index]["speed"] = 1.0
    changed["dead_space"] = []
    problems = verify(changed, claims)
    assert any("speed" in p for p in problems)
    assert any("dead_space" in p for p in problems)


def test_verify_refuses_unbacked_and_miscounted_claims() -> None:
    record = _record()
    assert verify(record, [Claim("x", "nothing behind this", ())]) == ["x: no fact behind it"]
    claim = summarise(record)[1]
    wrong = Claim(claim.id, claim.text, claim.facts[:-1], claim.count)
    assert any("backed by" in p for p in verify(record, [wrong]))


def test_nothing_to_say_says_nothing() -> None:
    record = _record()
    record.update(grading=[], dead_space=[], music=None, captions=None)
    for shot in record["plan"]["shots"]:
        shot["speed"], shot["hook"] = 1.0, False
    ids = {c.id for c in summarise(record)}
    assert ids == {"cuts"}


def test_pointers_resolve_like_rfc_6901() -> None:
    doc = {"a/b": {"~k": [10, 20]}}
    assert resolve(doc, "/a~1b/~0k/1") == 20
    assert resolve(doc, "") == doc
    for bad in ("/a~1b/~0k/2", "/nope", "a", "/a~1b/~0k/x"):
        with pytest.raises(PointerError):
            resolve(doc, bad)


# --- before/after ---------------------------------------------------------------------


def test_the_preview_starts_on_the_first_proxy_frame_at_or_after_the_scene() -> None:
    fps = 30
    assert first_frame(0.0, fps) == 0
    assert first_frame(1.0, fps) == 30
    assert first_frame(1.0001, fps) == 30, "written with three decimals, 1.000 is frame 30"
    assert first_frame(1.0006, fps) == 31
    assert first_frame(1.2345, fps) == 38
    spec = comparison(1.2345, 3.4, fps)
    assert spec.side_seconds("after", 0) == pytest.approx(FRAME_CENTRE / fps, abs=1e-6)
    assert spec.side_seconds("before", 0) == pytest.approx((38 + FRAME_CENTRE) / fps, abs=1e-6)
    with pytest.raises(ValueError):
        spec.side_seconds("after", spec.frame_count)


BITS = 8
BLOCK = 64


def _coded_frame(index: int, width: int = 640, height: int = 360) -> np.ndarray:
    """A frame carrying its own index: 8 black/white blocks plus a white and a black reference."""
    frame = np.full((height, width, 3), 128, dtype=np.uint8)
    for bit in range(BITS + 2):
        value = (index >> bit) & 1 if bit < BITS else (1 if bit == BITS else 0)
        x = 8 + bit * BLOCK
        frame[100 : 100 + BLOCK, x : x + BLOCK - 8] = 235 if value else 16
    return frame


def _write_coded_clip(path: Path, frames: int, fps: int) -> Path:
    width, height = 640, 360
    raw = b"".join(_coded_frame(i, width, height).tobytes() for i in range(frames))
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
         "-c:v", "libx264", "-crf", "8", "-pix_fmt", "yuv420p", "-g", "48",
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-color_range", "tv", str(path)],
        input=raw, check=True, timeout=120,
    )  # fmt: skip
    return path


def _decode(path: Path, seconds: float | None = None, number: int | None = None) -> int:
    """The index a frame carries: read at ``seconds`` with an input seek (as a player
    seeks), or as decoded frame ``number`` counted from the file's start."""
    seek = ["-ss", f"{seconds:.6f}"] if seconds is not None else []
    pick = f"select=eq(n\\,{number})," if number is not None else ""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *seek, "-i", str(path),
         "-vf", f"{pick}scale=640:360", "-frames:v", "1", "-fps_mode", "passthrough",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True, timeout=60,
    ).stdout  # fmt: skip
    luma = np.frombuffer(out, dtype=np.uint8).reshape(360, 640).astype(float)

    def block(bit: int) -> float:
        x = 8 + bit * BLOCK
        return float(luma[110 : 90 + BLOCK, x + 8 : x + BLOCK - 16].mean())

    threshold = (block(BITS) + block(BITS + 1)) / 2
    return sum(1 << bit for bit in range(BITS) if block(bit) > threshold)


def _frame_hash(index: int) -> str:
    return hashlib.sha256(index.to_bytes(4, "big")).hexdigest()[:16]


@needs_ffmpeg
def test_before_and_after_show_the_same_frame_at_three_positions(tmp_path: Path) -> None:
    """The guide's check: same timestamp both sides, asserted by a frame hash at 3 positions.

    A generated clip whose every frame carries its own index goes through the
    real proxy build and the real graded preview (Moody, 100%); each side is
    then read where the UI points its player, and the index each shows is
    hashed. Grading changes every pixel; it cannot change which frame it is.
    """
    fps = PROXY_RECIPE.fps
    source = _write_coded_clip(tmp_path / "coded.mp4", frames=150, fps=fps)
    proxy = tmp_path / "proxy.mp4"
    asyncio.run(
        builder.render(
            builder.build_proxy(
                source, proxy, display_width=640, display_height=360,
                color_primaries="bt709", color_transfer="bt709", color_space="bt709",
                color_range="tv", duration_seconds=5.0,
            )
        )
    )  # fmt: skip
    start, end = 1.2345, 3.9
    theme = PackLibrary().load("gym").theme("moody")
    preview = asyncio.run(
        grading.render_preview(
            proxy, tmp_path / "after.mp4", start_seconds=start, end_seconds=end,
            correction=Correction(exposure_stops=0.2, temperature_shift_k=300.0), theme=theme,
            pack_id="gym", intensity=100, luts_root=tmp_path / "luts",
        )
    )  # fmt: skip
    spec = comparison(start, end, fps)
    positions = (3, spec.frame_count // 2, spec.frame_count - 4)
    seen = []
    for frame in positions:
        before = _decode(proxy, seconds=spec.side_seconds("before", frame))
        after = _decode(preview, seconds=spec.side_seconds("after", frame))
        assert _frame_hash(before) == _frame_hash(after), (frame, before, after)
        # And it is the frame the mapping names: the preview's frame i is the proxy's offset + i.
        assert _decode(preview, number=frame) == _decode(proxy, number=spec.offset_frames + frame)
        assert _decode(proxy, number=spec.offset_frames + frame - 1) != after
        seen.append(_frame_hash(after))
    assert len(set(seen)) == len(positions), "three positions, three different frames"
