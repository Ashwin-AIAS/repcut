"""Measurements behind `make verify-02`. One subcommand per gate criterion.

`verify_02.sh` owns the PASS/FAIL formatting; this owns the measuring. The split
exists because several of Prompt 02's criteria are not expressible as a shell
one-liner - killing an engine mid-transfer, computing A/V drift from packet
timestamps, watching a websocket - and a criterion that cannot be measured
honestly is, per `.claude/rules/testing.md`, a wish rather than a gate.

Contract with the shell, so a criterion cannot pass by printing nothing:

- exactly one ``MEASURED: <value>`` line on stdout, always, pass or fail
- ``FAILED: <reason>`` on stdout for a failure, then exit 1
- exit 0 only when the criterion actually holds

No absolute path is ever printed: ``$DATA_DIR`` carries the OS username on this
machine (`.claude/rules/secrets.md`).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Self

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "engine"))

ENGINE_BOOT_TIMEOUT_S = 40.0
INGEST_TIMEOUT_S = 180.0
REQUEST_TIMEOUT_S = 30.0

# `.claude/rules/testing.md` fixes the cut-to-beat tolerance at 40ms, and an
# ingest that drifts more than that has already lost the budget before a beat
# grid is even computed.
MAX_DRIFT_MS = 40.0

EXPECTED_TABLES = {
    "projects",
    "media_blobs",
    "media_files",
    "derived_artifacts",
    "upload_sessions",
    "jobs",
}


def measured(value: str) -> None:
    print(f"MEASURED: {value}")


def failed(reason: str) -> None:
    print(f"FAILED: {reason}")


# --- toolchain --------------------------------------------------------------


def ffprobe_json(path: Path, *args: str) -> dict[str, object]:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", *args, "-of", "json", path.as_posix()],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    document: dict[str, object] = json.loads(completed.stdout)
    return document


def make_clip(
    destination: Path,
    *,
    seconds: float = 3.0,
    width: int = 640,
    height: int = 360,
    fps: int = 30,
    audio: bool = True,
    variable_frame_rate: bool = False,
    rotation: int | None = None,
) -> Path:
    """A synthetic clip, generated here and deleted with the scratch directory.

    No media is committed (`.claude/rules/git-and-ci.md`), so the gate's inputs
    are built the same way the test suite's are.
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
        f"testsrc2=size={width}x{height}:rate={fps}",
    ]
    if audio:
        argv += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
    argv += ["-t", str(seconds)]
    if variable_frame_rate:
        argv += [
            "-vf",
            "select='not(mod(n,3))+not(mod(n,7))'",
            "-fps_mode",
            "passthrough",
        ]
    argv += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30"]
    argv += ["-c:a", "aac", "-shortest"] if audio else ["-an"]
    argv += [destination.as_posix()]
    subprocess.run(argv, capture_output=True, check=True, timeout=180)

    if rotation is not None:
        rotated = destination.with_name(f"r-{destination.name}")
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-display_rotation",
                str(rotation),
                "-i",
                destination.as_posix(),
                "-c",
                "copy",
                rotated.as_posix(),
            ],
            capture_output=True,
            check=True,
            timeout=180,
        )
        rotated.replace(destination)
    return destination


# --- a real engine, in a real subprocess ------------------------------------


def free_port() -> int:
    import socket

    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def scratch_environment(data_dir: Path) -> dict[str, str]:
    """Environment for an engine pointed entirely at a scratch directory.

    ``DATA_DIR`` and ``DATABASE_URL`` are both overridden so the gate can never
    touch the developer's own media store - a gate that writes into real data is
    not idempotent, whatever it prints.
    """
    environment = dict(os.environ)
    environment["DATA_DIR"] = str(data_dir)
    environment["DATABASE_URL"] = (
        f"sqlite+aiosqlite:///{(data_dir / 'repcut.db').as_posix()}"
    )
    environment["LOG_LEVEL"] = "WARNING"
    environment["PYTHONPATH"] = str(REPO_ROOT / "engine")
    return environment


def migrate(environment: dict[str, str]) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "engine/alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        check=True,
        timeout=180,
    )


def engine_argv(port: int, *, reload: bool = False) -> list[str]:
    """The command that starts an engine. ``python -m repcut``, never uvicorn.

    Spelling out a ``python -m uvicorn`` line here is what let the gate diverge
    from `make dev`: uvicorn selects a Windows event loop with no subprocess
    transport whenever ``--reload`` is passed, `make dev` passes it and this did
    not, so every criterion below measured a configuration nobody ran. See
    ``repcut/loop.py``. The entry point owns the loop; the gate just calls it.
    """
    argv = [
        sys.executable,
        "-m",
        "repcut",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    if reload:
        argv.append("--reload")
    return argv


def boot(
    port: int, environment: dict[str, str], *, reload: bool = False
) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        engine_argv(port, reload=reload),
        cwd=REPO_ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + ENGINE_BOOT_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("the engine exited during boot")
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=2.0
            ) as response:
                if response.status == 200:
                    return process
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.3)
    process.kill()
    raise RuntimeError("the engine did not become healthy in time")


def request(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    json_body: object = None,
) -> tuple[int, object]:
    """One HTTP call against the running engine. Returns (status, parsed body)."""
    payload = body
    headers = {"Host": "127.0.0.1"}
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif body is not None:
        headers["Content-Type"] = "application/octet-stream"

    call = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=payload, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(call, timeout=REQUEST_TIMEOUT_S) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def upload(
    port: int, project_id: str, clip: Path, *, chunk: int = 1 << 16
) -> dict[str, object]:
    """Declare, chunk, finalize - the browser's sequence."""
    import hashlib

    payload = clip.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    status, opened = request(
        port,
        "POST",
        f"/projects/{project_id}/uploads",
        json_body={
            "display_name": clip.name,
            "size_bytes": len(payload),
            "chunk_size_bytes": chunk,
            "sha256": digest,
        },
    )
    assert isinstance(opened, dict)
    # A rejection is a result, not an exception: criterion 3 is measured on the
    # error body, so the caller gets whatever the engine said and decides.
    if status >= 400:
        return opened

    upload_id = opened["id"]
    offset = int(opened["bytes_received"])
    while offset < len(payload):
        status, sent = request(
            port,
            "PUT",
            f"/uploads/{upload_id}/chunk?offset={offset}",
            body=payload[offset : offset + chunk],
        )
        assert isinstance(sent, dict)
        if status >= 400:
            return sent
        offset = int(sent["bytes_received"])

    _, finalized = request(port, "POST", f"/uploads/{upload_id}/finalize")
    assert isinstance(finalized, dict)
    return finalized


def await_jobs(port: int) -> list[dict[str, object]]:
    """Block until no job is queued or running. Returns every job, newest first."""
    deadline = time.monotonic() + INGEST_TIMEOUT_S
    while time.monotonic() < deadline:
        _, jobs = request(port, "GET", "/jobs")
        assert isinstance(jobs, list)
        if all(job["status"] not in ("queued", "running") for job in jobs):
            return [job for job in jobs if isinstance(job, dict)]
        time.sleep(0.4)
    raise RuntimeError("an ingest job did not finish in time")


def new_project(port: int, name: str) -> str:
    _, project = request(port, "POST", "/projects", json_body={"name": name})
    assert isinstance(project, dict)
    return str(project["id"])


def media_of(port: int, project_id: str) -> list[dict[str, object]]:
    _, library = request(port, "GET", f"/projects/{project_id}/media")
    assert isinstance(library, list)
    return [item for item in library if isinstance(item, dict)]


def store_bytes(data_dir: Path) -> int:
    media = data_dir / "media"
    if not media.exists():
        return 0
    return sum(path.stat().st_size for path in media.rglob("*") if path.is_file())


def kill_engine(process: subprocess.Popen[bytes]) -> None:
    """Kill an engine and everything it started, without warning.

    ``Popen.kill`` alone is not enough under ``--reload``: uvicorn's reloader is
    the parent and the server is a spawned child, so killing the parent leaves
    the child holding the port and the next ``free_port`` boot races it. On
    Windows ``taskkill /T`` walks the tree, and ``/F`` keeps it as unconditional
    as ``Popen.kill`` - which criterion 4 depends on, because a graceful
    shutdown would prove nothing about resume.
    """
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=60,
        )
    process.kill()
    process.wait(timeout=30)


class Engine:
    """A running engine over a scratch DATA_DIR, torn down whatever happens.

    ``reload=True`` boots it the way `make dev` does. That is not a detail: it
    is the only difference between the two, and it is the argument that decides
    which event loop uvicorn builds (``repcut/loop.py``).
    """

    def __init__(self, *, reload: bool = False) -> None:
        self.reload = reload
        # ignore_cleanup_errors: on Windows a killed process's file handles are
        # released asynchronously, so SQLite's db/-wal/-shm can still be locked
        # a moment after `wait()` returns. The directory is under the OS temp
        # root and gets collected there; failing the gate on a cleanup race
        # would report a bug in the engine that is not one.
        self._scratch = TemporaryDirectory(
            prefix="repcut-gate02-", ignore_cleanup_errors=True
        )
        self.data_dir = Path(self._scratch.name) / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.environment = scratch_environment(self.data_dir)
        self.port = free_port()
        self.process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> Self:
        migrate(self.environment)
        self.process = boot(self.port, self.environment, reload=self.reload)
        return self

    def restart(self) -> None:
        """Kill without warning and boot again - what criterion 4 requires.

        ``kill_engine`` is ``SIGKILL`` on POSIX and ``TerminateProcess`` on
        Windows. Both are unconditional: no atexit hook runs, no buffer is
        flushed, no session is closed. That is the point - a graceful shutdown
        would prove nothing about resume.
        """
        if self.process is not None:
            kill_engine(self.process)
        self.port = free_port()
        self.process = boot(self.port, self.environment, reload=self.reload)

    def __exit__(self, *_: object) -> None:
        if self.process is not None and self.process.poll() is None:
            kill_engine(self.process)
        self._scratch.cleanup()


# --- criteria ----------------------------------------------------------------


def check_migrations() -> int:
    """1. upgrade / downgrade / upgrade, then the schema amendment 004 specifies."""
    from sqlalchemy import create_engine as create_sync_engine
    from sqlalchemy import inspect

    with TemporaryDirectory(
        prefix="repcut-gate02-mig-", ignore_cleanup_errors=True
    ) as scratch:
        data_dir = Path(scratch) / "data"
        data_dir.mkdir(parents=True)
        environment = scratch_environment(data_dir)
        for step in ("upgrade head", "downgrade base", "upgrade head"):
            action, target = step.split()
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "alembic",
                    "-c",
                    "engine/alembic.ini",
                    action,
                    target,
                ],
                cwd=REPO_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=180,
            )
            if result.returncode != 0:
                failed(f"alembic {step} exited {result.returncode}")
                measured(f"alembic {step} -> exit {result.returncode}")
                return 1

        engine = create_sync_engine(f"sqlite:///{(data_dir / 'repcut.db').as_posix()}")
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        blob_columns = {
            column["name"] for column in inspector.get_columns("media_blobs")
        }
        unique = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("derived_artifacts")
        }
        engine.dispose()

    problems = []
    if not EXPECTED_TABLES <= tables:
        problems.append(f"missing tables {sorted(EXPECTED_TABLES - tables)}")
    for column in ("fps_source", "fps_normalized", "is_variable_frame_rate"):
        if column not in blob_columns:
            problems.append(f"media_blobs.{column} missing")
    if ("sha256", "artifact_kind", "params_version") not in unique:
        problems.append(
            "derived_artifacts is missing its (sha256, kind, version) unique key"
        )

    measured(
        f"3 alembic steps ok, {len(EXPECTED_TABLES & tables)}/6 tables, unique key present"
    )
    if problems:
        failed("; ".join(problems))
        return 1
    return 0


def check_rejects_non_video() -> int:
    """3. A `.txt` and an `.mp4`-named non-video: named errors, and no rows."""
    with (
        Engine() as engine,
        TemporaryDirectory(
            prefix="repcut-gate02-src-", ignore_cleanup_errors=True
        ) as scratch,
    ):
        project_id = new_project(engine.port, "rejection")
        note = Path(scratch) / "notes.txt"
        note.write_text("not a video", encoding="utf-8")
        impostor = Path(scratch) / "clip.mp4"
        impostor.write_bytes(b"PK\x03\x04 definitely not video" * 128)

        outcomes = []
        for clip in (note, impostor):
            result = upload(engine.port, project_id, clip)
            outcomes.append(result)

        library = media_of(engine.port, project_id)

    codes = [str(result.get("error", {}).get("code", "?")) for result in outcomes]  # type: ignore[union-attr]
    measured(f"errors={codes} media_files={len(library)}")

    if any("traceback" in json.dumps(result).casefold() for result in outcomes):
        failed("a rejection leaked a traceback to the client")
        return 1
    if codes != ["unsupported_media_type", "not_a_video"]:
        failed(f"expected named errors, got {codes}")
        return 1
    if library:
        failed(f"{len(library)} rows written for rejected uploads; expected 0")
        return 1
    return 0


def check_resume_across_a_kill() -> int:
    """4. Kill the engine mid-transfer, restart, resume without the session id.

    Deliberately harder than the criterion's letter. Resuming by session id only
    proves the *client* remembered; the client here throws the id away and finds
    its transfer by (project, hash), which is what a refreshed browser tab has.
    Run twice, to show the whole criterion is idempotent.
    """
    import hashlib

    with (
        Engine() as engine,
        TemporaryDirectory(
            prefix="repcut-gate02-src-", ignore_cleanup_errors=True
        ) as scratch,
    ):
        clip = make_clip(
            Path(scratch) / "session.mp4", seconds=3.0, width=1280, height=720
        )
        payload = clip.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        half = len(payload) // 2

        rounds = []
        for attempt in range(2):
            project_id = new_project(engine.port, f"resume {attempt}")
            _, opened = request(
                engine.port,
                "POST",
                f"/projects/{project_id}/uploads",
                json_body={
                    "display_name": clip.name,
                    "size_bytes": len(payload),
                    "chunk_size_bytes": 1 << 16,
                    "sha256": digest,
                },
            )
            assert isinstance(opened, dict)
            request(
                engine.port,
                "PUT",
                f"/uploads/{opened['id']}/chunk?offset=0",
                body=payload[:half],
            )

            engine.restart()

            # The id is gone, as it would be for a tab that refreshed.
            status, found = request(
                engine.port,
                "GET",
                f"/projects/{project_id}/uploads/in-progress?sha256={digest}",
            )
            if status != 200 or not isinstance(found, dict):
                failed("the resumed transfer could not be found by (project, hash)")
                measured(f"lookup after restart -> HTTP {status}")
                return 1

            offset = int(found["bytes_received"])
            while offset < len(payload):
                _, sent = request(
                    engine.port,
                    "PUT",
                    f"/uploads/{found['id']}/chunk?offset={offset}",
                    body=payload[offset : offset + (1 << 16)],
                )
                assert isinstance(sent, dict)
                offset = int(sent["bytes_received"])

            _, finalized = request(
                engine.port, "POST", f"/uploads/{found['id']}/finalize"
            )
            assert isinstance(finalized, dict)
            await_jobs(engine.port)
            rounds.append(
                {
                    "offset": int(found["bytes_received"]),
                    "sha256": finalized.get("sha256"),
                    "references": len(media_of(engine.port, project_id)),
                }
            )

    measured(
        f"killed at {half}B, resumed from {rounds[0]['offset']}B; "
        f"hash ok={rounds[0]['sha256'] == digest}; references={rounds[0]['references']}; "
        f"second run references={rounds[1]['references']}"
    )
    if rounds[0]["offset"] <= 0 or rounds[0]["offset"] > half:
        failed(
            f"the resume offset {rounds[0]['offset']} is not a server-side reconciliation"
        )
        return 1
    if any(round_["sha256"] != digest for round_ in rounds):
        failed("the assembled file did not hash to the declared digest")
        return 1
    if any(round_["references"] != 1 for round_ in rounds):
        failed(f"expected exactly one media_files row per project, got {rounds}")
        return 1
    return 0


def check_duplicate_links() -> int:
    """5. Same clip into two projects: one blob, two references, one proxy."""
    with (
        Engine() as engine,
        TemporaryDirectory(
            prefix="repcut-gate02-src-", ignore_cleanup_errors=True
        ) as scratch,
    ):
        clip = make_clip(Path(scratch) / "clip.mp4", seconds=2.0)

        leg_day = new_project(engine.port, "leg day")
        first = upload(engine.port, leg_day, clip)
        await_jobs(engine.port)
        bytes_before = store_bytes(engine.data_dir)
        jobs_before = len(await_jobs(engine.port))

        push_day = new_project(engine.port, "push day")
        second = upload(engine.port, push_day, clip)
        await_jobs(engine.port)
        bytes_after = store_bytes(engine.data_dir)
        jobs_after = len(await_jobs(engine.port))

        sources = list((engine.data_dir / "media" / "blobs").rglob("source.*"))
        library = media_of(engine.port, leg_day) + media_of(engine.port, push_day)

    measured(
        f"store {bytes_before}B -> {bytes_after}B, blobs on disk={len(sources)}, "
        f"references={len(library)}, ingest jobs {jobs_before} -> {jobs_after}"
    )
    if bytes_after != bytes_before:
        failed(f"the store grew by {bytes_after - bytes_before}B on a duplicate upload")
        return 1
    if len(sources) != 1:
        failed(f"{len(sources)} copies of the source on disk; expected 1")
        return 1
    if len(library) != 2:
        failed(f"{len(library)} references; expected 2")
        return 1
    if jobs_after != jobs_before:
        failed(
            "the duplicate upload enqueued an ingest job; it should re-encode nothing"
        )
        return 1
    if (
        second.get("sha256") != first.get("sha256")
        or second.get("duplicate") is not True
    ):
        failed("the second upload was not recognised as the same bytes")
        return 1
    return 0


def _packet_end(path: Path, stream: str) -> float:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            stream,
            "-show_entries",
            "packet=pts_time,duration_time",
            "-of",
            "csv=p=0",
            path.as_posix(),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )
    end = 0.0
    for line in completed.stdout.splitlines():
        parts = [part for part in line.strip().split(",") if part and part != "N/A"]
        if parts:
            end = max(
                end, float(parts[0]) + (float(parts[1]) if len(parts) > 1 else 0.0)
            )
    return end


def _proxy_path(data_dir: Path, sha256: str) -> Path:
    from repcut.media.artifacts import PARAMS_VERSION, ArtifactKind
    from repcut.media.store import absolute, derived_path

    return absolute(
        data_dir,
        derived_path(
            sha256,
            ArtifactKind.PROXY.value,
            PARAMS_VERSION[ArtifactKind.PROXY],
            "proxy.mp4",
        ),
    )


def check_vfr_normalization() -> int:
    """6. A VFR source becomes a CFR proxy, and the audio still lines up."""
    with (
        Engine() as engine,
        TemporaryDirectory(
            prefix="repcut-gate02-src-", ignore_cleanup_errors=True
        ) as scratch,
    ):
        clip = make_clip(
            Path(scratch) / "vfr.mp4", seconds=4.0, variable_frame_rate=True
        )
        before = ffprobe_json(
            clip,
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate,avg_frame_rate",
        )["streams"][0]  # type: ignore[index]

        project_id = new_project(engine.port, "vfr")
        finalized = upload(engine.port, project_id, clip)
        await_jobs(engine.port)

        proxy = _proxy_path(engine.data_dir, str(finalized["sha256"]))
        after = ffprobe_json(
            proxy,
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate,avg_frame_rate",
        )["streams"][0]  # type: ignore[index]
        drift_ms = abs(_packet_end(proxy, "v:0") - _packet_end(proxy, "a:0")) * 1000
        recorded = media_of(engine.port, project_id)[0]

    measured(
        f"source r/avg={before['r_frame_rate']}/{before['avg_frame_rate']} -> "  # type: ignore[index]
        f"proxy {after['r_frame_rate']}/{after['avg_frame_rate']}, "  # type: ignore[index]
        f"A/V drift {drift_ms:.1f}ms (budget {MAX_DRIFT_MS:.0f}ms), "
        f"is_variable_frame_rate={recorded['is_variable_frame_rate']}"
    )
    if before["r_frame_rate"] == before["avg_frame_rate"]:  # type: ignore[index]
        failed("the fixture is not VFR, so this criterion measured nothing")
        return 1
    if after["r_frame_rate"] != after["avg_frame_rate"]:  # type: ignore[index]
        failed("the proxy is not constant frame rate")
        return 1
    if drift_ms >= MAX_DRIFT_MS:
        failed(f"A/V drift {drift_ms:.1f}ms exceeds the {MAX_DRIFT_MS:.0f}ms budget")
        return 1
    if recorded["is_variable_frame_rate"] is not True:
        failed("an MP4 source measured as VFR was not recorded as VFR")
        return 1
    return 0


def check_vfr_unknown_is_null() -> int:
    """6b. A container that cannot answer stores NULL, never False.

    Not in the guide's criterion list; added because the r-versus-avg heuristic
    was measured to be container-dependent while this prompt was being built.
    Prompt 05's beat sync reads this column, and a False that means "nobody
    measured" reintroduces exactly the drift criterion 6 exists to catch.
    """
    with (
        Engine() as engine,
        TemporaryDirectory(
            prefix="repcut-gate02-src-", ignore_cleanup_errors=True
        ) as scratch,
    ):
        matroska = make_clip(
            Path(scratch) / "vfr.mkv", seconds=4.0, variable_frame_rate=True
        )
        rates = ffprobe_json(
            matroska,
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate,avg_frame_rate",
        )["streams"][0]  # type: ignore[index]

        project_id = new_project(engine.port, "matroska")
        upload(engine.port, project_id, matroska)
        await_jobs(engine.port)
        recorded = media_of(engine.port, project_id)[0]

    stored = recorded["is_variable_frame_rate"]
    measured(
        f"matroska r/avg={rates['r_frame_rate']}/{rates['avg_frame_rate']} "  # type: ignore[index]
        f"(heuristic says {'variable' if rates['r_frame_rate'] != rates['avg_frame_rate'] else 'constant'}), "  # type: ignore[index]
        f"stored={stored!r}"
    )
    if stored is not None:
        failed(
            f"an unanswerable container stored {stored!r}; NULL means unknown, False is a claim"
        )
        return 1
    return 0


def check_rotation() -> int:
    """7. A rotation tag makes the stored resolution the display resolution."""
    with (
        Engine() as engine,
        TemporaryDirectory(
            prefix="repcut-gate02-src-", ignore_cleanup_errors=True
        ) as scratch,
    ):
        clip = make_clip(
            Path(scratch) / "portrait.mp4",
            seconds=2.0,
            width=1280,
            height=720,
            rotation=90,
        )
        coded = ffprobe_json(
            clip, "-select_streams", "v:0", "-show_entries", "stream=width,height"
        )["streams"][0]  # type: ignore[index]

        project_id = new_project(engine.port, "rotation")
        upload(engine.port, project_id, clip)
        await_jobs(engine.port)
        recorded = media_of(engine.port, project_id)[0]

    measured(
        f"coded {coded['width']}x{coded['height']} (rotation {recorded['rotation_degrees']}) -> "  # type: ignore[index]
        f"stored display {recorded['display_width']}x{recorded['display_height']}"
    )
    if (recorded["display_width"], recorded["display_height"]) != (720, 1280):
        failed("the stored resolution is the container's, not the display's")
        return 1
    if recorded["rotation_degrees"] != 90:
        failed(
            f"the rotation tag was read as {recorded['rotation_degrees']}, expected 90"
        )
        return 1
    return 0


def check_ingest_artifacts() -> int:
    """8. Strip cell count, proxy resolution, codec, duration and audio rate."""
    import math

    from repcut.media.artifacts import (
        PARAMS_VERSION,
        PROXY_RECIPE,
        THUMBNAIL_STRIP_RECIPE,
        ArtifactKind,
    )
    from repcut.media.store import absolute, derived_path

    seconds = 5.0
    with (
        Engine() as engine,
        TemporaryDirectory(
            prefix="repcut-gate02-src-", ignore_cleanup_errors=True
        ) as scratch,
    ):
        clip = make_clip(
            Path(scratch) / "clip.mp4",
            seconds=seconds,
            width=1920,
            height=1080,
            audio=True,
        )
        project_id = new_project(engine.port, "artifacts")
        finalized = upload(engine.port, project_id, clip)
        await_jobs(engine.port)
        digest = str(finalized["sha256"])

        proxy = _proxy_path(engine.data_dir, digest)
        strip = absolute(
            engine.data_dir,
            derived_path(
                digest,
                ArtifactKind.THUMBNAIL_STRIP.value,
                PARAMS_VERSION[ArtifactKind.THUMBNAIL_STRIP],
                "strip.jpg",
            ),
        )
        video = ffprobe_json(
            proxy, "-select_streams", "v:0", "-show_entries", "stream=codec_name,height"
        )["streams"][0]  # type: ignore[index]
        audio = ffprobe_json(
            proxy, "-select_streams", "a:0", "-show_entries", "stream=sample_rate"
        )["streams"][0]  # type: ignore[index]
        duration = float(
            ffprobe_json(proxy, "-show_entries", "format=duration")["format"][
                "duration"
            ]  # type: ignore[index]
        )
        tile = ffprobe_json(strip, "-show_entries", "stream=width,height")["streams"][0]  # type: ignore[index]

    expected_cells = max(
        1, math.ceil(seconds / THUMBNAIL_STRIP_RECIPE.seconds_per_frame)
    )
    cell_width = int(tile["width"]) / expected_cells
    measured(
        f"proxy {video['codec_name']} {video['height']}p {duration:.2f}s "  # type: ignore[index]
        f"audio {audio['sample_rate']}Hz; strip {tile['width']}x{tile['height']} "  # type: ignore[index]
        f"= {expected_cells} cells"
    )
    if video["codec_name"] != "h264" or int(video["height"]) != PROXY_RECIPE.height:  # type: ignore[index]
        failed("the proxy is not 720p H.264")
        return 1
    if int(audio["sample_rate"]) != PROXY_RECIPE.audio_sample_rate:  # type: ignore[index]
        failed("the proxy audio is not at the project sample rate")
        return 1
    if abs(duration - seconds) > 0.1:
        failed(
            f"the proxy is {duration:.2f}s, more than 0.1s from the source's {seconds:.2f}s"
        )
        return 1
    if int(tile["height"]) != THUMBNAIL_STRIP_RECIPE.height or cell_width != int(
        cell_width
    ):  # type: ignore[index]
        failed("the thumbnail strip is not ceil(duration/2) whole cells")
        return 1
    return 0


def watch_jobs(port: int, trigger: Callable[[], None]) -> list[dict[str, object]]:
    """Open ``/ws/jobs``, run ``trigger``, and collect events to a terminal one.

    The ordering is the whole point and is the ordering a browser must use too:
    subscribe first, *then* cause the job. Triggering first loses the ``queued``
    event, and for a job that finishes quickly loses the terminal event as well -
    which is how the first version of criterion 9b hung until its timeout rather
    than failing with a reason.

    ``websockets`` comes with ``uvicorn[standard]``, so the gate speaks the
    protocol through the same library the engine serves it with and adds no
    dependency of its own (P5). ``trigger`` runs on a thread because it is
    blocking HTTP and the socket has to stay drained while it works.
    """
    import asyncio
    import threading

    from websockets.asyncio.client import connect

    events: list[dict[str, object]] = []

    async def _watch() -> None:
        async with connect(f"ws://127.0.0.1:{port}/ws/jobs") as socket:
            worker = threading.Thread(target=trigger, daemon=True)
            worker.start()
            async with asyncio.timeout(INGEST_TIMEOUT_S):
                while True:
                    event = json.loads(await socket.recv())
                    if event.get("type") == "ping" or not event.get("job_id"):
                        continue
                    events.append(event)
                    if event.get("status") in ("succeeded", "failed", "cancelled"):
                        break
            worker.join(timeout=60)

    asyncio.run(_watch())
    return events


def check_job_lifecycle() -> int:
    """9. queued -> running (monotonic, named step) -> succeeded, over the socket."""
    with (
        Engine() as engine,
        TemporaryDirectory(
            prefix="repcut-gate02-src-", ignore_cleanup_errors=True
        ) as scratch,
    ):
        clip = make_clip(Path(scratch) / "clip.mp4", seconds=2.0)
        project_id = new_project(engine.port, "lifecycle")
        events = watch_jobs(
            engine.port, lambda: upload(engine.port, project_id, clip) and None
        )

    statuses = [str(event["status"]) for event in events]
    progress = [float(event["progress"]) for event in events]
    steps = [event.get("step") for event in events if event["status"] == "running"]

    measured(
        f"statuses={statuses} progress={[round(value, 2) for value in progress]} "
        f"steps={[step for step in steps if step]}"
    )
    if not statuses or statuses[0] != "queued":
        failed(f"the sequence did not start at queued: {statuses}")
        return 1
    if "running" not in statuses:
        failed("no running event was ever observed - that is a spinner, not progress")
        return 1
    if statuses[-1] != "succeeded":
        failed(f"the job ended {statuses[-1]}, not succeeded")
        return 1
    if progress != sorted(progress):
        failed(f"progress was not monotonic: {progress}")
        return 1
    if not any(step for step in steps):
        failed("running events carried no step name")
        return 1
    return 0


def check_failed_job_has_a_cause() -> int:
    """9b. A forced failure ends in `failed` with a cause and no traceback.

    Forced by deleting the blob's bytes and re-ingesting: a real condition the
    engine has to describe, not a mock. A non-video cannot be used here because
    it is rejected before a job exists - which is itself the right behaviour.
    """
    with (
        Engine() as engine,
        TemporaryDirectory(
            prefix="repcut-gate02-src-", ignore_cleanup_errors=True
        ) as scratch,
    ):
        clip = make_clip(Path(scratch) / "real.mp4", seconds=1.0)
        project_id = new_project(engine.port, "failure")
        upload(engine.port, project_id, clip)
        await_jobs(engine.port)
        media_file_id = str(media_of(engine.port, project_id)[0]["id"])

        for path in (engine.data_dir / "media" / "blobs").rglob("source.*"):
            path.unlink()

        reingest_status: list[int] = []

        def _reingest() -> None:
            status, _ = request(engine.port, "POST", f"/media/{media_file_id}/reingest")
            reingest_status.append(status)

        events = watch_jobs(engine.port, _reingest)

    if reingest_status and reingest_status[0] != 202:
        failed(f"reingest returned HTTP {reingest_status[0]}")
        measured(f"reingest -> HTTP {reingest_status[0]}")
        return 1

    terminal = events[-1] if events else {}
    cause = str(terminal.get("error") or "")
    measured(f"status={terminal.get('status')} cause={cause[:70]!r}")

    if terminal.get("status") != "failed":
        failed(f"a job over missing bytes ended {terminal.get('status')}, not failed")
        return 1
    if not cause:
        failed("the failure carried no cause for the UI to render")
        return 1
    if "Traceback" in cause or "\n" in cause or 'File "' in cause:
        failed("the failure cause carried a traceback")
        return 1
    return 0


def check_dev_configuration() -> int:
    """17. The engine `make dev` starts completes upload -> finalize -> ingest.

    The criterion the other twenty were missing. Every one of them booted a real
    uvicorn subprocess and drove real HTTP, so "in-process ASGI" was never the
    gap - the gap was narrower and easier to miss: the gate booted the engine
    *without* ``--reload`` and `make dev` boots it *with*. On Windows that single
    argument changes the event loop uvicorn builds, from one with a subprocess
    transport to one without, and every FFmpeg call the engine makes needs one.
    So the gate proved the pipeline worked in a configuration nobody ran, while
    the only configuration anyone did run failed on every upload.

    Three assertions, in the order they would fail:

    1. ``scripts/dev.sh`` launches the shared entry point. If a future edit
       inlines a uvicorn line again, the divergence is caught here rather than
       by a person with 2GB of footage.
    2. ``/health`` reports a loop that can spawn subprocesses. Cheap, and it
       localises the failure - a red here means the loop, not the pipeline.
    3. Upload, finalize and ingest actually complete, under ``--reload``. This
       is the measurement; 1 and 2 only explain it.
    """
    # Comment lines are dropped before matching. The comment in dev.sh that
    # explains why `-m uvicorn` must not be used says `-m uvicorn`, and a plain
    # substring search over the file reads the warning as the violation.
    dev_lines = [
        line
        for line in (REPO_ROOT / "scripts" / "dev.sh").read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    dev_code = "\n".join(dev_lines)
    launches_entry_point = "-m repcut" in dev_code
    inlines_uvicorn = "-m uvicorn" in dev_code

    with (
        Engine(reload=True) as engine,
        TemporaryDirectory(prefix="repcut-gate02-src-", ignore_cleanup_errors=True) as scratch,
    ):
        _, health = request(engine.port, "GET", "/health")
        assert isinstance(health, dict)

        clip = make_clip(Path(scratch) / "devconfig.mp4", seconds=2.0)
        project_id = new_project(engine.port, "dev configuration")
        finalized = upload(engine.port, project_id, clip)
        jobs = await_jobs(engine.port)
        library = media_of(engine.port, project_id)

    loop_name = str(health.get("event_loop"))
    can_spawn = health.get("event_loop_can_spawn")
    error = finalized.get("error")
    statuses = [str(job["status"]) for job in jobs]

    measured(
        f"dev.sh -m repcut={launches_entry_point}, loop={loop_name} "
        f"can_spawn={can_spawn}, finalize={'ok' if finalized.get('sha256') else error}, "
        f"ingest={statuses}, references={len(library)}"
    )

    if not launches_entry_point or inlines_uvicorn:
        failed("scripts/dev.sh does not launch `python -m repcut`; the gate boots something else")
        return 1
    if can_spawn is not True:
        failed(f"the engine's own loop ({loop_name}) cannot spawn subprocesses; FFmpeg cannot run")
        return 1
    if not finalized.get("sha256"):
        failed(f"finalize failed under the dev configuration: {error}")
        return 1
    if statuses != ["succeeded"]:
        failed(f"ingest under the dev configuration ended {statuses}, not ['succeeded']")
        return 1
    if len(library) != 1:
        failed(f"{len(library)} media_files rows after a successful upload; expected 1")
        return 1
    return 0


def check_finalize_never_leaks_a_path() -> int:
    """18. No /finalize response body carries a traceback or the OS username.

    Measured against the wire, not against intent: the bodies of a rejected
    upload, a duplicate, an incomplete transfer and a successful finalize are
    all read as raw bytes and searched. ``Users`` is the check that matters on
    this machine - the repository and ``$DATA_DIR`` both sit under a directory
    named for the OS user, so a leaked path is a leaked username
    (`.claude/rules/secrets.md`).

    The engine-side unit test (``test_error_boundary.py``) covers the same
    property against a forced exception, which is the case that actually
    regressed. This is the end-to-end half: it proves the real server, over real
    HTTP, answers JSON rather than Starlette's plain-text 500.
    """
    import hashlib

    bodies: dict[str, bytes] = {}

    with (
        Engine() as engine,
        TemporaryDirectory(prefix="repcut-gate02-src-", ignore_cleanup_errors=True) as scratch,
    ):
        project_id = new_project(engine.port, "leak check")

        # A finalize that must fail: the session is opened and nothing is sent.
        clip = make_clip(Path(scratch) / "leak.mp4", seconds=1.0)
        payload = clip.read_bytes()
        _, opened = request(
            engine.port,
            "POST",
            f"/projects/{project_id}/uploads",
            json_body={
                "display_name": clip.name,
                "size_bytes": len(payload),
                "chunk_size_bytes": 1 << 16,
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
        )
        assert isinstance(opened, dict)
        bodies["incomplete"] = raw_request(engine.port, f"/uploads/{opened['id']}/finalize")
        bodies["unknown-session"] = raw_request(
            engine.port, "/uploads/00000000-0000-4000-8000-000000000000/finalize"
        )

        # A finalize that must succeed, then the same one again (the idempotent
        # path), then one that must be rejected on its bytes.
        finalized = upload(engine.port, project_id, clip)
        assert finalized.get("sha256"), f"setup upload failed: {finalized}"
        await_jobs(engine.port)
        bodies["success"] = json.dumps(finalized).encode("utf-8")
        bodies["already-finalized"] = raw_request(
            engine.port, f"/uploads/{opened['id']}/finalize"
        )

        impostor = Path(scratch) / "impostor.mp4"
        impostor.write_bytes(b"PK\x03\x04 definitely not video" * 128)
        rejected = upload(engine.port, project_id, impostor)
        bodies["not-a-video"] = json.dumps(rejected).encode("utf-8")

    offenders = []
    for name, body in bodies.items():
        text = body.decode("utf-8", errors="replace")
        if "Users" in text or "Traceback" in text or 'File "' in text:
            offenders.append(name)
        # A body the UI cannot parse is its own failure: Starlette's unhandled
        # 500 is 21 bytes of text/plain, which is what this criterion caught.
        try:
            json.loads(text or "null")
        except ValueError:
            offenders.append(f"{name}(not-json)")

    measured(f"{len(bodies)} finalize responses checked, {len(offenders)} offending")
    if offenders:
        failed(f"finalize responses leaked a path or were not JSON: {offenders}")
        return 1
    return 0


def raw_request(port: int, path: str) -> bytes:
    """POST and return the response body verbatim, whatever the status.

    ``request`` parses JSON, which throws away exactly the evidence this
    criterion needs: the failure it was written for answered ``text/plain``.
    """
    call = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method="POST", headers={"Host": "127.0.0.1"}
    )
    try:
        with urllib.request.urlopen(call, timeout=REQUEST_TIMEOUT_S) as response:
            body: bytes = response.read()
            return body
    except urllib.error.HTTPError as error:
        errored: bytes = error.read()
        return errored



def check_launcher_shell_resolution() -> int:
    """21. `make` never spawns a bare `bash`, and `dev.sh` refuses WSL.

    Criterion 19 drives the launcher's whole lifecycle, and it was green
    throughout a session in which `make dev` was unusable. 19 spawns the script
    through ``dev_stack.bash_executable()``, a resolver that already rejected
    WSL; the Makefile spawned ``bash`` by bare name and did not. ``CreateProcess``
    searches ``System32`` before ``PATH``, ``System32\bash.exe`` is WSL's
    launcher, and a PowerShell ``PATH`` carries System32 but not Git's
    ``usr/bin`` - so the gate ran the launcher in Git Bash and the person ran it
    in a Linux VM.

    Nothing inside the script could see it. Interop still starts the Windows
    executables, so the stack comes up on the host; only the *observations* are
    made in the wrong namespace, which is why the symptom was a 90s timeout
    against an engine that had already logged "Application startup complete".

    So this criterion asserts a spawn decision rather than a behaviour -
    statically, because reproducing the real thing needs a second operating
    system, and a gate that needs WSL installed is a gate that gets skipped.
    """
    findings: list[str] = []

    # 1. No recipe spawns a shell by bare name. Anchored to recipe lines (a tab
    #    at column 0) so the header comment's prose is not matched.
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    bare: list[str] = []
    for number, line in enumerate(makefile.splitlines(), start=1):
        if not line.startswith("\t"):
            continue
        if re.match(r"(bash|sh)\s+\S", line.lstrip("\t").lstrip("@-+")):
            bare.append(f"Makefile:{number}")
    if bare:
        findings.append("recipe spawns a bare shell at " + ", ".join(bare))

    # 2. The resolver refuses WSL's launcher even when it is the only `bash` on
    #    PATH - which is precisely what a PowerShell PATH looks like. Asserted
    #    against a fabricated System32 so it holds on the Linux CI runner too.
    import posix_shell

    with TemporaryDirectory(prefix="repcut-gate02-shell-") as scratch:
        fake_system32 = Path(scratch) / "System32"
        fake_system32.mkdir()
        launcher = fake_system32 / ("bash.exe" if sys.platform == "win32" else "bash")
        launcher.write_text("", encoding="utf-8")
        launcher.chmod(0o755)

        saved = os.environ.copy()
        try:
            os.environ["SystemRoot"] = scratch
            os.environ["PATH"] = str(fake_system32)
            os.environ.pop("REPCUT_BASH", None)
            try:
                resolved = posix_shell.bash_executable()
            except posix_shell.ShellNotFoundError:
                # The right answer when the only candidate is WSL and no Git
                # Bash is installed: refuse, rather than return the VM.
                resolved = ""
        finally:
            os.environ.clear()
            os.environ.update(saved)

        if resolved and str(Path(resolved)).lower().startswith(str(fake_system32).lower()):
            findings.append("bash_executable() returned WSL's launcher when it was first on PATH")

    # 3. `dev.sh` refuses WSL on its own account, for whoever types
    #    `bash scripts/dev.sh` rather than `make dev`.
    dev_sh = (REPO_ROOT / "scripts" / "dev.sh").read_text(encoding="utf-8")
    if "/proc/version" not in dev_sh or "refusing to run" not in dev_sh:
        findings.append("scripts/dev.sh has no WSL refusal guard")

    if findings:
        failed("; ".join(findings))
        return 1

    measured("Makefile spawns no bare shell; resolver and dev.sh both refuse WSL")
    return 0


def _dev_stack_check(name: str) -> Callable[[], int]:
    """Defer importing ``dev_stack`` until the criterion actually runs.

    It starts real servers and drives a browser, so importing it eagerly would
    pull that machinery into every other criterion's process for nothing.
    """

    def run() -> int:
        import dev_stack

        checker: Callable[[], int] = getattr(dev_stack, name)
        return checker()

    return run


CHECKS: dict[str, Callable[[], int]] = {
    "migrations": check_migrations,
    "rejects-non-video": check_rejects_non_video,
    "resume": check_resume_across_a_kill,
    "duplicate": check_duplicate_links,
    "vfr": check_vfr_normalization,
    "vfr-unknown": check_vfr_unknown_is_null,
    "rotation": check_rotation,
    "artifacts": check_ingest_artifacts,
    "lifecycle": check_job_lifecycle,
    "failure-cause": check_failed_job_has_a_cause,
    "dev-configuration": check_dev_configuration,
    "finalize-no-leak": check_finalize_never_leaks_a_path,
    "dev-launcher": _dev_stack_check("check_dev_launcher"),
    "assembled-stack": _dev_stack_check("check_assembled_stack"),
    "launcher-shell": check_launcher_shell_resolution,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in CHECKS:
        print(f"usage: {Path(__file__).name} <{'|'.join(CHECKS)}>", file=sys.stderr)
        return 2
    return CHECKS[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())
