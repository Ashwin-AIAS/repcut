"""Shot-boundary detection: PySceneDetect's ``ContentDetector`` against the proxy.

Pure function: a path and a frame rate in, a list of :class:`SceneBoundary` out.
No SQLAlchemy import anywhere in this module - persistence is a later, separate
pass (see the module's own callers), which is what keeps this testable without
a database fixture and keeps the detector swappable without touching a schema.

Detection reads the **proxy**, deliberately (amendment 008 resolution 6): shot
boundaries are a timing decision, and the proxy already solved VFR by being
CFR - re-solving it here, against the source, would mean re-deriving frame
cadence PySceneDetect already assumes is constant. The boundaries this produces
are nonetheless timed against the **source** (``start_seconds``/``end_seconds``),
because the proxy preserves the source's full duration and only its frame
*rate* is normalized: the same wall-clock instant, in seconds, means the same
thing in both files, so the proxy's own timecodes are used as-is rather than
re-derived through a frame count (amendment 008 resolution 4 - a frame count
would be silently wrong the moment it is read against the wrong file's frame
rate, exactly the VFR-desync bug `.claude/rules/ffmpeg.md` warns about, in a
new shape).
"""

from pathlib import Path

from scenedetect import ContentDetector, SceneManager, open_video
from scenedetect.video_stream import SeekError, VideoOpenFailure, VideoStream

from repcut.analysis.params import SCENE_DETECTOR_RECIPE, SceneDetectorRecipe
from repcut.analysis.types import SceneBoundary
from repcut.logging import get_logger

logger = get_logger(__name__)


class SceneDetectionError(RuntimeError):
    """The proxy could not be opened, or produced nothing detection can trust.

    Covers three distinct PySceneDetect failure shapes as one: a missing path
    (``OSError`` from ``open_video``'s own existence check), a file OpenCV's
    backend cannot decode at all (``VideoOpenFailure``), and a video whose
    frame rate or duration could not be determined (``FrameRateUnavailable``,
    a subclass of ``VideoOpenFailure``, and this module's own check on
    ``video.duration``). All three mean the same thing to a caller: detection
    could not run, and the cause is worth one readable sentence, not three
    different exception types to catch.
    """


def _open(proxy_path: Path) -> VideoStream:
    try:
        return open_video(proxy_path.as_posix())
    except (OSError, VideoOpenFailure) as error:
        # Named: a missing path, or a file OpenCV's backend cannot decode at
        # all - garbage bytes, a container ffprobe already rejected upstream,
        # or (in production) a proxy render that never actually landed.
        raise SceneDetectionError("this proxy could not be opened for scene detection") from error


def _min_scene_len_frames(recipe: SceneDetectorRecipe, proxy_fps: float) -> int:
    """``recipe.minimum_scene_length`` in frames, against the PROXY's own rate.

    A duration, not a frame count, is what the recipe stores (its own
    docstring explains why); this is the one place it becomes a frame count,
    against the rate the *opened video* reports rather than a constant assumed
    to match - the proxy recipe's nominal fps and what a given proxy file
    actually measures at open time are the same number in practice, but reading
    it from the opened stream costs nothing and removes the assumption.
    """
    frames = round(recipe.minimum_scene_length.total_seconds() * proxy_fps)
    return max(1, frames)


def detect_scenes(
    proxy_path: Path,
    *,
    fps_source: float,
    recipe: SceneDetectorRecipe = SCENE_DETECTOR_RECIPE,
) -> list[SceneBoundary]:
    """Detect shot boundaries in ``proxy_path``, timed against the source's fps.

    ``fps_source`` is used only to derive ``start_frame_source``/
    ``end_frame_source`` (``round(seconds * fps_source)``) - the authoritative
    boundary is always the seconds value read off the proxy's own timeline.

    Raises ``ValueError`` for a non-positive ``fps_source`` (nothing downstream
    can use a frame handle derived from it) and ``SceneDetectionError`` for
    anything that stops the proxy itself from being read - see that class's
    docstring for the three cases it covers.
    """
    if fps_source <= 0:
        raise ValueError("fps_source must be positive")

    video = _open(proxy_path)
    proxy_fps = float(video.frame_rate)
    duration_seconds = float(video.duration.seconds)
    if proxy_fps <= 0 or duration_seconds <= 0:
        # Named condition, not a PySceneDetect exception: this is the shape a
        # technically-openable-but-unreadable file takes here (see the session
        # report - an empty file opens with OpenCV's own zero-length fallback
        # rather than raising), and `start_in_scene=True` below would otherwise
        # paper over it with one bogus zero-duration scene instead of failing.
        raise SceneDetectionError("this proxy reports no readable duration or frame rate")

    manager = SceneManager()
    manager.add_detector(
        ContentDetector(
            threshold=recipe.threshold,
            min_scene_len=_min_scene_len_frames(recipe, proxy_fps),
        )
    )
    try:
        manager.detect_scenes(video)
    except (VideoOpenFailure, SeekError) as error:
        # Named: detection started reading frames and failed partway through -
        # a truncated proxy, or a decode error mid-file. Distinct from `_open`
        # above, which fails before a single frame is read.
        raise SceneDetectionError("scene detection failed partway through this proxy") from error

    # `start_in_scene=True`: without it, a clip with zero detected cuts - one
    # continuous take, which is a common and entirely valid shape for a single
    # exercise set - reports zero scenes instead of the one scene it actually
    # is (measured; see the session report). A caller receiving an empty list
    # for a perfectly good clip would have no boundary to sample a frame from
    # at all.
    scene_list = manager.get_scene_list(start_in_scene=True)

    boundaries: list[SceneBoundary] = []
    for index, (start, end) in enumerate(scene_list):
        start_seconds = float(start.seconds)
        end_seconds = float(end.seconds)
        boundaries.append(
            SceneBoundary(
                sequence_index=index,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                start_frame_source=round(start_seconds * fps_source),
                end_frame_source=round(end_seconds * fps_source),
            )
        )

    logger.debug(
        "scenes_detected",
        scene_count=len(boundaries),
        proxy_fps=proxy_fps,
        proxy_duration_seconds=duration_seconds,
    )
    return boundaries


__all__ = ["SceneDetectionError", "detect_scenes"]
