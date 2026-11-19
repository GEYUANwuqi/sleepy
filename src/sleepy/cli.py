"""Public CLI: start the application or recover the local administrator."""

import logging
import os
import sys
from pathlib import Path

import click
from filelock import FileLock, Timeout
from pydantic import ValidationError

from . import __version__
from .security import HASHER, random_password
from .storage import Store
from .supervisor import Supervisor
from .web import STATIC_DIR


def data_option(function):
    return click.option(
        "--data-dir",
        type=click.Path(path_type=Path),
        default=Path("data"),
        show_default="./data，位于启动时的工作目录",
        help="数据库和运行锁目录",
    )(function)


@click.group()
@click.version_option(__version__)
def main():
    """Sleepy 站点服务管理。"""


@main.command()
@data_option
@click.option("--host", help="临时覆盖公开监听地址")
@click.option("--port", type=click.IntRange(1, 65535), help="临时覆盖公开端口")
@click.option("--ssl-certfile", type=click.Path(exists=True, dir_okay=False))
@click.option("--ssl-keyfile", type=click.Path(exists=True, dir_okay=False))
def run(data_dir, host, port, ssl_certfile, ssl_keyfile):
    """初始化并启动 Web 和 FastAPI 两个服务子进程。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not (STATIC_DIR / "index.html").is_file():
        raise click.ClickException(
            "缺少前端构建产物。源码开发请先执行 cd frontend && npm ci && npm run build。"
        )
    if os.name != "nt":
        os.umask(0o077)
    data_dir = data_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = FileLock(data_dir / "sleepy.lock", timeout=0)
    try:
        with lock:
            store = Store(data_dir)
            try:
                password = store.initialize()
                settings = store.settings()
            finally:
                store.close()
            click.echo(f"数据目录：{data_dir}")
            if password:
                click.echo(
                    f"首次登录临时密码：{password}\n首次登录必须修改密码；丢失后使用 sleepy reset pwd。"
                )
            overrides = {
                k: v
                for k, v in {
                    "host": host,
                    "port": port,
                    "ssl_certfile": ssl_certfile,
                    "ssl_keyfile": ssl_keyfile,
                }.items()
                if v is not None
            }
            values = settings.model_dump() | overrides
            validated = type(settings).model_validate(values)
            for path in (validated.ssl_certfile, validated.ssl_keyfile):
                if path and not Path(path).is_file():
                    raise click.ClickException(f"TLS 文件不存在：{path}")
            runtime = {
                k: getattr(validated, k)
                for k in ("host", "port", "ssl_certfile", "ssl_keyfile", "trusted_proxies")
            }
            runtime["overrides"] = list(overrides)

            def ready(url):
                click.echo(f"Sleepy 已就绪\n公开页面：{url}/\n管理页面：{url}/admin")

            Supervisor(data_dir, runtime).run(ready)
    except Timeout:
        raise click.ClickException("此数据目录已有运行中的 Sleepy 实例。") from None
    except (OSError, RuntimeError, ValidationError) as exc:
        raise click.ClickException(str(exc)) from None


@main.group()
def reset():
    """停服后执行的本机恢复操作。"""


@reset.command()
@data_option
def pwd(data_dir):
    """重置管理员密码并撤销全部管理会话，不修改设备 Key。"""
    data_dir = data_dir.expanduser().resolve()
    if not (data_dir / "sleepy.sqlite3").is_file():
        raise click.ClickException("数据目录不存在或数据库尚未初始化，请检查 --data-dir。")
    try:
        with FileLock(data_dir / "sleepy.lock", timeout=0):
            store = Store(data_dir)
            try:
                if not store.admin():
                    raise click.ClickException("管理员尚未初始化。")
                password = random_password()
                store.change_password(HASHER.hash(password), must_change=True)
            finally:
                store.close()
            click.echo(f"管理员密码已重置：{password}\n全部管理会话已撤销，下次登录必须修改密码。")
    except Timeout:
        raise click.ClickException("请先停止使用此数据目录的 Sleepy 实例，再重置密码。") from None


def run_cli():
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8")
    main()


if __name__ == "__main__":
    run_cli()
