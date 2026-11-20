"""Bundled static web server and fixed-loopback HTTP/SSE gateway."""

import asyncio
import contextlib
import logging
import time
from collections import Counter, deque
from contextlib import asynccontextmanager
from html import escape
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path
from uuid import uuid4

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .backend import INTERNAL_HEADER

LOG = logging.getLogger(__name__)
HOP_HEADERS = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"te",
    b"trailer",
    b"transfer-encoding",
    b"upgrade",
}
STATIC_DIR = Path(__file__).parent / "static"


class RejectCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False


def proxy_client(**kwargs):
    return httpx.AsyncClient(
        cookies=CookieJar(policy=RejectCookies()),
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(30, connect=5),
        limits=httpx.Limits(max_connections=512, max_keepalive_connections=32),
        **kwargs,
    )


def filtered_headers(raw, request=False):
    connection_names = {
        part.strip().lower()
        for key, value in raw
        if key.lower() == b"connection"
        for part in value.split(b",")
    }
    banned = HOP_HEADERS | connection_names
    result = []
    for key, value in raw:
        lower = key.lower()
        if lower in banned:
            continue
        if request and (lower == b"forwarded" or lower.startswith((b"x-forwarded-", b"x-sleepy-"))):
            continue
        result.append((key, value))
    return result


def metric_route(path):
    if path.startswith(("/_internal", "/assets/")) or path in ("/favicon.ico", "/robots.txt"):
        return None
    if path == "/" or path in ("/admin", "/admin/devices", "/admin/settings", "/admin/statistics"):
        return "page", "/admin" if path.startswith("/admin") else "/"
    if path in ("/api/status/events", "/api/admin/events"):
        return "sse", path
    if path in ("/api/device/report", "/api/smtc/report"):
        return "report", path
    if path == "/api/status":
        return "public_api", path
    if path.startswith("/api/admin/"):
        parts = path.split("/")
        if len(parts) == 5 and parts[3] == "tokens":
            return "admin_api", "/api/admin/tokens/{id}"
        if len(parts) >= 5 and parts[3] == "devices":
            return "admin_api", "/api/admin/devices/{id}" + ("/key" if parts[-1] == "key" else "")
        if parts[3] in (
            "devices",
            "smtc",
            "auth",
            "login",
            "logout",
            "password",
            "settings",
            "tokens",
            "status",
            "statistics",
        ):
            return "admin_api", "/api/admin/" + parts[3]
    return "unmatched", "unmatched"


class Metrics:
    def __init__(self, upstream, secret, client):
        self.upstream, self.secret, self.client = upstream, secret, client
        self.run, self.sequence = uuid4().hex, 0
        self.current = Counter()
        self.pending = deque()
        self.wakeup = asyncio.Event()
        self.categories = {"page", "public_api", "admin_api", "report", "sse", "unmatched"}
        self.enabled = True

    def record(self, scope, status, started):
        route = metric_route(scope["path"])
        if not route or not self.enabled:
            return
        category, label = route
        if category not in self.categories:
            return
        if category == "page" and (scope["method"] != "GET" or status not in (200, 304)):
            return
        method = (
            scope["method"]
            if scope["method"] in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
            else "OTHER"
        )
        self.current[(int(started // 60), category, label, method, status)] += 1
        if sum(self.current.values()) >= 100:
            self.wakeup.set()

    def enqueue(self):
        if not self.current:
            return
        self.sequence += 1
        rows = [
            dict(zip(("minute", "category", "route", "method", "status"), key), count=count)
            for key, count in self.current.items()
        ]
        self.current.clear()
        if len(self.pending) >= 120:
            dropped = self.pending.popleft()
            LOG.warning(
                "Metrics queue full; dropped %d requests", sum(r["count"] for r in dropped["rows"])
            )
        self.pending.append({"run": self.run, "sequence": self.sequence, "rows": rows})

    async def flush(self):
        self.enqueue()
        while self.pending:
            try:
                response = await self.client.post(
                    self.upstream + "/_internal/metrics",
                    json=self.pending[0],
                    headers={INTERNAL_HEADER: self.secret},
                    timeout=2,
                )
                response.raise_for_status()
            except httpx.HTTPError:
                return
            self.pending.popleft()

    async def loop(self):
        while True:
            try:
                response = await self.client.get(
                    self.upstream + "/_internal/settings",
                    headers={INTERNAL_HEADER: self.secret},
                    timeout=2,
                )
                response.raise_for_status()
                self.categories = set(response.json()["stats_categories"])
            except (httpx.HTTPError, ValueError, KeyError):
                pass
            try:
                await asyncio.wait_for(self.wakeup.wait(), 5)
            except TimeoutError:
                pass
            self.wakeup.clear()
            await self.flush()


class EdgeMiddleware:
    def __init__(self, app, owner):
        self.app, self.owner = app, owner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        status, counted, started = 499, False, time.time()
        is_sse = scope["path"] in ("/api/status/events", "/api/admin/events")

        async def output(message):
            nonlocal status, counted
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                image_sources = b"'self' data:"
                if scope["path"] == "/":
                    image_sources += b" http: https:"
                headers += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"),
                    (b"x-frame-options", b"DENY"),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src "
                        + image_sources
                        + b"; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                    ),
                ]
                if not scope["path"].startswith("/assets/"):
                    headers = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
                if is_sse:
                    self.owner.state.metrics.record(scope, status, started)
                    counted = True
            await send(message)

        try:
            await self.app(scope, receive, output)
        finally:
            if not counted:
                self.owner.state.metrics.record(scope, status, started)


def create_web(upstream: str, internal_token: str, static_dir: Path | None = None) -> Starlette:
    static_dir = static_dir or STATIC_DIR

    @asynccontextmanager
    async def lifespan(app):
        async with proxy_client() as client:
            app.state.client = client
            app.state.stopping = asyncio.Event()
            app.state.metrics = Metrics(upstream, internal_token, client)
            app.state.metric_task = asyncio.create_task(app.state.metrics.loop())
            try:
                yield
            finally:
                app.state.stopping.set()
                app.state.metric_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await app.state.metric_task
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(app.state.metrics.flush(), 2)
                if app.state.metrics.pending:
                    LOG.warning(
                        "Shutdown with %d unpersisted metrics batches",
                        len(app.state.metrics.pending),
                    )

    async def page(request):
        index = static_dir / "index.html"
        if request.url.path != "/":
            return FileResponse(index)
        try:
            response = await app.state.client.get(
                upstream + "/_internal/settings",
                headers={INTERNAL_HEADER: internal_token},
                timeout=2,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return FileResponse(index)
        site = response.json()
        html = index.read_text(encoding="utf-8").replace(
            "<title>似了吗？</title>", f"<title>{escape(site['title'])}</title>"
        )
        links = []
        for filing in site["filings"]:
            icon = (
                '<img src="/assets/beian.png" alt="" width="16" height="18">'
                if filing["kind"] == "police"
                else ""
            )
            links.append(
                f'<a href="{escape(filing["url"])}" target="_blank" rel="noopener noreferrer">{icon}{escape(filing["text"])}</a>'
            )
        if links:
            footer = (
                '<footer class="filing-links" aria-label="网站备案">' + "".join(links) + "</footer>"
            )
            html = html.replace("<noscript></noscript>", f"<noscript>{footer}</noscript>")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    async def health(request):
        if request.headers.get(INTERNAL_HEADER) != internal_token:
            return Response(status_code=404)
        return JSONResponse({"ready": True})

    async def proxy(request: Request):
        if request.url.path not in (
            "/api/status",
            "/api/status/events",
            "/api/device/report",
            "/api/smtc/report",
        ) and not request.url.path.startswith("/api/admin/"):
            return JSONResponse({"detail": "接口不存在"}, status_code=404)
        if app.state.stopping.is_set() or not app.state.backend_ready:
            return JSONResponse({"detail": "服务暂不可用，请稍后重试"}, status_code=503)
        host = request.headers.get("host", "")
        if not host or any(c in host for c in "/\\@ \t\r\n"):
            return Response(status_code=400)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                return JSONResponse({"detail": "请求体不能超过 64 KiB"}, status_code=413)
        headers = filtered_headers(request.headers.raw, request=True)
        headers += [
            (b"x-forwarded-proto", request.url.scheme.encode()),
            (b"x-forwarded-for", (request.client.host if request.client else "127.0.0.1").encode()),
        ]
        raw_path = request.scope.get("raw_path", request.url.path.encode())
        query = request.scope.get("query_string", b"")
        url = httpx.URL(upstream).copy_with(raw_path=raw_path + (b"?" + query if query else b""))
        streaming = request.url.path in ("/api/status/events", "/api/admin/events")
        outgoing = app.state.client.build_request(
            request.method,
            url,
            headers=headers,
            content=bytes(body),
            timeout=httpx.Timeout(60 if streaming else 30, connect=5),
        )
        try:
            response = await app.state.client.send(outgoing, stream=True)
        except httpx.HTTPError:
            return JSONResponse({"detail": "后端暂不可用，请稍后重试"}, status_code=503)

        async def stream():
            stopped = asyncio.create_task(app.state.stopping.wait()) if streaming else None
            chunk_task = None
            try:
                iterator = response.aiter_raw().__aiter__()
                while True:
                    if stopped:
                        chunk_task = asyncio.create_task(anext(iterator))
                        done, _ = await asyncio.wait(
                            [chunk_task, stopped], return_when=asyncio.FIRST_COMPLETED
                        )
                        if stopped in done:
                            break
                        chunk = chunk_task.result()
                    else:
                        chunk = await anext(iterator)
                    yield chunk
            except StopAsyncIteration:
                pass
            except httpx.HTTPError:
                LOG.info("Upstream stream disconnected")
            finally:
                for task in (chunk_task, stopped):
                    if task:
                        task.cancel()
                        with contextlib.suppress(
                            asyncio.CancelledError, StopAsyncIteration, httpx.HTTPError
                        ):
                            await task
                await response.aclose()

        result = StreamingResponse(stream(), status_code=response.status_code)
        result.raw_headers = filtered_headers(response.headers.raw)
        return result

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/_internal/health", health),
            Route(
                "/api/{path:path}",
                proxy,
                methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
            ),
            Mount("/assets", StaticFiles(directory=static_dir / "assets", check_dir=False)),
            *[
                Route(path, page)
                for path in (
                    "/",
                    "/admin",
                    "/admin/devices",
                    "/admin/settings",
                    "/admin/statistics",
                )
            ],
        ],
    )
    app.state.backend_ready = True
    app.add_middleware(EdgeMiddleware, owner=app)
    return app
