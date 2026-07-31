"""Capability probes for the local toolchain.

Both probes are deliberately synchronous and blocking: they shell out or touch
the CUDA driver. Callers must run them off the event loop via
``asyncio.to_thread``. They never raise - a missing tool is a reported
capability, not an error, because the engine has to boot on a machine with no
FFmpeg and no GPU at all.
"""

import importlib
import subprocess
from dataclasses import dataclass
from shutil import which

from repcut.config import TorchDevicePreference
from repcut.logging import get_logger
from repcut.models import TorchDeviceActive

logger = get_logger(__name__)

FFMPEG_PROBE_TIMEOUT_S = 10.0
_BYTES_PER_MB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class FfmpegProbe:
    """What the local FFmpeg install can do."""

    version: str | None
    has_libx264: bool


@dataclass(frozen=True, slots=True)
class TorchProbe:
    """What the local torch install and GPU can do."""

    cuda_available: bool
    gpu_name: str | None
    vram_free_mb: int | None
    vram_total_mb: int | None
    device: TorchDeviceActive


_TORCH_CPU = TorchProbe(
    cuda_available=False,
    gpu_name=None,
    vram_free_mb=None,
    vram_total_mb=None,
    device="cpu",
)
_FFMPEG_ABSENT = FfmpegProbe(version=None, has_libx264=False)


def _parse_ffmpeg_version(stdout: str) -> str | None:
    """Pull the bare version token out of ``ffmpeg -version`` output.

    First line looks like ``ffmpeg version 6.1.1-static https://...``; we want
    only the third token.
    """
    first_line = stdout.splitlines()[0] if stdout else ""
    tokens = first_line.split()
    if len(tokens) >= 3 and tokens[0] == "ffmpeg" and tokens[1] == "version":
        return tokens[2]
    return None


def probe_ffmpeg(executable: str = "ffmpeg") -> FfmpegProbe:
    """Report the FFmpeg version and whether the build includes libx264.

    Exports go through libx264 (see .claude/rules/ffmpeg.md), so a build without
    it is a capability gap the UI needs to know about up front.
    """
    if which(executable) is None:
        return _FFMPEG_ABSENT

    try:
        # Fixed argv list, never a shell string (.claude/rules/ffmpeg.md).
        completed = subprocess.run(
            [executable, "-version"],
            capture_output=True,
            text=True,
            timeout=FFMPEG_PROBE_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        # Named: FFmpeg is not installed or not on PATH. Health must still be 200.
        logger.warning("ffmpeg_probe_not_found")
        return _FFMPEG_ABSENT
    except subprocess.TimeoutExpired:
        # Named: a wedged or antivirus-stalled ffmpeg process. Do not hang /health.
        logger.warning("ffmpeg_probe_timeout", timeout_s=FFMPEG_PROBE_TIMEOUT_S)
        return _FFMPEG_ABSENT
    except PermissionError:
        # Named: the binary exists but is not executable by this user.
        logger.warning("ffmpeg_probe_permission_denied")
        return _FFMPEG_ABSENT
    except OSError:
        # Named: exec failures below PermissionError - bad image format, ENOMEM.
        logger.warning("ffmpeg_probe_os_error")
        return _FFMPEG_ABSENT

    if completed.returncode != 0:
        logger.warning("ffmpeg_probe_nonzero_exit", returncode=completed.returncode)
        return _FFMPEG_ABSENT

    stdout = completed.stdout or ""
    configuration = next(
        (line for line in stdout.splitlines() if line.startswith("configuration:")), ""
    )
    return FfmpegProbe(
        version=_parse_ffmpeg_version(stdout),
        has_libx264="libx264" in configuration,
    )


def probe_torch(preference: TorchDevicePreference = "auto") -> TorchProbe:
    """Report CUDA availability and VRAM headroom for the active device.

    ``preference`` mirrors the ``TORCH_DEVICE`` setting: ``cpu`` forces the CPU
    path without touching CUDA at all, ``auto`` and ``cuda`` probe the driver.
    CPU fallback is mandatory (.claude/rules/gpu-vram.md), so every failure path
    lands on the CPU answer rather than propagating.
    """
    if preference == "cpu":
        return _TORCH_CPU

    # Imported by name rather than with a static `import torch`: torch is not a
    # declared dependency (CUDA wheels are installed out-of-band, CI has no GPU),
    # so a static import would make type checking fail wherever torch is absent.
    try:
        torch = importlib.import_module("torch")
    except ImportError:
        # Named: torch is not installed. It is an optional dependency and the
        # engine must boot and serve /health without it.
        logger.info("torch_probe_not_installed")
        return _TORCH_CPU

    try:
        if not torch.cuda.is_available():
            return _TORCH_CPU
        gpu_name = str(torch.cuda.get_device_name(0))
        free_bytes, total_bytes = torch.cuda.mem_get_info()
    except RuntimeError:
        # Named: CUDA driver/runtime init failure - stale driver, no device,
        # WSL without a passthrough GPU. Degrade to CPU instead of crashing.
        logger.warning("torch_probe_cuda_init_failed")
        return _TORCH_CPU
    except OSError:
        # Named: the CUDA shared library is present but unloadable.
        logger.warning("torch_probe_cuda_library_unloadable")
        return _TORCH_CPU

    return TorchProbe(
        cuda_available=True,
        gpu_name=gpu_name,
        vram_free_mb=int(free_bytes) // _BYTES_PER_MB,
        vram_total_mb=int(total_bytes) // _BYTES_PER_MB,
        device="cuda",
    )


__all__ = [
    "FfmpegProbe",
    "TorchDeviceActive",
    "TorchProbe",
    "probe_ffmpeg",
    "probe_torch",
]
