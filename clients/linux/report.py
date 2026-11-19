"""Standalone Linux reporter. Fill in the settings below, then run this file."""

import argparse
import ctypes
import ctypes.util
import json
import os
import re
import shutil
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


def linux_activity(collect_app):
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" or not os.environ.get("DISPLAY"):
        return None, None
    using, status = None, None
    x11_name, xss_name = ctypes.util.find_library("X11"), ctypes.util.find_library("Xss")
    if x11_name and xss_name:

        class ScreenSaverInfo(ctypes.Structure):
            _fields_ = [
                ("window", ctypes.c_ulong),
                ("state", ctypes.c_int),
                ("kind", ctypes.c_int),
                ("til_or_since", ctypes.c_ulong),
                ("idle", ctypes.c_ulong),
                ("eventMask", ctypes.c_ulong),
            ]

        x11, xss = ctypes.CDLL(x11_name), ctypes.CDLL(xss_name)
        x11.XOpenDisplay.argtypes, x11.XOpenDisplay.restype = [ctypes.c_char_p], ctypes.c_void_p
        x11.XDefaultRootWindow.argtypes, x11.XDefaultRootWindow.restype = (
            [ctypes.c_void_p],
            ctypes.c_ulong,
        )
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xss.XScreenSaverQueryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(ScreenSaverInfo),
        ]
        display = x11.XOpenDisplay(None)
        if display:
            try:
                info = ScreenSaverInfo()
                if xss.XScreenSaverQueryInfo(
                    display, x11.XDefaultRootWindow(display), ctypes.byref(info)
                ):
                    using = info.idle < 300000 and info.state != 1
            finally:
                x11.XCloseDisplay(display)
    if collect_app and using and shutil.which("xprop"):
        try:
            result = subprocess.check_output(
                ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=1,
            )
            window = re.search(r"0x[\da-fA-F]+", result)
            if window:
                result = subprocess.check_output(
                    ["xprop", "-id", window[0], "_NET_WM_PID"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=1,
                )
                pid = re.search(r"=\s*(\d+)", result)
                if pid:
                    status = Path(os.readlink(f"/proc/{int(pid[1])}/exe")).name
        except (subprocess.SubprocessError, OSError):
            pass
    return using, status


def battery_status():
    for path in sorted(Path("/sys/class/power_supply").glob("*")):
        try:
            if (path / "type").read_text().strip() != "Battery":
                continue
        except OSError:
            continue
        try:
            percent = int((path / "capacity").read_text().strip())
        except (ValueError, OSError):
            percent = None
        try:
            state = (path / "status").read_text().strip()
        except OSError:
            state = ""
        charging = (
            state == "Charging"
            if state in ("Charging", "Discharging", "Full", "Not charging")
            else None
        )
        return percent if percent is not None and 0 <= percent <= 100 else None, charging
    return None, None


def collect():
    percent, charging = battery_status()
    try:
        using, status = linux_activity(COLLECT_APP)
    except (OSError, AttributeError):
        using, status = None, None
    return {"using": using, "status": status, "battery_percent": percent, "charging": charging}


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
    while True:
        payload = collect()
        if payload != previous or time.monotonic() - last >= interval:
            try:
                send_report(url, "/api/device/report", DEVICE_KEY, payload)
            except (OSError, HTTPException) as exc:
                print(f"上报失败（{type(exc).__name__}），{delay} 秒后重试。", file=sys.stderr)
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            previous, last, delay = payload, time.monotonic(), 2
        time.sleep(2)


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
    args = parser.parse_args()
    try:
        url = endpoint(DEVICE_KEY)
        if args.command == "report":
            payload = json.loads(args.json) if args.json is not None else collect()
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
