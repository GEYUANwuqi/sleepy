"""Standalone Windows reporter. Fill in the settings below, then run this file."""

import argparse
import asyncio
import ctypes
import json
import subprocess
import sys
import time
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from pathlib import Path
from urllib.parse import urlsplit

# 填写本站根地址和后台为这台设备生成的 Key。
SITE_URL = ""
DEVICE_KEY = ""
COLLECT_APP = False
SMTC_KEY = ""  # 仅 smtc 模式用到，填写后台生成的音乐 Key。


def windows_activity(collect_app):
    from ctypes import wintypes

    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    user = ctypes.windll.user32
    user.OpenInputDesktop.restype = wintypes.HANDLE
    user.SwitchDesktop.argtypes = [wintypes.HANDLE]
    user.CloseDesktop.argtypes = [wintypes.HANDLE]
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    info = LastInput(ctypes.sizeof(LastInput), 0)
    using = None
    if user.GetLastInputInfo(ctypes.byref(info)):
        idle_ms = (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
        using = idle_ms < 300000
    desktop = user.OpenInputDesktop(0, False, 0x0100)
    if desktop:
        try:
            if not user.SwitchDesktop(desktop):
                using = False
        finally:
            user.CloseDesktop(desktop)
    else:
        using = False
    status = None
    if collect_app and using:
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(pid))
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                size = wintypes.DWORD(32768)
                path = ctypes.create_unicode_buffer(size.value)
                if kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                    status = Path(path.value).name
            finally:
                kernel.CloseHandle(handle)
    return using, status


def battery_status():
    from ctypes import wintypes

    class PowerStatus(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_ubyte),
            ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte),
            ("SystemStatusFlag", ctypes.c_ubyte),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    power = PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(power)):
        return None, None
    flags = power.BatteryFlag
    percent = (
        power.BatteryLifePercent
        if flags not in (128, 255) and power.BatteryLifePercent <= 100
        else None
    )
    if power.ACLineStatus == 1 or (flags != 255 and flags & 8):
        return percent, True
    if power.ACLineStatus == 0 and flags != 255:
        return percent, False
    return percent, None


def collect():
    percent, charging = battery_status()
    try:
        using, status = windows_activity(COLLECT_APP)
    except (OSError, AttributeError):
        using, status = None, None
    return {
        "using": using,
        "status": status,
        "battery_percent": percent,
        "charging": charging,
    }


def performance():
    cpu = memory = gpu = None
    try:
        import psutil
    except ImportError:
        pass
    else:
        try:
            cpu = psutil.cpu_percent(interval=0.2)
            memory = psutil.virtual_memory().percent
        except (OSError, psutil.Error):
            pass
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            timeout=2,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        values = [int(line) for line in output.splitlines() if line.strip().isdigit()]
        gpu = max((value for value in values if 0 <= value <= 100), default=None)
    except (OSError, subprocess.SubprocessError):
        pass
    return {"cpu_percent": cpu, "memory_percent": memory, "gpu_percent": gpu}


def endpoint(key):
    url = urlsplit(SITE_URL.strip())
    if (
        url.scheme not in ("http", "https")
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.path not in ("", "/")
        or url.query
        or url.fragment
    ):
        raise ValueError("请在脚本顶部填写站点根地址 SITE_URL，例如 https://sleepy.example.com")
    if not key or not key.isascii() or not all(33 <= ord(char) <= 126 for char in key):
        raise ValueError("请在脚本顶部填写当前上报模式对应的 Key。")
    # Validate the port before starting the retry loop.
    _ = url.port
    return url


def send_report(url, path, key, payload):
    connection_type = HTTPSConnection if url.scheme == "https" else HTTPConnection
    connection = connection_type(url.hostname, port=url.port, timeout=10)
    try:
        connection.request(
            "POST",
            path,
            json.dumps(payload).encode(),
            {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        )
        status = connection.getresponse().status
    finally:
        connection.close()
    if status == 204:
        return
    if status in (401, 403, 422):
        raise SystemExit(f"上报被拒绝（HTTP {status}），请检查 Key、启用状态和字段。")
    raise OSError(f"上报失败（HTTP {status}）")


def run(url, interval):
    previous, last, delay = None, 0, 2
    readings, next_sample = {}, 0
    while True:
        payload = collect()
        if time.monotonic() >= next_sample:
            readings = performance()
            next_sample = time.monotonic() + 30
        if payload != previous or time.monotonic() - last >= interval:
            try:
                send_report(url, "/api/device/report", DEVICE_KEY, payload | readings)
            except (OSError, HTTPException) as exc:
                print(f"上报失败（{type(exc).__name__}），{delay} 秒后重试。", file=sys.stderr)
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            previous, last, delay = payload, time.monotonic(), 2
        time.sleep(2)


async def current_track(manager):
    session = manager.get_current_session()
    if session is None:
        return {"playback": "idle"}
    info = session.get_playback_info()
    if info is None:
        return {"playback": "idle"}
    playback = info.playback_status.name.lower()
    if playback not in ("playing", "paused"):
        return {"playback": "idle"}
    media = await session.try_get_media_properties_async()
    if media is None or not (media.title or "").strip():
        return {"playback": "idle"}
    return {
        "playback": playback,
        "title": media.title.strip()[:512],
        "artist": (media.artist or "").strip()[:512],
        "album": (media.album_title or "").strip()[:512],
    }


async def stream_music(url):
    try:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager
    except ImportError:
        raise SystemExit("SMTC 需要安装 winrt-windows-media-control。") from None
    manager, previous, last, delay = None, None, 0, 2
    while True:
        try:
            if manager is None:
                manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
            track = await current_track(manager)
            if track != previous or time.monotonic() - last >= 30:
                send_report(url, "/api/smtc/report", SMTC_KEY, track)
                previous, last = track, time.monotonic()
            delay = 2
        except (OSError, HTTPException) as exc:
            manager = None
            print(f"SMTC 上报失败（{type(exc).__name__}），{delay} 秒后重试。", file=sys.stderr)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        await asyncio.sleep(2)


def main():
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    periodic = commands.add_parser("run", help="持续采集并上报（默认）")
    periodic.add_argument("--interval", type=int, default=30, help="定期上报间隔，单位秒")
    once = commands.add_parser("report", help="上报一次后退出")
    once.add_argument("--json", help="手动指定 JSON 对象，省略则采集当前状态")
    commands.add_parser("smtc", help="使用音乐 Key 上报 Windows 系统媒体会话")
    args = parser.parse_args()
    try:
        if args.command == "smtc":
            asyncio.run(stream_music(endpoint(SMTC_KEY)))
            return
        url = endpoint(DEVICE_KEY)
        if args.command == "report":
            payload = json.loads(args.json) if args.json is not None else collect() | performance()
            if not isinstance(payload, dict):
                raise ValueError("上报内容必须是 JSON 对象。")
            send_report(url, "/api/device/report", DEVICE_KEY, payload)
            print("上报成功。")
        else:
            interval = getattr(args, "interval", 30)
            if not 5 <= interval <= 3600:
                parser.error("--interval 必须在 5 到 3600 秒之间")
            run(url, interval)
    except ValueError as exc:
        parser.error(str(exc))
    except (OSError, HTTPException) as exc:
        raise SystemExit(f"无法完成上报（{type(exc).__name__}），请检查站点地址和网络。") from None
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
