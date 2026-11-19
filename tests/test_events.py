import asyncio
import contextlib
import time

from sleepy.models import DeviceConfig
from sleepy.state import Events
from sleepy.storage import Store


async def test_deadline_publishes_offline_without_new_report(tmp_path):
    store = Store(tmp_path)
    store.initialize()
    id, _ = store.create_device(DeviceConfig(name="deadline", timeout=10))
    store.report(id, {"using": True, "status": "editor"})
    with store.db:
        store.db.execute("UPDATE devices SET last_updated=? WHERE id=?", (time.time() - 9.5, id))
    events = Events(store)
    events.refresh()
    queue = events.subscribe(False)
    admin_queue = events.subscribe(True)
    assert (await queue.get())["presence"]["code"] == "active"
    assert (await admin_queue.get())["devices"][0]["state"]["status"] == "editor"
    task = asyncio.create_task(events.timer())
    try:
        value = await asyncio.wait_for(queue.get(), 2)
        assert value["presence"]["code"] == "offline"
        assert value["devices"][0]["online"] == "offline"
        assert value["devices"][0]["state"] == {}
        assert value["site"]["display"]["offline"] == "不知道在干嘛喵"
        admin_device = (await asyncio.wait_for(admin_queue.get(), 2))["devices"][0]
        assert admin_device["state"] == {}
        assert admin_device["last_updated"] is not None
        store.report(id, {"using": False})
        events.refresh()
        device = (await queue.get())["devices"][0]
        assert device["online"] == "online"
        assert device["state"] == {"using": False}
    finally:
        events.stopping.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        store.close()
