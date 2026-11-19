# HTTP API

下列路径均相对于站点根地址。带请求体的示例使用 JSON。设备 Key、SMTC Key 和后台 API Token 各有独立用途，均通过 `Authorization: Bearer <凭据>` 发送。

## 查询状态与订阅更新

| 方法与路径 | 返回内容 |
| --- | --- |
| `GET /api/status` | JSON 快照，包含 `site`、`presence`、`devices`、`smtc` 和 `version` |
| `GET /api/status/events` | SSE 事件流。连接时和公开状态变化时均发送名为 `update` 的完整快照 |

这两个接口均可匿名调用，遵循相同的设备和字段可见性设置。使用 `curl -N http://127.0.0.1:8080/api/status/events` 可以观察实时更新。

设备超过离线阈值后，快照中该设备的 `online` 为 `"offline"`，动态字段 `state` 为空对象。收到下一次上报后，`state` 会包含本次可见的读数。

## 设备上报

在后台创建设备后，将其 Key 保存到环境变量 `SLEEPY_DEVICE_KEY`。每次请求提交该设备的一份完整动态快照，成功返回 `204`。

```bash
curl --fail-with-body -X POST http://127.0.0.1:8080/api/device/report \
  -H "Authorization: Bearer ${SLEEPY_DEVICE_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"using":true,"status":"工作中"}'
```

| 字段 | 接收的值 |
| --- | --- |
| `using` | 布尔值，表示设备是否正在使用 |
| `status` | 活动描述，最多 512 个字符 |
| `battery_percent`、`charging` | 电量为 0–100 的整数，充电状态为布尔值 |
| `cpu_percent`、`gpu_percent`、`memory_percent` | 使用率为 0–100 的数值 |
| 设备自定义字段 | 使用后台定义的字段标识与类型，直接放在 JSON 顶层 |

服务端通过 Key 识别设备，并记录接收时间。只有该设备配置为接收的字段会入库；未知字段会忽略，省略的接收字段会恢复为 `null`。Key 无效返回 `401`，设备停用返回 `403`，接收字段类型或范围有误返回 `422`。

## 音乐上报

开启 SMTC 后，将专属 Key 保存到 `SLEEPY_SMTC_KEY`。发送播放或暂停状态时必须带上曲名，歌手和专辑可选。

```bash
curl --fail-with-body -X POST http://127.0.0.1:8080/api/smtc/report \
  -H "Authorization: Bearer ${SLEEPY_SMTC_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"playback":"playing","title":"曲名","artist":"歌手","album":"专辑"}'
```

暂停时使用 `paused`，停止播放时发送 `{"playback":"idle"}` 清空曲目信息。成功返回 `204`；SMTC 已关闭或 Key 无效时返回 `401`。

## 使用 API Token 修改设置和状态

在「站点与安全 → API Token」中填写名称并创建 Token，复制到环境变量 `SLEEPY_API_TOKEN`。完整值仅在创建时显示。Token 可独立于登录会话使用，撤销后立即失效。

```bash
curl --fail-with-body -X PATCH http://127.0.0.1:8080/api/admin/settings \
  -H "Authorization: Bearer ${SLEEPY_API_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"title":"我的状态页","display":{"footer":"欢迎来访"}}'
```

| 方法 | `/api/admin/settings` 的行为 |
| --- | --- |
| `GET` | 返回 `settings`（已保存的设置）和 `runtime`（当前运行参数） |
| `PATCH` | 只更新指定字段。嵌套对象按键合并，数组整体替换 |
| `PUT` | 替换完整配置。请求体使用 `GET` 返回的 `settings` 对象 |

写入成功返回 `204`。API Token 可读写站点设置并切换当前状态，调用时无需 Cookie 或 CSRF 令牌。

### 切换当前状态

向 `PUT /api/admin/status` 提交状态标识。例如，让首页显示「睡觉中」8 小时：

```bash
curl --fail-with-body -X PUT http://127.0.0.1:8080/api/admin/status \
  -H "Authorization: Bearer ${SLEEPY_API_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"status":"sleeping","duration":28800}'
```

`duration` 以秒为单位，省略时为 1 小时，设为 `null` 则持续到手动解除。发送 `{"status":null}` 可立即恢复按设备自动判断。成功返回 `204`，未知标识返回 `422`。

### 改名与添加状态

通过 `PATCH /api/admin/settings` 更新 `labels`。例如，以下请求将 `reachable` 的显示名改为「活着」，同时创建「看书中」。标识始终用于 API 调用，名称用于首页展示。

```bash
curl --fail-with-body -X PATCH http://127.0.0.1:8080/api/admin/settings \
  -H "Authorization: Bearer ${SLEEPY_API_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"labels":{"reachable":{"name":"活着"},"reading":{"name":"看书中","description":"翻到下一页再说","color":"#5588b1"}}}'
```

随后向状态接口发送 `{"status":"reading"}` 即可显示新状态。

## 后台会话接口

以下接口供管理后台使用。先请求 `GET /api/admin/auth` 获取 Cookie 与 `csrf`，再向 `POST /api/admin/login` 提交 `{"password":"…"}`。登录及后续会话写请求都须附带当前 Cookie、与站点同源的 `Origin` 和 `X-CSRF-Token`。登录成功后使用返回的新 Cookie 和 `csrf`。管理会话有效期为 8 小时。

| 用途 | 方法与路径 |
| --- | --- |
| 登录状态、退出、改密 | `GET /api/admin/auth`、`POST /api/admin/logout`、`POST /api/admin/password` |
| 设备快照、管理事件流 | `GET /api/admin/devices`、`GET /api/admin/events` |
| 创建、修改、删除设备 | `POST /api/admin/devices`、`PUT /api/admin/devices/{id}`、`DELETE /api/admin/devices/{id}` |
| 重置设备 Key | `POST /api/admin/devices/{id}/key` |
| 设置手动状态 | `PUT /api/admin/status`，如 `{"status":"busy","duration":3600}`，传 `{"status":null}` 恢复自动判断 |
| 启停 SMTC、重置音乐 Key | `PUT /api/admin/smtc`（如 `{"enabled":true}`）、`POST /api/admin/smtc/key` |
| 列出、创建、撤销设置 Token | `GET /api/admin/tokens`、`POST /api/admin/tokens`（如 `{"name":"首页脚本"}`）、`DELETE /api/admin/tokens/{id}` |
| 访问统计 | `GET /api/admin/statistics` |
