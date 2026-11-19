"""One CLI parent, two spawn-safe children, pre-bound sockets and ordered shutdown."""

import base64
import contextlib
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field

import httpx

from .backend import INTERNAL_HEADER
from .security import token

LOG = logging.getLogger(__name__)


def bind_socket(host, port):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(128)
        sock.setblocking(False)
        return sock
    except BaseException:
        sock.close()
        raise


def python_process_options():
    # socket.share() and process supervision need the interpreter's PID, not a venv launcher.
    if os.name == "nt" and os.path.normcase(sys.executable) != os.path.normcase(
        sys._base_executable
    ):
        return {
            "executable": sys._base_executable,
            "env": {**os.environ, "__PYVENV_LAUNCHER__": sys.executable},
        }
    return {}


class Control:
    def __init__(self, pipe):
        self.pipe = pipe

    def send(self, value):
        self.pipe.write(json.dumps(value) + "\n")
        self.pipe.flush()

    def close(self):
        with contextlib.suppress(OSError):
            self.pipe.close()


class Process:
    """Small Popen adapter; no multiprocessing resource-tracker helper process."""

    def __init__(self, handle):
        self.handle = handle
        self.pid = handle.pid

    @property
    def exitcode(self):
        return self.handle.poll()

    def is_alive(self):
        return self.handle.poll() is None

    def join(self, timeout=None):
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.handle.wait(timeout=timeout)

    def terminate(self):
        self.handle.terminate()

    def kill(self):
        self.handle.kill()


@dataclass
class Child:
    name: str
    sock: socket.socket
    process: object = None
    control: object = None
    state: str = "stopped"
    restarts: deque = field(default_factory=deque)
    restart_at: float | None = None
    deadline: float = 0
    started_at: float | None = None
    exit_code: int | None = None


class Supervisor:
    def __init__(self, data_dir, runtime):
        self.data_dir, self.runtime = data_dir, runtime
        self.secret = token()
        self.children = {}
        self.stopping = False

    def notify_web(self, ready):
        web = self.children.get("web")
        if web and web.process and web.process.is_alive():
            with contextlib.suppress(OSError):
                web.control.send({"backend_ready": ready})

    def start_child(self, child):
        if child.control:
            child.control.close()
        kwargs = (
            {"pass_fds": (child.sock.fileno(),), "start_new_session": True}
            if os.name != "nt"
            else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        )
        handle = subprocess.Popen(
            [sys.executable, "-m", "sleepy.service"],
            stdin=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            **python_process_options(),
            **kwargs,
        )
        process, parent = Process(handle), Control(handle.stdin)
        child.control, child.process = parent, process
        socket_info = (
            {"fd": child.sock.fileno()}
            if os.name != "nt"
            else {"share": base64.b64encode(child.sock.share(handle.pid)).decode("ascii")}
        )
        parent.send(
            {
                "name": child.name,
                "socket": socket_info,
                "data_dir": str(self.data_dir),
                "upstream": self.upstream,
                "secret": self.secret,
                "runtime": self.runtime,
            }
        )
        child.state, child.started_at = "starting", time.time()
        child.deadline = time.monotonic() + 60
        child.restart_at = None
        LOG.info("%s starting pid=%s", child.name, process.pid)

    def ready(self, name):
        child = self.children[name]
        if not child.process.is_alive():
            return False
        url = self.upstream if name == "backend" else self.public_health
        try:
            response = httpx.get(
                url + "/_internal/health",
                headers={INTERNAL_HEADER: self.secret},
                timeout=0.3,
                trust_env=False,
                verify=False,
            )
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def stop(self):
        self.stopping = True
        deadline = time.monotonic() + 30
        # Prepare both; backend deliberately remains available for the web's metrics flush.
        for child in self.children.values():
            if child.control and child.process and child.process.is_alive():
                with contextlib.suppress(OSError):
                    child.control.send("prepare")
        for name, grace in (("web", 5), ("backend", 25)):
            child = self.children.get(name)
            if not child or not child.process:
                continue
            child.state = "stopping"
            if child.process.is_alive():
                with contextlib.suppress(OSError):
                    child.control.send("stop")
                child.process.join(timeout=max(0, min(grace, deadline - time.monotonic())))
            if child.process.is_alive():
                LOG.warning("%s exceeded shutdown grace; terminating", name)
                child.process.terminate()
                child.process.join(timeout=1)
            if child.process.is_alive():
                child.process.kill()
                child.process.join()
            child.exit_code, child.state = child.process.exitcode, "stopped"
            child.control.close()
            LOG.info("%s stopped exit=%s", name, child.exit_code)
        for child in self.children.values():
            child.sock.close()

    def run(self, on_ready=None):
        old_signals = {}
        handled_signals = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGBREAK"):
            handled_signals.append(signal.SIGBREAK)
        for sig in handled_signals:
            old_signals[sig] = signal.signal(sig, lambda *_: setattr(self, "stopping", True))
        try:
            backend_socket = bind_socket("127.0.0.1", 0)
            self.children["backend"] = Child("backend", backend_socket)
            self.upstream = f"http://127.0.0.1:{backend_socket.getsockname()[1]}"
            web_socket = bind_socket(self.runtime["host"], self.runtime["port"])
            self.children["web"] = Child("web", web_socket)
            scheme = "https" if self.runtime["ssl_certfile"] else "http"
            health_host = self.runtime["host"]
            if health_host in ("0.0.0.0", "::"):
                health_host = "127.0.0.1" if health_host == "0.0.0.0" else "::1"
            if ":" in health_host:
                health_host = f"[{health_host}]"
            self.public_health = f"{scheme}://{health_host}:{web_socket.getsockname()[1]}"
            startup_deadline = time.monotonic() + 60
            for child in self.children.values():
                self.start_child(child)
                while not self.stopping and not self.ready(child.name):
                    if not child.process.is_alive() or time.monotonic() >= startup_deadline:
                        raise RuntimeError(f"{child.name} 启动失败或就绪超时")
                    time.sleep(0.1)
                if self.stopping:
                    return
                child.state = "running"
            if on_ready:
                on_ready(self.public_health)
            while not self.stopping:
                now = time.monotonic()
                for child in self.children.values():
                    if child.process.is_alive():
                        if child.state == "starting":
                            if self.ready(child.name):
                                child.state = "running"
                                if child.name == "backend":
                                    self.notify_web(True)
                                elif self.children["backend"].state != "running":
                                    self.notify_web(False)
                            elif now >= child.deadline:
                                child.process.terminate()
                        continue
                    if child.restart_at is None:
                        child.process.join()
                        child.exit_code, child.state = child.process.exitcode, "failed"
                        if child.name == "backend":
                            self.notify_web(False)
                        LOG.warning(
                            "%s exited pid=%s code=%s",
                            child.name,
                            child.process.pid,
                            child.exit_code,
                        )
                        while child.restarts and child.restarts[0] < now - 300:
                            child.restarts.popleft()
                        if len(child.restarts) >= 3:
                            raise RuntimeError(f"{child.name} 在 5 分钟内耗尽重启次数")
                        child.restart_at = now + (2 ** (len(child.restarts) + 1))
                    if now >= child.restart_at:
                        child.restarts.append(now)
                        self.start_child(child)
                time.sleep(0.1)
        finally:
            self.stop()
            for sig, handler in old_signals.items():
                signal.signal(sig, handler)
