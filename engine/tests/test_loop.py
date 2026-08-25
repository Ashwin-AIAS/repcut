"""The event loop the engine requires.

Every FFmpeg call goes through ``asyncio.create_subprocess_exec``, which needs a
loop with a subprocess transport. On Windows only ``ProactorEventLoop`` has one,
and uvicorn selects ``SelectorEventLoop`` for the whole server whenever
``--reload`` is passed - so `make dev` ran the engine on a loop where every
invocation raised ``NotImplementedError``. See ``repcut.loop``.

These tests pin the mechanism. The end-to-end proof that the *shipped*
configuration works is gate criterion 17, because it is the only thing that can
boot a real server with the real arguments.
"""

import asyncio
import sys

import pytest

from repcut.loop import (
    LOOP_FACTORY,
    can_spawn_subprocesses,
    check_running_loop,
    event_loop,
    running_loop_name,
)


def test_the_factory_builds_a_loop_that_can_spawn_subprocesses() -> None:
    """The property the engine actually needs, asserted directly.

    Not ``isinstance(loop, ProactorEventLoop)``: the requirement is the
    capability, and asserting the class would pass on a platform where the class
    is right and the capability is not.
    """
    loop = event_loop()
    try:
        assert can_spawn_subprocesses(loop)
    finally:
        loop.close()


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows has a loop that cannot spawn")
def test_a_windows_selector_loop_is_detected_as_unable() -> None:
    """The negative case, which is the one that shipped.

    Without this the capability check could return True unconditionally and
    every other assertion here would still pass.
    """
    loop = asyncio.SelectorEventLoop()
    try:
        assert not can_spawn_subprocesses(loop)
    finally:
        loop.close()


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows has a loop that cannot spawn")
async def test_the_selector_loop_really_does_raise_not_implemented() -> None:
    """Prove the capability check is describing a real failure, not a convention.

    ``can_spawn_subprocesses`` reads a method off the loop class. That is only
    meaningful if a loop it rejects genuinely cannot spawn - otherwise the guard
    is a claim about naming. So the rejected loop is actually asked to spawn
    something, in its own thread, and the exception is the one from the report.
    """
    outcome: list[BaseException | None] = []

    def _run() -> None:
        loop = asyncio.SelectorEventLoop()

        async def _spawn() -> None:
            await asyncio.create_subprocess_exec(sys.executable, "-c", "")

        try:
            loop.run_until_complete(_spawn())
            outcome.append(None)
        except NotImplementedError as error:
            outcome.append(error)
        finally:
            loop.close()

    await asyncio.to_thread(_run)

    assert isinstance(outcome[0], NotImplementedError), (
        "a SelectorEventLoop spawned a subprocess; the capability check is measuring nothing"
    )


async def test_the_test_suite_itself_runs_on_a_capable_loop() -> None:
    """Why 51 tests could execute FFmpeg for real while the server could not.

    pytest-asyncio builds its loop from the default policy, which on Windows is
    Proactor. The suite was therefore never on the loop the server was on, and
    no amount of testing this module could have found the bug - only booting the
    server the way `make dev` boots it could.
    """
    assert can_spawn_subprocesses(asyncio.get_running_loop())
    assert check_running_loop() is True
    assert running_loop_name() != "none"


def test_the_factory_import_string_resolves() -> None:
    """``LOOP_FACTORY`` is resolved by uvicorn at runtime, by name.

    A rename that missed the constant would fail at engine boot with a message
    about an import string, which is a bad place to find out.
    """
    from uvicorn.importer import import_from_string

    assert import_from_string(LOOP_FACTORY) is event_loop


def test_uvicorn_would_otherwise_pick_an_unusable_loop() -> None:
    """The upstream behaviour this module exists to override, pinned.

    If a future uvicorn stops forcing a selector loop under ``--reload``, this
    fails and ``repcut.loop`` can be reconsidered. Until then it documents that
    the override is answering a real branch rather than a suspicion.
    """
    from uvicorn.config import Config

    reloading = Config("repcut.main:app", reload=True)
    assert reloading.use_subprocess is True

    factory = reloading.get_loop_factory()
    assert factory is not None
    built = factory()
    try:
        if sys.platform == "win32":
            assert not can_spawn_subprocesses(built), (
                "uvicorn no longer forces a selector loop under --reload; revisit repcut.loop"
            )
        else:
            assert can_spawn_subprocesses(built)
    finally:
        built.close()
