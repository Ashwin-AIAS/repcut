#!/usr/bin/env python3
"""Find (and with ``--apply``, remove) unreferenced bytes in ``$DATA_DIR``.

    python scripts/media_gc.py           # report only
    python scripts/media_gc.py --apply   # remove what the report lists

Orphan blob and derived folders, abandoned ``.part`` transfers and partials of
killed renders; see ``repcut.media.gc``. Stop the engine first: a collection
racing a live upload could abort a transfer the engine is still writing (the
seven-day rule makes that unlikely, not impossible). Prints paths relative to
``$DATA_DIR`` only.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "engine"))

from repcut.config import get_settings  # noqa: E402
from repcut.db import create_engine, create_session_factory  # noqa: E402
from repcut.media.gc import collect  # noqa: E402


async def run(apply: bool) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            found = await collect(session, settings.data_dir, apply=apply)
    finally:
        await engine.dispose()
    for label, items in (
        ("orphan blob folders", found.blob_dirs),
        ("orphan derived folders", found.derived_dirs),
        ("abandoned transfers", found.parts),
        ("killed-render partials", found.partials),
    ):
        print(f"{label}: {len(items)}")
        for item in items:
            print(f"  {item}")
    verb = "removed" if apply else "removable (run with --apply)"
    print(f"MEASURED: {found.count} unreferenced, {found.bytes / 2**20:.1f} MiB {verb}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    return asyncio.run(run(parser.parse_args().apply))


if __name__ == "__main__":
    raise SystemExit(main())
