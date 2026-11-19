# 开发验证说明

所有服务端测试使用临时 SQLite 和测试专用凭据；浏览器测试启动独立站点。
不要将真实设备 Key 或个人数据放入测试夹具。运行测试不需要公网部署。

## Python

```bash
uv sync --locked
uv run ruff check src tests clients frontend/tests
uv run pytest -q
```

覆盖以下行为：

- 首次初始化、强制改密、固定 8 小时会话、CSRF 与来源校验、会话撤销。
- Key 绑定、重置/停用/删除、扁平上报、严格类型、未知字段丢弃、缺省值覆盖。
- 单设备隐藏、字段隐私、公开版本不因非公开活动改变、总体状态与手动覆盖。
- 统计入口分类、内部流量排除、重复批次去重、SQLite 持久化。
- HTTPX 连接池复用时 Cookie 隔离、代理头与逐跳头处理。
- 实际 CLI 启动、恰好两个服务子进程、SSE 及时传输、后端崩溃恢复、重启预算耗尽、端口占用、父进程消失、正常退出回收。
- 内置 HTTPS 与 Secure Cookie；此项使用 OpenSSL 生成临时证书，无 OpenSSL 时会明确跳过。
- 本机密码重置、运行锁、数据及设备 Key 保留，独立脚本在禁用 site-packages 后的 HTTP 上报。

## 浏览器

```bash
cd frontend
npm ci
npm run build
npx playwright install chromium
npm run test:e2e
```

测试使用 `127.0.0.1:18765`，该端口需空闲。若要使用已有 Chromium，可设置仅供测试的
`SLEEPY_CHROMIUM=/absolute/path/to/chrome`，它不是应用部署配置。

真实浏览器覆盖首次改密、设备创建、HTTP 上报、首页实时更新、设备编辑与隐藏、
访客权限隔离、站点设置保存、统计页面与退出登录，并检查移动视口无横向溢出。
截图保存在 `frontend/test-results/`，不提交到仓库。

## 构建与平台边界

```bash
uv run --locked sleepy --help
node --check clients/android/report.js
```

仓库中的 `src/sleepy/static/` 包含预构建页面及前端依赖许可证，克隆源码后可直接启动服务。

Android 的脚本语法检查可在 Node.js 下执行。屏幕与电池采集、前台应用权限及锁屏后的持续上报需要在AutoX.js上运行。

Windows 工作流检查 Python 客户端入口。静默计划任务、登录后的桌面状态和 SMTC 音乐采集需要在 Windows 桌面会话中验证。

Linux X11 自动采集需要系统 X11/Xss 库；应用名称采集还需 `xprop`。
Wayland 或无桌面环境仍可主动上报 JSON，自动采集不到的值为 `null`。
