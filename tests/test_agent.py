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
                assert set(received[-1][2]) == {"using", "status", "battery_percent", "charging"}
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
