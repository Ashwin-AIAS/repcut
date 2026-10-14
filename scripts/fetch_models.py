#!/usr/bin/env python3
"""Fetch the model files the engine runs locally, once, digest-checked.

- silero-vad's ONNX model (MIT), speech detection (Prompt 05): a pinned release
  URL and a pinned sha256.
- faster-whisper "small" (MIT weights, CTranslate2 format), captions (Prompt
  06): from the faster-whisper authors' repository on Hugging Face.
- ``--gate`` adds two Piper voices (en_US ljspeech, de_DE thorsten; both
  trained on public-domain / CC0 speech) that ``verify-06`` uses to speak a
  KNOWN sentence for the caption accuracy check. Never used by the engine.

The engine never downloads anything itself; this script is the one place a model
arrives. For the Hugging Face files there is no digest in this repository yet
(they could not be fetched where this was written, amendment 019). So each
file is checked twice: against the sha256 Hugging Face publishes for it in the
``X-Linked-ETag`` header (integrity in transit), and, on every later run,
against ``PINNED.json`` written beside it at the first fetch (nothing changed
on disk). The digests and the repository commit are printed so they can be
pinned in code.

Writes to ``$DATA_DIR/models/`` (outside the repo, never committed). Idempotent.
Exit codes: 0 present or fetched and verified; 1 download or digest failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "engine"
sys.path.insert(0, str(ENGINE))

from repcut.captions.transcribe import (  # noqa: E402
    MODEL_FILES,
    MODEL_REPOSITORY,
    MODEL_STORED_DIR,
    PINS_FILENAME,
)
from repcut.config import get_settings  # noqa: E402
from repcut.edit.vad import MODEL_SHA256, MODEL_STORED_PATH, MODEL_URL  # noqa: E402
from repcut.media.store import absolute  # noqa: E402

HF = "https://huggingface.co"
VOICES_REPOSITORY = "rhasspy/piper-voices"
# (folder under models/, path inside the voices repository, file stem)
GATE_VOICES = (
    ("voice-en", "en/en_US/ljspeech/medium", "en_US-ljspeech-medium"),
    ("voice-de", "de/de_DE/thorsten/medium", "de_DE-thorsten-medium"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_vad(data_dir: Path) -> bool:
    target = absolute(data_dir, MODEL_STORED_PATH)
    if target.is_file() and _sha256(target) == MODEL_SHA256:
        print("present: speech detection model (verified)")
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    try:
        # A fixed https URL to a pinned release, never a value from config.
        with urllib.request.urlopen(MODEL_URL, timeout=60) as response:  # noqa: S310
            partial.write_bytes(response.read())
    except OSError as error:
        partial.unlink(missing_ok=True)
        print(f"FAILED: could not download the speech detection model ({type(error).__name__})")
        return False
    if _sha256(partial) != MODEL_SHA256:
        partial.unlink(missing_ok=True)
        print("FAILED: the downloaded model does not match its pinned digest; not kept")
        return False
    partial.replace(target)
    print("fetched: speech detection model (verified)")
    return True


def _download(url: str, target: Path) -> tuple[str | None, str | None]:
    """Stream ``url`` to ``target``; return (published sha256, repository commit)."""
    request = urllib.request.Request(url, headers={"User-Agent": "repcut-fetch-models"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        etag = response.headers.get("X-Linked-ETag") or ""
        commit = response.headers.get("X-Repo-Commit")
        with target.open("wb") as handle:
            for block in iter(lambda: response.read(1 << 20), b""):
                handle.write(block)
    published = etag.strip('"').removeprefix("W/").strip('"')
    return (published if len(published) == 64 else None), commit


def fetch_repository(label: str, folder: Path, repository: str, files: dict[str, str]) -> bool:
    """``files``: local name -> path inside the repository (fixed strings only)."""
    pins_path = folder / PINS_FILENAME
    if pins_path.is_file() and all((folder / name).is_file() for name in files):
        pins = json.loads(pins_path.read_text(encoding="utf-8"))
        bad = [n for n in files if _sha256(folder / n) != pins.get("sha256", {}).get(n)]
        if not bad:
            print(f"present: {label} (verified against {PINS_FILENAME})")
            return True
        print(f"changed on disk since it was fetched: {label} ({', '.join(bad)}); fetching again")
    folder.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    commit: str | None = None
    for name, remote in files.items():
        partial = folder / f"{name}.part"
        url = f"{HF}/{repository}/resolve/main/{remote}"
        try:
            published, commit_seen = _download(url, partial)
        except OSError as error:
            partial.unlink(missing_ok=True)
            print(f"FAILED: could not download {label} ({type(error).__name__})")
            return False
        digest = _sha256(partial)
        if published is not None and published != digest:
            partial.unlink(missing_ok=True)
            print(f"FAILED: {label}: {name} does not match the digest Hugging Face publishes")
            return False
        partial.replace(folder / name)
        digests[name] = digest
        commit = commit or commit_seen
    pins_path.write_text(
        json.dumps({"repository": repository, "commit": commit, "sha256": digests}, indent=2),
        encoding="utf-8",
    )
    print(f"fetched: {label} (repository commit {commit or 'unknown'})")
    for name, digest in digests.items():
        print(f"  sha256 {name}: {digest}")
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Fetch Repcut's local models once.")
    parser.add_argument("--gate", action="store_true", help="also fetch the gate's test voices")
    arguments = parser.parse_args(argv)
    data_dir = get_settings().data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    ok = fetch_vad(data_dir)
    ok = (
        fetch_repository(
            "speech-to-text model (faster-whisper small)",
            data_dir.joinpath(*MODEL_STORED_DIR.parts),
            MODEL_REPOSITORY,
            {name: name for name in MODEL_FILES},
        )
        and ok
    )
    if arguments.gate:
        for folder, path, stem in GATE_VOICES:
            ok = (
                fetch_repository(
                    f"gate voice {stem}",
                    data_dir / "models" / folder,
                    VOICES_REPOSITORY,
                    {
                        "voice.onnx": f"{path}/{stem}.onnx",
                        "voice.onnx.json": f"{path}/{stem}.onnx.json",
                    },
                )
                and ok
            )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
