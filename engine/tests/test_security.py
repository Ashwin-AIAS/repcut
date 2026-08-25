"""The network boundary, and the path checks the store now enforces.

Every test here corresponds to a finding from the security review. They are
written as regressions, not as coverage: each one fails against the code as it
was, which is the only evidence that the fix does something.
"""

from pathlib import Path

import pytest
from fastapi.middleware.cors import CORSMiddleware
from starlette.testclient import TestClient

from repcut.config import Settings
from repcut.main import app
from repcut.media.ffmpeg_builder import UnsafeSourceError, build_probe
from repcut.media.store import (
    UnsafeStorePathError,
    absolute,
    blob_directory,
    derived_directory,
    part_path,
    project_directory,
)
from repcut.security import is_allowed_origin, loopback_origins, warn_if_bound_publicly

_DIGEST = "a" * 64
_UUID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"


def _settings(**overrides: object) -> Settings:
    """Settings built directly, so `get_settings`' sync-root warning stays quiet."""
    return Settings(**overrides)  # type: ignore[arg-type]


# --- host binding ------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.1.1"])
def test_loopback_binding_is_not_warned_about(host: str) -> None:
    assert warn_if_bound_publicly(host) is False


# S104 suppressed: these literals are the inputs being *rejected*, not a binding.
@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.40", "0"])  # noqa: S104
def test_public_binding_is_warned_about(host: str) -> None:
    """An engine with no auth on a routable interface is the whole footage store."""
    assert warn_if_bound_publicly(host) is True


# --- WebSocket origin (CORS does not cover WebSockets) -----------------------


def test_ui_origin_may_open_the_job_socket() -> None:
    settings = _settings(ui_port=3000)
    for origin in loopback_origins(3000):
        assert is_allowed_origin(origin, settings) is True


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://evil.example",
        # Substring of an allowed origin: a prefix/suffix match would pass this.
        "http://localhost:3000.evil.example",
        "http://notlocalhost:3000",
    ],
)
def test_foreign_origin_may_not_open_the_job_socket(origin: str) -> None:
    """Cross-site WebSocket hijacking: the job stream names what the user films."""
    assert is_allowed_origin(origin, _settings(ui_port=3000)) is False


def test_missing_origin_is_allowed() -> None:
    """Non-browser clients send no Origin, and are not the threat model."""
    assert is_allowed_origin(None, _settings()) is True


# --- store path traversal ----------------------------------------------------


@pytest.mark.parametrize(
    "digest",
    ["../../etc/passwd", "..", "/etc/passwd", "A" * 64, "z" * 64, "", "a" * 63],
)
def test_blob_directory_refuses_a_non_digest(digest: str) -> None:
    with pytest.raises(UnsafeStorePathError):
        blob_directory(digest)


@pytest.mark.parametrize("project_id", ["../../..", "..", "a/b", "", "not-a-uuid"])
def test_project_directory_refuses_a_non_uuid(project_id: str) -> None:
    with pytest.raises(UnsafeStorePathError):
        project_directory(project_id)


def test_part_path_refuses_a_non_uuid() -> None:
    with pytest.raises(UnsafeStorePathError):
        part_path("../../../../etc/cron.d/payload")


def test_derived_directory_refuses_a_traversing_kind() -> None:
    with pytest.raises(UnsafeStorePathError):
        derived_directory(_DIGEST, "../../..", 1)


def test_valid_identifiers_still_build_their_paths() -> None:
    assert blob_directory(_DIGEST).as_posix() == f"media/blobs/aa/{_DIGEST}"
    assert project_directory(_UUID).as_posix() == f"projects/{_UUID}"
    assert part_path(_UUID).as_posix() == f"uploads/{_UUID}.part"


# --- absolute() containment --------------------------------------------------


def test_absolute_resolves_a_normal_stored_path(tmp_path: Path) -> None:
    resolved = absolute(tmp_path, "media/blobs/aa/source.mp4")
    assert resolved == (tmp_path / "media/blobs/aa/source.mp4").resolve()


def test_absolute_refuses_an_absolute_stored_path(tmp_path: Path) -> None:
    """`Path(base) / Path('/etc/shadow')` is `/etc/shadow` - the join is silent."""
    with pytest.raises(UnsafeStorePathError):
        absolute(tmp_path, "/etc/shadow")


def test_absolute_refuses_a_traversing_stored_path(tmp_path: Path) -> None:
    """PurePosixPath never normalises `..`, so it survives into the join."""
    with pytest.raises(UnsafeStorePathError):
        absolute(tmp_path, "media/../../../../etc/shadow")


def test_absolute_refuses_a_symlink_escaping_the_store(tmp_path: Path) -> None:
    """Resolving both sides is what makes the check real rather than textual."""
    outside = tmp_path / "outside"
    outside.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    try:
        (store / "escape").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform does not allow creating symlinks unprivileged")

    with pytest.raises(UnsafeStorePathError):
        absolute(store, "escape/secrets.txt")


# --- FFmpeg source ------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "http://169.254.169.254/latest/meta-data",
        "https://evil.example/clip.mp4",
        "rtmp://evil.example/live",
        "concat:a.mp4|b.mp4",
        "subfile:secret",
    ],
)
def test_probe_refuses_a_protocol_source(source: str) -> None:
    """FFmpeg opens these as network or composite inputs, not as files."""
    with pytest.raises(UnsafeSourceError):
        build_probe(Path(source))


def test_probe_refuses_a_source_that_looks_like_an_option() -> None:
    with pytest.raises(UnsafeSourceError):
        build_probe(Path("-y"))


def test_probe_accepts_a_plain_relative_path() -> None:
    command = build_probe(Path("media/blobs/aa/source.mp4"))
    assert command.source == "media/blobs/aa/source.mp4"


def test_probe_accepts_a_windows_drive_path() -> None:
    """`C:` is a drive letter, not a protocol - the check must not break Windows."""
    command = build_probe(Path("C:/repcut/data/media/source.mp4"))
    assert command.source.endswith("source.mp4")


# --- CORS surface -------------------------------------------------------------


def _cors_allowed_methods() -> set[str]:
    """The methods the installed CORS middleware advertises.

    Matched by class name rather than identity: Starlette stores the middleware
    as a `_MiddlewareFactory`, which mypy correctly refuses to compare against
    the class itself.
    """
    for middleware in app.user_middleware:
        if getattr(middleware.cls, "__name__", "") != CORSMiddleware.__name__:
            continue
        methods: object = middleware.kwargs["allow_methods"]
        if not isinstance(methods, list):
            raise AssertionError("allow_methods is not a list")
        return {str(method) for method in methods}
    raise AssertionError("CORS middleware is not installed")


def test_cors_allows_every_method_the_app_serves() -> None:
    """The allow-list is derived from the routes, not remembered alongside them.

    `PUT` was absent while `PUT /uploads/{id}/chunk` was the only way a browser
    can upload a clip, and `PATCH`/`DELETE` were listed with no route behind
    them. Neither showed up in the suite, because httpx's ASGITransport issues
    no preflight - so the first thing to notice would have been a real browser,
    against an engine whose tests were green.

    Asserting equality rather than containment: a method advertised without a
    route is surface offered for nothing, and is how the list drifted in the
    first place.
    """
    # Read from the OpenAPI surface rather than by walking `app.routes`: since
    # FastAPI 0.115 an included router stays wrapped in `_IncludedRouter` there,
    # so a naive walk finds only the four built-in doc routes and `/health` -
    # and would have reported "every served method is allowed" while missing
    # every route this test exists to cover.
    served = {
        method.upper()
        for operations in app.openapi()["paths"].values()
        for method in operations
        if method.upper() != "HEAD"  # Starlette adds HEAD to every GET for free.
    }
    assert served, "no HTTP routes found - the check would pass vacuously"
    assert _cors_allowed_methods() == served | {"OPTIONS"}


def test_a_browser_preflight_for_a_chunk_upload_is_answered() -> None:
    """The exact request a browser makes before the first chunk of every upload."""
    settings = _settings()
    origin = loopback_origins(settings.ui_port)[0]

    # `base_url` matters: TestClient defaults to `http://testserver`, which the
    # host allow-list correctly rejects with a 400 before CORS is ever reached.
    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.options(
            "/uploads/3f2504e0-4f89-41d3-9a0c-0305e82c3301/chunk",
            headers={
                "origin": origin,
                "access-control-request-method": "PUT",
                "access-control-request-headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-allow-origin"] == origin


def test_a_foreign_origin_gets_no_cors_grant() -> None:
    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.options(
            "/uploads/3f2504e0-4f89-41d3-9a0c-0305e82c3301/chunk",
            headers={
                "origin": "http://evil.example",
                "access-control-request-method": "PUT",
            },
        )

    assert "access-control-allow-origin" not in response.headers
