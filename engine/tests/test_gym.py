"""Reps and set captions: counted from joint angles, confirmed by the person, never judged."""

from __future__ import annotations

import ast
import math
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import numpy as np
import pytest
from conftest import Harness
from sqlalchemy import select
from test_edit_api import _project

from repcut.db.models import TasteEvent
from repcut.edit.types import Plan, Shot
from repcut.export import service as export_service
from repcut.gym import reps as gym_reps
from repcut.gym.captions import SetCaption, set_events
from repcut.gym.reps import (
    MAX_POSE_SECONDS,
    NO_REPS,
    NOBODY,
    POSE_FPS,
    PRODUCT_BOUNDARY,
    count_reps,
    segment_keypoints,
)


def _rot(v: tuple[float, float], degrees: float) -> tuple[float, float]:
    r = math.radians(degrees)
    return (v[0] * math.cos(r) - v[1] * math.sin(r), v[0] * math.sin(r) + v[1] * math.cos(r))


def _add(a: tuple[float, float], b: tuple[float, float], k: float = 1.0) -> tuple[float, float]:
    return (a[0] + k * b[0], a[1] + k * b[1])


def body(knee: float, hip: float, elbow: float) -> list[tuple[float, float, float]]:
    """A side-on figure (COCO-17, pixels) with these three joint angles in degrees."""
    ankle = (300.0, 600.0)
    down = (0.0, 1.0)
    k = _add(ankle, (0.0, -1.0), 120)
    kh = _rot(down, 180 - knee) if knee else (0.0, -1.0)
    kh = _rot((0.0, 1.0), knee)
    h = _add(k, kh, 120)
    hk = (-kh[0], -kh[1])
    hs = _rot(hk, hip)
    s = _add(h, hs, 150)
    e = _add(s, (0.2, 0.98), 90)
    es = ((s[0] - e[0]) / 90, (s[1] - e[1]) / 90)
    w = _add(e, _rot(es, elbow), 80)
    points = [(300.0, 200.0, 0.9)] * 17
    for left, right, p in (
        (5, 6, s),
        (7, 8, e),
        (9, 10, w),
        (11, 12, h),
        (13, 14, k),
        (15, 16, ankle),
    ):
        points[left] = (p[0], p[1], 0.9)
        points[right] = (p[0], p[1], 0.9)
    return points


def movement(
    reps: int,
    joint: str,
    seconds_per_rep: float,
    *,
    pause: float = 0.6,
    seed: int = 0,
    missing: float = 0.1,
) -> list[gym_reps.Keypoints]:
    """``reps`` repetitions of one joint, with noise, pauses at the top and dropped frames."""
    rng = np.random.default_rng(seed)
    fps = POSE_FPS
    frames: list[gym_reps.Keypoints] = []
    ranges = {"knee": (170.0, 75.0), "elbow": (165.0, 45.0), "hip": (175.0, 95.0)}
    top, bottom = ranges[joint]
    for _ in range(int(pause * fps)):
        frames.append(None)
    for _ in range(reps):
        held = int(pause * fps * rng.uniform(0.5, 1.5))
        cycle = int(seconds_per_rep * fps * rng.uniform(0.85, 1.15))
        for i in range(held + cycle):
            phase = 0.0 if i < held else (i - held) / cycle
            angle = top - (top - bottom) * (1 - math.cos(2 * math.pi * phase)) / 2
            angle += float(rng.normal(0, 3))
            angles = {"knee": 172.0, "hip": 176.0, "elbow": 160.0}
            angles[joint] = angle
            if joint == "knee":  # a squat bends the hip too
                angles["hip"] = 176.0 - 0.6 * (176.0 - angle * 0.9)
            frames.append(
                None
                if rng.random() < missing
                else body(angles["knee"], angles["hip"], angles["elbow"])
            )
    frames.extend(frames[-1:] * int(fps))
    return frames


# Three labelled sets, generated: (joint, true reps, seconds per rep, seed).
LABELLED = [("knee", 8, 2.2, 1), ("elbow", 5, 1.6, 2), ("hip", 12, 1.4, 3)]


@pytest.mark.parametrize(("joint", "truth", "tempo", "seed"), LABELLED)
def test_reps_are_counted_within_one_of_the_label(
    joint: str, truth: int, tempo: float, seed: int
) -> None:
    result = count_reps(movement(truth, joint, tempo, seed=seed))
    assert result.reps is not None, result.reason
    assert abs(result.reps - truth) <= 1, (joint, result)


def test_nobody_or_no_movement_gives_no_number() -> None:
    assert count_reps([None] * 60).reason == NOBODY
    still = [body(170, 175, 160)] * 90
    assert count_reps(still).reps is None and count_reps(still).reason == NO_REPS
    twitch = movement(6, "elbow", 1.5, seed=4)
    small = [None if kp is None else body(170, 175, 160 + (i % 5)) for i, kp in enumerate(twitch)]
    assert count_reps(small).reps is None


def test_the_pose_model_never_runs_over_a_whole_clip(tmp_path: Path) -> None:
    class Never:
        def keypoints(self, frame: object) -> None:
            raise AssertionError("must refuse before reading a frame")

    with pytest.raises(ValueError, match="too long"):
        segment_keypoints(tmp_path / "x.mp4", 0.0, MAX_POSE_SECONDS + 1, Never())  # type: ignore[arg-type]


def test_the_product_boundary_is_named_and_kept() -> None:
    assert "never scores form" in PRODUCT_BOUNDARY
    package = Path(__file__).parents[1] / "repcut" / "gym"
    banned = ("form_score", "physique", "body_fat", "bmi", "posture_score", "depth_score")
    for source in package.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        names = {
            n.name.lower()
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        }
        assert not [b for b in banned for name in names if b in name], source.name


def test_a_set_caption_says_only_what_was_confirmed() -> None:
    assert SetCaption("s", 8, "Back squat", 100.0).text() == "Back squat · 8 reps · 100 kg"
    assert SetCaption("s", 1, None, None).text() == "1 rep"
    assert SetCaption("s", None, None, 60.5, "lb").text() == "60.5 lb"
    shots = (
        Shot("h", "seg-a", "a" * 64, 1.0, 2.0, 0.0, 1.0, None, None, hook=True),
        Shot("1", "seg-a", "a" * 64, 1.0, 5.0, 1.0, 5.0, None, None),
        Shot("2", "seg-a", "a" * 64, 6.0, 7.0, 5.0, 6.0, None, None),
        Shot("3", "seg-b", "a" * 64, 9.0, 10.0, 6.0, 7.0, None, None),
    )
    plan = Plan(
        shots=shots,
        duration_seconds=7.0,
        target_seconds=7.0,
        music_start_seconds=0.0,
        snapped=False,
        track_sha256=None,
        seed=0,
    )
    events = set_events(plan, [SetCaption("seg-a", 8, "Squat", None)])
    assert len(events) == 1 and events[0].start == 1.0 and events[0].end == 4.0
    assert events[0].text.startswith("{\\an8}") and "8 reps" in events[0].text


async def test_sets_are_suggested_confirmed_and_burned_only_when_confirmed(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
) -> None:
    project = await _project(api, upload_clip, make_clip, clips=2)
    url = f"/projects/{project}/gym"
    listed = (await api.client.get(f"{url}/sets")).json()
    assert listed["sets"], "the generated clips move: there are hero moments"
    assert "never scores form" in listed["boundary"]
    first = listed["sets"][0]
    assert first["status"] == "suggested" and first["caption"] is None

    job = await api.client.post(f"{url}/count")
    assert job.status_code == 202
    await api.queue.drain()
    counted = (await api.client.get(f"{url}/sets")).json()["sets"][0]
    # No torch or weights in this suite: no number, and a sentence saying why.
    assert counted["reps_suggested"] is None and counted["reason"]

    async with api.session_factory() as session:
        inputs, _ = await export_service.gather(
            session, api.data_dir, project, track_subjects=False
        )
    assert not any("reps" in e.text.lower() for e in inputs.captions), "nothing burned before confirming"

    bad = await api.client.post(f"{url}/sets/{first['id']}", json={"action": "confirm"})
    assert bad.status_code == 422
    missing = await api.client.post(f"{url}/sets/nope", json={"action": "dismiss"})
    assert missing.status_code == 404
    done = await api.client.post(
        f"{url}/sets/{first['id']}",
        json={"action": "confirm", "reps": 8, "exercise": "Back squat", "weight": 100},
    )
    assert done.status_code == 200, done.text
    assert done.json()["caption"] == "Back squat · 8 reps · 100 kg"
    async with api.session_factory() as session:
        inputs, _ = await export_service.gather(
            session, api.data_dir, project, track_subjects=False
        )
        kinds = [e.kind for e in (await session.execute(select(TasteEvent))).scalars()]
    assert any("8 reps" in e.text.lower() for e in inputs.captions)
    assert "gym_caption_confirmed" in kinds
    if len(listed["sets"]) > 1:
        second = listed["sets"][1]["id"]
        gone = await api.client.post(f"{url}/sets/{second}", json={"action": "dismiss"})
        assert gone.json()["status"] == "dismissed"
