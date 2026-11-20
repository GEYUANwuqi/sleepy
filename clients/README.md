# 设备上报

在要上报状态的手机或电脑上运行对应脚本即可。Windows 和 Linux 的基础上报使用 Python 标准库，Android 使用 AutoX.js。

## 填写站点地址和设备 Key

1. 登录 Sleepy 管理后台，在「设备与状态」创建设备，复制生成的设备 Key。每台设备使用自己的 Key。
2. 下载对应平台的脚本，编辑开头的 `SITE_URL` 和 `DEVICE_KEY`。`SITE_URL` 填设备能访问的站点根地址，例如 `https://sleepy.example.com`，脚本会自动补上接口路径。如果站点部署在另一台机器上，请填那台机器的域名或局域网地址。
3. 基础字段包括使用状态和可读取的电池信息。需要上报前台应用名称时，开启脚本中的 `COLLECT_APP`，并在后台「编辑设备」中允许接收 `status` 字段。

```python
SITE_URL = "https://sleepy.example.com"
DEVICE_KEY = "这里填该设备的 Key"
COLLECT_APP = False
```

## 运行脚本

| 平台与脚本 | 第一次上报 | 持续上报 |
| --- | --- | --- |
| [Windows · report.py](windows/report.py) | 在脚本目录打开 PowerShell，执行 `py -3.12 report.py report` | `py -3.12 report.py` |
| [Linux · report.py](linux/report.py) | 在脚本目录执行 `python3 report.py report` | `python3 report.py` |
| [Android · report.js](android/report.js) | 导入 AutoX.js v6，填写配置后点运行 | 保持脚本运行 |

Windows 和 Linux 单次上报成功后，终端会显示「上报成功」。三个平台都可以在后台「设备与状态」查看对应设备卡片上的「上报于」时间。持续运行时，状态变化会触发上报，状态未变时默认每 30 秒上报一次。

收到 `401` 时，核对脚本中的设备 Key，重置过 Key 的设备需要填写新值。收到 `403` 时，检查该设备是否已启用。如果「上报于」时间已更新，但首页缺少某个字段，在「编辑设备」中检查该字段是否已启用、接收并公开。

## Android · AutoX.js

使用支持 Rhino 脚本的 AutoX.js v6 导入并运行 `report.js`。默认上报屏幕是否点亮且已解锁、电量和充电状态。若启用了 `COLLECT_APP`，还需为 AutoX.js 开启无障碍服务。[AutoX.js 下载](https://github.com/aiselp/AutoX/releases)

可在 AutoX.js 的运行管理中停止脚本。锁屏后经常掉线时，检查系统对 AutoX.js 的后台运行和电池限制。

## Linux · systemd 用户服务

要让 systemd 接管上报，将[用户服务文件](linux/sleepy-agent.service)下载到同一目录，然后执行以下命令：

```bash
client_dir="$HOME/.local/share/sleepy-client"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$client_dir" "$unit_dir"
install -m 600 report.py "$client_dir/report.py"
install -m 644 sleepy-agent.service "$unit_dir/sleepy-agent.service"
systemctl --user daemon-reload
systemctl --user enable --now sleepy-agent.service
systemctl --user status sleepy-agent.service
```

修改服务正在使用的脚本后，执行 `systemctl --user restart sleepy-agent.service` 重新加载。

使用 `journalctl --user -u sleepy-agent.service -f` 查看日志，使用 `systemctl --user disable --now sleepy-agent.service` 停止并取消自启。若无桌面服务器需要在用户登录前启动，可执行 `loginctl enable-linger "$USER"`（可能需要管理员授权）。

X11 桌面采集依赖 X11/Xss 库，获取应用名称另需 `xprop`。如果前台运行能识别桌面，但服务识别不到，在图形会话的终端执行 `systemctl --user import-environment DISPLAY XAUTHORITY XDG_SESSION_TYPE`，再执行 `systemctl --user restart sleepy-agent.service`。Wayland 或无桌面环境仍可上报，无法检测的活动字段为 `null`。

## Windows · 静默自启

Windows 客户端每 30 秒读取一次性能数据。用运行脚本的解释器执行 `py -3.12 -m pip install psutil` 可采集 CPU 和内存使用率。系统能运行 `nvidia-smi` 时还会采集 GPU 使用率，多卡取最高值。无法读取的指标为 `null`。前台应用或电源状态变化时会立即上报并复用最近的性能读数。

Windows 将插电或电池正在充电统一上报为 `charging: true`，插电满电时也显示「充电中」。在后台为设备启用、接收并公开预设的「充电状态」，即可在首页显示。

要静默自启，将[自启脚本](windows/install-startup.ps1)下载到同一目录，执行以下命令一次。计划任务会立即启动，以后在当前用户登录时通过同一解释器目录下的 `pythonw.exe` 在后台运行。移动脚本或 Python 安装目录后需重新注册。

```powershell
.\install-startup.ps1 -Python (py -3.12 -c "import sys; print(sys.executable)")
Get-ScheduledTask -TaskName "Sleepy Agent - $env:USERNAME"
```

停止并移除自启：

```powershell
Stop-ScheduledTask -TaskName "Sleepy Agent - $env:USERNAME"
Unregister-ScheduledTask -TaskName "Sleepy Agent - $env:USERNAME" -Confirm:$false
```

### Windows 音乐采集

在「设备与状态」打开「启用SMTC」，将音乐专属 Key 填入 Windows 脚本顶部的 `SMTC_KEY`。音乐模式读取 `SITE_URL` 和 `SMTC_KEY`，可与普通设备上报同时运行。在播放音乐的 Windows 10 1809 或更新系统中执行：

```powershell
py -3.12 -m pip install winrt-windows-media-control
py -3.12 report.py smtc
```

## 手动上报与调试

两个 Python 脚本均支持 `report` 和 `run`。例如，在 Linux 终端执行以下命令可发送一份指定内容的快照：

```bash
python3 report.py report --json '{"using":true,"status":"工作中"}'
```

用 `run` 在前台检查持续上报的情况，按 Ctrl+C 结束。要从自己的脚本直接发送请求，见[设备上报 API](../docs/api.md#设备上报)。
