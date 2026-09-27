"""Measurements behind `make verify-03`. One subcommand per gate criterion.

Same split as `verify_02_checks.py`, for the same reason: `verify_03.sh` owns
PASS/FAIL/SKIP formatting, this owns the measuring, and several of Prompt 03's
criteria are not expressible as a shell one-liner - a mocked Gemini transport,
a per-frame colour-tag comparison, a wall-clock budget.

**Reconciliation pass**: Track A and Track B have both landed. Every criterion
below is now written against the real, shipped API - `repcut.analysis.pipeline
.run_analysis(context: JobContext)`, `.sampler.pick_frame`, `.scenes
.detect_scenes`, `.motion.compute_scene_energy`, `.gemini_client`/`.cache
.analyze_scene_cached` - not the first pass's guessed signatures. Criteria
3-9, 12-14 drive the real job pipeline **in-process**, the same way
`engine/tests/test_analysis_pipeline.py` does: a real `start_engine`/
`stop_engine` against a scratch `$DATA_DIR`, `httpx.ASGITransport`, and
`repcut.analysis.pipeline._build_http_client` monkeypatched to an
`httpx.MockTransport` before any upload - never a live Gemini call
(`.claude/rules/testing.md`). Criterion 17 alone drives the real assembled
`make dev` stack, because that is the one criterion this prompt owes
(`docs/prompts/run-prompt-03.md`) and nothing in-process can stand in for it.

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
import os
import re
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout, suppress
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from repcut.config import Settings
    from repcut.jobs import JobQueue
    from repcut.media.metadata import MediaProperties
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
    printed by this module passes back through the shell wrapper.
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
    """Stamp BT.2020/HLG/bt2020nc onto ``clip``. Mirrors `engine/tests/conftest.py`."""
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
    """Two segments, a hard cut, a motion step and a loudness step.

    See `engine/tests/conftest.py`'s `make_motion_loudness_clip` for the
    measured per-segment numbers this fixture reproduces.
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


# --- probing without a running engine -----------------------------------------


def _prepare_media(clip: Path) -> tuple[Path, MediaProperties, str]:
    """Probe ``clip`` and render its proxy exactly as ingest would - no engine.

    Reuses Prompt 02's own code (`repcut.media.metadata.parse_probe`,
    `repcut.media.ffmpeg_builder.build_proxy`/`run`). Returns
    ``(proxy_path, MediaProperties, sha256)``.
    """
    from repcut.media import ffmpeg_builder
    from repcut.media.metadata import parse_color_properties, parse_probe

    document = v2.ffprobe_json(clip, "-show_format", "-show_streams")
    properties = parse_probe(document)
    # Colour too, as `media/ingest.py` passes it: without it an HDR fixture's
    # "proxy" skips the v2 normalisation ingest actually applies.
    colour = parse_color_properties(document)
    digest = hashlib.sha256(clip.read_bytes()).hexdigest()
    proxy = clip.with_name(f"proxy-{clip.name}")
    command = ffmpeg_builder.build_proxy(
        clip,
        proxy,
        display_width=properties.display_width,
        display_height=properties.display_height,
        color_primaries=colour.color_primaries,
        color_transfer=colour.color_transfer,
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
    # index column empty rather than dropping it).
    return [float(line.rstrip(",")) for line in completed.stdout.splitlines() if line.strip()]


# --- the real engine, in-process (mirrors engine/tests/test_analysis_pipeline.py) --


@dataclass(frozen=True, slots=True)
class Engine:
    client: httpx.AsyncClient
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    queue: JobQueue
    data_dir: Path


@asynccontextmanager
async def in_process_engine(
    data_dir: Path,
    *,
    gemini_api_key: str | None = FIXTURE_GEMINI_KEY,
    gemini_rpm_limit: int = 10,
    gemini_daily_limit: int = 1400,
) -> AsyncIterator[Engine]:
    """A real engine - real migrations, real job worker - over a scratch DB.

    Mirrors `engine/tests/test_analysis_pipeline.py`'s own `api` fixture
    override: a Gemini key is configured by default so `cache.py`'s "no key
    configured" branch is not what a criterion measures by accident, and
    every criterion that uses this must monkeypatch
    `repcut.analysis.pipeline._build_http_client` before its first upload, so
    no job can ever reach a real Gemini call.
    """
    from pydantic import SecretStr
    from repcut.config import Settings
    from repcut.main import app, start_engine, stop_engine

    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite+aiosqlite:///{(data_dir.parent / 'engine.db').as_posix()}",
        gemini_api_key=SecretStr(gemini_api_key) if gemini_api_key else None,
        gemini_rpm_limit=gemini_rpm_limit,
        gemini_daily_limit=gemini_daily_limit,
    )
    await start_engine(app, settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        yield Engine(
            client=client,
            settings=settings,
            session_factory=app.state.session_factory,
            queue=app.state.job_queue,
            data_dir=settings.data_dir,
        )
    await stop_engine(app)


class _PatchedTransport:
    """Swap `pipeline._build_http_client` for the duration of a `with` block."""

    def __init__(self, transport: httpx.BaseTransport) -> None:
        self._transport = transport
        self._original: Callable[[], httpx.AsyncClient] | None = None

    def __enter__(self) -> None:
        from repcut.analysis import pipeline

        self._original = pipeline._build_http_client
        pipeline._build_http_client = lambda: httpx.AsyncClient(transport=self._transport)

    def __exit__(self, *_exc: object) -> None:
        from repcut.analysis import pipeline

        if self._original is not None:
            pipeline._build_http_client = self._original


async def upload_clip(engine: Engine, project_id: str, path: Path) -> dict[str, object]:
    """Declare, chunk, finalize - the browser's sequence, over the ASGI transport."""
    payload = await asyncio.to_thread(path.read_bytes)
    digest = hashlib.sha256(payload).hexdigest()
    chunk_size = 1 << 16
    opened = await engine.client.post(
        f"/projects/{project_id}/uploads",
        json={
            "display_name": path.name,
            "size_bytes": len(payload),
            "chunk_size_bytes": chunk_size,
            "sha256": digest,
        },
    )
    opened.raise_for_status()
    body = opened.json()
    upload_id = body["id"]
    offset = int(body["bytes_received"])
    while offset < len(payload):
        chunk = payload[offset : offset + chunk_size]
        sent = await engine.client.put(
            f"/uploads/{upload_id}/chunk", params={"offset": offset}, content=chunk
        )
        sent.raise_for_status()
        offset = int(sent.json()["bytes_received"])
    finalized = await engine.client.post(f"/uploads/{upload_id}/finalize")
    finalized.raise_for_status()
    result: dict[str, object] = finalized.json()
    return result


async def new_project(engine: Engine, name: str) -> str:
    response = await engine.client.post("/projects", json={"name": name})
    response.raise_for_status()
    return str(response.json()["id"])


async def _scenes(engine: Engine, sha256: str) -> list[object]:
    from repcut.analysis.params import SCENE_PARAMS_VERSION
    from repcut.db.models import Scene
    from sqlalchemy import select

    async with engine.session_factory() as session:
        statement = (
            select(Scene)
            .where(Scene.sha256 == sha256, Scene.detector_params_version == SCENE_PARAMS_VERSION)
            .order_by(Scene.sequence_index)
        )
        return list((await session.execute(statement)).scalars().all())


async def _cache_rows(engine: Engine, scene_ids: list[str]) -> list[object]:
    from repcut.db.models import GeminiSceneCache
    from sqlalchemy import select

    if not scene_ids:
        return []
    async with engine.session_factory() as session:
        statement = select(GeminiSceneCache).where(GeminiSceneCache.scene_id.in_(scene_ids))
        return list((await session.execute(statement)).scalars().all())


async def _latest_job(engine: Engine, sha256: str, job_type: str) -> object:
    from repcut.db.models import Job
    from sqlalchemy import select

    async with engine.session_factory() as session:
        statement = (
            select(Job)
            .where(Job.sha256 == sha256, Job.job_type == job_type)
            .order_by(Job.created_at.desc())
        )
        job = (await session.execute(statement)).scalars().first()
    if job is None:
        raise RuntimeError(f"no {job_type} job was ever enqueued for this clip")
    return job


async def _ingest_and_analyze(engine: Engine, clip: Path) -> str:
    """Upload a clip and wait for its auto-enqueued ingest and analysis. Returns the sha256."""
    from repcut.analysis.pipeline import ANALYSIS_JOB_TYPE
    from repcut.db.models import JobStatus

    project_id = await new_project(engine, "gate")
    finalized = await upload_clip(engine, project_id, clip)
    await engine.queue.drain()
    digest = str(finalized["sha256"])

    ingest_job = await _latest_job(engine, digest, "ingest")
    if ingest_job.status != JobStatus.SUCCEEDED:  # type: ignore[attr-defined]
        raise RuntimeError(f"ingest did not succeed: {ingest_job.error}")  # type: ignore[attr-defined]
    analysis_job = await _latest_job(engine, digest, ANALYSIS_JOB_TYPE)
    if analysis_job.status != JobStatus.SUCCEEDED:  # type: ignore[attr-defined]
        raise RuntimeError(f"analysis did not succeed: {analysis_job.error}")  # type: ignore[attr-defined]
    return digest


# --- a mocked Gemini transport -------------------------------------------------


def _gemini_response(document: dict[str, object]) -> dict[str, object]:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(document)}]}}]}


def _mock_gemini_transport(
    responses: Iterable[tuple[int, object]],
) -> tuple[httpx.MockTransport, list[dict[str, object]]]:
    """An in-process stand-in for Gemini's endpoint - the "transport seam" the
    criteria are measured against. Same shape as `test_analysis_pipeline.py`'s
    own `_mock_transport`, duplicated for the same reason that file gives for
    duplicating it from `test_gemini_client.py`/`test_gemini_cache.py`.
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
    # key as `(video_hash, scene_id, prompt_version)` - the shipped schema
    # uses a UUID surrogate `id` plus a named unique constraint over
    # `(scene_id, gemini_prompt_version)`, folding `video_hash` into the FK to
    # a `scenes` row that is itself uniquely scoped to one blob's sha256
    # (`GeminiSceneCache`'s own docstring spells out the reasoning). Checked
    # here as the semantic equivalent of the documented key, not the literal
    # column list - see docs/reports/prompt-03.md for the amendment this
    # still owes.
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
    from repcut.analysis.sampler import pick_frame
    from repcut.analysis.types import SceneBoundary

    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = v2.make_clip(
            Path(scratch) / "portrait.mp4", seconds=2.0, width=1280, height=720, rotation=90
        )
        proxy, properties, _digest = _prepare_media(clip)
        coded = _image_dimensions(clip)
        proxy_dims = _image_dimensions(proxy)

        boundary = SceneBoundary(
            sequence_index=0,
            start_seconds=0.0,
            end_seconds=properties.duration_seconds,
            start_frame_source=0,
            end_frame_source=max(1, round(properties.duration_seconds * properties.fps_source)),
        )
        frame_path = Path(scratch) / "frame.jpg"
        asyncio.run(pick_frame(clip, boundary, frame_path))
        sampled_dims = _image_dimensions(frame_path)

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


def check_one_frame_per_scene_leaves_the_machine() -> int:
    """3. N detected scenes -> N outbound image parts, no audio, no path."""

    async def _run() -> tuple[int, int, str, bool, bool]:
        with TemporaryDirectory(prefix="repcut-gate03-eng-", ignore_cleanup_errors=True) as root:
            data_dir = Path(root) / "data"
            data_dir.mkdir(parents=True)
            src = Path(root) / "src"
            src.mkdir()
            clip = _build_motion_loudness_clip(src / "motion.mp4", segment_seconds=1.5)
            transport, requests = _mock_gemini_transport(
                [(200, _gemini_response({"content_type": "exercise"}))]
            )
            async with in_process_engine(data_dir) as engine:
                with _PatchedTransport(transport):
                    digest = await _ingest_and_analyze(engine, clip)
                scenes = await _scenes(engine, digest)

        raw = json.dumps(requests)
        image_parts = raw.count("inline_data")
        audio_hits = raw.count('"audio/')
        # `raw` is JSON, where a Windows path's backslashes arrive doubled - so
        # the escaped spelling is the one that could actually appear in it.
        escaped_root = json.dumps(root)[1:-1]
        filename_hit = clip.name in raw or root in raw or escaped_root in raw
        return len(scenes), len(requests), f"{image_parts}", audio_hits > 0, filename_hit

    scene_count, request_count, image_parts, audio_hit, filename_hit = asyncio.run(_run())

    measured(
        f"scenes={scene_count} requests={request_count} inline_data={image_parts} "
        f"audio_parts={audio_hit} filename_leaked={filename_hit}"
    )
    if scene_count < 2:
        failed(f"the motion/loudness fixture only produced {scene_count} scene(s)")
        return 1
    if request_count != scene_count:
        failed(f"{request_count} Gemini requests for {scene_count} scenes; expected one each")
        return 1
    if image_parts != str(scene_count):
        failed(f"{image_parts} image parts across {request_count} requests; expected {scene_count}")
        return 1
    if audio_hit:
        failed("an audio part crossed the transport seam - P4 forbids sending audio")
        return 1
    if filename_hit:
        failed("the clip's filename or scratch path appeared in a request body")
        return 1
    return 0


def check_repeat_run_costs_zero_calls() -> int:
    """4. Run twice; the second run makes zero requests, one cache hit per scene."""

    async def _run() -> tuple[int, int, int, int]:
        with TemporaryDirectory(prefix="repcut-gate03-eng-", ignore_cleanup_errors=True) as root:
            data_dir = Path(root) / "data"
            data_dir.mkdir(parents=True)
            clip = _build_motion_loudness_clip(Path(root) / "motion.mp4", segment_seconds=1.5)

            transport1, requests1 = _mock_gemini_transport(
                [(200, _gemini_response({"content_type": "exercise"}))]
            )
            async with in_process_engine(data_dir) as engine:
                with _PatchedTransport(transport1):
                    digest = await _ingest_and_analyze(engine, clip)
                first_scenes = await _scenes(engine, digest)

                from repcut.analysis.pipeline import ANALYSIS_JOB_TYPE
                from repcut.db.models import JobStatus

                transport2, requests2 = _mock_gemini_transport(
                    [(200, _gemini_response({"content_type": "exercise"}))]
                )
                with _PatchedTransport(transport2):
                    await engine.queue.enqueue(ANALYSIS_JOB_TYPE, sha256=digest)
                    await engine.queue.drain()
                rerun = await _latest_job(engine, digest, ANALYSIS_JOB_TYPE)
                if rerun.status != JobStatus.SUCCEEDED:  # type: ignore[attr-defined]
                    raise RuntimeError(f"rerun did not succeed: {rerun.error}")  # type: ignore[attr-defined]
                second_scenes = await _scenes(engine, digest)
        return len(requests1), len(first_scenes), len(requests2), len(second_scenes)

    requests1, scenes1, requests2, scenes2 = asyncio.run(_run())

    measured(
        f"run1 requests={requests1} scenes={scenes1}; run2 requests={requests2} scenes={scenes2}"
    )
    if not requests1:
        failed("the first run made zero API calls; the fixture proves nothing about caching")
        return 1
    if requests2:
        failed(f"the second run made {requests2} API calls; a repeat run must cost zero")
        return 1
    if scenes2 != scenes1:
        failed("the second run reported a different scene count than the first")
        return 1
    return 0


def check_prompt_version_invalidates() -> int:
    """5. Bumping `prompt_version` brings the calls back."""

    async def _run() -> tuple[int, int]:
        with TemporaryDirectory(prefix="repcut-gate03-eng-", ignore_cleanup_errors=True) as root:
            data_dir = Path(root) / "data"
            data_dir.mkdir(parents=True)
            clip = _build_motion_loudness_clip(Path(root) / "motion.mp4", segment_seconds=1.5)

            transport1, requests1 = _mock_gemini_transport(
                [(200, _gemini_response({"content_type": "exercise"}))]
            )
            async with in_process_engine(data_dir) as engine:
                with _PatchedTransport(transport1):
                    digest = await _ingest_and_analyze(engine, clip)

                from repcut.analysis import pipeline
                from repcut.db.models import JobStatus

                original_version = pipeline.GEMINI_PROMPT_VERSION
                transport2, requests2 = _mock_gemini_transport(
                    [(200, _gemini_response({"content_type": "exercise"}))]
                )
                try:
                    pipeline.GEMINI_PROMPT_VERSION = original_version + 1
                    with _PatchedTransport(transport2):
                        await engine.queue.enqueue(pipeline.ANALYSIS_JOB_TYPE, sha256=digest)
                        await engine.queue.drain()
                finally:
                    pipeline.GEMINI_PROMPT_VERSION = original_version
                rerun = await _latest_job(engine, digest, pipeline.ANALYSIS_JOB_TYPE)
                if rerun.status != JobStatus.SUCCEEDED:  # type: ignore[attr-defined]
                    raise RuntimeError(f"bumped rerun did not succeed: {rerun.error}")  # type: ignore[attr-defined]
        return len(requests1), len(requests2)

    requests1, requests2 = asyncio.run(_run())

    measured(f"v1 requests={requests1}; v2 (bumped) requests={requests2}")
    if not requests1:
        failed("the first run at the original prompt_version made zero calls")
        return 1
    if not requests2:
        failed("bumping GEMINI_PROMPT_VERSION made zero calls; the cache key is not versioned")
        return 1
    return 0


def check_limiter_fails_closed() -> int:
    """6. Bucket exhausted -> zero requests, asserted against the transport."""

    async def _run() -> tuple[int, int, int]:
        with TemporaryDirectory(prefix="repcut-gate03-eng-", ignore_cleanup_errors=True) as root:
            data_dir = Path(root) / "data"
            data_dir.mkdir(parents=True)
            clip = _build_motion_loudness_clip(Path(root) / "motion.mp4", segment_seconds=1.5)
            transport, requests = _mock_gemini_transport(
                [(200, _gemini_response({"content_type": "exercise"}))]
            )
            async with in_process_engine(
                data_dir, gemini_rpm_limit=0, gemini_daily_limit=0
            ) as engine:
                with _PatchedTransport(transport):
                    digest = await _ingest_and_analyze(engine, clip)
                scenes = await _scenes(engine, digest)
                cache_rows = await _cache_rows(engine, [scene.id for scene in scenes])  # type: ignore[attr-defined]
        return len(scenes), len(requests), len(cache_rows)

    scene_count, request_count, cache_row_count = asyncio.run(_run())

    measured(f"scenes={scene_count} requests={request_count} cache_rows={cache_row_count}")
    if scene_count < 2:
        failed("the fixture did not produce enough scenes to make this measurement meaningful")
        return 1
    if request_count:
        failed(f"the limiter let {request_count} request(s) through with the bucket at zero")
        return 1
    if cache_row_count:
        failed(f"{cache_row_count} cache row(s) written despite zero requests being made")
        return 1
    return 0


def check_malformed_json_handled() -> int:
    """7. Garbage -> one retry per scene -> `vlm: null`, a cache row still written."""

    async def _run() -> tuple[int, int, int, int]:
        with TemporaryDirectory(prefix="repcut-gate03-eng-", ignore_cleanup_errors=True) as root:
            data_dir = Path(root) / "data"
            data_dir.mkdir(parents=True)
            clip = v2.make_clip(Path(root) / "clip.mp4", seconds=2.0)
            garbage = b"not json at all {{{"
            transport, requests = _mock_gemini_transport([(200, garbage), (200, garbage)])
            async with in_process_engine(data_dir) as engine:
                with _PatchedTransport(transport):
                    digest = await _ingest_and_analyze(engine, clip)
                scenes = await _scenes(engine, digest)
                cache_rows = await _cache_rows(engine, [scene.id for scene in scenes])  # type: ignore[attr-defined]
        null_rows = sum(1 for row in cache_rows if row.raw_response_json is None)  # type: ignore[attr-defined]
        return len(scenes), len(requests), len(cache_rows), null_rows

    scene_count, request_count, cache_row_count, null_row_count = asyncio.run(_run())

    measured(
        f"scenes={scene_count} requests={request_count} cache_rows={cache_row_count} "
        f"(null={null_row_count})"
    )
    if request_count != 2 * scene_count:
        failed(
            f"{request_count} requests for {scene_count} scene(s) of pure garbage; "
            f"expected exactly one retry each ({2 * scene_count})"
        )
        return 1
    if cache_row_count != scene_count:
        failed(
            f"{cache_row_count} cache row(s) written for {scene_count} scene(s); expected one each"
        )
        return 1
    if null_row_count != scene_count:
        failed(f"{null_row_count} of {cache_row_count} cache rows are null; expected all of them")
        return 1
    return 0


def check_offline_completes() -> int:
    """8. A connection error -> pipeline finishes, exit 0, `vlm: null`, no cache row."""

    async def _run() -> tuple[int, bool, int]:
        with TemporaryDirectory(prefix="repcut-gate03-eng-", ignore_cleanup_errors=True) as root:
            data_dir = Path(root) / "data"
            data_dir.mkdir(parents=True)
            clip = _build_motion_loudness_clip(Path(root) / "motion.mp4", segment_seconds=1.5)
            async with in_process_engine(data_dir) as engine:
                with _PatchedTransport(_connection_refused_transport()):
                    digest = await _ingest_and_analyze(engine, clip)
                scenes = await _scenes(engine, digest)
                cache_rows = await _cache_rows(engine, [scene.id for scene in scenes])  # type: ignore[attr-defined]
        local_features = all(
            scene.motion_energy is not None and scene.audio_energy is not None  # type: ignore[attr-defined]
            for scene in scenes
        )
        return len(scenes), local_features, len(cache_rows)

    scene_count, local_features, cache_row_count = asyncio.run(_run())

    measured(f"scenes={scene_count} local_features={local_features} cache_rows={cache_row_count}")
    if not scene_count:
        failed(
            "no scenes were produced offline; local (non-Gemini) analysis should not depend on it"
        )
        return 1
    if not local_features:
        failed("motion/audio energy is missing offline - only the Gemini call should degrade")
        return 1
    if cache_row_count:
        failed(f"{cache_row_count} cache row(s) written despite every request failing to connect")
        return 1
    return 0


def check_no_key_leak() -> int:
    """9. The key's value in no log/report/fixture/error payload, nor a user path."""
    stdout, stderr = io.StringIO(), io.StringIO()

    async def _run() -> None:
        with TemporaryDirectory(prefix="repcut-gate03-eng-", ignore_cleanup_errors=True) as root:
            data_dir = Path(root) / "data"
            data_dir.mkdir(parents=True)
            clip = _build_motion_loudness_clip(Path(root) / "motion.mp4", segment_seconds=1.5)
            garbage_transport, _requests = _mock_gemini_transport([(200, b"not json {{{")])
            async with in_process_engine(data_dir) as engine:
                with _PatchedTransport(garbage_transport):
                    await _ingest_and_analyze(engine, clip)

            data_dir2 = Path(root) / "data2"
            data_dir2.mkdir(parents=True)
            clip2 = v2.make_clip(Path(root) / "clip2.mp4", seconds=2.0)
            async with in_process_engine(data_dir2) as engine2:
                with _PatchedTransport(_connection_refused_transport()):
                    await _ingest_and_analyze(engine2, clip2)

    with redirect_stdout(stdout), redirect_stderr(stderr):
        asyncio.run(_run())

    captured = stdout.getvalue() + stderr.getvalue()
    key_leaked = FIXTURE_GEMINI_KEY in captured
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
    from repcut.analysis.sampler import pick_frame
    from repcut.analysis.types import SceneBoundary

    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = v2.make_clip(Path(scratch) / "clip.mp4", seconds=2.0, rotation=90)
        _proxy, properties, _digest = _prepare_media(clip)
        boundary = SceneBoundary(
            sequence_index=0,
            start_seconds=0.0,
            end_seconds=properties.duration_seconds,
            start_frame_source=0,
            end_frame_source=max(1, round(properties.duration_seconds * properties.fps_source)),
        )
        frame_path = Path(scratch) / "frame.jpg"
        asyncio.run(pick_frame(clip, boundary, frame_path))
        document = v2.ffprobe_json(frame_path, "-show_format", "-show_streams")

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


# Criterion 11's fixture: known R'G'B' patches - 75% primaries and yellow, where
# a wrong Y'CbCr matrix moves furthest; mid grey, where a wrong range shows; a
# skin tone. Mirrors `engine/tests/test_ffmpeg_builder.py`'s pixel test.
_PATCHES = ((191, 0, 0), (0, 191, 0), (0, 0, 191), (191, 191, 0), (128, 128, 128), (200, 150, 120))
_PATCH_SIDE = 96
# (Kr, Kb): the matrix a JPEG decoder applies (JFIF, ITU-T T.871) and the one
# phone video and the proxy use.
_KR_KB = {"bt601": (0.299, 0.114), "bt709": (0.2126, 0.0722)}
# JPEG at -q:v 2 on flat patches measured 2.7 R'G'B' codes from the truth; the
# pre-fix wrong-matrix frame measured 32.6. Well clear of both.
_COLOUR_TOLERANCE = 8.0


def _make_bars(destination: Path) -> Path:
    """The patches as a phone records them: BT.709 matrix, limited range, tagged."""
    count = len(_PATCHES)
    sources = "".join(
        f"color=c=0x{r:02X}{g:02X}{b:02X}:s={_PATCH_SIDE}x{_PATCH_SIDE}:r=30:d=2[p{i}];"
        for i, (r, g, b) in enumerate(_PATCHES)
    )
    graph = (
        sources + "".join(f"[p{i}]" for i in range(count)) + f"hstack=inputs={count},format=gbrp,"
        "scale=in_range=pc:out_color_matrix=bt709:out_range=tv,format=yuv420p"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            graph,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-qp",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
            destination.as_posix(),
        ],
        capture_output=True,
        check=True,
        timeout=120,
    )
    return destination


def _patch_rgb(image: Path, matrix: str, *, full_range: bool) -> list[tuple[float, float, float]]:
    """Each patch centre's mean R'G'B', decoding the file's raw samples by ``matrix``.

    The samples are read as stored (chroma upsampled, nothing else) and the
    Y'CbCr equations applied here, so the decoder's assumption is written down
    rather than inherited from whatever library happens to open the file.
    """
    import numpy as np

    width = _PATCH_SIDE * len(_PATCHES)
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            image.as_posix(),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv444p",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout
    y, cb, cr = np.frombuffer(raw, np.uint8).reshape(3, _PATCH_SIDE, width).astype(np.float64)
    kr, kb = _KR_KB[matrix]
    if full_range:
        luma, pb, pr = y / 255.0, (cb - 128.0) / 255.0, (cr - 128.0) / 255.0
    else:
        luma, pb, pr = (y - 16.0) / 219.0, (cb - 128.0) / 224.0, (cr - 128.0) / 224.0
    red = luma + 2.0 * (1.0 - kr) * pr
    blue = luma + 2.0 * (1.0 - kb) * pb
    green = (luma - kr * red - kb * blue) / (1.0 - kr - kb)
    rgb = np.clip(np.stack([red, green, blue], axis=-1) * 255.0, 0.0, 255.0)
    quarter = _PATCH_SIDE // 4
    rows = slice(quarter, _PATCH_SIDE - quarter)
    return [
        tuple(
            rgb[rows, i * _PATCH_SIDE + quarter : (i + 1) * _PATCH_SIDE - quarter].mean(axis=(0, 1))
        )
        for i in range(len(_PATCHES))
    ]


def _max_error(
    got: Iterable[tuple[float, float, float]], want: Iterable[tuple[float, float, float]]
) -> float:
    return max(
        abs(a - b) for g, w in zip(got, want, strict=True) for a, b in zip(g, w, strict=True)
    )


def _sample(clip: Path, duration_seconds: float, fps: float, destination: Path) -> Path:
    """One frame through the shipped sampler - its own probe, its own recipe."""
    from repcut.analysis.sampler import pick_frame
    from repcut.analysis.types import SceneBoundary

    boundary = SceneBoundary(
        sequence_index=0,
        start_seconds=0.0,
        end_seconds=duration_seconds,
        start_frame_source=0,
        end_frame_source=max(1, round(duration_seconds * fps)),
    )
    asyncio.run(pick_frame(clip, boundary, destination))
    return destination


def _mean_luma(image: Path) -> float:
    stats = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"movie={image.name},signalstats",
            "-show_entries",
            "frame_tags=lavfi.signalstats.YAVG",
            "-of",
            "csv=p=0",
        ],
        cwd=image.parent,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return float(stats.stdout.strip().rstrip(","))


def check_frame_is_tone_mapped() -> int:
    """11. The sampled frame, decoded as every JPEG decoder does, shows the source's colours.

    Measured on pixels, never on tags: a JPEG carries no reliable colour tags,
    and ffprobe's ``bt470bg`` on one is the decoder's assumption, not a reading
    of the file. JFIF fixes that assumption - BT.601, full range - so the
    frame's samples must be written for it.

    - SDR: phone-style BT.709 bars through ``pick_frame``, decoded by JFIF,
      against the known patches. A negative control proves the fixture tells
      matrices apart: the same samples decoded as BT.709 must miss.
    - HDR: the same bars tagged HLG. The frame, decoded by JFIF, must equal the
      v2 proxy decoded by its own bt709/tv tags - what Gemini sees is what the
      person judges (amendment 012) - and its mean luma stays in a sane band,
      which an HLG signal read without a tone map does not.
    """
    expected = [tuple(float(v) for v in patch) for patch in _PATCHES]
    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        root = Path(scratch)
        sdr = _make_bars(root / "bars.mp4")
        sdr_frame = _sample(sdr, 2.0, 30.0, root / "sdr.jpg")
        sdr_error = _max_error(_patch_rgb(sdr_frame, "bt601", full_range=True), expected)
        control = _max_error(_patch_rgb(sdr_frame, "bt709", full_range=True), expected)

        hdr = _make_bars(root / "hdr.mp4")
        _write_hdr_tags(hdr)
        proxy, properties, _digest = _prepare_media(hdr)
        hdr_frame = _sample(
            hdr, properties.duration_seconds, properties.fps_source, root / "hdr.jpg"
        )
        judged = _patch_rgb(proxy, "bt709", full_range=False)
        hdr_error = _max_error(_patch_rgb(hdr_frame, "bt601", full_range=True), judged)
        mean_luma = _mean_luma(hdr_frame)

    measured(
        f"SDR bars vs truth, JFIF decode: max {sdr_error:.1f} "
        f"(same samples as BT.709: {control:.1f}); HDR frame vs proxy: max {hdr_error:.1f}; "
        f"HDR mean_luma={mean_luma:.1f}; tolerance {_COLOUR_TOLERANCE:.0f}"
    )
    if control <= _COLOUR_TOLERANCE:
        failed("the bars cannot tell BT.601 from BT.709 - the fixture proves nothing")
        return 1
    if sdr_error > _COLOUR_TOLERANCE:
        failed(f"an SDR frame decodes {sdr_error:.1f} codes from its source's colours")
        return 1
    if hdr_error > _COLOUR_TOLERANCE:
        failed(f"an HDR frame decodes {hdr_error:.1f} codes from the proxy a person judges")
        return 1
    if not (40.0 < mean_luma < 235.0):
        failed(f"mean luma {mean_luma:.1f} looks washed out or crushed, not tone-mapped")
        return 1
    return 0


def check_boundaries_survive_vfr() -> int:
    """12. Boundaries in seconds against the source map to a real source frame."""
    from repcut.analysis.scenes import detect_scenes

    with TemporaryDirectory(prefix="repcut-gate03-src-", ignore_cleanup_errors=True) as scratch:
        clip = v2.make_clip(Path(scratch) / "vfr.mp4", seconds=4.0, variable_frame_rate=True)
        proxy, properties, _digest = _prepare_media(clip)
        pts = _video_pts_list(clip)
        scenes = detect_scenes(proxy, fps_source=properties.fps_source)

    if not pts:
        measured("no source frame timestamps read")
        failed("could not read the VFR fixture's own frame timestamps to check against")
        return 1

    frame_duration_ms = (max(pts) / max(1, len(pts) - 1)) * 1000
    # Scenes are half-open, [start, end): the last scene's end is the clip's
    # end, one past its final frame, so `end_frame_source == len(pts)` is a
    # legitimate handle - and its instant is where the stream stops, the last
    # frame's pts plus that frame's own duration, not any frame's pts.
    last_step = pts[-1] - pts[-2] if len(pts) > 1 else 0.0
    instants = [*pts, pts[-1] + last_step]
    errors_ms: list[float] = []
    for scene in scenes:
        for seconds, frame_index, limit in (
            (scene.start_seconds, scene.start_frame_source, len(pts)),
            (scene.end_seconds, scene.end_frame_source, len(pts) + 1),
        ):
            # Deliberately unclamped: an index beyond those bounds is exactly
            # the "not a real source frame" failure this criterion exists to
            # catch, so it must surface, not be pulled back in range.
            if not (0 <= frame_index < limit):
                errors_ms.append(float("inf"))
                continue
            errors_ms.append(abs(instants[frame_index] - seconds) * 1000)

    max_error = max(errors_ms) if errors_ms else float("inf")
    measured(
        f"scenes={len(scenes)} avg_frame_duration={frame_duration_ms:.1f}ms "
        f"max_boundary_error={max_error:.1f}ms"
    )
    if not scenes:
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

    async def _run() -> tuple[int, list[float], list[float], list[float]]:
        with TemporaryDirectory(prefix="repcut-gate03-eng-", ignore_cleanup_errors=True) as root:
            data_dir = Path(root) / "data"
            data_dir.mkdir(parents=True)
            clip = _build_motion_loudness_clip(Path(root) / "motion.mp4", segment_seconds=2.0)
            transport, _requests = _mock_gemini_transport(
                [(200, _gemini_response({"content_type": "exercise"}))]
            )
            async with in_process_engine(data_dir) as engine:
                with _PatchedTransport(transport):
                    digest = await _ingest_and_analyze(engine, clip)
                scenes = await _scenes(engine, digest)
        motion = [s.motion_energy for s in scenes if s.motion_energy is not None]  # type: ignore[attr-defined]
        audio = [s.audio_energy for s in scenes if s.audio_energy is not None]  # type: ignore[attr-defined]
        combined = [s.energy_score for s in scenes if s.energy_score is not None]  # type: ignore[attr-defined]
        return len(scenes), motion, audio, combined

    scene_count, motion, audio, combined = asyncio.run(_run())
    motion_spread = (max(motion) - min(motion)) if motion else 0.0
    audio_spread = (max(audio) - min(audio)) if audio else 0.0
    combined_spread = (max(combined) - min(combined)) if combined else 0.0

    measured(
        f"scenes={scene_count} motion_spread={motion_spread:.3f} audio_spread={audio_spread:.3f} "
        f"energy_score min={min(combined, default=0):.1f} max={max(combined, default=0):.1f} "
        f"spread={combined_spread:.1f}"
    )
    if scene_count < 2:
        failed(
            f"only {scene_count} scene(s) detected; the fixture has two deliberately unequal ones"
        )
        return 1
    # A stated minimum, not zero: a spread the noise floor could produce by
    # accident would make this criterion pass by luck rather than by design.
    if combined_spread <= 5.0:
        failed(f"energy_score spread {combined_spread:.1f} (of 0-100) is not clearly non-flat")
        return 1
    return 0


def check_runtime_budget() -> int:
    """14. The guide's per-session budget (amendment 008), scaled to a synthetic clip."""
    footage_seconds = 20.0  # short: this still has to be a gate someone re-runs
    budget_seconds = footage_seconds * ANALYSIS_BUDGET_RATIO

    async def _run() -> tuple[int, float]:
        with TemporaryDirectory(prefix="repcut-gate03-eng-", ignore_cleanup_errors=True) as root:
            data_dir = Path(root) / "data"
            data_dir.mkdir(parents=True)
            clip = v2.make_clip(Path(root) / "budget.mp4", seconds=footage_seconds)
            transport, _requests = _mock_gemini_transport(
                [(200, _gemini_response({"content_type": "exercise"}))]
            )
            async with in_process_engine(data_dir) as engine:
                with _PatchedTransport(transport):
                    start = time.monotonic()
                    digest = await _ingest_and_analyze(engine, clip)
                    elapsed = time.monotonic() - start
                scenes = await _scenes(engine, digest)
        return len(scenes), elapsed

    scene_count, elapsed = asyncio.run(_run())

    measured(
        f"footage={footage_seconds:.0f}s budget={budget_seconds:.1f}s elapsed={elapsed:.1f}s "
        f"scenes={scene_count}"
    )
    # This includes ingest (the budget is analysis-only), is measured on CPU
    # only (amendment 008: "do not install torch" - optical flow is CPU here),
    # and the guide's figure was benchmarked on the target GPU laptop for a
    # ~15-clip session, not one synthetic clip on whatever machine runs this
    # gate - so a miss here is a signal to re-check the ratio (`/guide-amend`),
    # not an automatic FAIL of the whole gate. SKIP rather than FAIL.
    if elapsed > budget_seconds:
        skipped(
            f"{elapsed:.1f}s exceeds the {budget_seconds:.1f}s scaled budget (includes ingest, "
            "CPU-only, not the ROG this figure was benchmarked on) - re-check the ratio before "
            "treating this as a FAIL"
        )
        return 2
    return 0


def check_scripts_lint() -> int:
    """15. `ruff check scripts` clean, and no new UNJUSTIFIED `# noqa` (amendment 009).

    The criterion originally read "no `# noqa` was added" — this function
    accepting a justified one (a stated, checkable reason, same line or the
    lines immediately above it) is a deliberate rewrite of what it measures,
    recorded in `docs/guide-amendments/009-criterion-15-justified-noqa.md`,
    not a silent loosening.
    """
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
    if diff.returncode != 0:
        # A shallow clone has no tags, and git then prints to stderr with an
        # empty stdout - which would otherwise read as "+0 noqa" and pass
        # without a single diff line having been inspected.
        measured(f"git diff prompt-02-done...HEAD -> exit {diff.returncode}")
        skipped("the prompt-02-done tag is not resolvable here, so no diff could be inspected")
        return 2
    # An added noqa directive is only a problem when it is unjustified:
    # `run-prompt-03.md`'s own debt item says "fix OR JUSTIFY every finding...
    # do not add an ignore entry" - an ignore entry is `ignore = [...]` in
    # `pyproject.toml` (which would be silent and blanket), and a directive with
    # a reason beside the one line it excuses, matching the S603/S607
    # convention `ffmpeg_builder.py` already uses, is the justification the
    # debt item asks for. So this counts only a directive with neither a
    # trailing same-line reason nor a comment directly above it - the block of
    # comment lines ending on the line before, not any comment within reach.
    # This checks placement only; whether the words actually justify the
    # directive is review's job (amendment 009, "What the gate checks").
    diff_lines = diff.stdout.splitlines()
    unjustified_noqa: list[str] = []
    for index, line in enumerate(diff_lines):
        if not (line.startswith("+") and not line.startswith("+++") and "# noqa" in line):
            continue
        after_noqa = line.split("# noqa", 1)[-1]
        same_line_reason = "-" in after_noqa and bool(after_noqa.split("-", 1)[1].strip())
        prior = diff_lines[index - 1] if index > 0 else ""
        prior_text = prior[1:].strip() if prior[:1] in "+ " else ""
        preceding_is_comment = prior_text.startswith("#")
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
        time.sleep(0.5)
    finally:
        signal.signal(signal.SIGINT, previous)


def check_ctrl_c_clean() -> int:
    """16. `make dev` interrupted returns 130, no traceback, its ports free, no process left.

    The exit code alone cannot show what a person stopping the stack needs: that
    both ports are theirs again and nothing of it is still running. Both are
    measured before this gate's own cleanup runs.
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

    import signal

    argv = _make_dev_argv()
    if sys.platform == "win32":
        result = _interrupt_dev_stack(argv, _send_ctrl_c_windows)
    else:
        result = _interrupt_dev_stack(argv, lambda pid: os.kill(pid, signal.SIGINT))
    return _judge_ctrl_c(result)


def _make_dev_argv() -> list[str]:
    """What `make dev` runs after the interpreter, read from the Makefile's own recipe.

    Read, not retyped: a gate that launches its own spelling of the stack is
    testing a launcher nobody uses (the lesson of verify-02 criterion 21).
    """
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(makefile):
        if line.startswith("dev:"):
            recipe = makefile[index + 1].strip().lstrip("@").split()
            return recipe[1:]  # drop $(PY): this process's interpreter stands in for it
    raise ValueError("the Makefile has no `dev:` target")


@dataclass(frozen=True)
class StopResult:
    """What stopping the stack left behind, measured before any cleanup of ours ran."""

    exit_code: int | None
    output: str
    ports_held: list[int]
    survivors: list[str]
    process_count: int


def _descendants(pid: int) -> list[tuple[int, float, str]]:
    import psutil

    try:
        children = psutil.Process(pid).children(recursive=True)
    except psutil.NoSuchProcess:
        return []
    snapshot: list[tuple[int, float, str]] = []
    for child in children:
        # Named: a process may exit between the listing and the read.
        with suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            snapshot.append((child.pid, child.create_time(), child.name()))
    return snapshot


def _still_running(snapshot: list[tuple[int, float, str]]) -> list[str]:
    """The snapshot's processes still alive - by pid AND start time, so a reused pid is not one."""
    import psutil

    alive: list[str] = []
    for pid, created, name in snapshot:
        with suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            process = psutil.Process(pid)
            if process.create_time() == created and process.is_running():
                alive.append(f"{name}:{pid}")
    return alive


# A process taskkill has just ended can linger in the table for a moment; this
# is how long it has to leave, never how long a live process may run.
_EXIT_SETTLE_S = 5.0


def _interrupt_dev_stack(
    argv: list[str], deliver: Callable[[int], object], *, creationflags: int = 0
) -> StopResult:
    """Start the stack as `make dev` does, press Ctrl-C once it serves, measure what is left."""
    import threading

    import dev_stack

    stack = dev_stack.DevStack()
    launcher: subprocess.Popen[str] | None = None
    try:
        launcher = subprocess.Popen(
            [sys.executable, *argv],
            cwd=REPO_ROOT,
            env=stack.environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=creationflags,
        )
        deadline = time.monotonic() + dev_stack.STACK_READY_TIMEOUT_S
        ready = False
        while time.monotonic() < deadline and launcher.poll() is None:
            if dev_stack.port_open(stack.engine_port) and dev_stack.port_open(stack.ui_port):
                ready = True
                break
            time.sleep(0.5)
        if not ready:
            return StopResult(None, "stack never became ready", [], [], 0)
        snapshot = _descendants(launcher.pid)

        def _hard_kill() -> None:
            if launcher is not None and launcher.poll() is None:
                dev_stack.kill_pid_tree(launcher.pid)

        watchdog = threading.Timer(dev_stack.STACK_STOP_TIMEOUT_S + 30.0, _hard_kill)
        watchdog.daemon = True
        watchdog.start()
        try:
            deliver(launcher.pid)
            try:
                stdout, stderr = launcher.communicate(timeout=dev_stack.STACK_STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                # The whole tree, and bounded: `bash`, `node` and `uvicorn` hold
                # the pipes too, and an unbounded communicate() after killing
                # only the launcher waited on them forever instead of failing.
                dev_stack.kill_pid_tree(launcher.pid)
                # Named: a straggler may still hold a pipe; the verdict stands.
                with suppress(subprocess.TimeoutExpired):
                    launcher.communicate(timeout=30)
                return StopResult(None, "did not exit", [], [], len(snapshot))
        finally:
            watchdog.cancel()
        # Measured now, before `stack.close()` below cleans up after anything.
        held = [port for port in (stack.engine_port, stack.ui_port) if dev_stack.port_open(port)]
        settle = time.monotonic() + _EXIT_SETTLE_S
        survivors = _still_running(snapshot)
        while survivors and time.monotonic() < settle:
            time.sleep(0.25)
            survivors = _still_running(snapshot)
        return StopResult(launcher.returncode, stdout + stderr, held, survivors, len(snapshot))
    finally:
        if launcher is not None and launcher.poll() is None:
            dev_stack.kill_pid_tree(launcher.pid)
        stack.close()


def _judge_ctrl_c(result: StopResult) -> int:
    """16's verdict: 130, no traceback, both ports free, not one process of the stack left."""
    if result.exit_code is None:
        measured(f"{result.output} ({result.process_count} processes)")
        failed(f"`make dev` {result.output} after Ctrl-C")
        return 1
    has_traceback = "Traceback (most recent call last)" in result.output
    measured(
        f"exit={result.exit_code} traceback_in_output={has_traceback} "
        f"ports_held={result.ports_held} "
        f"survivors={len(result.survivors)}/{result.process_count}"
    )
    if has_traceback:
        failed("Ctrl-C produced a traceback on stdout or stderr")
        return 1
    if result.exit_code != 130:
        failed(f"exit code was {result.exit_code}, expected 130")
        return 1
    if result.ports_held:
        failed(f"port(s) {result.ports_held} still accepting connections after `make dev` exited")
        return 1
    if result.survivors:
        failed(f"processes of the stack outlived it: {', '.join(result.survivors[:6])}")
        return 1
    return 0


# 16b's script: marks itself running, then finishes with the status it was
# given. `trap : INT` lets it carry on past the interrupt, as verify_03.sh does
# while criterion 16 runs. $1 ready marker, $2 exit status.
_STATUS_SCRIPT = """trap : INT
: > "$1"
sleep 3
exit "$2"
"""

# The witness: a Python process on the wrapper's console, started there by this
# process rather than by bash - Git Bash starts native children with Ctrl-C
# switched off (measured: a probe under bash never saw one). Same console, same
# inherited state as the wrapper, so its verdict is the wrapper's too.
_WITNESS = """import pathlib, sys, time
pathlib.Path(sys.argv[1]).touch()
try:
    time.sleep(30)
    verdict = "no-INT"
except KeyboardInterrupt:
    verdict = "got-INT"
pathlib.Path(sys.argv[2]).write_text(verdict)
"""


def _wait_for(path: Path, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.1)


def _ctrl_c_on_a_fresh_console(launcher: subprocess.Popen[str], scratch: Path) -> str:
    """Ctrl-C on ``launcher``'s own console, never this process's; the witness's verdict.

    Windows broadcasts ``CTRL_C_EVENT`` to a whole console, so the launcher was
    started on a fresh one and this process joins it only to press the keys -
    starting the witness there first, so a Ctrl-C that never arrived cannot pass
    for one that was ignored.
    """
    import ctypes
    import signal

    ready, verdict = scratch / "witness-ready", scratch / "witness-verdict"
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.FreeConsole()  # 0 when there was none: nothing to undo
    if not kernel32.AttachConsole(launcher.pid):
        error = ctypes.get_last_error()
        kernel32.AttachConsole(ctypes.c_uint32(0xFFFFFFFF))
        raise OSError(f"AttachConsole failed: error {error}")
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    witness: subprocess.Popen[bytes] | None = None
    try:
        witness = subprocess.Popen(
            [sys.executable, "-c", _WITNESS, ready.as_posix(), verdict.as_posix()],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait_for(ready, 30)
        kernel32.SetConsoleCtrlHandler(None, True)
        if not kernel32.GenerateConsoleCtrlEvent(0, 0):
            raise OSError(f"GenerateConsoleCtrlEvent failed: error {ctypes.get_last_error()}")
        with suppress(subprocess.TimeoutExpired):  # named: judged by the verdict below
            witness.wait(timeout=10)
    finally:
        if witness is not None and witness.poll() is None:
            witness.kill()
        kernel32.FreeConsole()
        kernel32.AttachConsole(ctypes.c_uint32(0xFFFFFFFF))  # back to the parent's, if any
        kernel32.SetConsoleCtrlHandler(None, False)
        signal.signal(signal.SIGINT, previous)
    return verdict.read_text(encoding="utf-8") if verdict.exists() else "no verdict"


def _wrapper_status_after_ctrl_c(scratch: Path, script_status: int) -> tuple[int, str]:
    """``posix_shell.py``'s exit status when a Ctrl-C lands mid-run, and whether it landed."""
    import signal

    import dev_stack

    run = scratch / str(script_status)
    run.mkdir()
    script = run / "status.sh"
    script.write_bytes(_STATUS_SCRIPT.encode("utf-8"))
    ready = run / "script-ready"
    argv = [sys.executable, "scripts/posix_shell.py", script.as_posix(), ready.as_posix()]
    argv.append(str(script_status))
    creationflags = 0
    if sys.platform == "win32":
        import ctypes

        # "Ignore Ctrl-C" is inherited, and a shell started in its own process
        # group - as a sandboxed or CI one is - has it set. A person's terminal
        # does not; give the wrapper that.
        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)  # type: ignore[attr-defined]
        creationflags = subprocess.CREATE_NO_WINDOW
    wrapper = subprocess.Popen(
        argv,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )
    try:
        _wait_for(ready, 30)
        if sys.platform == "win32":
            delivered = _ctrl_c_on_a_fresh_console(wrapper, run)
        else:
            # The wrapper alone: on POSIX a terminal's Ctrl-C reaches it too.
            wrapper.send_signal(signal.SIGINT)
            delivered = "got-INT"
        stdout, stderr = wrapper.communicate(timeout=60)
    finally:
        if wrapper.poll() is None:
            dev_stack.kill_pid_tree(wrapper.pid)
    if "Traceback (most recent call last)" in stdout + stderr:
        return -1, delivered
    return wrapper.returncode, delivered


def check_wrapper_keeps_script_status() -> int:
    """16b. A Ctrl-C reaching `scripts/posix_shell.py` never replaces its script's exit status.

    The regression: criterion 16's Ctrl-C reaches every process on the console,
    including the `posix_shell.py` running verify_03.sh, which returned 130 for
    any Ctrl-C it saw - so `make verify-03` reported 130 for a gate that had
    finished with exit 1, and would have for one that finished green.

    Both statuses a gate ends with, 0 and 1, and a witness beside the wrapper
    proving the Ctrl-C actually arrived - without it, a Ctrl-C that never came
    would pass this vacuously. A wrapper exit of -1 means it printed a traceback.
    """
    results: dict[int, tuple[int, str]] = {}
    with TemporaryDirectory(prefix="repcut-gate03-ctrlc-", ignore_cleanup_errors=True) as scratch:
        for script_status in (0, 1):
            try:
                results[script_status] = _wrapper_status_after_ctrl_c(Path(scratch), script_status)
            except OSError as error:
                # Named: this environment will not let one process join another's
                # console. Not a verdict on the wrapper.
                measured(str(error))
                skipped("could not deliver a Ctrl-C on a console of the wrapper's own")
                return 2
    measured(
        "; ".join(
            f"script exit {status} -> wrapper exit {code} (witness {probe})"
            for status, (code, probe) in results.items()
        )
    )
    for status, (code, probe) in results.items():
        if probe != "got-INT":
            failed(f"the Ctrl-C never reached the processes under test ({probe})")
            return 1
        if code != status:
            failed(f"the script exited {status} and the wrapper reported {code}")
            return 1
    return 0


def _watch_analysis_steps(port: int, trigger: Callable[[], None]) -> list[str]:
    """Every `step` the ``analysis`` job reports over `/ws/jobs`, until it ends.

    Deliberately not `verify_02_checks.watch_jobs`: that helper stops at the
    FIRST terminal event it sees, which for an upload is ingest's (it finishes
    first, and analysis is only auto-enqueued after it) - reusing it here would
    return before the analysis job's own events, including the one this
    criterion exists to check, were ever collected. This watches until an
    ``analysis``-typed event itself reaches a terminal status.
    """
    import threading

    from websockets.asyncio.client import connect

    steps: list[str] = []

    async def _watch() -> None:
        async with connect(f"ws://127.0.0.1:{port}/ws/jobs") as socket:
            worker = threading.Thread(target=trigger, daemon=True)
            worker.start()
            async with asyncio.timeout(v2.INGEST_TIMEOUT_S):
                while True:
                    event = json.loads(await socket.recv())
                    if event.get("type") == "ping" or not event.get("job_id"):
                        continue
                    if event.get("job_type") != "analysis":
                        continue
                    step = event.get("step")
                    if isinstance(step, str):
                        steps.append(step)
                    if event.get("status") in ("succeeded", "failed", "cancelled"):
                        break
            worker.join(timeout=60)

    asyncio.run(_watch())
    return steps


def check_end_to_end_analysis() -> int:
    """17. Upload a fixture clip against a real `make dev` stack; see the analysis.

    Follows `verify_02_checks.check_assembled_stack`'s pattern exactly: a real
    `DevStack`, a real project, a real browser via `cdp_browser.inspect_page`.

    The disclosure is checked differently from the other two signals. It is
    genuinely transient - `PrivacyDisclosure.tsx` only renders while the
    running job's `step` matches "sending scene N of M to Gemini for
    analysis", and with no real Gemini key configured that step passes in
    well under a second - so a single post-hoc DOM snapshot (which is what
    `cdp_browser.inspect_page` takes) is not a reliable way to catch it: it
    was measured, while building this check, to miss the window entirely.
    Instead this connects to the real `/ws/jobs` socket the UI itself
    subscribes to (the same technique `verify_02_checks.watch_jobs` already
    uses) and watches for the exact step string during the run - which is, by
    `PrivacyDisclosure.tsx`'s own docstring, what actually *is* the
    disclosure ("that string appearing in the job stream is the moment
    frames are being sent"). Scene tags and the energy sparkline persist once
    populated, so those two are checked in the final DOM snapshot as before.
    """
    import dev_stack
    from cdp_browser import BrowserNotFoundError, inspect_page
    from websockets.exceptions import WebSocketException

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

        def _trigger() -> None:
            finalized = v2.upload(stack.engine_port, project_id, clip)
            if not finalized.get("sha256"):
                raise RuntimeError(f"upload failed: {finalized}")

        try:
            analysis_steps = _watch_analysis_steps(stack.engine_port, _trigger)
        except TimeoutError:
            measured("job watch timed out")
            failed("the analysis job never reached a terminal state over /ws/jobs")
            return 1
        except RuntimeError as error:
            measured("job watch failed")
            failed(f"could not observe the upload's jobs over /ws/jobs: {error}")
            return 1
        except (WebSocketException, OSError) as error:
            # Named: the socket dropped mid-watch with no close frame - the
            # engine went away, or the connection was reset under us (on
            # Windows, `ConnectionResetError: [WinError 64]`). Uncaught, this
            # criterion exits on a traceback and prints no MEASURED line at
            # all, which breaks this module's contract with `verify_03.sh`:
            # the run then reads as the gate itself dying rather than as this
            # criterion failing, and the other criteria never report.
            measured("job watch socket dropped")
            failed(f"the /ws/jobs socket closed before the analysis job finished: {error}")
            return 1

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
    has_scene_tags = bool(re.search(r"Scene\s+\d+", body))
    has_sparkline = bool(re.search(r"Energy across", body))
    disclosure_pattern = re.compile(r"^sending scene \d+ of \d+ to Gemini for analysis$")
    has_disclosure_step = any(disclosure_pattern.match(step) for step in analysis_steps)

    measured(
        f"scene_tags={has_scene_tags} sparkline={has_sparkline} "
        f"disclosure_step_seen={has_disclosure_step} (of {len(analysis_steps)} analysis steps) "
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
            ("the Gemini-send disclosure step", has_disclosure_step),
        )
        if not present
    ]
    if missing:
        failed(f"the per-clip view is missing: {', '.join(missing)}")
        return 1
    return 0


CHECKS: dict[str, Callable[[], int]] = {
    "migrations": check_migrations,
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
    "wrapper-keeps-status": check_wrapper_keeps_script_status,
    "end-to-end-analysis": check_end_to_end_analysis,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in CHECKS:
        print(f"usage: {Path(__file__).name} <{'|'.join(CHECKS)}>", file=sys.stderr)
        return 2
    try:
        return CHECKS[sys.argv[1]]()
    except ImportError as error:
        # A residual safety net, not the expected path any more: every import
        # above now names a real, shipped module/attribute. Kept so a future
        # rename surfaces here as a clean FAILED: line instead of a traceback.
        measured(f"import failed: {error}")
        failed(f"{error} - not implemented yet, or not in the shape this gate expects")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
