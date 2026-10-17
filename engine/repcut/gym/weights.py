"""The two YOLO weight files: pinned, digest-checked before anything opens them.

Both from the Ultralytics assets release ``v8.4.0`` on GitHub (YOLO26 nano;
AGPL-3.0, the licence Repcut itself carries). Fetched once by
``scripts/fetch_models.py`` into ``$DATA_DIR/models/yolo/`` - outside the
repository, never committed. The digests were measured on the downloaded
files when this was written (2026-10-17), so they are pins, not
trust-on-first-use.

Security (guide section 7): Ultralytics opens a ``.pt`` file with
``torch.load``, which unpickles. Repcut cannot change that loader, so the
guarantee is the one it can give: nothing hands a weights file to it unless
the file's sha256 matches the pin *at that moment*. A swapped file is refused,
never loaded.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

_RELEASE: Final = "https://github.com/ultralytics/assets/releases/download/v8.4.0"


@dataclass(frozen=True, slots=True)
class PinnedWeights:
    label: str
    url: str
    sha256: str
    size_bytes: int
    stored_path: PurePosixPath


DETECT: Final = PinnedWeights(
    label="person detector (YOLO26n)",
    url=f"{_RELEASE}/yolo26n.pt",
    sha256="9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef",
    size_bytes=5_544_453,
    stored_path=PurePosixPath("models", "yolo", "yolo26n.pt"),
)
POSE: Final = PinnedWeights(
    label="pose model (YOLO26n-pose)",
    url=f"{_RELEASE}/yolo26n-pose.pt",
    sha256="eb3bb8268828aeaf515cec23a4bfafd793944a86fe9af94ba7823609c14522a9",
    size_bytes=7_878_574,
    stored_path=PurePosixPath("models", "yolo", "yolo26n-pose.pt"),
)


class WeightsMissingError(RuntimeError):
    """The weights are absent or are not the pinned file. A fixed sentence."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_weights(path: Path, pinned: PinnedWeights) -> None:
    if not path.is_file():
        raise WeightsMissingError(
            f"the {pinned.label} is not installed; run scripts/fetch_models.py once"
        )
    if path.stat().st_size != pinned.size_bytes or _sha256(path) != pinned.sha256:
        raise WeightsMissingError(
            f"the {pinned.label} does not match its pinned version; it was not loaded. "
            "Run scripts/fetch_models.py to replace it."
        )


__all__ = ["DETECT", "POSE", "PinnedWeights", "WeightsMissingError", "verify_weights"]
