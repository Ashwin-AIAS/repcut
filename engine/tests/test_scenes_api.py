"""``GET /media/{sha256}/scenes`` and ``GET /media/{sha256}/scenes/{scene_id}/frame``.

The default ``api`` fixture (``conftest.py``, no Gemini key) is used throughout:
the analysis job it auto-enqueues on upload still detects scenes, samples
frames and measures energy - only the Gemini stage degrades, which is exactly
what these tests want (`vlm: null`, no live call, `.claude/rules/testing.md`).
"""

from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from conftest import Harness
from sqlalchemy import select

from repcut.analysis.params import SCENE_PARAMS_VERSION
from repcut.db.models import MediaBlob, Scene
from repcut.media.store import absolute

_UNKNOWN_SHA256 = "0" * 64
_UNKNOWN_UUID = "00000000-0000-4000-8000-000000000000"


async def _ingested_and_analyzed(
    api: Harness, upload_clip: Callable[..., Awaitable[httpx.Response]], source: Path
) -> str:
    """Upload a clip and wait for both ingest and its auto-enqueued analysis."""
    project = await api.client.post("/projects", json={"name": "session"})
    finalized = await upload_clip(project.json()["id"], source)
    assert finalized.status_code == 200, finalized.text
    await api.queue.drain()
    return finalized.json()["sha256"]  # type: ignore[no-any-return]


async def _scenes(api: Harness, sha256: str) -> list[Scene]:
    async with api.session_factory() as session:
        statement = (
            select(Scene)
            .where(Scene.sha256 == sha256, Scene.detector_params_version == SCENE_PARAMS_VERSION)
            .order_by(Scene.sequence_index)
        )
        return list((await session.execute(statement)).scalars().all())


# --- GET /media/{sha256}/scenes ------------------------------------------------


async def test_scenes_are_listed_in_sequence_order_with_a_null_vlm(
    api: Harness,
    make_motion_loudness_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    digest = await _ingested_and_analyzed(
        api, upload_clip, make_motion_loudness_clip(segment_seconds=1.5)
    )
    persisted = await _scenes(api, digest)
    assert len(persisted) >= 2, "the fixture's hard cut should produce at least two scenes"

    response = await api.client.get(f"/media/{digest}/scenes")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == len(persisted)
    assert [scene["sequence_index"] for scene in body] == sorted(
        scene["sequence_index"] for scene in body
    )
    for scene in body:
        assert scene["sha256"] == digest
        assert scene["has_sampled_frame"] is True
        assert scene["motion_energy"] is not None
        assert scene["audio_energy"] is not None
        assert scene["energy_score"] is not None
        # No Gemini key configured in this file's harness: every scene degrades.
        assert scene["vlm"] is None
        # A stored path carries the OS username on this machine and must never
        # be exposed (`.claude/rules/secrets.md`).
        assert "sampled_frame_path" not in scene


async def test_scenes_before_analysis_has_run_is_an_empty_list_not_a_404(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """The race the per-clip analysis view will actually hit.

    Finalize returns as soon as the blob exists; analysis is a job that has not
    started yet. That is not "clip missing" - `test_media_serving.py`'s own
    ``test_a_clip_whose_ingest_has_not_run_says_so`` makes the same distinction
    for the proxy, and here the honest answer is zero scenes, not an error.
    """
    project = await api.client.post("/projects", json={"name": "session"})
    finalized = await upload_clip(project.json()["id"], make_clip())
    assert finalized.status_code == 200, finalized.text
    # Deliberately no `queue.drain()`.

    response = await api.client.get(f"/media/{finalized.json()['sha256']}/scenes")

    assert response.status_code == 200
    assert response.json() == []


async def test_an_unknown_blob_is_a_named_404(api: Harness) -> None:
    response = await api.client.get(f"/media/{_UNKNOWN_SHA256}/scenes")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "media_blob_not_found"


async def test_a_malformed_sha256_never_reaches_the_database(api: Harness) -> None:
    response = await api.client.get("/media/not-a-digest/scenes")

    assert response.status_code == 422


# --- GET /media/{sha256}/scenes/{scene_id}/frame -------------------------------


async def test_the_sampled_frame_is_served_whole(
    api: Harness,
    make_motion_loudness_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    digest = await _ingested_and_analyzed(
        api, upload_clip, make_motion_loudness_clip(segment_seconds=1.5)
    )
    scenes = await _scenes(api, digest)
    scene = scenes[0]
    assert scene.sampled_frame_path is not None
    on_disk = absolute(api.data_dir, scene.sampled_frame_path).read_bytes()

    response = await api.client.get(f"/media/{digest}/scenes/{scene.id}/frame")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == str(len(on_disk))
    assert response.content == on_disk
    assert response.content[:2] == b"\xff\xd8"  # a real JPEG, not an empty file


async def test_a_range_request_returns_exactly_that_slice(
    api: Harness,
    make_motion_loudness_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    digest = await _ingested_and_analyzed(
        api, upload_clip, make_motion_loudness_clip(segment_seconds=1.5)
    )
    scene = (await _scenes(api, digest))[0]
    assert scene.sampled_frame_path is not None
    on_disk = absolute(api.data_dir, scene.sampled_frame_path).read_bytes()
    assert len(on_disk) > 200, "the fixture frame must be big enough to slice meaningfully"

    response = await api.client.get(
        f"/media/{digest}/scenes/{scene.id}/frame", headers={"range": "bytes=10-109"}
    )

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 10-109/{len(on_disk)}"
    assert response.headers["content-length"] == "100"
    assert response.content == on_disk[10:110]


async def test_a_scene_id_that_does_not_exist_is_a_named_404(
    api: Harness,
    make_motion_loudness_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    digest = await _ingested_and_analyzed(
        api, upload_clip, make_motion_loudness_clip(segment_seconds=1.5)
    )

    response = await api.client.get(f"/media/{digest}/scenes/{_UNKNOWN_UUID}/frame")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "scene_not_found"


async def test_a_malformed_scene_id_never_reaches_the_database(
    api: Harness,
    make_motion_loudness_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    digest = await _ingested_and_analyzed(
        api, upload_clip, make_motion_loudness_clip(segment_seconds=1.5)
    )

    response = await api.client.get(f"/media/{digest}/scenes/..%2F..%2Fetc%2Fpasswd/frame")

    assert response.status_code in (404, 422)


async def test_a_scene_whose_frame_has_not_been_sampled_yet_says_so(
    api: Harness, tmp_path: Path
) -> None:
    """Same shape as ``test_media_serving.py``'s ``artifact_not_ready``: the row
    exists, the derived bytes do not yet, and the two are told apart.
    """
    digest = "a" * 64
    async with api.session_factory() as session:
        session.add(
            MediaBlob(
                sha256=digest,
                size_bytes=1024,
                stored_path=f"media/blobs/{digest[:2]}/{digest}/source.mp4",
            )
        )
        scene = Scene(
            sha256=digest,
            detector_params_version=SCENE_PARAMS_VERSION,
            sequence_index=0,
            start_seconds=0.0,
            end_seconds=2.0,
            start_frame_source=0,
            end_frame_source=60,
        )
        session.add(scene)
        await session.commit()
        scene_id = scene.id

    response = await api.client.get(f"/media/{digest}/scenes/{scene_id}/frame")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "artifact_not_ready"


async def test_an_unknown_blob_with_a_well_formed_scene_id_is_scene_not_found(
    api: Harness,
) -> None:
    """Wrong clip and wrong scene id read identically - `api/errors.py`'s own
    reasoning for one error class covering both, so a caller cannot use this
    response to learn which blobs exist.
    """
    response = await api.client.get(f"/media/{_UNKNOWN_SHA256}/scenes/{_UNKNOWN_UUID}/frame")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "scene_not_found"
