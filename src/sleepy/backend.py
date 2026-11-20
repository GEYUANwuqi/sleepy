"""FastAPI business service. All authentication and visibility checks live here."""

import asyncio
import contextlib
import json
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import DeviceConfig, MusicReport, Settings, device_adapter
from .security import HASHER, validate_password, verify_password
from .state import Events
from .storage import Store

COOKIE = "sleepy_session"
INTERNAL_HEADER = "x-sleepy-internal"


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class TokenCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=80)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class SmtcSwitch(BaseModel):
    enabled: bool


class ManualStatus(BaseModel):
    status: str | None = None
    duration: int | None = Field(default=3600, ge=60, le=31536000)


class MetricRow(BaseModel):
    minute: int = Field(ge=0)
    category: str = Field(max_length=32)
    route: str = Field(max_length=100)
    method: str = Field(max_length=16)
    status: int = Field(ge=0, le=599)
    count: int = Field(ge=1, le=1000000)


class MetricBatch(BaseModel):
    run: str = Field(min_length=1, max_length=64)
    sequence: int = Field(ge=1)
    rows: list[MetricRow] = Field(max_length=10000)


def create_backend(data_dir: Path, internal_token: str, runtime: dict | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        store = Store(data_dir)
        app.state.store = store
        app.state.events = Events(store)
        app.state.events.refresh()
        timer = asyncio.create_task(app.state.events.timer())
        try:
            yield
        finally:
            app.state.events.stopping.set()
            timer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer
            store.close()

    app = FastAPI(
        title="Sleepy", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    attempts: dict[str, deque] = defaultdict(deque)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def invalid_payload(request, exc):
        # Do not echo passwords, credentials or the original request in validation errors.
        return JSONResponse(
            {
                "detail": "请求字段校验失败",
                "errors": [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()
                ],
            },
            status_code=422,
        )

    def store() -> Store:
        return app.state.store

    def refresh():
        app.state.events.refresh()

    def check_csrf(request: Request, session):
        origin = request.headers.get("origin", "")
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin != expected:
            raise HTTPException(403, "请求来源不匹配")
        if not session or not secrets.compare_digest(
            request.headers.get("x-csrf-token", ""), session["csrf"]
        ):
            raise HTTPException(403, "CSRF 校验失败，请刷新登录页面")

    def admin(request: Request, write=False, forced=False):
        session = store().session(request.cookies.get(COOKIE))
        if not session or not session["authenticated"]:
            raise HTTPException(401, "请先登录")
        if write:
            check_csrf(request, session)
        if store().admin()["must_change"] and not forced:
            raise HTTPException(403, "请先修改初始密码")
        return session

    def authorize_automation(request: Request, write=False):
        header = request.headers.get("authorization")
        if header is None:
            admin(request, write=write)
            return
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not store().valid_settings_token(value):
            raise HTTPException(
                401, "API Token 无效或已撤销", headers={"WWW-Authenticate": "Bearer"}
            )
        if store().admin()["must_change"]:
            raise HTTPException(403, "请先修改初始密码")

    def internal(request):
        if not secrets.compare_digest(request.headers.get(INTERNAL_HEADER, ""), internal_token):
            raise HTTPException(403, "禁止访问")

    def device(id):
        value = store().device(id)
        if value is None:
            raise HTTPException(404, "设备不存在")
        return value

    def cookie(response, request, value, max_age):
        response.set_cookie(
            COOKIE,
            value,
            max_age=max_age,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            path="/api/admin",
        )

    @app.get("/_internal/health")
    async def health(request: Request):
        internal(request)
        return {"ready": True}

    @app.get("/_internal/settings")
    async def internal_settings(request: Request):
        internal(request)
        settings = store().settings()
        return {
            "stats_categories": settings.stats_categories,
            "title": settings.title,
            "filings": settings.display.filing_links(),
        }

    @app.post("/_internal/metrics", status_code=204)
    async def metrics_ingest(request: Request, batch: MetricBatch):
        internal(request)
        store().ingest_metrics(batch.run, batch.sequence, [r.model_dump() for r in batch.rows])

    @app.get("/api/status")
    async def public_status():
        refresh()
        return app.state.events.current()

    async def event_stream(request, is_admin):
        session = admin(request) if is_admin else None
        refresh()
        events = app.state.events
        queue = events.subscribe(is_admin)

        async def generate():
            heartbeat = time.monotonic()
            try:
                yield "retry: 3000\n\n"
                while not events.stopping.is_set():
                    if session:
                        current = store().session(request.cookies.get(COOKIE))
                        if (
                            not current
                            or not current["authenticated"]
                            or store().admin()["must_change"]
                        ):
                            yield "event: session-expired\ndata: {}\n\n"
                            return
                    try:
                        value = await asyncio.wait_for(queue.get(), timeout=1 if session else 20)
                        yield f"id: {value['version']}\nevent: update\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"
                    except TimeoutError:
                        pass
                    if time.monotonic() - heartbeat >= 20:
                        yield "event: heartbeat\ndata: {}\n\n"
                        heartbeat = time.monotonic()
            finally:
                events.subscribers[is_admin].discard(queue)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/status/events")
    async def public_events(request: Request):
        return await event_stream(request, False)

    @app.get("/api/admin/events")
    async def admin_events(request: Request):
        return await event_stream(request, True)

    @app.post("/api/device/report", status_code=204)
    async def report(request: Request):
        auth = request.headers.get("authorization", "")
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(422, "请求必须是 JSON 对象") from None
        value = store().device_by_key(auth[7:]) if auth.startswith("Bearer ") else None
        if value is None:
            raise HTTPException(401, "无效的设备 Key")
        if not value["config"].enabled:
            raise HTTPException(403, "设备已停用")
        state = device_adapter(value["config"]).validate(payload)
        store().report(value["id"], state)
        refresh()

    @app.put("/api/admin/smtc")
    async def toggle_smtc(request: Request, body: SmtcSwitch):
        admin(request, write=True)
        key = store().set_smtc(body.enabled)
        refresh()
        return {"key": key}

    @app.post("/api/admin/smtc/key")
    async def rotate_smtc(request: Request):
        admin(request, write=True)
        key = store().rotate_smtc_key()
        if key is None:
            raise HTTPException(409, "请先启用 SMTC")
        return {"key": key}

    @app.post("/api/smtc/report", status_code=204)
    async def report_smtc(request: Request, body: MusicReport):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or not store().report_smtc(auth[7:], body):
            raise HTTPException(401, "SMTC 未启用或 Key 无效")
        refresh()

    @app.get("/api/admin/auth")
    async def auth_status(request: Request, response: Response):
        session = store().session(request.cookies.get(COOKIE))
        if not session:
            value, csrf, expires = store().new_session(False, 1800)
            cookie(response, request, value, 1800)
            return {"authenticated": False, "csrf": csrf, "expires": expires, "must_change": False}
        return {
            "authenticated": bool(session["authenticated"]),
            "csrf": session["csrf"],
            "expires": session["expires"],
            "must_change": bool(session["authenticated"] and store().admin()["must_change"]),
        }

    @app.post("/api/admin/login")
    async def login(request: Request, response: Response, body: Login):
        session = store().session(request.cookies.get(COOKIE))
        check_csrf(request, session)
        address = request.client.host if request.client else "local"
        now = time.monotonic()
        if len(attempts) > 1000:
            for ip in list(attempts):
                if not attempts[ip] or attempts[ip][-1] < now - 60:
                    del attempts[ip]
            if len(attempts) > 1000 and address not in attempts:
                raise HTTPException(429, "登录请求过多，请稍后重试")
        bucket = attempts[address]
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= 10:
            raise HTTPException(429, "尝试过于频繁，请稍后重试", headers={"Retry-After": "60"})
        bucket.append(now)
        encoded = store().admin()["password"]
        valid = await asyncio.to_thread(verify_password, encoded, body.password)
        if not valid or store().admin()["password"] != encoded:
            raise HTTPException(401, "密码不正确")
        store().delete_session(request.cookies.get(COOKIE, ""))
        value, csrf, expires = store().new_session(True, 8 * 3600)
        cookie(response, request, value, 8 * 3600)
        return {
            "authenticated": True,
            "csrf": csrf,
            "expires": expires,
            "must_change": bool(store().admin()["must_change"]),
        }

    @app.post("/api/admin/logout", status_code=204)
    async def logout(request: Request, response: Response):
        admin(request, write=True, forced=True)
        store().delete_session(request.cookies[COOKIE])
        response.delete_cookie(COOKIE, path="/api/admin")

    @app.post("/api/admin/password", status_code=204)
    async def change_password(request: Request, response: Response, body: PasswordChange):
        admin(request, write=True, forced=True)
        try:
            validate_password(body.new_password)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if body.current_password == body.new_password:
            raise HTTPException(422, "新密码必须与原密码不同")
        old_hash = store().admin()["password"]
        if not await asyncio.to_thread(verify_password, old_hash, body.current_password):
            raise HTTPException(403, "当前密码不正确")
        new_hash = await asyncio.to_thread(HASHER.hash, body.new_password)
        if store().admin()["password"] != old_hash:
            raise HTTPException(409, "密码已变更，请重新登录")
        store().change_password(new_hash)
        response.delete_cookie(COOKIE, path="/api/admin")

    @app.get("/api/admin/devices")
    async def list_devices(request: Request):
        admin(request)
        refresh()
        return app.state.events.current(True)

    @app.post("/api/admin/devices", status_code=201)
    async def create_device(request: Request, body: DeviceConfig):
        admin(request, write=True)
        if body.conflicting_new_fields(None):
            raise HTTPException(422, "字段标识与预设字段或设备资料冲突")
        id, key = store().create_device(body)
        refresh()
        return {"id": id, "key": key}

    @app.put("/api/admin/devices/{id}", status_code=204)
    async def update_device(id: str, request: Request, body: DeviceConfig):
        admin(request, write=True)
        if body.conflicting_new_fields(device(id)["config"]):
            raise HTTPException(422, "字段标识与预设字段或设备资料冲突")
        store().update_device(id, body)
        refresh()

    @app.delete("/api/admin/devices/{id}", status_code=204)
    async def delete_device(id: str, request: Request):
        admin(request, write=True)
        device(id)
        store().delete_device(id)
        refresh()

    @app.post("/api/admin/devices/{id}/key")
    async def reset_key(id: str, request: Request):
        admin(request, write=True)
        device(id)
        return {"key": store().rotate_key(id)}

    @app.get("/api/admin/tokens")
    async def list_tokens(request: Request):
        admin(request)
        return store().settings_tokens()

    @app.post("/api/admin/tokens", status_code=201)
    async def create_token(request: Request, body: TokenCreate):
        admin(request, write=True)
        return store().create_settings_token(body.name)

    @app.delete("/api/admin/tokens/{token_id}", status_code=204)
    async def revoke_token(request: Request, token_id: str):
        admin(request, write=True)
        if not store().revoke_settings_token(token_id):
            raise HTTPException(404, "API Token 不存在")

    @app.get("/api/admin/settings")
    async def settings(request: Request):
        authorize_automation(request)
        return {"settings": store().settings().model_dump(), "runtime": runtime or {}}

    def persist_settings(body: Settings):
        current_manual = store().manual()
        if current_manual and current_manual["status"] not in body.labels:
            raise HTTPException(422, "请先解除正在使用的手动状态，再移除该状态")
        store().save_settings(body)
        refresh()

    @app.put("/api/admin/settings", status_code=204)
    async def update_settings(request: Request, body: Settings):
        authorize_automation(request, write=True)
        persist_settings(body)

    @app.patch("/api/admin/settings", status_code=204)
    async def patch_settings(request: Request, body: dict):
        authorize_automation(request, write=True)
        current = store().settings().model_dump(mode="json")

        def merge(target, changes):
            for key, value in changes.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    merge(target[key], value)
                else:
                    target[key] = value

        merge(current, body)
        persist_settings(Settings.model_validate(current))

    @app.put("/api/admin/status", status_code=204)
    async def manual_status(request: Request, body: ManualStatus):
        authorize_automation(request, write=True)
        if body.status is not None and body.status not in store().settings().labels:
            raise HTTPException(422, "状态不存在")
        store().set_manual(body.status, body.duration)
        refresh()

    @app.get("/api/admin/statistics")
    async def statistics(request: Request, category: str | None = None):
        admin(request)
        tz = ZoneInfo(store().settings().timezone)
        now = datetime.now(tz)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        boundaries = {
            "day": today,
            "week": today - timedelta(days=today.weekday()),
            "month": today.replace(day=1),
            "year": today.replace(month=1, day=1),
        }
        clause, args = ("WHERE category=?", [category]) if category else ("", [])
        totals = {
            "total": store()
            .db.execute(f"SELECT COALESCE(SUM(count),0) FROM metrics {clause}", args)
            .fetchone()[0]
        }
        for name, start in boundaries.items():
            conditions = "WHERE minute>=? AND minute<=?" + (" AND category=?" if category else "")
            params = [int(start.timestamp() // 60), int(time.time() // 60), *args]
            totals[name] = (
                store()
                .db.execute(f"SELECT COALESCE(SUM(count),0) FROM metrics {conditions}", params)
                .fetchone()[0]
            )
        rows = [
            dict(row)
            for row in store().db.execute(
                f"SELECT category,route,method,status,SUM(count) AS count FROM metrics {clause} GROUP BY category,route,method,status ORDER BY count DESC",
                args,
            )
        ]
        return {"timezone": str(tz), "totals": totals, "routes": rows}

    return app
