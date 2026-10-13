"""The music library: ``$DATA_DIR/music/``, scanned, digested, analysed once.

The folder is the user's. Nothing is downloaded into it and nothing in it is
committed (`frontend-and-licensing.md`). What this module guarantees:

- **A track is a regular file directly in the folder**, with an audio suffix,
  under ``MAX_TRACK_BYTES``, and its resolved path is still in the folder - a
  symlink or a name that resolves elsewhere is ignored, not followed.
- **No path component derives from a track's name.** The name is shown in the
  picker; everything this module writes is keyed by the track's sha256
  (``store.music_cache_path``), and a track is re-found by digest, never by a
  stored name.
- **Decoding goes through the validated-source path** (``ffmpeg_builder``), so
  a file named like a protocol never reaches FFmpeg as one.
- **Analysis runs once per (digest, analysis version).**

The folder gets a README on first scan stating the licence rule, because the
rule belongs where the files are dropped.
"""

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.db.models import MusicTrack, MusicTrackStatus, utcnow
from repcut.media.ffmpeg_builder import FFmpegError, build_pcm_decode, run, temp_target
from repcut.media.store import absolute, music_cache_path, music_directory
from repcut.music.analysis import (
    ANALYSIS_SAMPLE_RATE,
    ANALYSIS_VERSION,
    TrackAnalysis,
    TrackAnalysisError,
    analyse,
)

AUDIO_SUFFIXES = frozenset({".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"})
MAX_TRACK_BYTES = 200 * 1024 * 1024
_CHUNK = 1024 * 1024

README_NAME = "README.md"
README_TEXT = """# Your Repcut music library

Drop tracks here (mp3, wav, flac, m4a, aac, ogg, opus). Repcut analyses each
one once for its beat, energy and drop, and recommends tracks for your edits.

**Only tracks you own or that are royalty-free for your use.** Repcut never
downloads music, and a track's licence is yours to check. If a track requires
attribution, note it in `LICENSES.md` here (source, licence, attribution).

Posting somewhere strict about music? Use "export without music": the edit
stays cut to the beat and you add the track inside the platform's own app.

This folder lives in your Repcut data folder, outside the code and outside any
cloud-sync folder. Nothing here is uploaded anywhere.
"""


@dataclass(frozen=True, slots=True)
class TrackFile:
    display_name: str
    path: Path
    size_bytes: int


def library_root(data_dir: Path) -> Path:
    root = absolute(data_dir, music_directory())
    root.mkdir(parents=True, exist_ok=True)
    readme = root / README_NAME
    if not readme.exists():
        readme.write_text(README_TEXT, encoding="utf-8")
    return root


def list_track_files(root: Path) -> list[TrackFile]:
    """Every acceptable track file directly in ``root``, sorted by name."""
    resolved_root = root.resolve()
    found: list[TrackFile] = []
    with os.scandir(resolved_root) as entries:
        for entry in entries:
            name = entry.name
            if (
                name.startswith(".")
                or entry.is_symlink()
                or not entry.is_file(follow_symlinks=False)
            ):
                continue
            if Path(name).suffix.casefold() not in AUDIO_SUFFIXES:
                continue
            path = Path(entry.path).resolve()
            if path.parent != resolved_root:
                continue
            size = entry.stat(follow_symlinks=False).st_size
            if 0 < size <= MAX_TRACK_BYTES:
                found.append(TrackFile(name[:255], path, size))
    return sorted(found, key=lambda track: track.display_name)


def digest_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            hasher.update(chunk)
    return hasher.hexdigest()


async def scan(session: AsyncSession, data_dir: Path) -> dict[str, TrackFile]:
    """Record every track in the folder; mark the rest absent. Returns digest -> file."""
    files = await asyncio.to_thread(list_track_files, library_root(data_dir))
    by_digest: dict[str, TrackFile] = {}
    for track in files:
        digest = await asyncio.to_thread(digest_file, track.path)
        by_digest.setdefault(digest, track)
    rows = {row.sha256: row for row in (await session.execute(select(MusicTrack))).scalars()}
    for digest, track in by_digest.items():
        row = rows.get(digest)
        if row is None:
            session.add(
                MusicTrack(
                    sha256=digest,
                    display_name=track.display_name,
                    size_bytes=track.size_bytes,
                    status=MusicTrackStatus.PENDING,
                    present=True,
                )
            )
            continue
        row.present = True
        row.display_name = track.display_name
        if row.analysis_version != ANALYSIS_VERSION and row.status != MusicTrackStatus.PENDING:
            row.status = MusicTrackStatus.PENDING
    for digest, row in rows.items():
        if digest not in by_digest:
            row.present = False
    await session.commit()
    return by_digest


async def decode(data_dir: Path, track: TrackFile, sha256: str) -> np.ndarray:
    """Mono float32 PCM at the analysis rate. The scratch file is removed after."""
    destination = absolute(data_dir, music_cache_path(sha256, "analysis.f32"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = temp_target(destination)
    try:
        await run(build_pcm_decode(track.path, temp, sample_rate=ANALYSIS_SAMPLE_RATE))
        return await asyncio.to_thread(np.fromfile, temp, dtype=np.float32)
    finally:
        temp.unlink(missing_ok=True)


async def analyse_track(
    session: AsyncSession, data_dir: Path, row: MusicTrack, track: TrackFile
) -> None:
    """Analyse one pending track and store the result, or the reason it cannot be used."""
    try:
        samples = await decode(data_dir, track, row.sha256)
        result = await asyncio.to_thread(analyse, samples, ANALYSIS_SAMPLE_RATE)
    except TrackAnalysisError as error:
        # Named: a track with no usable beat. Kept, shown as unusable, with why.
        row.status = MusicTrackStatus.REJECTED
        row.reason = error.reason
    except FFmpegError as error:
        # Named: FFmpeg could not decode it (not audio, truncated, unsupported).
        row.status = MusicTrackStatus.FAILED
        row.reason = error.cause
    else:
        row.status = MusicTrackStatus.ANALYSED
        row.reason = None
        row.bpm = result.bpm
        row.duration_seconds = result.duration_seconds
        row.analysis_json = json.dumps(result.to_json())
    row.analysis_version = ANALYSIS_VERSION
    row.analysed_at = utcnow()
    await session.commit()


def stored_analysis(row: MusicTrack) -> TrackAnalysis | None:
    if row.status != MusicTrackStatus.ANALYSED or row.analysis_json is None:
        return None
    if row.analysis_version != ANALYSIS_VERSION:
        return None
    return TrackAnalysis.from_json(json.loads(row.analysis_json))


async def find_file(data_dir: Path, sha256: str) -> TrackFile | None:
    """Re-find a track by digest. A renamed file is still found; a changed one is not."""
    files = await asyncio.to_thread(list_track_files, library_root(data_dir))
    for track in files:
        if await asyncio.to_thread(digest_file, track.path) == sha256:
            return track
    return None


__all__ = [
    "AUDIO_SUFFIXES",
    "MAX_TRACK_BYTES",
    "README_TEXT",
    "TrackFile",
    "analyse_track",
    "decode",
    "digest_file",
    "find_file",
    "library_root",
    "list_track_files",
    "scan",
    "stored_analysis",
]
