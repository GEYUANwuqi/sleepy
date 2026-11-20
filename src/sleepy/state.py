"""Permission-filtered snapshots and bounded, latest-state subscriptions."""

import asyncio
import time
from uuid import uuid4

from .models import DeviceHints
from .storage import Store


def online(device, now):
    if not device["config"].enabled:
        return "disabled"
    updated = device["last_updated"]
    if updated is None:
        return "unknown"
    return "online" if now - updated < device["config"].timeout else "offline"


def snapshot(store: Store, admin: bool, now: float | None = None):
    now = time.time() if now is None else now
    settings, devices = store.settings(), store.devices()
    visible = [
        d
        for d in devices
        if not settings.hide_all and not d["config"].hidden and d["config"].enabled
    ]
    participants = [
        d for d in visible if d["config"].participates and "using" in d["config"].public_fields
    ]
    fresh = [d for d in participants if online(d, now) == "online"]
    manual = store.manual()
    manual = manual if manual and (manual["expires"] is None or manual["expires"] > now) else None
    if manual:
        code = manual["status"]
    elif not participants or all(d["last_updated"] is None for d in participants):
        code = "unknown"
    elif any(d["state"].get("using") is True for d in fresh):
        code = "active"
    elif any(d["state"].get("using") is None for d in fresh):
        code = "reachable"
    elif fresh:
        code = "idle"
    else:
        code = "offline"
    result = []
    for d in devices if admin else visible:
        c = d["config"]
        connection = online(d, now)
        readings = {} if connection == "offline" else d["state"]
        item = {
            "id": d["id"],
            "name": c.name,
            "kind": c.kind,
            "os": c.os,
            "online": connection,
            "order": c.order,
            "hints": c.hints.model_dump() if c.hints else None,
            "custom_fields": [
                f.model_dump(exclude_none=True)
                for f in c.custom_fields
                if admin or f.key in c.public_fields
            ],
            "state": {k: v for k, v in readings.items() if admin or k in c.public_fields},
        }
        # Exact report times are private: changes to non-public fields must not leak via SSE.
        if admin:
            item.update(config=c.model_dump(), last_updated=d["last_updated"])
        result.append(item)
    result.sort(
        key=lambda d: (
            -(d["online"] == "online" and d["state"].get("using") is True)
            if settings.using_first
            else 0,
            d["order"],
            d["name"],
            d["id"],
        )
    )
    label = settings.labels.get(code, settings.labels["unknown"])
    value = {
        "site": {
            "title": settings.title,
            "filings": settings.display.filing_links(),
            "description": settings.description,
            "theme": settings.theme,
            "display": settings.display.model_dump(mode="json") | DeviceHints().model_dump(),
        },
        "presence": {"code": code, **label.model_dump(), "manual": bool(manual)},
        "devices": result,
        "smtc": store.smtc(now) if admin or not settings.hide_all else None,
    }
    if admin:
        value["manual"] = manual
    return value


class Events:
    def __init__(self, store: Store):
        self.store = store
        self.epoch = uuid4().hex
        self.versions = {False: 0, True: 0}
        self.latest = {False: None, True: None}
        self.subscribers = {False: set(), True: set()}
        self.stopping = asyncio.Event()

    def refresh(self):
        for admin in (False, True):
            value = snapshot(self.store, admin)
            if value == self.latest[admin]:
                continue
            self.latest[admin] = value
            self.versions[admin] += 1
            for queue in tuple(self.subscribers[admin]):
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(self.current(admin))

    def current(self, admin=False):
        return {**self.latest[admin], "version": f"{self.epoch}:{self.versions[admin]}"}

    def subscribe(self, admin):
        # No await between reading the snapshot and registering its queue.
        queue = asyncio.Queue(maxsize=1)
        self.subscribers[admin].add(queue)
        queue.put_nowait(self.current(admin))
        return queue

    async def timer(self):
        while not self.stopping.is_set():
            self.refresh()
            now = time.time()
            deadlines = [
                d["last_updated"] + d["config"].timeout
                for d in self.store.devices()
                if d["last_updated"] is not None and d["config"].enabled
            ]
            manual = self.store.manual()
            if manual and manual["expires"]:
                deadlines.append(manual["expires"])
            delay = min([max(0.01, t - now) for t in deadlines if t > now] + [1.0])
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=delay)
            except TimeoutError:
                pass
