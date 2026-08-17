"""Criteria that drive `make dev` itself, and the browser that talks to it.

Everything else in `verify_02_checks.py` boots a component: an engine
subprocess, a test client, a filter graph. Three times in Prompt 02 every one of
those was green while the assembled product did not work - uploads 500'd on the
event loop `make dev` selects, the jobs socket was refused by a policy no test
enforces, and the launcher printed both service URLs with the UI already dead.

The common cause is not three bugs. It is that nothing in the gate ever started
the thing a person starts. These two criteria do.

The contract with `verify_02.sh` is the same as the rest: exactly one
``MEASURED:`` line, ``FAILED:`` and exit 1 on failure, and never an absolute
path in the output (`.claude/rules/secrets.md`).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Self

REPO_ROOT = Path(__file__).resolve().parents[1]

IS_WINDOWS = sys.platform == "win32"

# A cold `next dev` compiles a turbopack graph before it serves anything, and
# the engine migrates before it binds. Generous, because a timeout that fires on
# a healthy machine is a gate that gets ignored.
STACK_READY_TIMEOUT_S = 240.0
STACK_STOP_TIMEOUT_S = 90.0
PAGE_COMPILE_TIMEOUT_S = 300.0


def measured(value: str) -> None:
    print(f"MEASURED: {value}")


def failed(reason: str) -> None:
    print(f"FAILED: {reason}")


class ShellNotFoundError(RuntimeError):
    """No POSIX shell that can run `scripts/dev.sh` against this machine's ports."""


def bash_executable() -> str:
    """An absolute path to a POSIX shell, never the bare name ``bash``.

    On Windows ``CreateProcess`` searches ``System32`` before ``PATH``, and
    ``System32\\bash.exe`` is WSL's launcher. Spawning ``["bash", ...]`` therefore
    runs the script inside a Linux VM: it inherits none of the environment passed
    to it, and its ports are a different network namespace, so the launcher
    started on the wrong ports and its preflight looked at the wrong machine.
    ``shutil.which`` uses ``PATH`` and finds Git Bash - the two disagree, and only
    one of them is the shell `make dev` actually uses.
    """
    configured = os.environ.get("REPCUT_BASH", "").strip()
    if configured and Path(configured).is_file():
        return configured

    found = shutil.which("bash")
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    if found is not None and not found.lower().startswith(
        str(Path(system_root) / "System32").lower()
    ):
        return found

    for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
        if Path(candidate).is_file():
            return candidate
    raise ShellNotFoundError("no POSIX shell found; install Git Bash or set REPCUT_BASH")


def free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with closing(socket.socket()) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((host, port)) == 0


def port_listener_pids(port: int) -> list[str]:
    """PIDs listening on ``port``. The same question `dev.sh` answers in shell."""
    if IS_WINDOWS:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, check=False,
            timeout=60,
        )
        pids: list[str] = []
        for line in completed.stdout.splitlines():
            fields = line.split()
            if len(fields) < 5 or fields[0] != "TCP" or fields[3] != "LISTENING":
                continue
            if fields[1].rsplit(":", 1)[-1] == str(port):
                pids.append(fields[4])
        return sorted(set(pids))
    if shutil.which("lsof") is not None:
        completed = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, check=False, timeout=60,
        )
        return sorted(set(completed.stdout.split()))
    return []


def kill_pid_tree(pid: str | int) -> None:
    """Kill a process and its children. Only ever used on processes we started."""
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, check=False, timeout=60,
        )
    else:
        subprocess.run(["kill", "-9", str(pid)], capture_output=True, check=False, timeout=60)


class DevStack:
    """`scripts/dev.sh`, started the way `make dev` starts it.

    Scratch ports and a scratch ``DATA_DIR`` so the gate never touches the
    developer's own media store, and so a stale port on the machine cannot make
    the criterion fail for a reason that is not the criterion's.

    The launcher is invoked through a wrapper that records its MSYS pid, because
    a Ctrl-C has to be delivered as a real ``SIGINT`` to the shell running the
    trap. Windows has no way to send one to another process group from Python,
    and MSYS ``kill`` does - it is the same signal the terminal would send.
    """

    def __init__(self, *, engine_port: int | None = None, ui_port: int | None = None) -> None:
        self._scratch = TemporaryDirectory(prefix="repcut-devstack-", ignore_cleanup_errors=True)
        self.root = Path(self._scratch.name)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine_port = engine_port or free_port()
        self.ui_port = ui_port or free_port()
        self._log_path = self.root / "dev.log"
        self._pid_path = self.root / "dev.pid"
        self._log_handle: object | None = None
        self.process: subprocess.Popen[bytes] | None = None

    # --- lifecycle ---------------------------------------------------------

    def environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        environment["DATA_DIR"] = str(self.data_dir)
        environment["DATABASE_URL"] = (
            f"sqlite+aiosqlite:///{(self.data_dir / 'repcut.db').as_posix()}"
        )
        environment["ENGINE_PORT"] = str(self.engine_port)
        environment["UI_PORT"] = str(self.ui_port)
        environment["ENGINE_HOST"] = "127.0.0.1"
        # Deliberately NOT set: ENGINE_URL and NEXT_PUBLIC_ENGINE_URL. `dev.sh`
        # derives both from ENGINE_PORT, and that derivation is part of what is
        # under test - the browser has to end up pointed at the engine this
        # launcher actually started.
        environment.pop("ENGINE_URL", None)
        environment.pop("NEXT_PUBLIC_ENGINE_URL", None)
        environment["REPCUT_DEV_PIDFILE"] = self._pid_path.as_posix()
        environment["LOG_LEVEL"] = "INFO"
        return environment

    def start(self) -> None:
        handle = self._log_path.open("wb")
        self._log_handle = handle
        self.process = subprocess.Popen(
            [bash_executable(), "-c", 'echo $$ > "$REPCUT_DEV_PIDFILE"; exec bash scripts/dev.sh'],
            cwd=REPO_ROOT,
            env=self.environment(),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )

    def output(self) -> str:
        if self._log_handle is not None:
            self._log_handle.flush()  # type: ignore[attr-defined]
        if not self._log_path.exists():
            return ""
        return self._log_path.read_text(encoding="utf-8", errors="replace")

    def wait_ready(self, timeout: float = STACK_READY_TIMEOUT_S) -> bool:
        """Both ports accepting, and the launcher has said so. False if it died."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                return False
            if (
                port_open(self.engine_port)
                and port_open(self.ui_port)
                and "engine →" in self.output()
            ):
                return True
            time.sleep(0.5)
        return False

    def interrupt(self) -> None:
        """Deliver a real SIGINT to the launcher, as Ctrl-C would."""
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not self._pid_path.exists():
            time.sleep(0.2)
        if not self._pid_path.exists():
            return
        msys_pid = self._pid_path.read_text(encoding="utf-8").strip()
        subprocess.run(
            [bash_executable(), "-c", f"kill -INT {msys_pid}"],
            capture_output=True, check=False, timeout=60,
        )

    def wait_exit(self, timeout: float = STACK_STOP_TIMEOUT_S) -> int | None:
        if self.process is None:
            return None
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Named: the launcher did not finish its own shutdown in time. That
            # is a failure of the thing under test, so it is reported as None
            # rather than raised past the criterion.
            return None

    def ports_free(self, grace: float = 10.0) -> bool:
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not port_open(self.engine_port) and not port_open(self.ui_port):
                return True
            time.sleep(0.25)
        return False

    def force_stop(self) -> None:
        """Last resort teardown, so a failing criterion does not leak a stack."""
        if self.process is not None and self.process.poll() is None:
            kill_pid_tree(self.process.pid)
            self.process.wait(timeout=30)
        for port in (self.engine_port, self.ui_port):
            for pid in port_listener_pids(port):
                kill_pid_tree(pid)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.force_stop()
        if self._log_handle is not None:
            self._log_handle.close()  # type: ignore[attr-defined]
            self._log_handle = None
        self._scratch.cleanup()

    # --- talking to the stack ----------------------------------------------

    def engine_request(self, method: str, path: str, payload: object = None) -> tuple[int, object]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Host": "127.0.0.1"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        call = urllib.request.Request(
            f"http://127.0.0.1:{self.engine_port}{path}", data=body, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(call, timeout=60) as reply:
                return reply.status, json.loads(reply.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"null")

    def ui_get(self, path: str, timeout: float = PAGE_COMPILE_TIMEOUT_S) -> int:
        """Fetch a UI route, which is also what compiles it on a cold dev server."""
        call = urllib.request.Request(f"http://localhost:{self.ui_port}{path}")
        try:
            with urllib.request.urlopen(call, timeout=timeout) as reply:
                return int(reply.status)
        except urllib.error.HTTPError as error:
            return int(error.code)


# --- criterion 19: the launcher's own lifecycle ------------------------------


def _phase_restart(findings: list[str]) -> bool:
    """C1: Ctrl-C leaves nothing listening, and the next run starts clean."""
    with DevStack() as stack:
        stack.start()
        if not stack.wait_ready():
            findings.append("first run never became ready")
            return False

        stack.interrupt()
        code = stack.wait_exit()
        if code is None:
            findings.append("the launcher did not exit after SIGINT")
            return False
        if not stack.ports_free():
            findings.append(
                f"ports {stack.engine_port}/{stack.ui_port} still held after Ctrl-C"
            )
            return False

        # The same ports again, immediately. This is the run that used to die on
        # EADDRINUSE while the engine carried on and the exit code stayed 0.
        second = DevStack(engine_port=stack.engine_port, ui_port=stack.ui_port)
        try:
            second.start()
            if not second.wait_ready():
                findings.append("the second run on the same ports never became ready")
                return False
            if "EADDRINUSE" in second.output():
                findings.append("the second run reported EADDRINUSE")
                return False
            second.interrupt()
            if second.wait_exit() is None:
                findings.append("the second run did not exit after SIGINT")
                return False
            if not second.ports_free():
                findings.append("ports still held after the second run")
                return False
        finally:
            second.close()
    return True


def _phase_occupied_port(findings: list[str]) -> bool:
    """C2/C3: a port someone else holds is refused, named, and nothing is started."""
    with DevStack() as stack, closing(socket.socket()) as squatter:
        squatter.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        squatter.bind(("127.0.0.1", stack.ui_port))
        squatter.listen(1)

        stack.start()
        code = stack.wait_exit(timeout=120)
        output = stack.output()

        if code is None:
            findings.append("the launcher did not exit when a port was occupied")
            return False
        if code == 0:
            findings.append("the launcher exited 0 with a port it could not bind")
            return False
        if str(stack.ui_port) not in output:
            findings.append("the refusal did not name the port")
            return False
        if str(os.getpid()) not in output:
            findings.append("the refusal did not name the owning PID")
            return False
        remedy = "taskkill" if IS_WINDOWS else "kill -TERM"
        if remedy not in output:
            findings.append("the refusal did not print a reclaim command")
            return False
        if port_open(stack.engine_port):
            findings.append("the engine was left running after the refusal")
            return False
    return True


def _phase_half_death(findings: list[str]) -> bool:
    """C2: one side dying takes the other down, loudly and non-zero."""
    with DevStack() as stack:
        stack.start()
        if not stack.wait_ready():
            findings.append("the stack never became ready before the kill")
            return False

        victims = port_listener_pids(stack.ui_port)
        if not victims:
            findings.append("could not find the process holding the UI port")
            return False
        for pid in victims:
            kill_pid_tree(pid)

        code = stack.wait_exit(timeout=120)
        output = stack.output()
        if code is None:
            findings.append("the launcher kept running after the UI died")
            return False
        if code == 0:
            findings.append("the launcher exited 0 with the UI dead")
            return False
        if "ui" not in output:
            findings.append("the failure did not name which service died")
            return False
        if port_open(stack.engine_port):
            findings.append("the engine was left running after the UI died")
            return False
    return True


def check_dev_launcher() -> int:
    """19. `make dev` reclaims its ports, refuses one it does not own, fails loudly.

    Three phases, in the order the defects were reported:

    1. Start, Ctrl-C, assert both ports free, start again with no EADDRINUSE.
    2. Occupy the UI port; assert a non-zero exit naming the port, the owning
       PID and the command that frees it - and that nothing was started.
    3. Kill the UI mid-run; assert the launcher names it, takes the engine down
       and exits non-zero.

    Every phase runs the real `scripts/dev.sh`, on scratch ports and a scratch
    DATA_DIR, with a real `next dev` and a real uvicorn behind it.
    """
    try:
        bash_executable()
    except ShellNotFoundError as error:
        measured("no POSIX shell")
        failed(str(error))
        return 1

    findings: list[str] = []
    phases = {
        "restart": _phase_restart,
        "occupied-port": _phase_occupied_port,
        "half-death": _phase_half_death,
    }
    results: dict[str, bool] = {}
    for name, phase in phases.items():
        results[name] = phase(findings)
        if not results[name]:
            break

    measured(", ".join(f"{name}={'ok' if ok else 'FAILED'}" for name, ok in results.items()))
    if all(results.get(name, False) for name in phases):
        return 0
    failed("; ".join(findings) or "a launcher phase failed without a reason")
    return 1


# --- criterion 20: the assembled product -------------------------------------


def check_assembled_stack() -> int:
    """20. `make dev`, a real browser, a real project, and the jobs socket open.

    The single assertion that would have caught the Windows event loop, the
    launcher printing URLs for a dead UI, and the CSP refusing `ws://` - because
    it is the only one that starts what a person starts and looks at what a
    person sees.
    """
    from cdp_browser import BrowserNotFoundError, inspect_page

    with DevStack() as stack:
        stack.start()
        if not stack.wait_ready():
            measured("stack did not start")
            failed("`make dev` never reached both ports; see the launcher output")
            return 1

        project_name = "assembled stack"
        status, project = stack.engine_request("POST", "/projects", {"name": project_name})
        if status != 201 or not isinstance(project, dict):
            measured(f"POST /projects -> HTTP {status}")
            failed("could not create a project against the running stack")
            return 1
        project_id = str(project["id"])

        # Fetching the route is also what compiles it, so the browser does not
        # have to race a cold turbopack build.
        page_status = stack.ui_get(f"/projects/{project_id}")
        if page_status != 200:
            measured(f"editor page -> HTTP {page_status}")
            failed("the editor page did not render against the running stack")
            return 1

        page_url = f"http://localhost:{stack.ui_port}/projects/{project_id}"
        try:
            report = asyncio.run(inspect_page(page_url, observe_seconds=15.0))
        except BrowserNotFoundError as error:
            measured("no browser")
            failed(f"cannot assert the assembled app without a browser: {error}")
            return 1

    accepted = report.accepted_socket("/ws/jobs")
    # Asserted positively, not as the absence of "Connecting to the engine…".
    # The editor renders an error card for an unreachable engine, and that card
    # does not contain the connecting text either - so the absence test passed
    # against a page reading "No such project", which is the "green signal,
    # broken product" shape this criterion exists to refuse.
    editor_rendered = project_name in report.body_text
    panel_idle = "No jobs running." in report.body_text
    connecting = "Connecting to the engine" in report.body_text

    if connecting:
        panel = "stuck on Connecting…"
    elif panel_idle:
        panel = "connected"
    else:
        panel = "not rendered"

    measured(
        f"ports=up editor={'rendered' if editor_rendered else 'WRONG PAGE'} "
        f"jobs_socket={'accepted' if accepted else 'never opened'} "
        f"csp_violations={len(report.csp_violations)} panel={panel}"
    )

    if report.csp_violations:
        failed(f"the browser refused a request: {report.csp_violations[0][:140]}")
        return 1
    if not editor_rendered:
        failed(f"the browser did not get the editor; the page read: {report.body_text[:110]!r}")
        return 1
    if not accepted:
        attempted = ", ".join(report.websocket_attempts) or "nothing"
        failed(f"/ws/jobs never completed a handshake; the page opened: {attempted}")
        return 1
    if not panel_idle:
        failed("the jobs panel never reached its connected state")
        return 1
    return 0


__all__ = ["DevStack", "check_assembled_stack", "check_dev_launcher"]
