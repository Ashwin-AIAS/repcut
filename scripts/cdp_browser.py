"""A minimal Chrome DevTools Protocol client, for asserting the assembled app.

Why a browser at all: the two defects that cost Prompt 02 the most time were
both invisible to every other kind of test. The Content-Security-Policy is a
header no unit test enforces, and it silently refused the jobs socket; the
launcher printed URLs for a process that had already died. Neither is reachable
from pytest, from vitest, or from an HTTP client - a *browser* is the only thing
that runs the policy, the bundle and the socket together.

Why not Playwright: it is a 300MB download of a second Chromium (P5 is about
euros, but the same instinct applies to a gate that has to be runnable), and
Chrome and Edge are already installed on any machine that can open the app. The
protocol is a JSON-over-WebSocket wire format and the four messages needed here
fit in one file, over ``websockets``, which ``uvicorn[standard]`` already pulls
in. No new dependency.

Nothing here is used by the engine or the UI at runtime; it exists for the gate.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

# Order matters: an explicitly configured browser wins, then Chrome, then Edge.
# Both ship on Windows; the POSIX names are there so this is not Windows-only.
_CANDIDATE_PATHS: tuple[str, ...] = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
_CANDIDATE_COMMANDS: tuple[str, ...] = (
    "google-chrome",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
)

BROWSER_START_TIMEOUT_S = 30.0


class BrowserNotFoundError(RuntimeError):
    """No Chromium-family browser could be located to drive."""


@dataclass
class PageReport:
    """What the browser saw while the page was open."""

    #: (url, http status) for every WebSocket handshake that got a response.
    websocket_handshakes: list[tuple[str, int]] = field(default_factory=list)
    #: Every WebSocket URL the page tried to open, response or not.
    websocket_attempts: list[str] = field(default_factory=list)
    #: Text of every Content-Security-Policy refusal the browser logged.
    csp_violations: list[str] = field(default_factory=list)
    #: Text of every other error-level console entry.
    console_errors: list[str] = field(default_factory=list)
    #: `document.body.innerText` when the observation window closed.
    body_text: str = ""

    def accepted_socket(self, path: str) -> bool:
        """Whether a socket at ``path`` completed its handshake (HTTP 101)."""
        return any(
            url.endswith(path) and status == 101 for url, status in self.websocket_handshakes
        )


def find_browser() -> str:
    """Path to a Chromium-family browser, or raise.

    Raises rather than returning None: a gate that cannot open a browser cannot
    assert the product works, and reporting that as a pass is the exact failure
    this file was written to close.
    """
    configured = os.environ.get("REPCUT_BROWSER", "").strip()
    if configured:
        if Path(configured).is_file():
            return configured
        raise BrowserNotFoundError("REPCUT_BROWSER is set but does not point at a file")

    for candidate in _CANDIDATE_PATHS:
        if Path(candidate).is_file():
            return candidate
    for command in _CANDIDATE_COMMANDS:
        found = shutil.which(command)
        if found is not None:
            return found
    raise BrowserNotFoundError(
        "no Chrome, Chromium or Edge found; set REPCUT_BROWSER to a browser executable"
    )


def _free_port() -> int:
    import socket

    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _debugger_url(port: int) -> str:
    deadline = time.monotonic() + BROWSER_START_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            target = f"http://127.0.0.1:{port}/json/version"
            with urllib.request.urlopen(target, timeout=2) as reply:
                document = json.loads(reply.read())
                url: str = document["webSocketDebuggerUrl"]
                return url
        except (urllib.error.URLError, OSError, TimeoutError, KeyError, ValueError):
            # Named: the browser has not opened its debugging port yet. Every one
            # of these is "not ready", not "broken", until the deadline says so.
            time.sleep(0.3)
    raise BrowserNotFoundError("the browser did not open its debugging port in time")


class _Connection:
    """One CDP WebSocket, with id bookkeeping and a shared inbound queue."""

    def __init__(self, socket: object) -> None:
        self._socket = socket
        self._next_id = 0

    async def send(
        self, method: str, params: dict[str, object] | None = None, session: str | None = None
    ) -> int:
        self._next_id += 1
        message: dict[str, object] = {"id": self._next_id, "method": method, "params": params or {}}
        if session is not None:
            message["sessionId"] = session
        await self._socket.send(json.dumps(message))  # type: ignore[attr-defined]
        return self._next_id

    async def recv(self) -> dict[str, object]:
        """Read one message. Callers wrap this in ``asyncio.timeout`` for their
        own budget - a ``timeout`` parameter on an async function invites
        confusion with that convention, so the deadline lives at the call site.
        """
        raw = await self._socket.recv()  # type: ignore[attr-defined]
        document: dict[str, object] = json.loads(raw)
        return document


async def _attach_page(connection: _Connection) -> str:
    """Open a fresh tab and return its CDP session id."""
    await connection.send("Target.setDiscoverTargets", {"discover": True})
    await connection.send("Target.createTarget", {"url": "about:blank"})

    target_id: str | None = None
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        async with asyncio.timeout(10):
            message = await connection.recv()
        method = message.get("method")
        params = message.get("params", {})
        if not isinstance(params, dict):
            raise TypeError("CDP message .params was not a JSON object")
        if method == "Target.targetCreated" and target_id is None:
            info = params.get("targetInfo", {})
            if not isinstance(info, dict):
                raise TypeError("CDP targetInfo was not a JSON object")
            if info.get("type") == "page":
                target_id = str(info["targetId"])
                await connection.send(
                    "Target.attachToTarget", {"targetId": target_id, "flatten": True}
                )
        if method == "Target.attachedToTarget":
            return str(params["sessionId"])
    raise BrowserNotFoundError("the browser never attached a page target")


async def _collect(connection: _Connection, session: str, seconds: float) -> PageReport:
    report = PageReport()
    # `webSocketHandshakeResponseReceived` carries a requestId and no url, so the
    # url has to be remembered from `webSocketCreated`. Without this the report
    # would say a socket was accepted without being able to say which one - and
    # Next's own HMR socket is open on every dev page, so "a socket connected"
    # is not evidence about the jobs socket.
    socket_urls: dict[str, str] = {}
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            async with asyncio.timeout(1.0):
                message = await connection.recv()
        except TimeoutError:
            # Named: a quiet second on the protocol. Expected, and not the end
            # of the observation window.
            continue
        method = str(message.get("method", ""))
        params = message.get("params", {})
        if not isinstance(params, dict):
            continue

        if method == "Network.webSocketCreated":
            url = str(params.get("url", ""))
            socket_urls[str(params.get("requestId", ""))] = url
            report.websocket_attempts.append(url)
        elif method == "Network.webSocketHandshakeResponseReceived":
            response = params.get("response", {})
            if isinstance(response, dict):
                url = socket_urls.get(str(params.get("requestId", "")), "")
                report.websocket_handshakes.append((url, int(response.get("status", 0))))
        elif method == "Log.entryAdded":
            entry = params.get("entry", {})
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text", ""))
            if entry.get("source") == "security" and "Content Security Policy" in text:
                report.csp_violations.append(text)
            elif entry.get("level") == "error":
                report.console_errors.append(text)
    return report


async def _body_text(connection: _Connection, session: str) -> str:
    message_id = await connection.send(
        "Runtime.evaluate",
        {"expression": "document.body ? document.body.innerText : ''", "returnByValue": True},
        session,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        async with asyncio.timeout(10):
            message = await connection.recv()
        if message.get("id") != message_id:
            continue
        result = message.get("result", {})
        if not isinstance(result, dict):
            raise TypeError("CDP Runtime.evaluate result was not a JSON object")
        inner = result.get("result", {})
        return str(inner.get("value", "")) if isinstance(inner, dict) else ""
    return ""


async def inspect_page(
    url: str, *, observe_seconds: float = 12.0, profile_dir: Path | None = None
) -> PageReport:
    """Open ``url`` in a headless browser and report what happened on it.

    The report is deliberately about *mechanism* - which sockets opened, which
    the policy refused - rather than a screenshot, because the failures this
    guards against are invisible on screen until you know to look for them.
    """
    from websockets.asyncio.client import connect

    executable = find_browser()
    debug_port = _free_port()
    profile = profile_dir or Path(os.environ.get("TEMP", ".")) / f"repcut-cdp-{debug_port}"

    # Sync Popen, deliberately, in an async function: `_debugger_url` below
    # polls with a blocking `urlopen` too, and teardown needs a real process
    # handle to `.kill()` and `.wait()` synchronously in `finally` so it
    # completes before `shutil.rmtree` touches the profile directory. This is
    # a one-shot gate launcher, not a hot path - converting only the spawn to
    # `asyncio.create_subprocess_exec` would not remove the other blocking
    # call and would still need sync teardown, buying nothing.
    process = subprocess.Popen(  # noqa: ASYNC220 - see comment above
        [
            executable,
            "--headless=new",
            f"--remote-debugging-port={debug_port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        endpoint = _debugger_url(debug_port)
        async with connect(endpoint, max_size=None) as socket:
            connection = _Connection(socket)
            session = await _attach_page(connection)
            for domain in ("Network", "Log", "Runtime", "Page"):
                await connection.send(f"{domain}.enable", {}, session)
            await asyncio.sleep(0.5)
            await connection.send("Page.navigate", {"url": url}, session)
            report = await _collect(connection, session, observe_seconds)
            report.body_text = await _body_text(connection, session)
            return report
    finally:
        process.kill()
        process.wait(timeout=30)
        shutil.rmtree(profile, ignore_errors=True)


__all__ = ["BrowserNotFoundError", "PageReport", "find_browser", "inspect_page"]
