"""Measurements behind `make verify-03`. One subcommand per gate criterion.

Same split as `verify_02_checks.py`, for the same reason: `verify_03.sh` owns
PASS/FAIL/SKIP formatting, this owns the measuring, and several of Prompt 03's
criteria are not expressible as a shell one-liner - a mocked Gemini transport,
a per-frame colour-tag comparison, a wall-clock budget.

**This pass is written before Prompt 03's implementation exists.** Every
criterion below that needs `engine/repcut/analysis/` imports the exact module
path amendment 008 fixes (`docs/guide-amendments/008-...md`), so until Track A
lands - or lands with a different name than this gate calls - each one fails
with Python's own `ImportError`/`ModuleNotFoundError` - caught once, in
`main()`, and turned into a named `FAILED:` line - rather than a raw traceback.
That is the intended, correct state of this file today: see
`docs/reports/prompt-03.md` for which criteria are structurally ready to go
green the moment the import resolves, and which need a second look once the
real API shape exists.

Contract with the shell, unchanged from Prompt 02:

- exactly one ``MEASURED: <value>`` line on stdout, always, pass, fail or skip
- ``FAILED: <reason>`` on stdout for a failure, then exit 1
- ``SKIPPED: <reason>`` on stdout for a skip, then exit 2 - the same convention
  `check_plan_titles.py` already uses, so `verify_03.sh`'s `criterion()` reads
  it the same way
- exit 0 only when the criterion actually holds

No absolute path is ever printed: ``$DATA_DIR`` carries the OS username on this
machine (`.claude/rules/secrets.md`).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Protocol

import httpx

if TYPE_CHECKING:
    from repcut.media.metadata import MediaProperties

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_02_checks as v2  # noqa: E402 - after the sys.path insert it depends on

# A dummy value, never a real credential (`.claude/rules/secrets.md`). It exists
# so criterion 9 has something concrete to search *for* - a check that never
# configures a key cannot prove the key is absent from the output, it can only
# fail to notice one either way.
FIXTURE_GEMINI_KEY = "repcut-gate-fixture-key-not-real"

# Amendment 008 (docs/guide-amendments/008-...md) already quotes the guide's
# runtime line verbatim, as an accepted amendment - a short quotation to explain
# a resolution, which the plan-leak guard treats differently from bulk
# transcription (`docs/guide-amendments/006-...md`). Reusing that already-public
# figure here, rather than re-reading the private guide, keeps this file within
# the same boundary: "5 minutes for a ~10-minute (~15-clip) session" is public
# because Ashwin's own amendment made it so, not because this script consulted
# $REPCUT_GUIDE_PATH.
ANALYSIS_BUDGET_RATIO = 5.0 / 10.0  # analysis wall-clock <= this * footage duration

# Per `.claude/rules/testing.md`, cuts land within 40ms of the beat grid; scene
# boundaries derived from source frame timestamps get the same budget here.
MAX_BOUNDARY_ERROR_MS = 40.0


_USER_PATH = re.compile(r"[A-Za-z]:[\\/][Uu]sers[\\/][^\\/\s\"']+|/home/[^/\s\"']+")


def scrub(text: str) -> str:
    """Redact an absolute path carrying the OS username.

    Same pattern as `verify_02.sh`'s ``scrub()`` (criterion 9 reuses it by
    name, per this prompt's brief), applied here too because not every string
    printed by this module passes back through the shell wrapper - a Python
    ``ImportError`` for a module that exists but lacks the expected name
    includes the module's absolute file path verbatim (measured while
    ``sampler.py`` was mid-flight), and that is exactly the shape secrets.md
    forbids printing.
    """
    return _USER_PATH.sub("<HOME>", text)


def measured(value: str) -> None:
    print(f"MEASURED: {scrub(value)}")


def failed(reason: str) -> None:
    print(f"FAILED: {scrub(reason)}")


def skipped(reason: str) -> None:
    print(f"SKIPPED: {scrub(reason)}")


# --- fixtures not covered by verify_02_checks.make_clip ----------------------


def _write_hdr_tags(clip: Path) -> None:
    """Stamp BT.2020/HLG/bt2020nc onto ``clip``. Mirrors `engine/tests/conftest.py`.

    Duplicated rather than imported: the gate and the pytest suite are different
    processes with no fixture-sharing, which is also why `make_clip` itself is
    already duplicated between `conftest.py` and `verify_02_checks.py`. See
    `conftest.py`'s `_write_hdr_tags` for the measurement behind the exact
    flags - a stream copy alone was not trusted to be portable across FFmpeg
    versions, so this is a real (cheap, `ultrafast`) re-encode.
    """
    tagged = clip.with_name(f"hdr-{clip.name}")
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            clip.as_posix(),
            "-map",
            "0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "arib-std-b67",
            "-colorspace",
            "bt2020nc",
            "-color_range",
            "tv",
            "-x264-params",
            "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
            "-c:a",
            "copy",
            tagged.as_posix(),
        ],
        capture_output=True,
        check=True,
        timeout=120,
    )
    tagged.replace(clip)


def _build_motion_loudness_clip(
    destination: Path,
    *,
    segment_seconds: float = 2.0,
    fps: int = 30,
    width: int = 640,
    height: int = 360,
) -> Path:
    """Two segments, a hard cut, a motion step and a loudness step. See
    `engine/tests/conftest.py`'s `make_motion_loudness_clip` for the measured
    per-segment numbers this fixture is built to reproduce; duplicated here for
    the same reason as `_write_hdr_tags` above.
    """
    argv = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:size={width}x{height}:rate={fps}:duration={segment_seconds}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=220:sample_rate=44100:duration={segment_seconds}",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={width}x{height}:rate={fps}:duration={segment_seconds}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=880:sample_rate=44100:duration={segment_seconds}",
        "-filter_complex",
        ("[1:a]volume=0.02[a0];[3:a]volume=0.9[a1];[0:v][a0][2:v][a1]concat=n=2:v=1:a=1[v][a]"),
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "30",
        "-c:a",
        "aac",
        destination.as_posix(),
    ]
    subprocess.run(argv, capture_output=True, check=True, timeout=120)
    return destination


# --- probing and rendering without a running engine ---------------------------


def _prepare_media(clip: Path) -> tuple[Path, MediaProperties, str]:
    """Probe ``clip`` and render its proxy exactly as ingest would - no engine.

    Reuses Prompt 02's own code (`repcut.media.metadata.parse_probe`,
    `repcut.media.ffmpeg_builder.build_proxy`/`run`) rather than re-deriving
    display dimensions or a proxy recipe by hand, so this measures against the
    same rotation-aware, colour-explicit logic Prompt 02's gate already proved.
    Returns ``(proxy_path, MediaProperties, sha256)``.
    """
    from repcut.media import ffmpeg_builder
    from repcut.media.metadata import parse_probe

    document = v2.ffprobe_json(clip, "-show_format", "-show_streams")
    properties = parse_probe(document)
    digest = hashlib.sha256(clip.read_bytes()).hexdigest()
    proxy = clip.with_name(f"proxy-{clip.name}")
    command = ffmpeg_builder.build_proxy(
        clip,
        proxy,
        display_height=properties.display_height,
        duration_seconds=properties.duration_seconds,
    )
    asyncio.run(ffmpeg_builder.run(command))
    return proxy, properties, digest


def _image_dimensions(path: Path) -> tuple[int, int]:
    document = v2.ffprobe_json(
        path, "-select_streams", "v:0", "-show_entries", "stream=width,height"
    )
    stream = document["streams"][0]  # type: ignore[index]
    return int(stream["width"]), int(stream["height"])  # type: ignore[index]


def _video_pts_list(path: Path, *, stream: str = "v:0") -> list[float]:
    """Every frame's presentation timestamp, in seconds, in decode order."""
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            stream,
            "-show_entries",
            "frame=pts_time",
            "-of",
            "csv=p=0",
            path.as_posix(),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    # `csv=p=0` still emits a trailing comma per line (ffprobe leaves the frame
    # index column empty rather than dropping it) - measured against this
    # repo's FFmpeg the same way `engine/tests/test_conftest_fixtures.py` was.
    return [float(line.rstrip(",")) for line in completed.stdout.splitlines() if line.strip()]


# --- a mocked Gemini transport -------------------------------------------------


def _mock_gemini_transport(
    responses: Iterable[tuple[int, object]],
) -> tuple[httpx.MockTransport, list[dict[str, object]]]:
    """An in-process stand-in for Gemini's endpoint - the "transport seam" the
    criteria are measured against.

    ``responses`` is consumed one entry per request, in order; the last entry
    repeats for every request past the end of the list, so a check only has to
    queue as many distinct answers as it cares about. A ``bytes`` entry is sent
    verbatim (criterion 7's malformed JSON); anything else is JSON-encoded.

    Deliberately ``httpx.MockTransport`` rather than a local HTTP server: the
    six modules this prompt adds are a library, not a new route surface (Track
    B's UI reads their output through the existing job/analysis view, not
    through a mock-shaped API this gate would have to invent) - so injecting the
    transport the client itself uses is the seam that actually exists, and it
    captures exactly what would have crossed the wire, request body included.
    """
    queue = list(responses)
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            body = json.loads(request.content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {"_raw": request.content.decode("utf-8", errors="replace")}
        captured.append(body)
        index = min(len(captured), len(queue)) - 1
        status, content = queue[index] if queue else (200, {"candidates": []})
        if isinstance(content, (bytes, bytearray)):
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=content)

    return httpx.MockTransport(handler), captured


def _connection_refused_transport() -> httpx.MockTransport:
    """A transport that raises the way a dead network does, not a 4xx/5xx."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused (gate fixture)", request=request)

    return httpx.MockTransport(handler)


# --- criteria ------------------------------------------------------------------


def check_migrations() -> int:
    """1. upgrade/downgrade/upgrade, then the two new tables amendment 008 adds."""
    from sqlalchemy import create_engine as create_sync_engine
    from sqlalchemy import inspect

    with TemporaryDirectory(prefix="repcut-gate03-mig-", ignore_cleanup_errors=True) as scratch:
        data_dir = Path(scratch) / "data"
        data_dir.mkdir(parents=True)
        environment = v2.scratch_environment(data_dir)
        for step in ("upgrade head", "downgrade base", "upgrade head"):
            action, target = step.split()
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "engine/alembic.ini", action, target],
                cwd=REPO_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=180,
            )
            if result.returncode != 0:
                measured(f"alembic {step} -> exit {result.returncode}")
                failed(f"alembic {step} exited {result.returncode}")
                return 1

        engine = create_sync_engine(f"sqlite:///{(data_dir / 'repcut.db').as_posix()}")
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        cache_unique: list[set[str]] = []
        scenes_unique: list[set[str]] = []
        if "gemini_scene_cache" in tables:
            cache_unique = [
                set(c["column_names"])
                for c in inspector.get_unique_constraints("gemini_scene_cache")
            ]
        if "scenes" in tables:
            scenes_unique = [
                set(c["column_names"]) for c in inspector.get_unique_constraints("scenes")
            ]
        engine.dispose()

    expected_tables = {"scenes", "gemini_scene_cache"}
    missing_tables = expected_tables - tables

    # Amendment 008 and `.claude/skills/gemini-free-tier` both name the cache
    # key as `(video_hash, scene_id, prompt_version)` - but this codebase's own
    # established idiom for a composite content-addressed key is a UUID
    # surrogate `id` plus a *named unique constraint* over the real columns,
    # not a literal multi-column primary key (`derived_artifacts` /
    # `uq_derived_artifacts_key`, gated in Prompt 02). So this checks
    # `get_unique_constraints`, not `get_pk_constraint`, for either of two
    # shapes:
    #
    #  (a) a literal 3-column constraint naming something video/hash-shaped,
    #      a scene id, and a prompt version, or
    #  (b) a 2-column constraint on (scene id, prompt version) *if and only
    #      if* `scenes` itself has a unique constraint that scopes a scene id
    #      to one video (a hash column) - i.e. the video is still pinned
    #      down, just transitively through the scene FK rather than restated
    #      on every cache row.
    #
    # (b) is what the schema actually being built against this gate does
    # (`repcut/db/models.py`'s `GeminiSceneCache` docstring spells out the
    # reasoning) - accepted here rather than forced back to the letter of the
    # skill file, but the divergence is real and is called out in
    # docs/reports/prompt-03.md so it gets a guide amendment rather than
    # staying silent (`CLAUDE.md`: "never silently deviate from the guide").
    key_ok = False
    key_shape = "none found"
    for constraint in cache_unique:
        has_scene = any("scene" in c for c in constraint)
        has_prompt_version = any("prompt_version" in c for c in constraint)
        has_hash = any(c in ("video_hash", "sha256") or "hash" in c for c in constraint)
        if len(constraint) == 3 and has_scene and has_prompt_version and has_hash:
            key_ok = True
            key_shape = f"3-column {sorted(constraint)}"
            break
        if len(constraint) == 2 and has_scene and has_prompt_version:
            scoped = any(
                any("scene" not in c and ("hash" in c or c == "sha256") for c in scene_constraint)
                and any("scene" not in c for c in scene_constraint)  # not just re-matching itself
                for scene_constraint in scenes_unique
            )
            if scoped:
                key_ok = True
                key_shape = f"2-column {sorted(constraint)} + scenes scoped by {scenes_unique}"
                break
            key_shape = (
                f"2-column {sorted(constraint)}, but `scenes` has no hash-scoped "
                "unique constraint to imply video_hash"
            )

    measured(
        f"3 alembic steps ok, tables present={sorted(expected_tables & tables)}, "
        f"gemini_scene_cache unique constraints={[sorted(c) for c in cache_unique]}, "
        f"key shape={key_shape}"
    )
    if missing_tables:
        failed(f"missing tables {sorted(missing_tables)}")
        return 1
    if not key_ok:
        failed(
            "gemini_scene_cache has no composite key equivalent to (video_hash, scene_id, "
            "prompt_version) - directly or via a scene FK scoped to one video"
        )
        return 1
    return 0


def check_frame_source_dimensions() -> int:
    """2. The sampled frame's dimensions are the SOURCE's display dimensions."""
    from repcut.analysis.sampler import sample_scene_frame

    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = v2.make_clip(
            Path(scratch) / "portrait.mp4", seconds=2.0, width=1280, height=720, rotation=90
        )
        proxy, properties, digest = _prepare_media(clip)
        coded = _image_dimensions(clip)
        proxy_dims = _image_dimensions(proxy)

        frame_path = sample_scene_frame(
            data_dir=Path(scratch) / "data",
            sha256=digest,
            source_path=clip,
            start_seconds=0.0,
            end_seconds=properties.duration_seconds,
            sequence_index=0,
            display_width=properties.display_width,
            display_height=properties.display_height,
            rotation_degrees=properties.rotation_degrees,
        )
        sampled_dims = _image_dimensions(Path(frame_path))

    measured(
        f"source coded={coded} display={(properties.display_width, properties.display_height)} "
        f"proxy={proxy_dims} sampled={sampled_dims}"
    )
    expected = (properties.display_width, properties.display_height)
    if sampled_dims != expected:
        failed(f"sampled frame is {sampled_dims}, expected the display dimensions {expected}")
        return 1
    if sampled_dims == proxy_dims and proxy_dims != expected:
        failed("sampled frame matches the proxy's dimensions, not the source's")
        return 1
    return 0


class SceneResultLike(Protocol):
    """The shape one scene of `AnalysisResultLike.scenes` is expected to have.

    A `Protocol`, not `repcut.analysis.pipeline`'s real return type - that
    module does not exist yet. Spelling out the expected shape here (rather
    than typing the pipeline call `Any`) is what lets every criterion below
    that reads `.motion_energy`, `.vlm` and so on typecheck against something
    more specific than "anything", and documents this gate's specification for
    Track A's result shape in one place.
    """

    sequence_index: int
    start_seconds: float
    end_seconds: float
    start_frame_source: int
    end_frame_source: int
    sampled_frame_path: str
    motion_energy: float | None
    audio_energy: float | None
    vlm: dict[str, object] | None


class AnalysisResultLike(Protocol):
    """What `repcut.analysis.pipeline.run_analysis` is expected to return."""

    scenes: list[SceneResultLike]
    warning: str | None


async def _run_pipeline(
    clip: Path,
    *,
    scratch: Path,
    gemini_transport: httpx.BaseTransport | None,
    gemini_api_key: str | None = FIXTURE_GEMINI_KEY,
    gemini_rpm_limit: int = 60,
    gemini_daily_limit: int = 1000,
    gemini_prompt_version: int = 1,
) -> AnalysisResultLike:
    """Shared entry point for criteria 3-8 and 11-14.

    ``repcut.analysis.pipeline.run_analysis`` is this gate's specification for
    Track A's orchestration surface: one call, given a source, its rendered
    proxy and an injectable Gemini transport, returning one result carrying
    every scene's boundaries, energy and (possibly null) VLM description. The
    exact shape is negotiable - it is what Track A has not been built against
    yet - but the criteria below need *some* single seam to assert against, and
    a library entry point is the one amendment 008 already commits to ("a
    sampled frame is a pure function... not a project-folder file"; the same
    reasoning makes analysis a pure function of its inputs, not a route).
    """
    from repcut.analysis.pipeline import run_analysis

    proxy, properties, digest = _prepare_media(clip)
    return await run_analysis(
        data_dir=scratch,
        sha256=digest,
        source_path=clip,
        proxy_path=proxy,
        display_width=properties.display_width,
        display_height=properties.display_height,
        rotation_degrees=properties.rotation_degrees,
        fps_source=properties.fps_source,
        gemini_api_key=gemini_api_key,
        gemini_rpm_limit=gemini_rpm_limit,
        gemini_daily_limit=gemini_daily_limit,
        gemini_prompt_version=gemini_prompt_version,
        gemini_transport=gemini_transport,
    )


def _valid_scene_response(tag: str = "bench press") -> dict[str, object]:
    """A well-formed Gemini reply, shaped like the schema Track A validates."""
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {"exercise": tag, "environment": "home gym", "confidence": 0.9}
                            )
                        }
                    ]
                }
            }
        ]
    }


def check_one_frame_per_scene_leaves_the_machine() -> int:
    """3. N detected scenes -> N outbound image parts, no audio, no path."""
    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = _build_motion_loudness_clip(Path(scratch) / "motion.mp4", segment_seconds=1.5)
        transport, requests = _mock_gemini_transport([(200, _valid_scene_response())])
        result = asyncio.run(
            _run_pipeline(clip, scratch=Path(scratch) / "data", gemini_transport=transport)
        )

    scene_count = len(result.scenes)
    # `json.dumps` re-serialises every captured body, well-formed or not (a
    # malformed capture is stored as `{"_raw": "..."}`), so one substring count
    # over the whole dump covers both shapes without a separate branch.
    raw = json.dumps(requests)
    image_parts = raw.count("inline_data")
    audio_hits = raw.count('"audio/')
    filename_hit = clip.name in raw or scratch in raw

    measured(
        f"scenes={scene_count} requests={len(requests)} inline_data={image_parts} "
        f"audio_parts={audio_hits} filename_leaked={filename_hit}"
    )
    if scene_count < 2:
        failed(
            f"the motion/loudness fixture only produced {scene_count} scene(s); nothing to count"
        )
        return 1
    if len(requests) != scene_count:
        failed(f"{len(requests)} Gemini requests for {scene_count} scenes; expected one each")
        return 1
    if image_parts != scene_count:
        failed(
            f"{image_parts} image parts across {len(requests)} requests; expected exactly one each"
        )
        return 1
    if audio_hits:
        failed("an audio part crossed the transport seam - P4 forbids sending audio")
        return 1
    if filename_hit:
        failed("the clip's filename or scratch path appeared in a request body")
        return 1
    return 0


def check_repeat_run_costs_zero_calls() -> int:
    """4. Run twice; the second run makes zero requests, one cache hit per scene."""
    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = _build_motion_loudness_clip(Path(scratch) / "motion.mp4", segment_seconds=1.5)
        data_dir = Path(scratch) / "data"

        transport1, requests1 = _mock_gemini_transport([(200, _valid_scene_response())])
        first = asyncio.run(_run_pipeline(clip, scratch=data_dir, gemini_transport=transport1))

        transport2, requests2 = _mock_gemini_transport([(200, _valid_scene_response())])
        second = asyncio.run(_run_pipeline(clip, scratch=data_dir, gemini_transport=transport2))

    measured(
        f"run1 requests={len(requests1)} scenes={len(first.scenes)}; "
        f"run2 requests={len(requests2)} scenes={len(second.scenes)}"
    )
    if not requests1:
        failed("the first run made zero API calls; the fixture proves nothing about caching")
        return 1
    if requests2:
        failed(f"the second run made {len(requests2)} API calls; a repeat run must cost zero")
        return 1
    if len(second.scenes) != len(first.scenes):
        failed("the second run reported a different scene count than the first")
        return 1
    return 0


def check_prompt_version_invalidates() -> int:
    """5. Bumping `prompt_version` brings the calls back."""
    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = _build_motion_loudness_clip(Path(scratch) / "motion.mp4", segment_seconds=1.5)
        data_dir = Path(scratch) / "data"

        transport1, requests1 = _mock_gemini_transport([(200, _valid_scene_response())])
        asyncio.run(
            _run_pipeline(
                clip, scratch=data_dir, gemini_transport=transport1, gemini_prompt_version=1
            )
        )
        transport2, requests2 = _mock_gemini_transport([(200, _valid_scene_response())])
        asyncio.run(
            _run_pipeline(
                clip, scratch=data_dir, gemini_transport=transport2, gemini_prompt_version=1
            )
        )
        transport3, requests3 = _mock_gemini_transport([(200, _valid_scene_response())])
        asyncio.run(
            _run_pipeline(
                clip, scratch=data_dir, gemini_transport=transport3, gemini_prompt_version=2
            )
        )

    measured(f"v1 first={len(requests1)} v1 repeat={len(requests2)} v2 after bump={len(requests3)}")
    if not requests1:
        failed("the first run at prompt_version=1 made zero calls")
        return 1
    if requests2:
        failed(f"the unchanged repeat made {len(requests2)} calls; caching is not working")
        return 1
    if not requests3:
        failed("bumping prompt_version made zero calls; the cache key is not versioned")
        return 1
    return 0


def check_limiter_fails_closed() -> int:
    """6. Bucket exhausted -> zero requests, asserted against the transport."""
    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = _build_motion_loudness_clip(Path(scratch) / "motion.mp4", segment_seconds=1.5)
        transport, requests = _mock_gemini_transport([(200, _valid_scene_response())])
        result = asyncio.run(
            _run_pipeline(
                clip,
                scratch=Path(scratch) / "data",
                gemini_transport=transport,
                gemini_rpm_limit=0,
                gemini_daily_limit=0,
            )
        )

    measured(f"scenes={len(result.scenes)} requests={len(requests)}")
    if len(result.scenes) < 2:
        failed("the fixture did not produce enough scenes to make this measurement meaningful")
        return 1
    if requests:
        failed(f"the limiter let {len(requests)} request(s) through with the bucket at zero")
        return 1
    if any(scene.vlm is not None for scene in result.scenes):
        failed("a scene has a VLM result despite zero requests being made")
        return 1
    return 0


def check_malformed_json_handled() -> int:
    """7. Garbage response -> exactly one retry -> `vlm: null`, exit 0."""
    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = v2.make_clip(Path(scratch) / "clip.mp4", seconds=2.0)
        garbage = b"not json at all {{{"
        transport, requests = _mock_gemini_transport([(200, garbage), (200, garbage)])
        result = asyncio.run(
            _run_pipeline(clip, scratch=Path(scratch) / "data", gemini_transport=transport)
        )

    measured(
        f"requests={len(requests)} scenes={len(result.scenes)} vlm={[s.vlm for s in result.scenes]}"
    )
    if len(requests) != 2:
        failed(f"{len(requests)} requests for one scene of garbage; expected exactly one retry (2)")
        return 1
    if any(scene.vlm is not None for scene in result.scenes):
        failed("a scene has a non-null VLM result from two malformed responses")
        return 1
    return 0


def check_offline_completes() -> int:
    """8. A connection error -> pipeline finishes, exit 0, `vlm: null`, a warning."""
    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = _build_motion_loudness_clip(Path(scratch) / "motion.mp4", segment_seconds=1.5)
        result = asyncio.run(
            _run_pipeline(
                clip,
                scratch=Path(scratch) / "data",
                gemini_transport=_connection_refused_transport(),
            )
        )

    local_features_present = all(
        scene.motion_energy is not None and scene.audio_energy is not None
        for scene in result.scenes
    )
    measured(
        f"scenes={len(result.scenes)} local_features={local_features_present} "
        f"vlm_all_null={all(s.vlm is None for s in result.scenes)} warning={result.warning!r}"
    )
    if not result.scenes:
        failed(
            "no scenes were produced offline; local (non-Gemini) analysis should not depend on it"
        )
        return 1
    if not local_features_present:
        failed("motion/audio energy is missing offline - only the Gemini call should degrade")
        return 1
    if any(scene.vlm is not None for scene in result.scenes):
        failed("a scene has a VLM result despite every request failing to connect")
        return 1
    if not result.warning:
        failed("the pipeline completed offline with no warning for the UI to render")
        return 1
    return 0


def check_no_key_leak() -> int:
    """9. The key's value in no log/report/fixture/error payload, nor a user path.

    ``redirect_stdout``/``redirect_stderr`` only catch writes made through
    ``sys.stdout``/``sys.stderr`` at call time. If Track A configures
    ``structlog`` to hold its own reference to the real stream (rather than
    looking it up per call, which is the stdlib ``logging`` default and worth
    checking `repcut/logging.py` still does), this check would need a
    ``structlog.testing.capture_logs()`` block added alongside it - noted here
    because it is exactly the kind of gap that makes a leak check pass for the
    wrong reason.
    """
    stdout, stderr = io.StringIO(), io.StringIO()
    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = _build_motion_loudness_clip(Path(scratch) / "motion.mp4", segment_seconds=1.5)
        # A malformed response and a dead transport both exercise error-path
        # logging - the paths most likely to interpolate something they should
        # not, which is exactly why criterion 9 needs more than the happy path.
        garbage_transport, _ = _mock_gemini_transport([(200, b"not json {{{")])
        with redirect_stdout(stdout), redirect_stderr(stderr):
            asyncio.run(
                _run_pipeline(
                    clip, scratch=Path(scratch) / "data", gemini_transport=garbage_transport
                )
            )
            asyncio.run(
                _run_pipeline(
                    clip,
                    scratch=Path(scratch) / "data2",
                    gemini_transport=_connection_refused_transport(),
                )
            )

    captured = stdout.getvalue() + stderr.getvalue()
    key_leaked = FIXTURE_GEMINI_KEY in captured
    # Same shape as verify_02.sh's scrub(): a Windows or POSIX home directory
    # naming the OS user.
    path_pattern = re.compile(r"[A-Za-z]:[\\/][Uu]sers[\\/][^\\/\s\"']+|/home/[^/\s\"']+")
    path_hit = path_pattern.search(captured)

    measured(
        f"captured_bytes={len(captured)} key_leaked={key_leaked} user_path_leaked={bool(path_hit)}"
    )
    if key_leaked:
        failed("the fixture Gemini key's value appeared in stdout/stderr")
        return 1
    if path_hit:
        start = max(0, path_hit.start() - 20)
        snippet = path_pattern.sub("<HOME>", captured[start : path_hit.end() + 10])
        failed(f"an absolute user path appeared in output: {snippet!r}")
        return 1
    return 0


def check_frame_carries_no_metadata() -> int:
    """10. ffprobe of a sampled frame: no EXIF, no GPS, no timed metadata."""
    from repcut.analysis.sampler import sample_scene_frame

    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = v2.make_clip(Path(scratch) / "clip.mp4", seconds=2.0, rotation=90)
        # Only the source is sampled from (amendment 008 resolution 3); the
        # rendered proxy plays no part in this criterion and is discarded.
        _proxy, properties, digest = _prepare_media(clip)
        frame_path = sample_scene_frame(
            data_dir=Path(scratch) / "data",
            sha256=digest,
            source_path=clip,
            start_seconds=0.0,
            end_seconds=properties.duration_seconds,
            sequence_index=0,
            display_width=properties.display_width,
            display_height=properties.display_height,
            rotation_degrees=properties.rotation_degrees,
        )
        document = v2.ffprobe_json(Path(frame_path), "-show_format", "-show_streams")

    streams = document.get("streams", [])
    tags: dict[str, object] = {}
    for stream in streams:  # type: ignore[union-attr]
        tags.update(stream.get("tags", {}))
    tags.update(document.get("format", {}).get("tags", {}))  # type: ignore[union-attr]
    side_data = [entry for stream in streams for entry in stream.get("side_data_list", [])]  # type: ignore[union-attr]
    suspect_tag_keys = [key for key in tags if re.search(r"gps|exif|location", key, re.IGNORECASE)]
    timed_streams = [s for s in streams if s.get("codec_type") not in ("video",)]  # type: ignore[union-attr]

    measured(
        f"streams={len(streams)} suspect_tags={suspect_tag_keys} "
        f"side_data={len(side_data)} non_video_streams={len(timed_streams)}"
    )
    if suspect_tag_keys:
        failed(f"the sampled frame carries GPS/EXIF-shaped tags: {suspect_tag_keys}")
        return 1
    if side_data:
        failed(
            f"the sampled frame carries {len(side_data)} side-data entr(y/ies) beyond the picture"
        )
        return 1
    if timed_streams:
        failed(f"the sampled frame has {len(timed_streams)} non-video stream(s)")
        return 1
    return 0


def check_frame_is_tone_mapped() -> int:
    """11. Against the HDR fixture: BT.709 out, mean luma in a sane band."""
    from repcut.analysis.sampler import sample_scene_frame

    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = v2.make_clip(Path(scratch) / "hdr.mp4", seconds=2.0, hdr=True)
        _proxy, properties, digest = _prepare_media(clip)
        frame_path = sample_scene_frame(
            data_dir=Path(scratch) / "data",
            sha256=digest,
            source_path=clip,
            start_seconds=0.0,
            end_seconds=properties.duration_seconds,
            sequence_index=0,
            display_width=properties.display_width,
            display_height=properties.display_height,
            rotation_degrees=properties.rotation_degrees,
        )
        colour = v2.ffprobe_json(
            Path(frame_path),
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=color_primaries,color_transfer,color_space",
        )["streams"][0]  # type: ignore[index]
        stats = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"movie={Path(frame_path).name},signalstats",
                "-show_entries",
                "frame_tags=lavfi.signalstats.YAVG",
                "-of",
                "csv=p=0",
            ],
            cwd=Path(frame_path).parent,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        mean_luma = float(stats.stdout.strip().rstrip(","))

    measured(
        f"primaries={colour.get('color_primaries')} transfer={colour.get('color_transfer')} "
        f"space={colour.get('color_space')} mean_luma={mean_luma:.1f}"
    )
    if colour.get("color_primaries") not in ("bt709", None):
        failed(f"sampled frame primaries are {colour.get('color_primaries')!r}, expected bt709")
        return 1
    if colour.get("color_transfer") not in ("bt709", None):
        failed(f"sampled frame transfer is {colour.get('color_transfer')!r}, expected bt709")
        return 1
    # An HLG signal read without a tone map crushes into the 0-30 range on an
    # SDR pipeline (measured on the untouched HDR fixture); a sane extract
    # should land in the mid-range testsrc2 actually occupies.
    if not (40.0 < mean_luma < 235.0):
        failed(f"mean luma {mean_luma:.1f} looks washed out or crushed, not tone-mapped")
        return 1
    return 0


def check_boundaries_survive_vfr() -> int:
    """12. Boundaries in seconds against the source map to a real source frame."""
    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = v2.make_clip(Path(scratch) / "vfr.mp4", seconds=4.0, variable_frame_rate=True)
        # The pipeline call first: it is what raises `ModuleNotFoundError` today,
        # and it should fail fast on that rather than on the ffprobe parsing below.
        result = asyncio.run(
            _run_pipeline(clip, scratch=Path(scratch) / "data", gemini_transport=None)
        )
        pts = _video_pts_list(clip)

    if not pts:
        measured("no source frame timestamps read")
        failed("could not read the VFR fixture's own frame timestamps to check against")
        return 1

    frame_duration_ms = (max(pts) / max(1, len(pts) - 1)) * 1000  # average cadence, not nominal fps
    errors_ms: list[float] = []
    for scene in result.scenes:
        for seconds, frame_index in (
            (scene.start_seconds, scene.start_frame_source),
            (scene.end_seconds, scene.end_frame_source),
        ):
            if not (0 <= frame_index < len(pts)):
                errors_ms.append(float("inf"))
                continue
            errors_ms.append(abs(pts[frame_index] - seconds) * 1000)

    max_error = max(errors_ms) if errors_ms else float("inf")
    measured(
        f"scenes={len(result.scenes)} avg_frame_duration={frame_duration_ms:.1f}ms "
        f"max_boundary_error={max_error:.1f}ms"
    )
    if not result.scenes:
        failed("no scenes were produced against the VFR fixture")
        return 1
    if max_error > max(MAX_BOUNDARY_ERROR_MS, frame_duration_ms):
        failed(
            f"max boundary error {max_error:.1f}ms exceeds one frame duration "
            f"({frame_duration_ms:.1f}ms) / the {MAX_BOUNDARY_ERROR_MS:.0f}ms budget"
        )
        return 1
    return 0


def check_energy_curves_not_flat() -> int:
    """13. Per-scene energy varies by a stated minimum across scenes."""
    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = _build_motion_loudness_clip(Path(scratch) / "motion.mp4", segment_seconds=2.0)
        result = asyncio.run(
            _run_pipeline(clip, scratch=Path(scratch) / "data", gemini_transport=None)
        )

    motion = [scene.motion_energy for scene in result.scenes]
    audio = [scene.audio_energy for scene in result.scenes]
    motion_spread = (max(motion) - min(motion)) if motion else 0.0
    audio_spread = (max(audio) - min(audio)) if audio else 0.0

    measured(
        f"scenes={len(result.scenes)} motion min={min(motion, default=0):.3f} "
        f"max={max(motion, default=0):.3f} spread={motion_spread:.3f}; "
        f"audio min={min(audio, default=0):.3f} max={max(audio, default=0):.3f} "
        f"spread={audio_spread:.3f}"
    )
    if len(result.scenes) < 2:
        failed(
            f"only {len(result.scenes)} scene(s) detected; the fixture has two "
            "deliberately unequal ones"
        )
        return 1
    # A stated minimum, not zero: a spread the noise floor could produce by
    # accident would make this criterion pass by luck rather than by design.
    if motion_spread <= 0.05:
        failed(f"motion energy spread {motion_spread:.3f} is not clearly non-flat")
        return 1
    if audio_spread <= 0.05:
        failed(f"audio energy spread {audio_spread:.3f} is not clearly non-flat")
        return 1
    return 0


def check_runtime_budget() -> int:
    """14. The guide's per-session budget (amendment 008), scaled to a synthetic clip."""
    footage_seconds = 20.0  # short: this still has to be a gate someone re-runs
    budget_seconds = footage_seconds * ANALYSIS_BUDGET_RATIO

    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = v2.make_clip(Path(scratch) / "budget.mp4", seconds=footage_seconds)
        transport, _ = _mock_gemini_transport([(200, _valid_scene_response())])
        start = time.monotonic()
        result = asyncio.run(
            _run_pipeline(clip, scratch=Path(scratch) / "data", gemini_transport=transport)
        )
        elapsed = time.monotonic() - start

    measured(
        f"footage={footage_seconds:.0f}s budget={budget_seconds:.1f}s elapsed={elapsed:.1f}s "
        f"scenes={len(result.scenes)}"
    )
    # This is a CPU-only measurement (amendment 008 §"Do not install torch" -
    # optical flow is CPU in this prompt), against the guide's figure which was
    # benchmarked on the target GPU laptop for a GPU-accelerated pipeline. A
    # CPU run on a synthetic clip is not the same claim, so a miss here is a
    # signal to re-check the ratio (`/guide-amend`), not an automatic FAIL of
    # the whole gate - hence SKIP rather than FAIL when it is exceeded.
    if elapsed > budget_seconds:
        skipped(
            f"{elapsed:.1f}s exceeds the {budget_seconds:.1f}s scaled budget on this (CPU-only, "
            "non-ROG) machine; re-check against real GPU hardware before treating this as a FAIL"
        )
        return 2
    return 0


def check_scripts_lint() -> int:
    """15. `ruff check scripts` clean, once `make lint` actually runs it."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    lint_lines: list[str] = []
    in_lint = False
    for line in makefile.splitlines():
        if line.startswith("lint:"):
            in_lint = True
            continue
        if in_lint:
            if line.startswith("\t"):
                lint_lines.append(line)
            else:
                break
    wired_in = any("ruff check" in line and "scripts" in line for line in lint_lines)

    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "scripts", "--statistics"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    finding_count = 0
    for line in result.stdout.splitlines():
        head = line.strip().split(None, 1)
        if head and head[0].isdigit():
            finding_count += int(head[0])

    # git diff against the last tagged prompt, not against main: the branch is
    # `prompt-03`, and `main` may be behind it once this branch merges. This
    # gate script itself is excluded: it is a brand-new file that legitimately
    # contains the noqa directive's own text in string literals (it searches
    # for that text in other files' diffs below), which is data, not a
    # directive - diffing it against a `prompt-02-done` state in which it did
    # not exist would count every occurrence as "added" for the wrong reason.
    diff = subprocess.run(
        [
            "git",
            "diff",
            "prompt-02-done...HEAD",
            "--",
            "scripts/*.py",
            ":(exclude)scripts/verify_03_checks.py",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    # An added noqa directive is only a problem when it is unjustified:
    # `run-prompt-03.md`'s own debt item says "fix OR JUSTIFY every finding...
    # do not add an ignore entry" - an ignore entry is `ignore = [...]` in
    # `pyproject.toml` (which would be silent and blanket), and a directive with
    # a reason beside the one line it excuses, matching the S603/S607
    # convention `ffmpeg_builder.py` already uses, is the justification the
    # debt item asks for. So this counts only a directive with neither a
    # trailing same-line reason nor a comment on the line(s) immediately before
    # it in the diff.
    diff_lines = diff.stdout.splitlines()
    unjustified_noqa: list[str] = []
    for index, line in enumerate(diff_lines):
        if not (line.startswith("+") and not line.startswith("+++") and "# noqa" in line):
            continue
        after_noqa = line.split("# noqa", 1)[-1]
        same_line_reason = "-" in after_noqa and bool(after_noqa.split("-", 1)[1].strip())
        # Any comment among the last few lines counts, not only the nearest
        # one: a block comment justifying two consecutive noqa'd lines at once
        # (`check_plan_leak.py`'s two RUF001s, one comment) is a real pattern
        # this codebase already uses, and only checking the single nearest
        # line would read the second of the pair as unexplained.
        preceding_is_comment = any(
            (prior[1:].strip() if prior[:1] in "+- " else prior.strip()).startswith("#")
            for prior in diff_lines[max(0, index - 5) : index]
        )
        if not (same_line_reason or preceding_is_comment):
            unjustified_noqa.append(line.strip())
    added_noqa = len(unjustified_noqa)

    if not wired_in:
        measured(
            f"make lint does not check scripts/ yet; ruff currently finds {finding_count} issue(s)"
        )
        skipped(
            "scripts/ has a ruff config (pyproject.toml) but the debt item - wiring it into "
            "`make lint` and fixing every finding - has not landed yet (run-prompt-03.md, "
            "'Two debt items folded in')"
        )
        return 2

    measured(
        f"ruff check scripts -> exit {result.returncode}, {finding_count} finding(s), "
        f"+{added_noqa} noqa"
    )
    if result.returncode != 0:
        failed(f"ruff check scripts found {finding_count} issue(s)")
        return 1
    if added_noqa:
        failed(f"{added_noqa} new `# noqa` line(s) added to scripts/ since prompt-02-done")
        return 1
    return 0


def _send_ctrl_c_windows(pid: int) -> None:
    """Deliver a real ``CTRL_C_EVENT`` to another process, on Windows.

    ``GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)`` broadcasts to *every* process
    sharing the caller's console - Windows will not target ``CTRL_C_EVENT`` at
    any other group, which is exactly what a real terminal's Ctrl-C does too
    (everything attached to that console gets it at once, this gate script's
    own process included, since the launcher below is spawned without
    ``CREATE_NEW_CONSOLE``/``CREATE_NEW_PROCESS_GROUP`` and so shares it). This
    process's own handling is disabled first via ``signal.signal(SIGINT,
    SIG_IGN)`` so only the target actually reacts.

    ``os.kill(pid, signal.CTRL_C_EVENT)`` is the documented stdlib spelling of
    the same call and was tried first; on the machine this was built on it
    delivered nothing at all, traced to this process itself having no attached
    console (`GetConsoleWindow() == 0`, checked by the caller before this
    function is ever reached) - not a bug in the call itself.

    ``CTRL_BREAK_EVENT`` would let the event be targeted at an isolated process
    group instead of broadcasting - but Windows' default action for it is
    immediate termination, not ``KeyboardInterrupt``. Sending it would kill
    ``posix_shell.py`` without ever reaching the handler this criterion exists
    to check.
    """
    import ctypes
    import signal

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    ctrl_c_event = 0

    print(
        f"[ctrl-c-clean] broadcasting CTRL_C_EVENT on the shared console (target pid={pid})",
        file=sys.stderr,
    )
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        if not kernel32.GenerateConsoleCtrlEvent(ctrl_c_event, 0):
            raise OSError(f"GenerateConsoleCtrlEvent failed: error {ctypes.get_last_error()}")
        time.sleep(0.5)  # let the event actually deliver before restoring our own handler
    finally:
        signal.signal(signal.SIGINT, previous)


def check_ctrl_c_clean() -> int:
    """16. `make dev` interrupted returns 130, no traceback on stdout or stderr.

    Spawns the real entry point `make dev` uses - `python scripts/posix_shell.py
    scripts/dev.sh`, not `dev.sh` directly - because the bug this criterion
    guards (run-prompt-03.md open issue 7) is specifically a ``KeyboardInterrupt``
    escaping ``posix_shell.py``'s own ``subprocess.call``, which a test that only
    signals ``dev.sh`` would never exercise at all.
    """
    posix_shell_source = (REPO_ROOT / "scripts" / "posix_shell.py").read_text(encoding="utf-8")
    landed = "except KeyboardInterrupt" in posix_shell_source

    if not landed:
        measured("scripts/posix_shell.py has no KeyboardInterrupt handler yet")
        skipped(
            "the Ctrl-C fix (run-prompt-03.md open issue 7: `except KeyboardInterrupt: return 130` "
            "in scripts/posix_shell.py's subprocess.call) has not landed yet"
        )
        return 2

    # A Windows Ctrl-C is a *console* signal (`GenerateConsoleCtrlEvent`), and
    # delivering one to another process requires this process to be attached to
    # a real console itself. Measured on the machine this gate was built on:
    # spawned from this agent harness's shell, `kernel32.GetConsoleWindow()`
    # returns 0 - there is no console to attach from - and every delivery
    # mechanism tried (`os.kill(..., CTRL_C_EVENT)` on the shared console,
    # `AttachConsole`+`GenerateConsoleCtrlEvent` against an isolated one) either
    # silently delivered nothing or killed the target outright
    # (`STATUS_CONTROL_C_EXIT`, 0xC000013A) rather than letting Python raise
    # `KeyboardInterrupt` the way a real terminal's Ctrl-C does. That is a
    # property of *this sandboxed shell*, not evidence about `posix_shell.py`'s
    # fix - so this SKIPs rather than reporting a confusing FAIL for an OS/
    # terminal interaction the code under test has no part in. Run this
    # criterion from a real terminal (`make verify-03`, or `python
    # scripts/verify_03_checks.py ctrl-c-clean` directly, from cmd.exe or
    # PowerShell) to get a real answer.
    if sys.platform == "win32":
        import ctypes

        has_console = bool(ctypes.windll.kernel32.GetConsoleWindow())  # type: ignore[attr-defined]
        if not has_console:
            measured("this process has no attached console (GetConsoleWindow() == 0)")
            skipped(
                "cannot deliver a real Ctrl-C without a console attached to this process - "
                "run `make verify-03` from an actual terminal (cmd.exe/PowerShell), not this "
                "sandboxed shell, to exercise this criterion for real"
            )
            return 2

    # Import deferred: dev_stack starts real servers, and every other criterion
    # in this file would pay for importing it even when this one SKIPs above.
    import signal

    import dev_stack

    stack = dev_stack.DevStack()  # port/env plumbing only; never call stack.start()
    launcher: subprocess.Popen[str] | None = None
    try:
        # No special creation flags: the launcher shares this process's console
        # on purpose (see `_send_ctrl_c_windows`), which is also what actually
        # happens when a person runs `make dev` in their own terminal.
        #
        # The exact command `make dev` runs, from the repo root - `posix_shell.py`
        # resolves its own script path via `__file__` but runs the CHILD (bash)
        # with `cwd=REPO_ROOT`, so the argument has to be repo-relative
        # (`scripts/dev.sh`), not relative to wherever this launcher is spawned.
        launcher = subprocess.Popen(
            [sys.executable, "scripts/posix_shell.py", "scripts/dev.sh"],
            cwd=REPO_ROOT,
            env=stack.environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        print("[ctrl-c-clean] waiting for the stack to become ready...", file=sys.stderr)
        deadline = time.monotonic() + dev_stack.STACK_READY_TIMEOUT_S
        ready = False
        while time.monotonic() < deadline:
            if launcher.poll() is not None:
                break
            if dev_stack.port_open(stack.engine_port) and dev_stack.port_open(stack.ui_port):
                ready = True
                break
            time.sleep(0.5)
        if not ready:
            measured("stack did not become ready through posix_shell.py")
            failed("could not reach a ready state through the real `make dev` entry point")
            return 1
        print("[ctrl-c-clean] ready; sending Ctrl-C...", file=sys.stderr)

        # A watchdog, not just the two timeouts below: `_send_ctrl_c_windows`'s
        # console-attach calls are a blocking Win32 API with no timeout
        # parameter of their own, so a wedged console (measured to happen at
        # least once while this check was being built) would otherwise hang the
        # whole gate rather than fail this one criterion.
        import threading

        def _hard_kill() -> None:
            print("[ctrl-c-clean] watchdog fired; killing the launcher", file=sys.stderr)
            if launcher is not None and launcher.poll() is None:
                dev_stack.kill_pid_tree(launcher.pid)

        watchdog = threading.Timer(dev_stack.STACK_STOP_TIMEOUT_S + 30.0, _hard_kill)
        watchdog.daemon = True
        watchdog.start()
        try:
            if sys.platform == "win32":
                _send_ctrl_c_windows(launcher.pid)
            else:
                launcher.send_signal(signal.SIGINT)
            print("[ctrl-c-clean] Ctrl-C sent; waiting for exit...", file=sys.stderr)

            try:
                stdout, stderr = launcher.communicate(timeout=dev_stack.STACK_STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                launcher.kill()
                stdout, stderr = launcher.communicate()
                measured("launcher did not exit after Ctrl-C")
                failed("scripts/posix_shell.py did not exit after Ctrl-C within the timeout")
                return 1
        finally:
            watchdog.cancel()
    finally:
        if launcher is not None and launcher.poll() is None:
            dev_stack.kill_pid_tree(launcher.pid)
        stack.close()  # also reclaims engine_port/ui_port if anything is still listening

    code = launcher.returncode
    has_traceback = "Traceback (most recent call last)" in (stdout + stderr)
    measured(f"exit={code} traceback_in_output={has_traceback}")
    if has_traceback:
        failed("Ctrl-C produced a traceback on stdout or stderr")
        return 1
    if code != 130:
        failed(f"exit code was {code}, expected 130")
        return 1
    return 0


def check_end_to_end_analysis() -> int:
    """17. Upload a fixture clip against a real `make dev` stack; see the analysis.

    Follows `verify_02_checks.check_assembled_stack`'s pattern exactly: a real
    `DevStack`, a real project, a real browser via `cdp_browser.inspect_page` -
    the same CDP harness, not a second one. The only new step is waiting for
    analysis (not just ingest) to finish and asserting what a person would
    actually see: scene tags, an energy sparkline, the P4 disclosure text.

    Track B does not exist yet in this pass, so this is expected to FAIL with a
    named, specific reason (no scene tags rendered) rather than crash - the
    stack itself, upload and ingest are all real Prompt 02 behaviour and should
    keep working throughout.
    """
    import dev_stack
    from cdp_browser import BrowserNotFoundError, inspect_page

    with (
        dev_stack.DevStack() as stack,
        TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch,
    ):
        stack.start()
        if not stack.wait_ready():
            measured("stack did not start")
            failed("`make dev` never reached both ports; see the launcher output")
            return 1

        clip = _build_motion_loudness_clip(Path(scratch) / "motion.mp4", segment_seconds=1.5)
        project_name = "prompt-03 e2e"
        status, project = stack.engine_request("POST", "/projects", {"name": project_name})
        if status != 201 or not isinstance(project, dict):
            measured(f"POST /projects -> HTTP {status}")
            failed("could not create a project against the running stack")
            return 1
        project_id = str(project["id"])

        finalized = v2.upload(stack.engine_port, project_id, clip)
        if not finalized.get("sha256"):
            measured(f"upload -> {finalized}")
            failed("uploading the fixture clip against the running stack failed")
            return 1

        v2.await_jobs(stack.engine_port)
        # Analysis is triggered explicitly (criteria 3-8's own assumption:
        # `POST /media/{id}/analyze`), tolerated as a 404 here in case Track B
        # instead triggers it automatically on ingest or on first page view -
        # either way, `await_jobs` below is what actually waits for it.
        media_id = None
        _, library = stack.engine_request("GET", f"/projects/{project_id}/media")
        if isinstance(library, list) and library:
            first = library[0]
            if isinstance(first, dict):
                media_id = first.get("id")
        if media_id is not None:
            stack.engine_request("POST", f"/media/{media_id}/analyze")
        v2.await_jobs(stack.engine_port)

        page_status = stack.ui_get(f"/projects/{project_id}")
        if page_status != 200:
            measured(f"editor page -> HTTP {page_status}")
            failed("the per-clip project page did not render")
            return 1

        page_url = f"http://localhost:{stack.ui_port}/projects/{project_id}"
        try:
            report = asyncio.run(inspect_page(page_url, observe_seconds=15.0))
        except BrowserNotFoundError as error:
            measured("no browser")
            failed(f"cannot assert the analysis view without a browser: {error}")
            return 1

    body = report.body_text
    # Loose, deliberately: Track B's exact copy is not this gate's business to
    # pin down. "some scene-shaped label" is a Laplacian-sharp stand-in for
    # "the per-clip view rendered analysis output" without hard-coding the
    # words a future component happens to use.
    has_scene_tags = bool(re.search(r"scene\s*\d|exercise|environment", body, re.IGNORECASE))
    has_sparkline = bool(re.search(r"energy|sparkline", body, re.IGNORECASE))
    has_disclosure = bool(
        re.search(r"sent to gemini|sampled frame.*gemini|gemini.*frame", body, re.IGNORECASE)
    )

    measured(
        f"scene_tags={has_scene_tags} sparkline={has_sparkline} disclosure={has_disclosure} "
        f"csp_violations={len(report.csp_violations)}"
    )
    if report.csp_violations:
        failed(f"the browser refused a request: {report.csp_violations[0][:140]}")
        return 1
    missing = [
        name
        for name, present in (
            ("scene tags", has_scene_tags),
            ("energy sparkline", has_sparkline),
            ("P4 disclosure text", has_disclosure),
        )
        if not present
    ]
    if missing:
        failed(f"the per-clip view is missing: {', '.join(missing)} (Track B not implemented yet)")
        return 1
    return 0


CHECKS: dict[str, Callable[[], int]] = {
    "migrations": check_migrations,
    "end-to-end-analysis": check_end_to_end_analysis,
    "frame-source": check_frame_source_dimensions,
    "one-frame-per-scene": check_one_frame_per_scene_leaves_the_machine,
    "repeat-run-zero-calls": check_repeat_run_costs_zero_calls,
    "prompt-version-invalidates": check_prompt_version_invalidates,
    "limiter-fails-closed": check_limiter_fails_closed,
    "malformed-json": check_malformed_json_handled,
    "offline-completes": check_offline_completes,
    "no-key-leak": check_no_key_leak,
    "frame-no-metadata": check_frame_carries_no_metadata,
    "frame-tone-mapped": check_frame_is_tone_mapped,
    "boundaries-survive-vfr": check_boundaries_survive_vfr,
    "energy-not-flat": check_energy_curves_not_flat,
    "runtime-budget": check_runtime_budget,
    "scripts-lint": check_scripts_lint,
    "ctrl-c-clean": check_ctrl_c_clean,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in CHECKS:
        print(f"usage: {Path(__file__).name} <{'|'.join(CHECKS)}>", file=sys.stderr)
        return 2
    try:
        return CHECKS[sys.argv[1]]()
    except ImportError as error:
        # The expected state of most criteria in this file until Track A lands.
        # Two shapes, both caught here rather than as a bare traceback: the
        # whole module missing (`ModuleNotFoundError`, e.g. `repcut.analysis.
        # pipeline` before it exists at all) and a module that exists but does
        # not yet export the name this gate calls (plain `ImportError` -
        # `cannot import name 'sample_scene_frame' from 'repcut.analysis.
        # sampler'`, measured while `sampler.py` was mid-flight and had the
        # module but not yet that function). Both mean the same thing: the
        # piece this criterion needs is not there in the shape expected yet.
        measured(f"import failed: {error}")
        failed(f"{error} - not implemented yet, or not in the shape this gate expects (Track A)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
