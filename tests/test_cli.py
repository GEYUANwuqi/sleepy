import json
import re

from click.testing import CliRunner
from filelock import FileLock

from sleepy.cli import main
from sleepy.models import DeviceConfig, device_adapter
from sleepy.security import verify_password
from sleepy.storage import Store


def test_startup_preserves_existing_site(tmp_path):
    store = Store(tmp_path)
    store.initialize()
    config = DeviceConfig.model_validate(
        {
            "name": "工作机",
            "custom_fields": [
                {"key": "plugged", "name": "插电", "type": "boolean"},
                {"key": "note", "name": "备注", "type": "text"},
            ],
            "enabled_fields": ["plugged"],
            "receive_fields": ["plugged"],
            "public_fields": [],
        }
    )
    identity, key = store.create_device(config)
    store.report(identity, {"plugged": False})
    store.set_smtc(True)
    store.set_manual("busy", None)
    store.create_settings_token("automation")
    store.new_session(True, 3600)
    store.ingest_metrics(
        "previous",
        1,
        [
            {
                "minute": 1,
                "category": "page",
                "route": "/",
                "method": "GET",
                "status": 200,
                "count": 17,
            }
        ],
    )
    old = {
        "title": "保留标题",
        "description": "一些设备，一点日常。",
        "display": {"in_use": "以前的提示", "footer": "已有页脚"},
        "local_setting": [1, 2],
    }
    with store.db:
        store.db.execute("UPDATE settings SET data=? WHERE id=1", (json.dumps(old),))
    before = list(store.db.iterdump())
    store.close()

    store = Store(tmp_path)
    try:
        assert store.initialize() is None
        assert list(store.db.iterdump()) == before
        device = store.device_by_key(key)
        assert device["id"] == identity and device["config"] == config
        assert device_adapter(device["config"]).validate({"plugged": True}) == {"plugged": True}
        settings = store.settings()
        assert settings.description == old["description"] and settings.display.filing_links() == []
        settings.display.icp_filing = "测试备案号"
        store.save_settings(settings)
        saved = json.loads(store.db.execute("SELECT data FROM settings").fetchone()[0])
        assert saved["local_setting"] == old["local_setting"]
        assert saved["display"]["in_use"] == "以前的提示"
    finally:
        store.close()


def test_reset_revokes_sessions_and_preserves_devices(tmp_path):
    store = Store(tmp_path)
    initial = store.initialize()
    device, key = store.create_device(DeviceConfig(name="test"))
    session, _, _ = store.new_session(True, 28800)
    store.close()
    result = CliRunner().invoke(main, ["reset", "pwd", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    password = re.search(r"密码已重置：(.+)", result.output).group(1)
    store = Store(tmp_path)
    assert store.session(session) is None
    assert store.admin()["must_change"]
    assert verify_password(store.admin()["password"], password)
    assert not verify_password(store.admin()["password"], initial)
    assert store.device_by_key(key)["id"] == device
    store.close()


def test_reset_refuses_running_or_missing_database(tmp_path):
    runner = CliRunner()
    missing = tmp_path / "missing"
    assert runner.invoke(main, ["reset", "pwd", "--data-dir", str(missing)]).exit_code != 0
    assert not missing.exists()
    store = Store(tmp_path)
    store.initialize()
    password_hash = store.admin()["password"]
    with FileLock(tmp_path / "sleepy.lock"):
        result = runner.invoke(main, ["reset", "pwd", "--data-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert store.admin()["password"] == password_hash
    store.close()
