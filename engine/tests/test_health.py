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

from repcut import __version__, probes
from repcut.models import HealthResponse

HEALTH_FIELDS = {
    "engine_version",
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


async def test_health_returns_all_ten_fields(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == HEALTH_FIELDS

    health = HealthResponse.model_validate(payload)
    assert health.engine_version == __version__
    assert health.torch_device_active in {"cuda", "cpu"}
    assert isinstance(health.gemini_api_key_set, bool)


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
