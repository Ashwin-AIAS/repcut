"""``analysis/gemini_client.py`` - the pure API call.

Gemini is always mocked (`.claude/rules/testing.md`): every test here builds
an ``httpx.MockTransport`` and never reaches a socket. The API key used
throughout is a fixture string, never a real credential
(`.claude/rules/secrets.md`).
"""

import json
from collections.abc import Mapping
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs

from repcut.analysis.gemini_client import (
    GeminiAPIError,
    GeminiSceneResult,
    GeminiTransportError,
    SceneContext,
    _strip_exif_before_upload,
    analyze_frame,
)
from repcut.config import Settings

# Fixture-only. Never a real key (`.claude/rules/secrets.md`).
FIXTURE_GEMINI_KEY = "repcut-test-fixture-key-not-real"


def _settings(tmp_path: Path, *, api_key: str | None = FIXTURE_GEMINI_KEY) -> Settings:
    return Settings(
        data_dir=tmp_path, gemini_api_key=SecretStr(api_key) if api_key is not None else None
    )


def _context() -> SceneContext:
    return SceneContext(
        duration_seconds=3.5,
        position_seconds=12.25,
        motion_energy=0.62,
        audio_energy=0.18,
    )


def _gemini_response(document: Mapping[str, object]) -> dict[str, object]:
    """Wrap ``document`` the way `generateContent` wraps its structured JSON."""
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(document)}]}}]}


def _mock_transport(
    responses: list[tuple[int, object]],
) -> tuple[httpx.MockTransport, list[dict[str, object]]]:
    """A queued, request-capturing stand-in for Gemini's endpoint.

    Mirrors ``scripts/verify_03_checks.py``'s ``_mock_gemini_transport`` -
    duplicated rather than imported, the same way ``conftest.py`` and that gate
    script already duplicate fixtures between the pytest suite and the gate
    (different processes, no fixture-sharing).
    """
    queue = list(responses)
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            body = json.loads(request.content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {"_raw": request.content.decode("utf-8", errors="replace")}
        captured.append(body)
        index = min(len(captured), len(queue)) - 1
        status, content = queue[index] if queue else (200, {"candidates": []})
        if isinstance(content, bytes):
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=content)

    return httpx.MockTransport(handler), captured


def _connect_error_transport() -> tuple[httpx.MockTransport, list[int]]:
    """Raises on every request, the way a dead network does. Counts attempts."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("connection refused (test fixture)", request=request)

    return httpx.MockTransport(handler), calls


async def _client(transport: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport)


async def test_sends_exactly_one_frame_and_no_path_or_audio(tmp_path: Path) -> None:
    frame_path = tmp_path / "frame.jpg"
    frame_bytes = b"not-a-real-jpeg-but-bytes-are-bytes"
    frame_path.write_bytes(frame_bytes)
    document = {
        "content_type": "exercise",
        "exercise_guess": "barbell back squat",
        "environment": "home gym",
        "lighting_quality": "bright",
        "lighting_temperature": "neutral",
        "lighting_direction": "front",
        "energy_level": "high",
        "aesthetic_notes": "tight framing on the rack",
    }
    transport, requests = _mock_transport([(200, _gemini_response(document))])

    async with await _client(transport) as client:
        result = await analyze_frame(
            frame_path,
            context=_context(),
            settings=_settings(tmp_path / "settings"),
            client=client,
        )

    assert result == GeminiSceneResult.model_validate(document)
    assert len(requests) == 1

    body = requests[0]
    raw = json.dumps(body)
    assert raw.count("inline_data") == 1
    assert '"audio/' not in raw
    assert frame_path.name not in raw
    assert str(tmp_path) not in raw

    parts = body["contents"][0]["parts"]  # type: ignore[index]
    inline = next(part["inline_data"] for part in parts if "inline_data" in part)
    assert inline["mime_type"] == "image/jpeg"
    text = next(part["text"] for part in parts if "text" in part)
    assert f"{_context().duration_seconds:.2f}" in text
    assert f"{_context().position_seconds:.2f}" in text
    assert "0.62" in text
    assert "0.18" in text


async def test_malformed_json_gets_exactly_one_retry_then_none(tmp_path: Path) -> None:
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    garbage = b"not json at all {{{"
    transport, requests = _mock_transport([(200, garbage), (200, garbage)])

    async with await _client(transport) as client:
        result = await analyze_frame(
            frame_path,
            context=_context(),
            settings=_settings(tmp_path / "settings"),
            client=client,
        )

    assert result is None
    assert len(requests) == 2


async def test_malformed_then_valid_retry_recovers(tmp_path: Path) -> None:
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    document = {"content_type": "exercise", "exercise_guess": None, "environment": None}
    transport, requests = _mock_transport(
        [(200, b"garbage {{{"), (200, _gemini_response(document))]
    )

    async with await _client(transport) as client:
        result = await analyze_frame(
            frame_path,
            context=_context(),
            settings=_settings(tmp_path / "settings"),
            client=client,
        )

    assert result is not None
    assert result.content_type == "exercise"
    assert len(requests) == 2

    second_parts = requests[1]["contents"][0]["parts"]  # type: ignore[index]
    second_text = next(part["text"] for part in second_parts if "text" in part)
    assert "could not be parsed as JSON" in second_text


async def test_transport_error_raises_named_exception(tmp_path: Path) -> None:
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    transport, calls = _connect_error_transport()

    async with await _client(transport) as client:
        with pytest.raises(GeminiTransportError) as excinfo:
            await analyze_frame(
                frame_path,
                context=_context(),
                settings=_settings(tmp_path / "settings"),
                client=client,
            )

    assert len(calls) == 1
    assert FIXTURE_GEMINI_KEY not in str(excinfo.value)


async def test_rate_limited_response_raises_with_status_code(tmp_path: Path) -> None:
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    transport, requests = _mock_transport([(429, {"error": {"message": "quota exceeded"}})])

    async with await _client(transport) as client:
        with pytest.raises(GeminiAPIError) as excinfo:
            await analyze_frame(
                frame_path,
                context=_context(),
                settings=_settings(tmp_path / "settings"),
                client=client,
            )

    assert excinfo.value.status_code == 429
    assert len(requests) == 1


async def test_no_key_configured_makes_zero_requests(tmp_path: Path) -> None:
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    transport, requests = _mock_transport([(200, _gemini_response({}))])

    async with await _client(transport) as client:
        result = await analyze_frame(
            frame_path,
            context=_context(),
            settings=_settings(tmp_path / "settings", api_key=None),
            client=client,
        )

    assert result is None
    assert requests == []


async def test_key_never_appears_in_logs_across_failure_paths(tmp_path: Path) -> None:
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")

    with capture_logs() as logs:
        malformed_transport, _ = _mock_transport([(200, b"garbage {{{"), (200, b"garbage {{{")])
        async with await _client(malformed_transport) as client:
            await analyze_frame(
                frame_path,
                context=_context(),
                settings=_settings(tmp_path / "settings1"),
                client=client,
            )

        error_transport, _ = _connect_error_transport()
        async with await _client(error_transport) as client:
            with pytest.raises(GeminiTransportError):
                await analyze_frame(
                    frame_path,
                    context=_context(),
                    settings=_settings(tmp_path / "settings2"),
                    client=client,
                )

        rate_limited_transport, _ = _mock_transport([(429, {"error": "quota"})])
        async with await _client(rate_limited_transport) as client:
            with pytest.raises(GeminiAPIError):
                await analyze_frame(
                    frame_path,
                    context=_context(),
                    settings=_settings(tmp_path / "settings3"),
                    client=client,
                )

    serialized = json.dumps(logs)
    assert FIXTURE_GEMINI_KEY not in serialized


# --- EXIF defense-in-depth, unit-tested directly ------------------------------


def _jpeg_with_exif() -> bytes:
    """SOI, an APP1/Exif segment, a harmless APP0 segment, then EOI."""
    exif_payload = b"Exif\x00\x00" + b"\x00" * 8
    # JPEG segment length includes the 2 length bytes themselves, not just the
    # payload - the same convention `_strip_exif_before_upload` parses by.
    app1 = b"\xff\xe1" + (len(exif_payload) + 2).to_bytes(2, "big") + exif_payload
    app0_payload = b"JFIF\x00" + b"\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    app0 = b"\xff\xe0" + (len(app0_payload) + 2).to_bytes(2, "big") + app0_payload
    return b"\xff\xd8" + app1 + app0 + b"\xff\xd9"


def test_strip_exif_removes_the_exif_segment_only() -> None:
    stripped = _strip_exif_before_upload(_jpeg_with_exif())
    assert b"Exif" not in stripped
    assert b"JFIF" in stripped
    assert stripped.startswith(b"\xff\xd8")
    assert stripped.endswith(b"\xff\xd9")


def test_strip_exif_leaves_a_clean_jpeg_untouched() -> None:
    app0_payload = b"JFIF\x00" + b"\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    app0 = b"\xff\xe0" + (len(app0_payload) + 2).to_bytes(2, "big") + app0_payload
    clean = b"\xff\xd8" + app0 + b"\xff\xd9"
    assert _strip_exif_before_upload(clean) == clean


def test_strip_exif_never_raises_on_malformed_input() -> None:
    truncated = b"\xff\xd8\xff\xe1\x00"  # a marker announcing a length it does not have
    assert _strip_exif_before_upload(truncated) == truncated
    assert _strip_exif_before_upload(b"") == b""
    not_jpeg = b"just some bytes"
    assert _strip_exif_before_upload(not_jpeg) == not_jpeg
