import re

from click.testing import CliRunner
from filelock import FileLock

from sleepy.cli import main
from sleepy.models import DeviceConfig
from sleepy.security import verify_password
from sleepy.storage import Store


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
