"""The engine's network boundary.

Repcut has no accounts and no login, by design: it is one person's engine on one
person's laptop, and P5 rules out an auth service. That decision is defensible
only if the boundary is drawn somewhere else, because "no authentication" and
"reachable" together mean anyone who can send the engine a request owns every
clip on the machine.

Binding to loopback is not that boundary. Two attacks cross it from a browser
tab the user already has open, carrying no credential because there is none to
steal:

- **DNS rebinding.** A page on ``attacker.example`` is served normally, then the
  attacker's DNS re-answers its own hostname as ``127.0.0.1`` with a one-second
  TTL. The browser re-resolves, keeps treating the page as same-origin with
  ``attacker.example``, and now reaches the engine - so the attacker both sends
  requests *and reads the replies*. The same-origin policy does not stop this;
  it is the thing being fooled. The one field that still names the attacker is
  ``Host``, which is why it is checked here. This is the bug class that hit
  Jupyter, Ollama and every "just bind to localhost" desktop service.

- **Cross-site request forgery.** Any page can POST to ``http://localhost:8000``
  cross-origin. CORS governs whether the *response* is readable; it does not
  stop the request being *executed*. A form post from an unrelated tab therefore
  reaches every mutating route the engine exposes. Prompt 02 is adding upload
  and project routes right now, so this stops being theoretical this week.

Neither check costs a dependency, a service or a euro (P5), and neither asks the
user to authenticate to their own laptop.
"""

from urllib.parse import urlsplit

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from repcut.config import Settings
from repcut.logging import get_logger

logger = get_logger(__name__)

# Every spelling of "this machine" a browser or a script may put in ``Host``.
# ``TrustedHostMiddleware`` compares against the hostname with the port stripped,
# so these carry no port.
LOOPBACK_HOSTS: tuple[str, ...] = ("localhost", "127.0.0.1", "::1", "[::1]")

# Hostnames that resolve to loopback but are attacker-controlled labels, so a
# rebinding attack can point them here. Enumerated because a `*.localhost`
# wildcard in the allow-list would admit them.
# The S104 suppression below is deliberate: bandit reads the literal as "binding
# to all interfaces", but this set is the thing that *detects* that binding and
# warns about it. The rule is matching the string, not the behaviour.
_REBINDING_BAIT: frozenset[str] = frozenset({"0.0.0.0", "0", "[::]", "::"})  # noqa: S104


def _hosts_for(settings: Settings) -> list[str]:
    """The exact ``Host`` values the engine will answer to.

    An explicit list, never a wildcard. ``TrustedHostMiddleware`` accepts
    ``"*"`` and accepts leading-dot wildcards, and both would readmit the
    rebinding case this function exists to close.
    """
    hosts = list(LOOPBACK_HOSTS)
    for extra in settings.extra_allowed_hosts_list:
        if extra not in hosts:
            hosts.append(extra)
    return hosts


def loopback_origins(*ports: int) -> list[str]:
    """Browser origins on this machine at ``ports``.

    Both schemes' hostnames are enumerated because ``http://localhost:3000`` and
    ``http://127.0.0.1:3000`` are *different origins* to a browser, and the user
    may type either into the address bar.
    """
    origins: list[str] = []
    for port in ports:
        for host in ("localhost", "127.0.0.1", "[::1]"):
            origins.append(f"http://{host}:{port}")
    return origins


def _cors_origins(settings: Settings) -> list[str]:
    """Origins allowed to read an engine response.

    The UI's own origin, plus anything the user added deliberately. Never
    ``"*"``: with ``allow_credentials`` off a wildcard is not a credential leak,
    but it does hand every page on the internet read access to the health,
    project and media metadata of a machine it happens to be able to reach.
    """
    origins = loopback_origins(settings.ui_port)

    # The configured UI URL may not be on `ui_port` - a reverse proxy, or a
    # second dev server. Take its origin as authoritative too.
    configured = urlsplit(settings.engine_url)
    if configured.scheme in ("http", "https") and configured.netloc:
        engine_origin = f"{configured.scheme}://{configured.netloc}"
        if engine_origin not in origins:
            origins.append(engine_origin)

    for extra in settings.extra_allowed_origins_list:
        if extra not in origins:
            origins.append(extra)
    return origins


def is_allowed_origin(origin: str | None, settings: Settings) -> bool:
    """Whether a WebSocket handshake from ``origin`` may be accepted.

    **CORS does not cover WebSockets.** This is the part that surprises people:
    a browser will happily open ``ws://localhost:8000/ws/jobs`` from any page on
    any domain, send the handshake with that page's ``Origin``, and hand the
    script the full duplex stream. No preflight, no ``Access-Control-*``
    negotiation, nothing for ``CORSMiddleware`` to refuse - it never sees a
    WebSocket scope.

    Repcut's job socket streams every ingest event: job ids, project ids,
    content hashes, failure causes, and the display names those jobs came from.
    That is a list of what the user films and when, readable by any tab. Cross-
    site WebSocket hijacking is the name; the ``Origin`` header is the only
    defence, because it is the one field the browser sets and a page cannot
    forge.

    A missing ``Origin`` is allowed: non-browser clients (the gate's test
    client, ``websocat``, a future CLI) do not send one, and they are not the
    threat - a page that could omit the header could also just make the request
    from a server instead.
    """
    if origin is None:
        return True
    return origin in _cors_origins(settings)


def warn_if_bound_publicly(host: str) -> bool:
    """Warn when the engine is about to listen on a non-loopback interface.

    ``scripts/dev.sh`` passes ``--host 127.0.0.1``, but nothing stops a person
    running ``uvicorn repcut.main:app --host 0.0.0.0`` after reading a tutorial.
    That publishes an engine with no authentication, holding the user's footage,
    to every device on the gym's wifi. The host allow-list still rejects the
    requests, so this is a warning rather than a refusal - but it is a loud one,
    because the failure it describes is silent otherwise.

    Returns True when the binding is public.
    """
    if host in _REBINDING_BAIT or (host not in LOOPBACK_HOSTS and not host.startswith("127.")):
        logger.warning(
            "engine_bound_to_public_interface",
            host=host,
            impact=(
                "the engine has no authentication and holds local footage; on a "
                "shared network any device can reach it"
            ),
            fix="bind to 127.0.0.1 (make dev does this), or set EXTRA_ALLOWED_HOSTS deliberately",
        )
        return True
    return False


def install_security_middleware(app: FastAPI, settings: Settings) -> None:
    """Attach the host allow-list and the CORS policy, in that order.

    Order matters and is the reverse of what it looks like: Starlette applies
    middleware outermost-last, so adding ``TrustedHost`` after ``CORS`` puts it
    *in front*. A request with a forged ``Host`` is then rejected before the CORS
    layer can answer its preflight and tell the attacker what is allowed.
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(settings),
        # No cookies, no Authorization header, nothing ambient to forge with -
        # the engine has no credentials at all, and turning this on would make
        # the origin list load-bearing for something it does not need to carry.
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["content-type", "accept", "content-range", "x-repcut-upload-offset"],
        max_age=600,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_hosts_for(settings))


__all__ = [
    "LOOPBACK_HOSTS",
    "install_security_middleware",
    "is_allowed_origin",
    "loopback_origins",
    "warn_if_bound_publicly",
]
