import json
import time

from conftest import sign_in

from sleepy.models import REPORT_FIELDS
from sleepy.security import validate_password, verify_password
from sleepy.state import snapshot


def new_device(client, headers, **kwargs):
    response = client.post(
        "/api/admin/devices", json={"name": "测试设备", **kwargs}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def report(client, key, payload):
    return client.post(
        "/api/device/report", json=payload, headers={"Authorization": f"Bearer {key}"}
    )


def test_first_login_password_sessions_and_csrf(backend):
    client, app, initial, _ = backend
    assert len(initial) == 12
    validate_password(initial)
    assert initial != app.state.store.admin()["password"]
    assert verify_password(app.state.store.admin()["password"], initial)
    public = client.get("/api/status")
    assert "set-cookie" not in public.headers
    assert client.get("/api/admin/devices").status_code == 401
    headers = sign_in(client, initial)
    assert client.get("/api/admin/devices").status_code == 403
    body = {"current_password": initial, "new_password": "NewPass123!"}
    assert client.post("/api/admin/password", json=body).status_code == 403
    assert (
        client.post(
            "/api/admin/password", json=body, headers=headers | {"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/admin/password", json=body | {"new_password": "lowercase"}, headers=headers
        ).status_code
        == 422
    )
    old_cookie = client.cookies.get("sleepy_session")
    assert client.post("/api/admin/password", json=body, headers=headers).status_code == 204
    assert app.state.store.session(old_cookie) is None
    headers = sign_in(client, "NewPass123!")
    auth = client.get("/api/admin/auth").json()
    assert 28790 < auth["expires"] - time.time() <= 28800
    client.get("/api/admin/devices")
    assert client.get("/api/admin/auth").json()["expires"] == auth["expires"]
    with app.state.store.db:
        app.state.store.db.execute("UPDATE sessions SET expires=0")
    assert client.get("/api/admin/devices").status_code == 401


def test_flat_report_strict_validation_and_defaults(admin_client):
    client, app, headers, _ = admin_client
    d = new_device(client, headers, kind="laptop", public_fields=["using", "battery_percent"])
    key = d["key"]
    assert (
        report(
            client,
            key,
            {
                "using": True,
                "status": "Editor",
                "battery_percent": 86,
                "charging": False,
                "id": "other",
                "last_updated": 123,
                "extra": "discard",
            },
        ).status_code
        == 204
    )
    value = app.state.store.device(d["id"])
    assert value["state"] == {
        "using": True,
        "status": "Editor",
        "battery_percent": 86,
        "charging": False,
    }
    assert value["last_updated"] > 123
    updated = value["last_updated"]
    for invalid in (
        {"battery_percent": 101},
        {"using": "false"},
        {"battery_percent": True},
        {"battery_percent": "86"},
        [],
    ):
        response = report(client, key, invalid)
        assert response.status_code == 422
        assert "input" not in response.text
        assert app.state.store.device(d["id"])["last_updated"] == updated
    assert report(client, key, {"using": True}).status_code == 204
    assert app.state.store.device(d["id"])["state"] == {
        "using": True,
        "status": None,
        "battery_percent": None,
        "charging": None,
    }
    assert report(client, key, {"fields": {"using": True}}).status_code == 204
    assert all(value is None for value in app.state.store.device(d["id"])["state"].values())
    assert report(client, "bad-key", {}).status_code == 401
    assert (
        client.post(
            "/api/admin/devices", json={"name": "bad"}, headers={"Authorization": f"Bearer {key}"}
        ).status_code
        == 403
    )  # Existing session requires CSRF.
    assert client.get("/api/device/report").status_code == 405


def test_visibility_key_rotation_and_device_disable(admin_client):
    client, app, headers, _ = admin_client
    d = new_device(client, headers)
    assert report(client, d["key"], {"using": True, "status": "private"}).status_code == 204
    before = client.get("/api/status").json()
    assert before["presence"]["code"] == "active"
    assert before["devices"][0]["state"] == {"using": True}
    report(client, d["key"], {"using": True, "status": "different private activity"})
    assert client.get("/api/status").json() == before
    config = app.state.store.device(d["id"])["config"].model_dump()
    assert (
        client.put(
            f"/api/admin/devices/{d['id']}", json=config | {"hidden": True}, headers=headers
        ).status_code
        == 204
    )
    hidden = client.get("/api/status").json()
    assert hidden["devices"] == [] and hidden["presence"]["code"] == "unknown"
    report(client, d["key"], {"using": False})
    assert client.get("/api/status").json() == hidden
    assert len(client.get("/api/admin/devices").json()["devices"]) == 1
    rotated = client.post(f"/api/admin/devices/{d['id']}/key", headers=headers).json()["key"]
    assert report(client, d["key"], {}).status_code == 401
    assert report(client, rotated, {}).status_code == 204
    assert (
        client.put(
            f"/api/admin/devices/{d['id']}", json=config | {"enabled": False}, headers=headers
        ).status_code
        == 204
    )
    assert report(client, rotated, {}).status_code == 403
    assert client.delete(f"/api/admin/devices/{d['id']}", headers=headers).status_code == 204
    assert report(client, rotated, {}).status_code == 401


def test_disabling_fields_and_changing_type_clears_state(admin_client):
    client, app, headers, _ = admin_client
    d = new_device(client, headers, kind="laptop")
    report(client, d["key"], {"using": True, "battery_percent": 42})
    config = app.state.store.device(d["id"])["config"].model_dump()
    config.update(receive_fields=["using"], public_fields=["using"])
    client.put(f"/api/admin/devices/{d['id']}", json=config, headers=headers)
    assert app.state.store.device(d["id"])["state"] == {"using": True}
    assert (
        report(
            client, d["key"], {"using": True, "battery_percent": "invalid-but-disabled"}
        ).status_code
        == 204
    )
    config["kind"] = "server"
    client.put(f"/api/admin/devices/{d['id']}", json=config, headers=headers)
    assert app.state.store.device(d["id"])["last_updated"] is None
    assert app.state.store.device(d["id"])["state"] == {"using": None}


def test_presence_manual_override_and_expiry(admin_client):
    client, app, headers, _ = admin_client
    a = new_device(client, headers)
    b = new_device(client, headers, name="闲置设备")
    report(client, a["key"], {"using": True})
    report(client, b["key"], {"using": True})
    with app.state.store.db:
        app.state.store.db.execute(
            "UPDATE devices SET last_updated=? WHERE id=?", (time.time() - 500, b["id"])
        )
    assert snapshot(app.state.store, False)["presence"]["code"] == "active"
    assert (
        client.put(
            "/api/admin/status", json={"status": "busy", "duration": 60}, headers=headers
        ).status_code
        == 204
    )
    assert snapshot(app.state.store, False)["presence"]["code"] == "busy"
    assert snapshot(app.state.store, False, time.time() + 61)["presence"]["code"] == "active"
    assert snapshot(app.state.store, False, time.time() + 181)["presence"]["code"] == "offline"


def test_statistics_dedup_and_internal_auth(admin_client):
    client, app, _headers, _ = admin_client
    batch = {
        "run": "test-run",
        "sequence": 1,
        "rows": [
            {
                "minute": int(time.time() // 60),
                "category": "page",
                "route": "/",
                "method": "GET",
                "status": 200,
                "count": 3,
            }
        ],
    }
    assert client.post("/_internal/metrics", json=batch).status_code == 403
    for _ in range(2):
        assert (
            client.post(
                "/_internal/metrics",
                json=batch,
                headers={"x-sleepy-internal": "test-internal-token"},
            ).status_code
            == 204
        )
    result = client.get("/api/admin/statistics").json()
    assert result["totals"] == {"total": 3, "day": 3, "week": 3, "month": 3, "year": 3}
    assert client.get("/api/admin/statistics?category=report").json()["totals"]["total"] == 0
    stored = " ".join(
        str(tuple(row)) for row in app.state.store.db.execute("SELECT * FROM metrics")
    )
    assert "test-internal-token" not in stored


def test_passwords_and_keys_not_stored_in_plaintext(admin_client):
    client, app, headers, _path = admin_client
    d = new_device(client, headers)
    dump = "\n".join(app.state.store.db.iterdump())
    assert d["key"] not in dump
    assert "NewPass123!" not in dump
    assert client.cookies.get("sleepy_session") not in dump
    assert d["key"] not in json.dumps(client.get("/api/admin/devices").json())


def test_device_local_fields_visibility_and_schema_changes(admin_client):
    client, app, headers, _ = admin_client
    temperature = {"key": "temperature", "name": "温度", "type": "number"}
    config = {
        "name": "温度计",
        "custom_fields": [temperature],
        "receive_fields": ["temperature", "cpu_percent", "gpu_percent", "memory_percent"],
        "public_fields": ["temperature", "cpu_percent"],
    }
    first = new_device(client, headers, **config)
    second = new_device(client, headers, name="其他设备")
    values = {"temperature": 34.5, "cpu_percent": 0, "gpu_percent": 100, "memory_percent": 18.5}
    assert report(client, first["key"], values).status_code == 204
    assert report(client, second["key"], values).status_code == 204
    assert "temperature" not in app.state.store.device(second["id"])["state"]

    def public_device():
        devices = client.get("/api/status").json()["devices"]
        return next(device for device in devices if device["id"] == first["id"])

    assert public_device()["state"] == {"temperature": 34.5, "cpu_percent": 0}
    assert public_device()["custom_fields"] == [temperature]
    for invalid in ({"temperature": "not a number"}, {"cpu_percent": False}, {"gpu_percent": 101}):
        assert report(client, first["key"], invalid).status_code == 422
    assert app.state.store.device(first["id"])["state"] == values
    endpoint = f"/api/admin/devices/{first['id']}"
    config["public_fields"] = []
    assert client.put(endpoint, headers=headers, json=config).status_code == 204
    assert public_device()["custom_fields"] == [] and public_device()["state"] == {}
    assert app.state.store.device(first["id"])["state"]["temperature"] == 34.5
    config["custom_fields"] = [temperature | {"type": "boolean"}]
    assert client.put(endpoint, headers=headers, json=config).status_code == 204
    assert app.state.store.device(first["id"])["state"]["temperature"] is None
    assert report(client, first["key"], {"temperature": False}).status_code == 204
    config["custom_fields"] = []
    config["receive_fields"].remove("temperature")
    assert client.put(endpoint, headers=headers, json=config).status_code == 204
    assert "temperature" not in app.state.store.device(first["id"])["state"]
    for key in ("status", "id", "name", "_bad"):
        config["custom_fields"] = [temperature | {"key": key}]
        assert client.put(endpoint, headers=headers, json=config).status_code == 422


def test_later_preset_preserves_existing_device_field(admin_client, monkeypatch):
    client, app, headers, _ = admin_client
    field = {"key": "sensor_flag", "type": "boolean", "name": "已触发"}
    config = {
        "name": "传感器",
        "custom_fields": [field],
        "receive_fields": [field["key"]],
        "public_fields": [],
    }
    device = new_device(client, headers, **config)
    monkeypatch.setitem(REPORT_FIELDS, field["key"], (str | None, None))
    assert client.get("/api/admin/devices").status_code == 200
    assert (
        client.put(
            f"/api/admin/devices/{device['id']}",
            headers=headers,
            json=config | {"name": "原传感器"},
        ).status_code
        == 204
    )
    assert report(client, device["key"], {"sensor_flag": True}).status_code == 204
    assert app.state.store.device(device["id"])["state"] == {"sensor_flag": True}
    assert report(client, device["key"], {"sensor_flag": "text"}).status_code == 422
    assert client.post("/api/admin/devices", headers=headers, json=config).status_code == 422


def test_enabled_field_without_permissions_survives_edits(admin_client):
    client, app, headers, _ = admin_client
    device = new_device(client, headers, enabled_fields=["cpu_percent"])
    key, identity = device["key"], device["id"]
    config = app.state.store.device(identity)["config"].model_dump()
    assert config["enabled_fields"] == ["cpu_percent"]
    assert config["receive_fields"] == config["public_fields"] == []
    assert report(client, key, {"cpu_percent": 50}).status_code == 204
    assert app.state.store.device(identity)["state"] == {}
    url = f"/api/admin/devices/{identity}"
    assert (
        client.put(
            url, headers=headers, json=config | {"receive_fields": ["gpu_percent"]}
        ).status_code
        == 422
    )
    assert (
        client.put(
            url, headers=headers, json=config | {"public_fields": ["cpu_percent"]}
        ).status_code
        == 422
    )
    assert (
        client.put(
            url, headers=headers, json=config | {"receive_fields": ["cpu_percent"]}
        ).status_code
        == 204
    )
    assert report(client, key, {"cpu_percent": 50}).status_code == 204
    assert app.state.store.device(identity)["state"] == {"cpu_percent": 50}
    assert client.put(url, headers=headers, json=config).status_code == 204
    saved = app.state.store.device(identity)
    assert saved["config"].enabled_fields == ["cpu_percent"] and saved["state"] == {}


def test_single_smtc_key_and_track_lifecycle(admin_client):
    client, app, headers, _ = admin_client
    assert client.get("/api/status").json()["smtc"] is None
    path = "/api/admin/smtc"
    key = client.put(path, headers=headers, json={"enabled": True}).json()["key"]
    assert client.put(path, headers=headers, json={"enabled": True}).json()["key"] is None
    assert client.get("/api/status").json()["smtc"] == {"playback": "unknown"}
    device = new_device(client, headers)
    track = {"title": "测试曲目", "artist": "歌手", "album": "专辑", "playback": "playing"}

    def music_report(key, data):
        return client.post(
            "/api/smtc/report", headers={"Authorization": f"Bearer {key}"}, json=data
        )

    assert music_report(device["key"], track).status_code == 401
    assert report(client, key, {}).status_code == 401
    assert music_report(key, track).status_code == 204
    public = client.get("/api/status").json()
    assert public["smtc"] == track and public["presence"]["code"] == "unknown"
    assert key not in json.dumps(public)
    assert snapshot(app.state.store, False, time.time() + 91)["smtc"] == {"playback": "unknown"}
    config = app.state.store.settings()
    config.hide_all = True
    app.state.store.save_settings(config)
    assert client.get("/api/status").json()["smtc"] is None
    assert client.get("/api/admin/devices").json()["smtc"] == track
    rotated = client.post(path + "/key", headers=headers).json()["key"]
    assert rotated != key and music_report(key, track).status_code == 401
    assert client.put(path, headers=headers, json={"enabled": False}).status_code == 200
    assert music_report(rotated, track).status_code == 401
    assert client.put(path, headers=headers, json={"enabled": True}).json()["key"] is None
    assert client.get("/api/admin/devices").json()["smtc"] == {"playback": "unknown"}
    assert music_report(rotated, {"playback": "idle"}).status_code == 204
    assert client.get("/api/admin/devices").json()["smtc"]["title"] == ""


def test_settings_token_without_session_and_revocation(admin_client):
    client, app, headers, _ = admin_client
    endpoint = "/api/admin/settings"
    management = "/api/admin/tokens"
    assert client.post(management, json={"name": "脚本"}).status_code == 403
    response = client.post(management, headers=headers, json={"name": "脚本"})
    assert response.status_code == 201
    issued = response.json()
    secret = issued["token"]
    listed = client.get(management).json()
    assert listed == [{k: v for k, v in issued.items() if k != "token"}]
    assert secret not in str(
        app.state.store.db.execute("SELECT token_hash FROM settings_tokens").fetchone()[0]
    )
    session = client.cookies.get("sleepy_session")
    client.cookies.clear()
    bearer = {"Authorization": "Bearer " + secret}
    assert client.get(endpoint).status_code == 401
    status_api = "/api/admin/status"
    assert client.put(status_api, json={"status": "sleeping"}).status_code == 401
    assert client.put(status_api, headers=bearer, json={"status": "sleeping"}).status_code == 204
    assert client.get("/api/status").json()["presence"]["name"] == "睡觉中"
    assert client.put(status_api, headers=bearer, json={"status": "missing"}).status_code == 422
    custom = {"labels": {"reading": {"name": "看书中", "color": "#5588b1"}}}
    assert client.patch(endpoint, headers=bearer, json=custom).status_code == 204
    assert (
        client.put(
            status_api, headers=bearer, json={"status": "reading", "duration": None}
        ).status_code
        == 204
    )
    assert client.get("/api/status").json()["presence"]["name"] == "看书中"
    assert client.put(status_api, headers=bearer, json={"status": None}).status_code == 204
    assert client.get("/api/status").json()["presence"]["manual"] is False
    original = client.get(endpoint, headers=bearer).json()["settings"]
    patch = {"title": "自动化", "display": {"footer": "定时更新"}}
    assert client.patch(endpoint, headers=bearer, json=patch).status_code == 204
    saved = client.get(endpoint, headers=bearer).json()["settings"]
    assert saved == original | {
        "title": "自动化",
        "display": original["display"] | patch["display"],
    }
    assert (
        client.patch(endpoint, headers=bearer, json={"display": {"card_opacity": 200}}).status_code
        == 422
    )
    assert client.get(endpoint, headers=bearer).json()["settings"] == saved
    assert client.put(endpoint, headers=bearer, json=original).status_code == 204
    assert client.get(management, headers=bearer).status_code == 401
    assert client.post(management, headers=bearer, json={"name": "新令牌"}).status_code == 401
    assert client.delete(management + "/" + issued["id"], headers=bearer).status_code == 401
    assert client.get("/api/admin/devices", headers=bearer).status_code == 401
    client.cookies.set("sleepy_session", session, path="/api/admin")
    assert client.patch(endpoint, json=patch).status_code == 403
    assert client.delete(management + "/" + issued["id"], headers=headers).status_code == 204
    client.cookies.clear()
    assert client.patch(endpoint, headers=bearer, json=patch).status_code == 401
    assert client.put(status_api, headers=bearer, json={"status": "sleeping"}).status_code == 401
