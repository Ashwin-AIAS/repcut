"""A stand-in for Gemini's `generateContent`, on loopback, for gates that run a real stack.

A gate that starts `make dev` starts an engine that reads the repository's
`.env` - and with it the developer's real `GEMINI_API_KEY`. Until Prompt 04's
review, verify-03's criterion 17 did exactly that: every gate run uploaded a
clip and sent its sampled frames to Google on the developer's own free-tier
quota, which is both a live call from a test (`.claude/rules/testing.md`) and a
spend of a budget measured in tens of requests a day.

So a stack under test gets a fixture key and `GEMINI_API_BASE` pointed here.
`Settings` accepts a loopback `http` base and nothing else besides the real
endpoint, so this cannot be pointed anywhere a frame could leave the machine.

Stdlib only, like the rest of the gate's process plumbing. Counts what it is
sent, so a criterion can print the number it was judged on.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Self

# Not a credential: the value a stack under test is given instead of a real key.
FIXTURE_KEY = "repcut-gate-stub-key-not-real"

# The answer every request gets: a well-formed scene description, so the
# engine's parse, cache write and UI rendering all run as they would for real.
_SCENE = {
    "content_type": "exercise",
    "exercise_guess": None,
    "environment": "home gym",
    "lighting_quality": "bright",
    "lighting_temperature": "neutral",
    "lighting_direction": "front",
    "energy_level": "med",
    "aesthetic_notes": "gate stub answer, not a model's",
}
_BODY = json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(_SCENE)}]}}]}).encode(
    "utf-8"
)
# A request body far over one JPEG frame plus the prompt is not a scene request.
_MAX_REQUEST_BYTES = 16 * 1024 * 1024


class GeminiStub:
    """A threaded HTTP server answering every POST like a successful Gemini call."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests = 0
        stub = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                if length > _MAX_REQUEST_BYTES or not self.path.endswith(":generateContent"):
                    self.send_error(400)
                    return
                self.rfile.read(length)
                with stub._lock:
                    stub._requests += 1
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(_BODY)))
                self.end_headers()
                self.wfile.write(_BODY)

            def log_message(self, format: str, *args: object) -> None:
                # Quiet: the gate's own output is the report, not an access log.
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        """What `GEMINI_API_BASE` is set to: the engine appends `/models/...`."""
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1beta"

    @property
    def requests(self) -> int:
        with self._lock:
            return self._requests

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.stop()
