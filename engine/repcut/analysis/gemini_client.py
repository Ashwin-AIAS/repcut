"""Gemini 2.0 Flash scene tagging - the pure API call.

This module makes exactly one kind of request: one sampled frame plus compact,
path-free scene metadata, to Gemini's REST `generateContent` endpoint. It never
touches the database and never retries a network failure - both are
``cache.py``'s job (`.claude/skills/gemini-free-tier`: "client + response
schemas + SQLite cache + rate limiter" split between the two).

The P4 boundary is enforced at the type level, not by convention:
``SceneContext`` has no field that could hold a path or a filename, so nothing
that reaches this module *can* leak one through the context, whatever the
caller does. The frame itself travels as image bytes read from disk, never as
a path string in the request.

Uses ``httpx`` directly rather than the Gemini SDK - one fewer dependency to
audit on a public repo, and one fewer thing with its own telemetry to check
(CLAUDE.md, already decided). The API key travels in the ``x-goog-api-key``
header, never the URL's query string: ``secrets.md`` treats a
credential-bearing URL as a secret shape in its own right, and an exception
raised from a failed request must never be able to echo one back.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError

from repcut.config import Settings
from repcut.logging import get_logger

logger = get_logger(__name__)

GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_REQUEST_TIMEOUT_SECONDS = 30.0

_FRAME_MIME_TYPE = "image/jpeg"


class GeminiTransportError(Exception):
    """The request never reached Gemini - DNS, refused connection, timeout.

    Constructed with a fixed message, never ``str(error)`` from the underlying
    ``httpx`` exception: that text can echo the request object, and this stays
    true regardless of whether the key ever could have ended up in the URL.
    """


class GeminiAPIError(Exception):
    """Gemini answered, but not with 2xx - covers 429 and 5xx alike.

    Retrying a non-2xx status is ``cache.py``'s job (exponential backoff with
    jitter, capped). This module never retries one itself; the only retry it
    owns is the malformed-JSON-body case in :func:`analyze_frame`, which is a
    different failure (a 2xx response Gemini's own text could not be parsed
    from) and is handled without raising at all.
    """

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"gemini responded with status {status_code}")


class GeminiSceneResult(BaseModel):
    """One scene's structured description. Untrusted until this validates it.

    Every field is optional: a response that only answers half the schema is
    still a usable partial answer, and forcing every field would turn "the
    model was not confident about lighting_direction" into a hard failure of
    the whole scene instead of one null value.
    """

    content_type: str | None = None
    exercise_guess: str | None = None
    environment: str | None = None
    lighting_quality: str | None = None
    lighting_temperature: str | None = None
    lighting_direction: str | None = None
    # "low" | "med" | "high" - free text, not a Literal: a hallucinated fourth
    # value should surface as an odd string a human notices, not a
    # ValidationError that turns an otherwise-good response into `None`.
    energy_level: str | None = None
    aesthetic_notes: str | None = None


@dataclass(frozen=True, slots=True)
class SceneContext:
    """Compact, path-free scene metadata sent alongside the one sampled frame.

    This is the P4 boundary enforced by the type system rather than by
    convention (CLAUDE.md's own framing): there is no field here a path or a
    filename could be smuggled through, so a caller cannot violate "never send
    more than the one frame plus compact text context" by accident.

    ``position_seconds`` is the scene's start against the source clip's own
    timebase (amendment 008's seconds-not-frames convention) - the only
    "position" meaningful without also knowing the whole clip's duration.
    """

    duration_seconds: float
    position_seconds: float
    motion_energy: float | None = None
    audio_energy: float | None = None


_PROMPT_TEMPLATE = (
    "You are labeling one still frame sampled from a single detected shot "
    '("scene") in a home-workout video, to help an automatic video editor '
    "decide how to use this footage. You are given exactly one image from "
    "this scene and a few numbers describing it - no video, no audio, and no "
    "other frames from the clip.\n"
    "\n"
    "Scene metadata (measured, not guessed):\n"
    "- scene duration: {duration_seconds:.2f} seconds\n"
    "- scene start time in the source clip: {position_seconds:.2f} seconds\n"
    "- measured motion energy for this scene: {motion_energy}\n"
    "- measured audio energy for this scene: {audio_energy}\n"
    "\n"
    "Describe only what this single frame actually shows. Do not invent "
    "detail the image does not support, and do not use the audio/motion "
    "numbers as anything more than context - you cannot see or hear them. If "
    "you are not confident about a field, use null for it instead of "
    "guessing.\n"
    "\n"
    "Return a JSON object with exactly these fields, and nothing else:\n"
    '- content_type: what kind of shot this is - e.g. "exercise", "rest", '
    '"setup", "talking", "transition"\n'
    "- exercise_guess: the specific exercise visibly being performed, e.g. "
    '"barbell back squat", or null if none is identifiable\n'
    '- environment: the setting shown, e.g. "home gym", "commercial gym", '
    '"outdoors", "living room"\n'
    '- lighting_quality: one of "bright", "dim", "harsh", "soft", "mixed"\n'
    '- lighting_temperature: one of "warm", "neutral", "cool"\n'
    '- lighting_direction: one of "front", "side", "back", "overhead", '
    '"mixed"\n'
    '- energy_level: one of "low", "med", "high" - the perceived intensity '
    "of the moment shown, for editing pace\n"
    "- aesthetic_notes: one short sentence on framing, composition or visual "
    "quality relevant to editing this shot\n"
    "\n"
    "Respond with the JSON object only - no markdown code fences, no extra "
    "text before or after it."
)

_JSON_ONLY_REINFORCEMENT = (
    "\n\nYour previous reply could not be parsed as JSON. Reply with the "
    "JSON object only this time - no markdown fences, no commentary, "
    "nothing before or after it."
)

# The Gemini API's own `Schema.type` enum (proto field names, not OpenAPI's
# lowercase) - the REST endpoint accepts snake_case JSON keys as a direct
# proto-JSON mapping, and every field in `GeminiSceneResult` is an optional
# string, so one shape covers all eight.
_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "OBJECT",
    "properties": {
        field: {"type": "STRING", "nullable": True}
        for field in (
            "content_type",
            "exercise_guess",
            "environment",
            "lighting_quality",
            "lighting_temperature",
            "lighting_direction",
            "energy_level",
            "aesthetic_notes",
        )
    },
}


def _format_metric(value: float | None) -> str:
    """Render a measured number for the prompt, or say plainly it is absent."""
    return "not measured" if value is None else f"{value:.2f}"


def _build_prompt(context: SceneContext, *, reinforce_json: bool = False) -> str:
    """The full prompt text for one request, optionally reinforced for a retry."""
    text = _PROMPT_TEMPLATE.format(
        duration_seconds=context.duration_seconds,
        position_seconds=context.position_seconds,
        motion_energy=_format_metric(context.motion_energy),
        audio_energy=_format_metric(context.audio_energy),
    )
    if reinforce_json:
        text += _JSON_ONLY_REINFORCEMENT
    return text


# --- EXIF defense in depth ---------------------------------------------------

_JPEG_SOI = b"\xff\xd8"
_APP1_MARKER = 0xFFE1
_START_OF_SCAN_MARKER = 0xFFDA
_EXIF_SIGNATURE = b"Exif\x00\x00"


def _strip_exif_before_upload(data: bytes) -> bytes:
    """Defense in depth: drop any EXIF APP1 segment before a frame is uploaded.

    Extraction (`ffmpeg_builder.py`, amendment 008) already owns not writing
    EXIF/GPS onto the sampled frame on disk in the first place - this is a
    second, independent check on the upload path itself, so a P4 boundary this
    important does not rest on one component alone getting it right. If the
    file on disk ever does carry EXIF (a future extractor bug, a frame read
    from somewhere else entirely), it is stripped here before a single byte of
    it leaves the machine.

    Walks JPEG marker segments by hand rather than adding an image-parsing
    dependency: every marker is `0xFF <marker> <length-hi> <length-lo>
    <payload>`, and an APP1 segment beginning "Exif\\x00\\x00" is dropped
    whole. Malformed or non-JPEG input is returned unchanged rather than
    raised on - this must never be the reason an otherwise-good frame fails to
    upload.
    """
    if not data.startswith(_JPEG_SOI):
        return data
    output = bytearray(data[:2])
    index = 2
    try:
        while index < len(data):
            if data[index] != 0xFF:
                output.extend(data[index:])
                break
            marker = (data[index] << 8) | data[index + 1]
            # Markers with no length field: SOI/EOI and the restart markers.
            if marker in (0xFFD8, 0xFFD9) or 0xFFD0 <= marker <= 0xFFD7:
                output.extend(data[index : index + 2])
                index += 2
                continue
            if marker == _START_OF_SCAN_MARKER:
                # Everything from here on is entropy-coded scan data, not
                # further markers - copy the rest through untouched.
                output.extend(data[index:])
                break
            length = (data[index + 2] << 8) | data[index + 3]
            segment_end = index + 2 + length
            payload = data[index + 4 : segment_end]
            if marker == _APP1_MARKER and payload.startswith(_EXIF_SIGNATURE):
                index = segment_end
                continue  # dropped: never appended to output
            output.extend(data[index:segment_end])
            index = segment_end
    except IndexError:
        # Truncated or malformed: never fail the upload over this check.
        return data
    return bytes(output)


# --- request/response plumbing ------------------------------------------------


def _extract_text(payload: object) -> str | None:
    """Pull the model's text out of a `generateContent` response body."""
    if not isinstance(payload, dict):
        return None
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    first = candidates[0]
    if not isinstance(first, dict):
        return None
    content = first.get("content")
    if not isinstance(content, dict):
        return None
    parts = content.get("parts")
    if not isinstance(parts, list):
        return None
    texts = [
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    if not texts:
        return None
    return "".join(texts)


def _parse_result(payload: object) -> GeminiSceneResult | None:
    """Validate a response body into a result, or None for anything unusable."""
    text = _extract_text(payload)
    if text is None:
        return None
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(document, dict):
        return None
    try:
        return GeminiSceneResult.model_validate(document)
    except ValidationError:
        return None


async def _post(
    frame_bytes: bytes, prompt_text: str, *, api_key: str, client: httpx.AsyncClient
) -> object:
    """One `generateContent` call. Raises for anything but a 2xx HTTP response."""
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt_text},
                    {
                        "inline_data": {
                            "mime_type": _FRAME_MIME_TYPE,
                            "data": base64.b64encode(frame_bytes).decode("ascii"),
                        }
                    },
                ],
            }
        ],
        "generation_config": {
            "response_mime_type": "application/json",
            "response_schema": _RESPONSE_SCHEMA,
        },
    }
    url = f"{GEMINI_API_BASE}/models/{GEMINI_MODEL}:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    try:
        response = await client.post(
            url, json=body, headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS
        )
    except httpx.RequestError as error:
        # Named: DNS failure, refused connection, timeout, a dropped
        # connection mid-response - never the request never reaches Gemini at
        # all. Logged by type only; never `str(error)`, which can echo the
        # request `secrets.md` treats as secret-shaped in its own right.
        logger.warning("gemini_request_failed", error_type=type(error).__name__)
        raise GeminiTransportError("could not reach the Gemini API") from error

    if response.status_code >= httpx.codes.BAD_REQUEST:
        logger.warning("gemini_response_error", status_code=response.status_code)
        raise GeminiAPIError(response.status_code)

    try:
        return response.json()
    except json.JSONDecodeError:
        # The HTTP envelope itself was not JSON - a malformed body, not a
        # transport failure. Treated the same as an unparseable inner `text`.
        return None


async def analyze_frame(
    frame_path: Path,
    *,
    context: SceneContext,
    settings: Settings,
    client: httpx.AsyncClient,
) -> GeminiSceneResult | None:
    """Send exactly one sampled frame plus compact context; get a validated result.

    Never sends ``frame_path`` itself or its filename - only the bytes read
    from it, and only after :func:`_strip_exif_before_upload`'s defense-in-depth
    pass.

    Malformed JSON (a 2xx response Gemini's own text does not parse into the
    schema) gets exactly one retry, with :data:`_JSON_ONLY_REINFORCEMENT`
    appended to the prompt. If the retry is malformed too, this returns
    ``None`` rather than raising - the caller (``cache.py``) still counts this
    as a completed round trip (see ``GeminiSceneCache``'s own docstring) and
    caches the null result, because asking a third time would not get a
    different answer to the same frame and prompt.

    Raises :class:`GeminiTransportError` / :class:`GeminiAPIError` for
    anything that is *not* Gemini answering with a body it could not parse -
    those are ``cache.py``'s concern (backoff), not this function's. Returns
    ``None`` without making a request at all when no key is configured, so a
    caller that reaches this function directly (bypassing ``cache.py``'s own
    upfront check) still degrades rather than sending an unauthenticated
    request.
    """
    if settings.gemini_api_key is None or not settings.gemini_api_key_set:
        logger.info("gemini_analyze_frame_skipped_no_key")
        return None
    api_key = settings.gemini_api_key.get_secret_value()

    frame_bytes = await asyncio.to_thread(frame_path.read_bytes)
    frame_bytes = _strip_exif_before_upload(frame_bytes)

    for attempt, reinforce in enumerate((False, True)):
        prompt_text = _build_prompt(context, reinforce_json=reinforce)
        payload = await _post(frame_bytes, prompt_text, api_key=api_key, client=client)
        result = _parse_result(payload)
        if result is not None:
            return result
        logger.info("gemini_response_unparseable", attempt=attempt + 1)

    return None


__all__ = [
    "GEMINI_API_BASE",
    "GEMINI_MODEL",
    "GeminiAPIError",
    "GeminiSceneResult",
    "GeminiTransportError",
    "SceneContext",
    "analyze_frame",
]
