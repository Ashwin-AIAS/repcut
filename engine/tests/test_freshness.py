"""Lazy regeneration: a clip made under a superseded recipe is re-derived when opened.

A v1 clip is simulated rather than produced by old code, which no longer
exists: its proxy file is moved under the previous version's directory and its
rows relabelled, exactly the state a real Prompt 03 library is in. Versions are
derived from the current ones (``testing.md``), never written as literals.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from conftest import Harness
from sqlalchemy import func, select, update

from repcut.analysis.params import SCENE_PARAMS_VERSION
from repcut.db.models import DerivedArtifact, Job, Scene
from repcut.media.artifacts import PARAMS_VERSION, ArtifactKind
from repcut.media.ingest import PROXY_FILENAME
from repcut.media.store import absolute, derived_path

PROXY_VERSION = PARAMS_VERSION[ArtifactKind.PROXY]


async def _upload(
    api: Harness, upload_clip: Callable[..., Awaitable[httpx.Response]], source: Path
) -> tuple[str, str, str]:
    project = await api.client.post("/projects", json={"name": "session"})
    project_id = project.json()["id"]
    finalized = await upload_clip(project_id, source)
    assert finalized.status_code == 200, finalized.text
    await api.queue.drain()
    body = finalized.json()
    return project_id, body["media_file_id"], body["sha256"]


async def _age_to_previous_versions(api: Harness, sha256: str) -> Path:
    """Put a current clip into the state a pre-bump clip is in. Returns the old proxy file."""
    previous_proxy = PROXY_VERSION - 1
    async with api.session_factory() as session:
        row = (
            await session.execute(
                select(DerivedArtifact).where(
                    DerivedArtifact.sha256 == sha256,
                    DerivedArtifact.artifact_kind == ArtifactKind.PROXY.value,
                )
            )
        ).scalar_one()
        old_stored = derived_path(sha256, ArtifactKind.PROXY.value, previous_proxy, PROXY_FILENAME)
        old_file = absolute(api.data_dir, old_stored)
        old_file.parent.mkdir(parents=True, exist_ok=True)
        absolute(api.data_dir, row.stored_path).replace(old_file)
        row.stored_path = str(old_stored)
        row.params_version = previous_proxy
        await session.execute(
            update(Scene)
            .where(Scene.sha256 == sha256)
            .values(detector_params_version=SCENE_PARAMS_VERSION - 1)
        )
        await session.commit()
    return old_file


async def _job_count(api: Harness) -> int:
    async with api.session_factory() as session:
        return int((await session.execute(select(func.count(Job.id)))).scalar_one())


async def _open(api: Harness, media_file_id: str) -> list[str]:
    response = await api.client.post(f"/media/{media_file_id}/ensure-current")
    assert response.status_code == 200, response.text
    ids: list[str] = response.json()["enqueued_job_ids"]
    return ids


async def test_opening_a_current_clip_enqueues_nothing(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    _, media_file_id, _ = await _upload(api, upload_clip, make_clip(seconds=2.0))

    assert await _open(api, media_file_id) == []


async def test_a_stale_clip_is_regenerated_once_and_keeps_its_old_files(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    _, media_file_id, sha256 = await _upload(api, upload_clip, make_clip(seconds=2.0))
    old_proxy = await _age_to_previous_versions(api, sha256)

    first = await _open(api, media_file_id)
    second = await _open(api, media_file_id)

    assert len(first) == 2, "one ingest for the proxy, one analysis for the scenes"
    assert second == [], "the first open's jobs are still queued; a second open adds none"

    await api.queue.drain()
    third = await _open(api, media_file_id)
    assert third == []

    async with api.session_factory() as session:
        current_proxy = (
            await session.execute(
                select(DerivedArtifact).where(
                    DerivedArtifact.sha256 == sha256,
                    DerivedArtifact.artifact_kind == ArtifactKind.PROXY.value,
                    DerivedArtifact.params_version == PROXY_VERSION,
                )
            )
        ).scalar_one()
        versions = set(
            (
                await session.execute(
                    select(Scene.detector_params_version).where(Scene.sha256 == sha256)
                )
            )
            .scalars()
            .all()
        )

    assert absolute(api.data_dir, current_proxy.stored_path).is_file()
    assert old_proxy.is_file(), "a bump changes the key; it never deletes the old file"
    assert versions == {SCENE_PARAMS_VERSION - 1, SCENE_PARAMS_VERSION}


async def test_a_duplicate_upload_of_a_current_clip_still_enqueues_nothing(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """Prompt 02's invariant survives the move of the freshness checks."""
    source = make_clip(seconds=2.0)
    project_id, _, _ = await _upload(api, upload_clip, source)
    before = await _job_count(api)

    again = await upload_clip(project_id, source)

    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert await _job_count(api) == before


async def test_an_unknown_clip_is_a_named_404(api: Harness) -> None:
    response = await api.client.post("/media/00000000-0000-4000-8000-000000000000/ensure-current")

    assert response.status_code == 404


async def test_a_malformed_id_never_reaches_the_database(api: Harness) -> None:
    response = await api.client.post("/media/..%2F..%2Fetc/ensure-current")

    assert response.status_code in (404, 422)
