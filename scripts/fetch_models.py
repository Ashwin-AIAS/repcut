#!/usr/bin/env python3
"""Fetch the pinned model files the engine runs locally, once, digest-checked.

Today: silero-vad's ONNX model (MIT) for speech detection (Prompt 05). The
engine never downloads anything itself; this script is the one place a model
arrives, from a pinned release URL, and a file whose sha256 does not match is
deleted rather than kept. Idempotent: a present, matching file is left alone.

Writes to ``$DATA_DIR/models/`` (outside the repo, never committed).

Exit codes: 0 present or fetched and verified; 1 download or digest failure.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "engine"
sys.path.insert(0, str(ENGINE))

from repcut.config import get_settings  # noqa: E402
from repcut.edit.vad import MODEL_SHA256, MODEL_STORED_PATH, MODEL_URL  # noqa: E402
from repcut.media.store import absolute  # noqa: E402


def main() -> int:
    data_dir = get_settings().data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    target = absolute(data_dir, MODEL_STORED_PATH)
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == MODEL_SHA256:
        print("present: speech detection model (verified)")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    try:
        # A fixed https URL to a pinned release, never a value from config.
        with urllib.request.urlopen(MODEL_URL, timeout=60) as response:  # noqa: S310
            partial.write_bytes(response.read())
    except OSError as error:
        # Named: no network, DNS, TLS or HTTP failure. Nothing half-written stays.
        partial.unlink(missing_ok=True)
        print(f"FAILED: could not download the speech detection model ({type(error).__name__})")
        return 1
    if hashlib.sha256(partial.read_bytes()).hexdigest() != MODEL_SHA256:
        partial.unlink(missing_ok=True)
        print("FAILED: the downloaded model does not match its pinned digest; not kept")
        return 1
    partial.replace(target)
    print("fetched: speech detection model (verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
