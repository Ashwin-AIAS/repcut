"""Pydantic response models for the engine API.

These shapes are the contract with the UI and are mirrored by Zod schemas there,
so every field is explicitly typed and optionality is deliberate: a field is
``None`` exactly when the probe behind it could legitimately fail.
"""

from typing import Literal

from pydantic import BaseModel, Field

TorchDeviceActive = Literal["cuda", "cpu"]


class HealthResponse(BaseModel):
    """Capability report for the engine and its local toolchain.

    Contains no secret values and no machine-local paths - only whether a
    credential is configured.
    """

    engine_version: str = Field(description="Version of the repcut engine package.")
    event_loop: str = Field(
        description="Class name of the asyncio event loop the engine is serving on."
    )
    event_loop_can_spawn: bool = Field(
        description=(
            "Whether that loop can start subprocesses. False means no FFmpeg or "
            "ffprobe call can run, however healthy the rest of the toolchain looks."
        )
    )
    ffmpeg_version: str | None = Field(
        default=None,
        description="Detected FFmpeg version token, or null when FFmpeg is unavailable.",
    )
    ffmpeg_has_libx264: bool = Field(
        description="Whether the detected FFmpeg build was configured with libx264."
    )
    cuda_available: bool = Field(description="Whether torch reports a usable CUDA device.")
    gpu_name: str | None = Field(
        default=None, description="CUDA device name, or null when no GPU is in use."
    )
    vram_free_mb: int | None = Field(
        default=None, description="Free VRAM in MB, or null when no GPU is in use."
    )
    vram_total_mb: int | None = Field(
        default=None, description="Total VRAM in MB, or null when no GPU is in use."
    )
    torch_device_active: TorchDeviceActive = Field(
        description="Device the engine will actually run inference on."
    )
    data_dir_writable: bool = Field(
        description="Whether the configured data directory exists and accepts writes."
    )
    gemini_api_key_set: bool = Field(
        description="Whether a Gemini API key is configured. The value is never exposed."
    )
