"""Orphan collection for the content-addressed store (amendment 004, owed here).

Three kinds of bytes outlive what made them, and nothing else ever removes
them:

- **blob folders** under ``media/blobs/`` and **derived folders** under
  ``media/derived/`` whose sha256 has no ``media_blobs`` row (an ingest that
  failed after writing, a database restored from an older copy);
- **abandoned transfers**: an ``uploads/*.part`` with no session that is still
  in progress, or whose in-progress session has not moved for
  ``abandoned_after`` (the session is then marked aborted - its client can
  start again, it cannot resume bytes that are gone);
- **render partials** (``.<stem>.<8 hex>.partial<suffix>``, see
  ``ffmpeg_builder.temp_target``) older than ``partial_after``: a render that
  was killed. A younger one may be a render still writing, so it stays.

Everything referenced stays, and so do superseded ``params_version`` folders
(a recipe bump is non-destructive, amendment 004 §6). ``collect`` with
``apply=False`` only reports. Every path is resolved through ``store.absolute``
so nothing outside ``$DATA_DIR`` can be named, let alone removed.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.db.models import MediaBlob, UploadSession, UploadStatus, utcnow
from repcut.media.store import absolute

ABANDONED_AFTER = timedelta(days=7)
PARTIAL_AFTER_S = 3600.0
_DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")
_PARTIAL = re.compile(r"\A\..+\.[0-9a-f]{8}\.partial(\.[A-Za-z0-9]+)?\Z")


@dataclass
class Collection:
    """What is (or, with ``apply``, was) unreferenced, as paths relative to ``$DATA_DIR``."""

    blob_dirs: list[str] = field(default_factory=list)
    derived_dirs: list[str] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)
    partials: list[str] = field(default_factory=list)
    bytes: int = 0
    applied: bool = False

    @property
    def count(self) -> int:
        return len(self.blob_dirs) + len(self.derived_dirs) + len(self.parts) + len(self.partials)


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _sha_dirs(root: Path) -> list[Path]:
    """``<root>/<2 hex>/<sha256>`` folders, skipping anything not shaped so."""
    if not root.is_dir():
        return []
    return [
        d
        for shard in sorted(root.iterdir())
        if shard.is_dir()
        for d in sorted(shard.iterdir())
        if d.is_dir() and _DIGEST.match(d.name) and d.name.startswith(shard.name)
    ]


def _scan(
    data_dir: Path, known: set[str], live_parts: set[str], partial_after_s: float, apply: bool
) -> tuple[Collection, list[Path]]:
    """The filesystem half of ``collect``: blocking, so it runs in a thread."""
    found = Collection(applied=apply)
    targets: list[Path] = []
    for kind, bucket in (("blobs", found.blob_dirs), ("derived", found.derived_dirs)):
        for folder in _sha_dirs(absolute(data_dir, f"media/{kind}")):
            if folder.name not in known:
                bucket.append(f"media/{kind}/{folder.parent.name}/{folder.name}")
                targets.append(folder)
    uploads = absolute(data_dir, "uploads")
    if uploads.is_dir():
        for part in sorted(uploads.glob("*.part")):
            stored = f"uploads/{part.name}"
            if stored not in live_parts:
                found.parts.append(stored)
                targets.append(part)
    now = time.time()
    for partial in sorted(data_dir.rglob(".*.partial*")):
        old = partial.is_file() and now - partial.stat().st_mtime >= partial_after_s
        if old and _PARTIAL.match(partial.name):
            relative = partial.relative_to(data_dir).as_posix()
            found.partials.append(relative)
            targets.append(absolute(data_dir, relative))
    found.bytes = sum(_size(t) for t in targets)
    return found, targets


def _remove(targets: list[Path]) -> None:
    for target in targets:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)


async def collect(
    session: AsyncSession,
    data_dir: Path,
    *,
    apply: bool = False,
    abandoned_after: timedelta = ABANDONED_AFTER,
    partial_after_s: float = PARTIAL_AFTER_S,
) -> Collection:
    """Find the store's orphans; remove them when ``apply``. Idempotent."""
    known = set((await session.execute(select(MediaBlob.sha256))).scalars())
    cutoff = utcnow() - abandoned_after
    live_parts: set[str] = set()
    stale_sessions: list[str] = []
    rows = await session.execute(
        select(UploadSession.id, UploadSession.part_path, UploadSession.updated_at).where(
            UploadSession.status == UploadStatus.IN_PROGRESS
        )
    )
    for session_id, part, updated in rows:
        if updated is not None and updated < cutoff:
            stale_sessions.append(session_id)
        else:
            live_parts.add(part)

    found, targets = await asyncio.to_thread(
        _scan, data_dir, known, live_parts, partial_after_s, apply
    )
    if apply:
        await asyncio.to_thread(_remove, targets)
        if stale_sessions:
            await session.execute(
                update(UploadSession)
                .where(UploadSession.id.in_(stale_sessions))
                .values(status=UploadStatus.ABORTED)
            )
            await session.commit()
    return found


__all__ = ["ABANDONED_AFTER", "PARTIAL_AFTER_S", "Collection", "collect"]
