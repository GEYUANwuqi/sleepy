# Sleepy

Sleepy 用于自托管个人状态展示。首页显示总体状态、设备近况，也可以显示 Windows 正在播放的音乐。项目灵感来自 [sleepy-project/sleepy](https://github.com/sleepy-project/sleepy)。

## 部署

克隆仓库后，可以用 Docker Compose 部署，也可以直接用 Python 运行。

```bash
git clone --branch main https://github.com/GEYUANwuqi/sleepy.git
cd sleepy
```

### Docker Compose（推荐用于服务器）

安装带 Compose 插件的 Docker，在仓库根目录执行以下命令。镜像在本地构建，数据库保存在宿主机的 `./data/`。如要接管已有数据，先停止使用该目录的源码实例。

```bash
mkdir -p data
export SLEEPY_UID="$(id -u)" SLEEPY_GID="$(id -g)"
docker compose up -d --build
docker compose logs sleepy
```

首次启动的临时密码会出现在容器日志中。默认入口为 `http://127.0.0.1:8080`，适合在服务器上接入反向代理。局域网访问、更新和密码重置见 [Docker 部署说明](docs/guide.md#docker-compose-运维)。

### Python 与 venv

从源码运行需要 Python 3.12 或更高版本。

Linux 或 macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m sleepy.cli run
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m sleepy.cli run
```

已有 Python 环境时，直接在该环境中执行 `python -m pip install .` 和 `python -m sleepy.cli run` 即可。

### uv

```bash
uv sync --locked --no-dev
uv run --locked --no-dev sleepy run
```

## 首次使用

使用终端输出的临时密码登录[管理后台](http://127.0.0.1:8080/admin)并修改密码。在「设备与状态」创建设备，将站点地址和生成的设备 Key 填入对应平台的上报脚本，在设备上运行即可。设备上报后，可在[本机首页](http://127.0.0.1:8080/)查看状态。站点设置和设备数据默认保存在启动目录下的 `data/sleepy.sqlite3`。

## 文档

- [使用指南](docs/guide.md) · 首页外观、设备与字段、总体状态、音乐、监听地址和密码恢复。
- [客户端接入](clients/README.md) · Android AutoX.js 脚本、Linux systemd 服务、Windows 静默自启与音乐采集。
- [HTTP API](docs/api.md) · 状态查询、实时事件、设备上报，以及使用 API Token 修改设置和切换状态。

## 许可证

[MIT 许可证](LICENSE)
