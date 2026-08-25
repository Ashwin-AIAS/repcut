"""The event loop the engine requires, and the check that it got one.

## The bug this exists to prevent

Every FFmpeg and ffprobe call the engine makes goes through
``asyncio.create_subprocess_exec`` (`media/ffmpeg_builder.py`). That needs an
event loop with a subprocess transport. On Windows exactly one stock loop has
one: ``ProactorEventLoop``. ``SelectorEventLoop`` inherits
``BaseEventLoop._make_subprocess_transport``, whose entire body is ``raise
NotImplementedError`` - so under it, *every* invocation is dead.

Python has defaulted to Proactor on Windows since 3.8, so this never comes up
by accident. uvicorn reintroduces it deliberately::

    # uvicorn/loops/asyncio.py
    def asyncio_loop_factory(use_subprocess: bool = False):
        if sys.platform == "win32" and not use_subprocess:
            return asyncio.ProactorEventLoop
        return asyncio.SelectorEventLoop

    # uvicorn/config.py
    @property
    def use_subprocess(self) -> bool:
        return bool(self.reload or self.workers > 1)

``use_subprocess`` is uvicorn's *own* multiprocessing, unrelated to ours, and
``--reload`` sets it. So `make dev` - and only `make dev`, because it is the
only launcher that passes ``--reload`` - ran the entire engine on a loop that
could not run FFmpeg. Uploads reached ``finalize``, hashed, verified, and then
died in ``ffprobe``.

## Why this is a factory and not a policy

The obvious fix - ``asyncio.set_event_loop_policy(WindowsProactorEventLoopPolicy())``
before uvicorn starts - does nothing, and it is worth being explicit about why,
because it fails silently. uvicorn 0.36+ does not ask the policy for a loop; it
builds one directly::

    # uvicorn/server.py
    asyncio_run(self.serve(...), loop_factory=self.config.get_loop_factory())

and ``asyncio.Runner(loop_factory=...)`` calls the factory and never consults
the policy. Measured, not assumed: with a Proactor policy installed, a Runner
given uvicorn's reload factory still yields ``_WindowsSelectorEventLoop``.
``--loop asyncio`` does not help either - that key maps to the same factory,
which still branches on ``use_subprocess``.

What *does* work is uvicorn's documented escape hatch: when ``--loop`` is not
one of its four built-in names it is imported as a custom loop factory
(``Config.get_loop_factory``). ``LOOP_FACTORY`` below is that import string, and
``repcut/__main__.py`` passes it so no launcher has to remember to.

## Why not move subprocesses to threads instead

Because it is a bigger change than it looks and buys less than it appears to.
``create_subprocess_exec`` is not blocking work on the loop - it is fully async -
so ``code-style.md``'s "blocking work goes to a thread/process executor" does not
ask for it. Doing it anyway would rewrite the FFmpeg stdout progress drain and
the kill-on-cancel path, and it would not stop there: ``ProgressReporter.fraction``
is called synchronously from inside that drain loop and calls
``JobQueue.publish``, which does ``put_nowait`` on ``asyncio.Queue`` objects.
``asyncio.Queue`` is not thread-safe, so running the drain on a worker thread
makes the WebSocket fan-out - criterion 9 - a data race. That is three tested
paths rewritten to work around one branch in a dependency.
"""

import asyncio
import asyncio.base_events
import sys

from repcut.logging import get_logger

logger = get_logger(__name__)

# Passed to uvicorn as `--loop`. Not one of its built-in names, so uvicorn
# imports it as a custom loop factory. Kept as a constant because it is a string
# uvicorn resolves at runtime: renaming `event_loop` without changing this would
# fail at boot rather than at import.
LOOP_FACTORY = "repcut.loop:event_loop"

# The implementation that raises NotImplementedError. Every loop that can
# actually spawn a subprocess overrides it; a loop that cannot has inherited
# this exact function object. Compared by identity rather than by loop class,
# so uvloop and any future loop are judged on what they implement rather than
# on a name this module would have to keep a list of.
_NO_SUBPROCESS_SUPPORT = getattr(
    asyncio.base_events.BaseEventLoop, "_make_subprocess_transport", None
)


def event_loop() -> asyncio.AbstractEventLoop:
    """Build the loop the engine runs on. uvicorn's ``--loop`` entry point.

    Windows gets Proactor explicitly, which is both the platform default and the
    only stock Windows loop with a subprocess transport. Everywhere else the
    platform's own default is used untouched: on Linux and macOS the selector
    loop spawns subprocesses perfectly well, and overriding a loop that works -
    or one an operator chose on purpose - would be the same mistake in the other
    direction.
    """
    if sys.platform == "win32":
        return asyncio.ProactorEventLoop()
    return asyncio.new_event_loop()


def can_spawn_subprocesses(loop: asyncio.AbstractEventLoop) -> bool:
    """Whether ``loop`` can start a child process.

    Reads the capability off the loop rather than inferring it from the platform
    or the class name, because the thing that decides at runtime is whether
    ``_make_subprocess_transport`` was overridden.
    """
    return getattr(type(loop), "_make_subprocess_transport", None) is not _NO_SUBPROCESS_SUPPORT


def running_loop_name() -> str:
    """The class name of the loop currently running. For logs and /health."""
    try:
        return type(asyncio.get_running_loop()).__name__
    except RuntimeError:
        # Named: called outside a loop - a synchronous test, or a script. Not a
        # failure, just nothing to report.
        return "none"


def check_running_loop() -> bool:
    """Log which loop the engine got, and shout if it cannot run FFmpeg.

    Called from the lifespan, so it observes the loop the server is *actually*
    serving on rather than one this module built. That distinction is the whole
    point: the launcher can be bypassed - ``python -m uvicorn repcut.main:app
    --reload`` still works and still picks the broken loop - and when it is, the
    engine has to say so at boot instead of answering 500 to every upload.

    A warning rather than a refusal, for the same reason ``/health`` reports a
    missing FFmpeg instead of refusing to boot: the UI needs a reachable engine
    to render the gap. The three surfaces together are the fix - this line,
    ``/health.event_loop_can_spawn``, and ``FFmpegLoopError`` on the request.

    Returns whether the loop is usable.
    """
    name = running_loop_name()
    try:
        usable = can_spawn_subprocesses(asyncio.get_running_loop())
    except RuntimeError:
        # Named: no running loop. Nothing to check and nothing to warn about.
        return True

    if not usable:
        logger.error(
            "event_loop_cannot_spawn_subprocesses",
            event_loop=name,
            impact="every FFmpeg and ffprobe call will fail; uploads cannot be finalized",
            fix="start the engine with `make dev` (python -m repcut), which selects the right loop",
        )
        return False

    logger.info("event_loop_selected", event_loop=name)
    return True


__all__ = [
    "LOOP_FACTORY",
    "can_spawn_subprocesses",
    "check_running_loop",
    "event_loop",
    "running_loop_name",
]
