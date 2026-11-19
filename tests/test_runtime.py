"""Real sockets and child processes: lifecycle, SSE, restart, HTTPS and packaged assets."""

import contextlib
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time

import httpx
import psutil
import pytest

from sleepy.security import HASHER
from sleepy.storage import Store
from sleepy.supervisor import python_process_options


def eventually(work, timeout=12):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = work()
            if value:
                return value
        except (httpx.HTTPError, psutil.Error, AssertionError) as exc:
            last_error = exc
        time.sleep(0.1)
    raise AssertionError(f"Timed out: {last_error}")


def graceful_stop(process):
    process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)


@contextlib.contextmanager
def running(tmp_path, tls=False):
    store = Store(tmp_path)
    store.initialize()
    store.change_password(HASHER.hash("Initial123!"), must_change=False)
    store.close()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    args = [
        sys.executable,
        "-m",
        "sleepy.cli",
        "run",
        "--data-dir",
        str(tmp_path),
        "--port",
        str(port),
    ]
    if tls:
        cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        args += ["--ssl-certfile", str(cert), "--ssl-keyfile", str(key)]
    log_path = tmp_path / "runtime.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            args,
            stdout=log,
            stderr=log,
            **python_process_options(),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        children = []
        base = f"{'https' if tls else 'http'}://127.0.0.1:{port}"
        try:
            with httpx.Client(
                base_url=base,
                timeout=2,
                trust_env=False,
                verify=ssl.create_default_context(cafile=str(tmp_path / "cert.pem"))
                if tls
                else True,
            ) as client:

                def ready():
                    if process.poll() is not None:
                        pytest.fail(log_path.read_text(encoding="utf-8", errors="replace"))
                    return client.get("/api/status").status_code == 200

                eventually(ready)
                children = psutil.Process(process.pid).children()
                try:
                    yield process, client, base, log_path
                except Exception:
                    print(log_path.read_text(encoding="utf-8", errors="replace"))
                    raise
        finally:
            if process.poll() is None:
                graceful_stop(process)
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            for child in children:
                if child.is_running():
                    with contextlib.suppress(psutil.Error):
                        child.kill()


def live_login(client, base):
    csrf = client.get("/api/admin/auth").json()["csrf"]
    headers = {"Origin": base, "X-CSRF-Token": csrf}
    response = client.post("/api/admin/login", json={"password": "Initial123!"}, headers=headers)
    assert response.status_code == 200
    headers["X-CSRF-Token"] = response.json()["csrf"]
    return headers


def test_three_processes_sse_restart_and_clean_shutdown(tmp_path):
    with running(tmp_path) as (process, client, base, log_path):
        parent = psutil.Process(process.pid)
        children = parent.children()
        assert len(children) == 2
        assert client.get("/").status_code == 200
        assert client.get("/assets/missing.js").status_code == 404
        assert client.get("/api/missing").status_code == 404
        assert client.get("/_internal/settings").status_code == 404
        assert client.get("/api/../_internal/settings").status_code == 404
        assert client.get("/api/admin/devices").status_code == 401
        headers = live_login(client, base)
        d = client.post("/api/admin/devices", json={"name": "runtime"}, headers=headers).json()
        assert (
            client.post(
                "/api/device/report",
                json={"using": True},
                headers={"Authorization": f"Bearer {d['key']}"},
            ).status_code
            == 204
        )
        with httpx.Client(base_url=base, timeout=2, trust_env=False) as guest:
            assert guest.get("/api/admin/devices").status_code == 401
            assert not guest.cookies
        with client.stream("GET", "/api/status/events") as stream:
            assert stream.headers["content-type"].startswith("text/event-stream")
            lines = stream.iter_lines()
            data = next(line for line in lines if line.startswith("data:"))
            assert "runtime" in data  # Delivered immediately, while the stream stays open.
        backend_pid = int(
            re.findall(r"backend starting pid=(\d+)", log_path.read_text(encoding="utf-8"))[-1]
        )
        psutil.Process(backend_pid).kill()
        # An in-flight request can predate the supervisor noticing the exit.
        # Keep polling during the two-second backoff even if that request stalls.
        eventually(lambda: client.get("/api/status", timeout=0.25).status_code == 503, timeout=2)
        eventually(lambda: client.get("/api/status").status_code == 200)
        assert client.get("/api/status").json()["devices"][0]["name"] == "runtime"
        assert client.get("/api/admin/devices").status_code == 200  # Session survived restart.
        active_children = parent.children()
        assert len(active_children) == 2
        with client.stream("GET", "/api/status/events") as stream:
            next(line for line in stream.iter_lines() if line.startswith("data:"))
            graceful_stop(process)
            process.wait(timeout=10)
        assert process.returncode == 0
        assert all(not child.is_running() for child in active_children)
        log = log_path.read_text(encoding="utf-8")
        assert "web stopped exit=0" in log and "backend stopped exit=0" in log
        assert "Fatal Python error" not in log
        store = Store(tmp_path)
        assert (
            store.db.execute("SELECT SUM(count) FROM metrics WHERE category='page'").fetchone()[0]
            >= 1
        )
        store.close()


def test_parent_disappearance_stops_children(tmp_path):
    with running(tmp_path) as (process, _client, _base, _log_path):
        children = psutil.Process(process.pid).children()
        process.kill()
        process.wait(timeout=2)
        eventually(
            lambda: all(not p.is_running() or p.status() == psutil.STATUS_ZOMBIE for p in children)
        )


@pytest.mark.skipif(
    shutil.which("openssl") is None,
    reason="openssl required to generate temporary test certificate",
)
def test_builtin_https_and_secure_cookie(tmp_path):
    with running(tmp_path, tls=True) as (_process, client, base, _log_path):
        response = client.get("/api/admin/auth")
        assert "Secure" in response.headers["set-cookie"]
        assert "HttpOnly" in response.headers["set-cookie"]
        assert live_login(client, base)
        assert client.get("/api/admin/devices").status_code == 200


def test_logout_ends_admin_stream_and_public_stream_remains_available(tmp_path):
    with running(tmp_path) as (_process, client, base, _log_path):
        headers = live_login(client, base)
        with client.stream("GET", "/api/admin/events") as stream:
            lines = stream.iter_lines()
            next(line for line in lines if line.startswith("data:"))
            assert client.post("/api/admin/logout", headers=headers).status_code == 204
            assert (
                next(line for line in lines if line.startswith("event:"))
                == "event: session-expired"
            )
        with client.stream("GET", "/api/status/events") as stream:
            assert next(line for line in stream.iter_lines() if line.startswith("data:"))


def test_restart_budget_stops_the_entire_application(tmp_path):
    with running(tmp_path) as (process, client, _base, log_path):
        for attempt in range(4):
            pids = re.findall(r"backend starting pid=(\d+)", log_path.read_text(encoding="utf-8"))
            psutil.Process(int(pids[-1])).kill()
            if attempt < 3:
                count = len(pids)
                eventually(
                    lambda previous_count=count: (
                        len(
                            re.findall(
                                r"backend starting pid=(\d+)", log_path.read_text(encoding="utf-8")
                            )
                        )
                        > previous_count
                    ),
                    timeout=12,
                )
                eventually(lambda: client.get("/api/status").status_code == 200)
        process.wait(timeout=10)
        assert process.returncode != 0
        assert "耗尽重启次数" in log_path.read_text(encoding="utf-8")
        assert "web stopped exit=0" in log_path.read_text(encoding="utf-8")


def test_occupied_public_port_leaves_no_children(tmp_path):
    store = Store(tmp_path)
    store.initialize()
    store.close()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "sleepy.cli",
                "run",
                "--data-dir",
                str(tmp_path),
                "--port",
                str(sock.getsockname()[1]),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=False,
        )
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "starting pid=" not in result.stderr
