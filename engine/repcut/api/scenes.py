"""One clip's detected scenes and their Gemini tagging, if analysis reached them.

Scenes are keyed on the blob (``sha256``), not on a project's reference to it
(`db/models.py`'s ``Scene`` docstring) - a clip re-added to a second project
reuses the scenes already detected for it, exactly as it already reuses its
proxy. This module reads that key directly rather than through a
``media_file_id``, which is why it is its own router rather than folded into
``api/projects.py`` (project/media_file scoped) or ``api/media.py`` (which
still owns the one route here that serves bytes rather than JSON - see
``get_scene_frame``).
"""

from typing import Annotated

from fastapi import APIRouter
from fastapi import Path as PathParam
from sqlalchemy import select

from repcut.analysis.params import SCENE_PARAMS_VERSION
from repcut.analysis.pipeline import GEMINI_PROMPT_VERSION
from repcut.api.deps import SessionDep
from repcut.api.errors import MediaBlobNotFoundError
from repcut.api.schemas import SHA256_PATTERN, SceneResponse, SceneVLMResponse
from repcut.db.models import GeminiSceneCache, MediaBlob, Scene
from repcut.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["scenes"])

# `^...$`, not `\A...\Z`: pydantic compiles `pattern` with Rust's regex crate,
# which rejects `\A` outright - same note as `api/schemas.py`, where this
# pattern is defined.
Sha256Path = Annotated[str, PathParam(pattern=SHA256_PATTERN)]


def _vlm_response(cache: GeminiSceneCache | None) -> SceneVLMResponse | None:
    """The scene's cached Gemini tagging, or None when there is nothing to show.

    A row whose ``raw_response_json`` is null - a malformed answer, cached as
    such per ``GeminiSceneCache``'s own docstring - renders as null here too:
    the UI cannot tell "not analyzed yet" from "analyzed, nothing usable came
    back" from this field alone, and does not need to; both mean there is no
    tag to render for this scene right now.
    """
    if cache is None or cache.raw_response_json is None:
        return None
    return SceneVLMResponse(
        content_type=cache.content_type,
        exercise_guess=cache.exercise_guess,
        environment=cache.environment,
        lighting_quality=cache.lighting_quality,
        lighting_temperature=cache.lighting_temperature,
        lighting_direction=cache.lighting_direction,
        energy_level=cache.energy_level,
        aesthetic_notes=cache.aesthetic_notes,
    )


@router.get(
    "/media/{sha256}/scenes",
    response_model=list[SceneResponse],
    summary="One clip's detected scenes",
)
async def list_scenes(sha256: Sha256Path, session: SessionDep) -> list[SceneResponse]:
    """Every scene at the current detector version, in order, with its Gemini tag if any.

    Version-scoped the same way ``api/projects.py``'s ``_artifact_kinds_by_blob``
    scopes artifacts: a scene set detected under a superseded
    ``detector_params_version`` still has rows, but they are not what a caller
    reading "this clip's scenes" today should see.
    """
    blob = await session.get(MediaBlob, sha256)
    if blob is None:
        raise MediaBlobNotFoundError("that clip is not in the media library")

    statement = (
        select(Scene, GeminiSceneCache)
        .outerjoin(
            GeminiSceneCache,
            (GeminiSceneCache.scene_id == Scene.id)
            & (GeminiSceneCache.gemini_prompt_version == GEMINI_PROMPT_VERSION),
        )
        .where(Scene.sha256 == sha256, Scene.detector_params_version == SCENE_PARAMS_VERSION)
        .order_by(Scene.sequence_index)
    )
    rows = (await session.execute(statement)).all()
    return [
        SceneResponse(
            id=scene.id,
            sha256=scene.sha256,
            sequence_index=scene.sequence_index,
            start_seconds=scene.start_seconds,
            end_seconds=scene.end_seconds,
            start_frame_source=scene.start_frame_source,
            end_frame_source=scene.end_frame_source,
            has_sampled_frame=scene.sampled_frame_path is not None,
            motion_energy=scene.motion_energy,
            audio_energy=scene.audio_energy,
            energy_score=scene.energy_score,
            vlm=_vlm_response(cache),
            created_at=scene.created_at,
        )
        for scene, cache in rows
    ]


__all__ = ["router"]
