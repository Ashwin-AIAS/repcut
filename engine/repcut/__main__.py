"""``python -m repcut`` - the one way the engine is started.

Before this existed there were three launchers (``scripts/dev.sh``,
``scripts/verify_01.sh``, ``verify_02_checks.boot``), each spelling out its own
``python -m uvicorn`` line, and they disagreed about ``--reload``. That
disagreement *was* the bug: ``--reload`` is what makes uvicorn pick a Windows
event loop with no subprocess transport (see ``repcut.loop``), so the gate
booted an engine that could run FFmpeg while `make dev` booted one that could
not. Twenty criteria passed against a configuration nobody ran.

So the loop choice is not a flag a launcher passes - it is the property of a
single entry point that every launcher goes through. Adding a fourth launcher
that forgets it is now impossible without importing uvicorn directly.

Host, port and log level come from ``Settings`` like everything else, so the
bind address stays one value in one place (`.claude/rules/security.md`).
"""

import argparse
from pathlib import Path

import uvicorn

from repcut.config import get_settings
from repcut.loop import LOOP_FACTORY

# The engine's package directory. Watched instead of the repository root so a
# `.md` edit or a `ui/` save does not restart the engine - the reloader used to
# watch the whole repo, including `.next/`, because uvicorn defaults to the
# working directory.
_ENGINE_DIRECTORY = str(Path(__file__).resolve().parents[1])


def build_parser() -> argparse.ArgumentParser:
    """Only the flags a launcher actually varies. Everything else is Settings."""
    parser = argparse.ArgumentParser(prog="python -m repcut", description="Run the Repcut engine.")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="restart on source changes (development). Watches engine/ only.",
    )
    parser.add_argument("--host", default=None, help="override ENGINE_HOST")
    parser.add_argument("--port", type=int, default=None, help="override ENGINE_PORT")
    parser.add_argument("--log-level", default=None, help="uvicorn log level")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Start uvicorn on a loop that can spawn subprocesses."""
    arguments = build_parser().parse_args(argv)
    settings = get_settings()

    uvicorn.run(
        "repcut.main:app",
        host=arguments.host or settings.engine_host,
        port=arguments.port or settings.engine_port,
        log_level=(arguments.log_level or settings.log_level).lower(),
        reload=arguments.reload,
        reload_dirs=[_ENGINE_DIRECTORY] if arguments.reload else None,
        # The point of this module. uvicorn resolves any `--loop` value that is
        # not one of its four built-in names as a custom loop factory, which is
        # the only supported way to override the SelectorEventLoop it forces on
        # Windows whenever reload or multiple workers are in play.
        loop=LOOP_FACTORY,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
