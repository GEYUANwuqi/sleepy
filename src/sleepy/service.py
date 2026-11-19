"""Private child entry point; bootstrap secrets arrive over an inherited pipe."""

import asyncio
import base64
import contextlib
import json
import os
import signal
import socket
import sys
import threading
from pathlib import Path

import uvicorn

from .backend import create_backend
from .web import create_web


def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    def control_lines():
        # Do not hold a BufferedReader lock in a daemon thread during interpreter shutdown.
        pending = b""
        while chunk := os.read(sys.stdin.fileno(), 4096):
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                yield line

    lines = control_lines()
    bootstrap_line = next(lines, None)
    if bootstrap_line is None:
        return
    bootstrap = json.loads(bootstrap_line)
    info = bootstrap["socket"]
    sock = (
        socket.fromshare(base64.b64decode(info["share"]))
        if "share" in info
        else socket.socket(fileno=info["fd"])
    )
    sock.setblocking(False)
    name, runtime = bootstrap["name"], bootstrap["runtime"]
    app = (
        create_backend(Path(bootstrap["data_dir"]), bootstrap["secret"], runtime)
        if name == "backend"
        else create_web(bootstrap["upstream"], bootstrap["secret"])
    )
    config = uvicorn.Config(
        app,
        workers=1,
        access_log=False,
        log_level="warning",
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1" if name == "backend" else runtime["trusted_proxies"],
        timeout_graceful_shutdown=3 if name == "web" else 20,
        ssl_certfile=runtime["ssl_certfile"] if name == "web" else None,
        ssl_keyfile=runtime["ssl_keyfile"] if name == "web" else None,
    )
    server = uvicorn.Server(config)
    server.capture_signals = contextlib.nullcontext

    async def run():
        loop = asyncio.get_running_loop()
        commands = asyncio.Queue()

        def read_control():
            try:
                for line in lines:
                    message = json.loads(line)
                    loop.call_soon_threadsafe(commands.put_nowait, message)
                    if message == "stop":
                        return
                loop.call_soon_threadsafe(commands.put_nowait, "stop")
            except (OSError, ValueError, RuntimeError):
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(commands.put_nowait, "stop")

        threading.Thread(target=read_control, name="parent-control", daemon=True).start()

        async def watch():
            while True:
                message = await commands.get()
                if message == "stop":
                    break
                if name == "web" and isinstance(message, dict) and "backend_ready" in message:
                    app.state.backend_ready = message["backend_ready"]
            if name == "backend" and hasattr(app.state, "events"):
                app.state.events.stopping.set()
            if name == "web" and hasattr(app.state, "stopping"):
                app.state.stopping.set()
            server.should_exit = True

        watcher = asyncio.create_task(watch())
        try:
            await server.serve(sockets=[sock])
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
            sock.close()

    # The supervisor keeps the listening socket for future children. A Windows
    # proactor would bind it to this process's I/O completion port after accept.
    asyncio.run(run(), loop_factory=asyncio.SelectorEventLoop if os.name == "nt" else None)


if __name__ == "__main__":
    main()
