import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from runpy import run_path
from threading import Thread

import pytest

CLIENTS = Path(__file__).resolve().parents[1] / "clients"


@pytest.mark.parametrize("platform", ["linux", "windows"])
def test_standalone_report(tmp_path, platform):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (
                    self.path,
                    self.headers["Authorization"],
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            self.send_response(204)
            self.end_headers()

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        source = (CLIENTS / platform / "report.py").read_text(encoding="utf-8")
        script = tmp_path / "report.py"
        script.write_text(
            source.replace(
                'SITE_URL = ""', f'SITE_URL = "http://127.0.0.1:{server.server_port}"'
            ).replace('DEVICE_KEY = ""', 'DEVICE_KEY = "test-key"'),
            encoding="utf-8",
        )
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            # Isolate from site-packages, the checkout and the invoking environment.
            command = [sys.executable, "-I", "-S", str(script), "report"]
            subprocess.run(
                command + ["--json", '{"using":true}'], cwd=tmp_path, check=True, timeout=10
            )
            assert received == [("/api/device/report", "Bearer test-key", {"using": True})]
            if (platform == "windows" and sys.platform == "win32") or sys.platform.startswith(
                platform
            ):
                subprocess.run(command, cwd=tmp_path, check=True, timeout=10)
                fields = {"using", "status", "battery_percent", "charging"}
                if platform == "windows":
                    fields.update(("cpu_percent", "memory_percent", "gpu_percent"))
                assert set(received[-1][2]) == fields
        finally:
            server.shutdown()
            worker.join()


@pytest.mark.parametrize("platform", ["linux", "windows"])
@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/test",
        "http://user:password@host",
        "https://host/?key=secret",
        "https://host/path",
    ],
)
def test_invalid_endpoint(url, platform, monkeypatch):
    endpoint = run_path(str(CLIENTS / platform / "report.py"))["endpoint"]
    monkeypatch.setitem(endpoint.__globals__, "SITE_URL", url)
    with pytest.raises(ValueError):
        endpoint("test-key")


@pytest.mark.parametrize(
    "ac, flags, percent, success, expected",
    [
        (1, 1, 100, True, (100, True)),
        (1, 0, 80, True, (80, True)),
        (0, 9, 72, True, (72, True)),
        (255, 8, 50, True, (50, True)),
        (0, 0, 67, True, (67, False)),
        (1, 128, 255, True, (None, True)),
        (1, 255, 255, True, (None, True)),
        (0, 255, 255, True, (None, None)),
        (255, 0, 50, True, (50, None)),
        (1, 8, 50, False, (None, None)),
    ],
)
def test_windows_power_report(monkeypatch, ac, flags, percent, success, expected):
    import ctypes
    from types import SimpleNamespace

    def get_power(pointer):
        power = pointer._obj
        power.ACLineStatus, power.BatteryFlag, power.BatteryLifePercent = ac, flags, percent
        return success

    collect = run_path(str(CLIENTS / "windows/report.py"))["collect"]
    kernel = SimpleNamespace(GetSystemPowerStatus=get_power)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel), raising=False)
    monkeypatch.setitem(collect.__globals__, "windows_activity", lambda _: (None, None))
    payload = collect()
    assert payload == {
        "using": None,
        "status": None,
        "battery_percent": expected[0],
        "charging": expected[1],
    }


def test_windows_activity_reports_reuse_performance_sample(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    run = run_path(str(CLIENTS / "windows/report.py"))["run"]
    now = 0
    sent = []
    performance = Mock(side_effect=[{"cpu_percent": 21.0}, {"cpu_percent": 36.0}])

    def sleep(seconds):
        nonlocal now
        now += seconds
        if now > 32:
            raise StopIteration

    def send(url, path, key, payload):
        sent.append((now, payload))

    for key, value in {
        "time": SimpleNamespace(monotonic=lambda: now, sleep=sleep),
        "collect": lambda: {"using": now >= 2},
        "send_report": send,
        "performance": performance,
    }.items():
        monkeypatch.setitem(run.__globals__, key, value)
    with pytest.raises(StopIteration):
        run(None, 30)
    assert sent == [
        (0, {"using": False, "cpu_percent": 21.0}),
        (2, {"using": True, "cpu_percent": 21.0}),
        (32, {"using": True, "cpu_percent": 36.0}),
    ]
    assert performance.call_count == 2


@pytest.mark.parametrize("playback", ["PLAYING", "PAUSED", "STOPPED"])
async def test_smtc_collects_current_session(playback):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    current_track = run_path(str(CLIENTS / "windows/report.py"))["current_track"]

    manager = Mock()
    manager.get_current_session.return_value = None
    assert await current_track(manager) == {"playback": "idle"}
    session = Mock()
    manager.get_current_session.return_value = session
    session.get_playback_info.return_value.playback_status.name = playback
    session.try_get_media_properties_async = AsyncMock(
        return_value=SimpleNamespace(title=" 曲名 ", artist="歌手", album_title="专辑")
    )
    result = await current_track(manager)
    if playback == "STOPPED":
        assert result == {"playback": "idle"}
        session.try_get_media_properties_async.assert_not_called()
    else:
        assert result == {
            "playback": playback.lower(),
            "title": "曲名",
            "artist": "歌手",
            "album": "专辑",
        }
        session.try_get_media_properties_async.return_value = None
        assert await current_track(manager) == {"playback": "idle"}
