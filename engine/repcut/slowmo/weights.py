"""The interpolation weights: one pinned file, verified before anything loads it.

Security (guide section 7): weights load from a pinned path with a recorded
checksum, and nothing calls ``torch.load`` on a file that has not just been
hashed against that checksum. ``load_verified`` is the only loader in the
codebase; it hashes the bytes it is about to read, and then reads them with
``weights_only=True``, which refuses pickled code even if a digest were ever
pinned to a hostile file.

The file: RIFE model 4.6 (Practical-RIFE, MIT), as published in the vs-rife
project's ``model`` release on GitHub. Fetched by ``scripts/fetch_models.py``
into ``$DATA_DIR/models/`` - outside the repository, never committed. The
digest was measured on the downloaded file when this was written, so it is a
pin, not trust-on-first-use.
"""

import hashlib
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Final

RIFE_MODEL_VERSION: Final = "4.6"
RIFE_URL: Final = "https://github.com/HolyWu/vs-rife/releases/download/model/flownet_v4.6.pkl"
RIFE_SHA256: Final = "008646e761f0e67cb77f0c6c44cfe3c3e5a05d9d9465311b9681ca650ce030db"
RIFE_SIZE_BYTES: Final = 21_273_159
RIFE_STORED_PATH: Final = PurePosixPath("models", "rife", "flownet_v4.6.pkl")

# The state dict is saved from a DataParallel wrapper: every key carries this prefix.
_KEY_PREFIX = "module."

NOT_INSTALLED = (
    "the slow-motion model is not installed; run scripts/fetch_models.py once. "
    "Slow-motion shots play at normal speed until then."
)
MISMATCH = (
    "the slow-motion model does not match its pinned version; it was not loaded. "
    "Run scripts/fetch_models.py to replace it."
)


class WeightsUnavailableError(RuntimeError):
    """The weights are missing or are not the pinned file. A fixed sentence."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(path: Path) -> None:
    """Raise unless ``path`` is the pinned weights file, byte for byte."""
    if not path.is_file():
        raise WeightsUnavailableError(NOT_INSTALLED)
    if path.stat().st_size != RIFE_SIZE_BYTES or sha256_of(path) != RIFE_SHA256:
        raise WeightsUnavailableError(MISMATCH)


# torch is optional (amendment 003) and passed in rather than imported, so this
# module stays importable - and its check testable - without it. Any: tensors.
def load_verified(path: Path, torch: ModuleType) -> dict[str, Any]:
    """The network's state dict, from a file verified immediately before it is read.

    Hash, then load: the check and the read are of the same path in the same
    call, so there is no window in which a verified file is swapped for another
    between a check made at startup and a load made later.
    """
    verify(path)
    raw = torch.load(path, map_location="cpu", weights_only=True)
    if not isinstance(raw, dict):
        raise WeightsUnavailableError(MISMATCH)
    return {
        str(key).removeprefix(_KEY_PREFIX): value
        for key, value in raw.items()
        if str(key).startswith(_KEY_PREFIX)
    }


__all__ = [
    "MISMATCH",
    "NOT_INSTALLED",
    "RIFE_MODEL_VERSION",
    "RIFE_SHA256",
    "RIFE_SIZE_BYTES",
    "RIFE_STORED_PATH",
    "RIFE_URL",
    "WeightsUnavailableError",
    "load_verified",
    "sha256_of",
    "verify",
]
