"""/health must answer 200 on any machine: no FFmpeg, no torch, no GPU."""

import subprocess
import sys
from collections.abc import Sequence
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import Never

import httpx
import pytest
from conftest import Harness

from repcut import __version__, probes
from repcut.api import jobs as jobs_api
from repcut.api.jobs import JOBS_SOCKET_PATH
from repcut.main import app as engine_app
from repcut.main import jobs_socket_ready
from repcut.models import HealthResponse

HEALTH_FIELDS = {
    "engine_version",
    # An FFmpeg on PATH is not the same as an FFmpeg the engine can run: under a
    # Windows selector loop it can see the binary and still fail every call
    # (see repcut.loop). /health has to report the loop, or it reports a
    # capability the engine does not have.
    "event_loop",
    "event_loop_can_spawn",
    # The socket is the whole of the job UI. It was green everywhere while the
    # panel said "Connecting to the engine…" forever, so /health has to carry a
    # verdict on it rather than leaving it the one capability nobody reports.
    "jobs_socket_ready",
    "ffmpeg_version",
    "ffmpeg_has_libx264",
    "cuda_available",
    "gpu_name",
    "vram_free_mb",
    "vram_total_mb",
    "torch_device_active",
    "data_dir_writable",
    "gemini_api_key_set",
}


class _TorchBlockingFinder(MetaPathFinder):
    """Meta path finder that makes ``import torch`` raise, simulating no torch."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        if fullname == "torch" or fullname.startswith("torch."):
            raise ImportError("simulated: torch is not installed")
        return None


def _simulate_no_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Purge any imported torch and block re-import for the duration of a test."""
    for name in [n for n in sys.modules if n == "torch" or n.startswith("torch.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_TorchBlockingFinder(), *sys.meta_path])


async def test_health_reports_every_capability_field(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == HEALTH_FIELDS

    health = HealthResponse.model_validate(payload)
    assert health.engine_version == __version__
    assert health.torch_device_active in {"cuda", "cpu"}
    assert isinstance(health.gemini_api_key_set, bool)
    # The suite runs on pytest-asyncio's loop, which comes from the default
    # policy and can spawn. Asserting it here is what makes the field's False
    # case meaningful: if this were ever False the whole media pipeline would be
    # dead, and the 51 tests that execute FFmpeg would say so first.
    assert health.event_loop_can_spawn is True
    assert health.event_loop
    assert isinstance(health.jobs_socket_ready, bool)


async def test_health_reports_the_jobs_socket_ready_on_a_booted_engine(api: Harness) -> None:
    """The socket is the whole of the job UI, so /health carries a verdict on it.

    It was green everywhere while the panel said "Connecting to the engine…"
    forever, because nothing reported on the one capability that was missing.
    """
    response = await api.client.get("/health")

    assert response.status_code == 200
    assert HealthResponse.model_validate(response.json()).jobs_socket_ready is True


async def test_health_reports_a_dead_job_worker(api: Harness) -> None:
    """A stopped worker makes the socket useless, and /health must say so.

    The socket still accepts a connection with no worker behind it: it replays
    an empty query and then streams nothing, forever, which on screen is an idle
    engine. That is the failure mode this field exists to name, and it is what
    makes the True above mean something.
    """
    await api.queue.stop()
    try:
        response = await api.client.get("/health")
    finally:
        await api.queue.start()

    assert response.status_code == 200
    assert HealthResponse.model_validate(response.json()).jobs_socket_ready is False


async def test_jobs_socket_ready_is_false_without_the_route(api: Harness) -> None:
    """Mounted at the path the UI asks for, or it is not ready.

    A rename of the route would otherwise leave /health green while every client
    got a 404 on the handshake.
    """
    jobs_router = jobs_api.router
    original = list(jobs_router.routes)
    jobs_router.routes = [
        route for route in original if getattr(route, "path", None) != JOBS_SOCKET_PATH
    ]
    try:
        assert jobs_socket_ready(engine_app) is False
    finally:
        jobs_router.routes = original
    # Restored, and the verdict with it - otherwise this test would leave every
    # later test in the session looking at an engine with no jobs socket.
    assert jobs_socket_ready(engine_app) is True


async def test_health_ok_without_torch(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _simulate_no_torch(monkeypatch)

    response = await client.get("/health")

    assert response.status_code == 200
    health = HealthResponse.model_validate(response.json())
    assert health.cuda_available is False
    assert health.torch_device_active == "cpu"
    assert health.gpu_name is None
    assert health.vram_free_mb is None
    assert health.vram_total_mb is None


async def test_health_ok_without_ffmpeg(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise_not_found(*args: object, **kwargs: object) -> Never:
        raise FileNotFoundError(2, "simulated: ffmpeg is not installed")

    monkeypatch.setattr(subprocess, "run", _raise_not_found)

    response = await client.get("/health")

    assert response.status_code == 200
    health = HealthResponse.model_validate(response.json())
    assert health.ffmpeg_version is None
    assert health.ffmpeg_has_libx264 is False


def test_probe_torch_honours_cpu_preference() -> None:
    """TORCH_DEVICE=cpu must not touch CUDA at all."""
    probe = probes.probe_torch("cpu")

    assert probe.device == "cpu"
    assert probe.cuda_available is False
    assert probe.gpu_name is None


def test_probe_ffmpeg_reports_absent_binary() -> None:
    probe = probes.probe_ffmpeg("repcut-nonexistent-ffmpeg-binary")

    assert probe.version is None
    assert probe.has_libx264 is False


def test_parse_ffmpeg_version_handles_garbage() -> None:
    assert probes._parse_ffmpeg_version("ffmpeg version 6.1.1-static https://example.invalid")
    assert probes._parse_ffmpeg_version("") is None
    assert probes._parse_ffmpeg_version("not ffmpeg output") is None
