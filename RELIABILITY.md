# 连接恢复、1033 与地址轮换

源码传输修订：`1033-repair-r1`。**源码已更新不等于运行进程已更新**；完整关闭并重新打开 GUI 后，实际 `/health` 必须包含这个 `transport_revision` 才算加载。仅点击“修复连接”不会重新加载 Python 类。

## 先区分三个环节

1. **本地 MCP**：Windows 上的 HTTP 监听器和工具服务。
2. **cloudflared → Cloudflare**：连接器的出站连接；当前命令已使用 `--protocol http2`。
3. **客户端 → 公网 MCP**：当前主机名、秘密路径、Cloudflare 路由以及实际 JSON-RPC 响应。

- HTTP **530 不一律等于 1033**。本修订只在 530 响应体包含 1033 时归类为 `cloudflare_1033`。
- Cloudflare **Error 1033**：边缘无法找到健康的连接器。不能据此直接断定本地 Python 服务崩溃。
- **502** 常见于连接器无法访问其配置的本地源站；应查监听端口、协议及源站，不应把反复换隧道当作万能修复。
- **TLS EOF** 是探测路径的传输错误，不等于 1033，也不证明其他客户端连不上。
- `/ready=200` 仅证明该 cloudflared 有到边缘的活动连接，**不证明端到端 MCP 可用**。

## 本次修订的恢复策略

| 证据 | 行为 |
|---|---|
| 只分配到域名 | 不宣称公网成功；等待实际 initialize 验证 |
| 公网 initialize 返回正确 JSON-RPC、请求 ID 和 `openbridge-mcp` | `RUNNING_ONLINE`，允许复制当前地址 |
| 自检失败，但最近 45 秒有经鉴权、匹配当前域名的公网 MCP 请求 | `DEGRADED`；保留该入口，不因本机自检失败而重建 |
| 自检是 EOF/DNS/TLS 等探测路径故障，但连接器 `/ready=200` | 已验证入口保持 `DEGRADED`，不因这种矛盾证据盲目轮换；未验证过的地址不发布为成功 |
| 明确 530/1033，连续至少 3 次且自第一条连续证据起至少 45 秒，无新的有效公网流量 | 请求重建；本地工具调用不能无限压住这个判断；单独的 `/ready=200` 也不能否定持续 1033 |
| 普通、无法验证的故障，没有公网流量或健康连接器的反证 | 保留约 120 秒的恢复策略及 180 秒上限；生产判断不再使用不分来源的 `last_client_activity` |
| cloudflared 退出或启动失败 | 监督线程重试；退避 1、2、4、8、16、30 秒，上限 30 秒 |
| 监督逻辑意外抛错 | 记录脱敏原因、回收自己持有的子进程后进入退避；不能静默结束监督 |
| 用户主动停止或关闭 GUI | 取消恢复，关闭自己管理的服务；不会自动开启 Computer Use |

“公网活动”仅是健康判断证据，**不是新的鉴权途径**。POST 先经过原有秘密路径鉴权，再检查当前 Host、HTTPS 转发信息、Cf-Ray、有效 CF-Connecting-IP。普通 loopback 请求以及 `bridge-health` / `openbridge-health` 自检均不计入。持有凭据的调用者已有工作区权限；这些请求头不作为对不可信客户端的新授权边界。

正常约 10 秒检查一次，异常约 5 秒；还要加每次探测耗时。45/120/180 秒是策略阈值，**不是精确恢复 SLA**，更不能保证重建后的公网一定成功。

### 有界探测

- 公网 initialize 在独立 Python 子进程中执行，socket 等待上限 8 秒，父进程总预算 12 秒，覆盖 DNS/TLS/响应读取卡顿。
- 超预算时只终止并回收探测子进程，不把健康检查超时当作立即杀死 cloudflared 的理由。
- 秘密 URL 经 stdin 传给子进程，不出现在其命令行参数；错误信息脱敏。
- 保留 TLS 证书和主机名验证；不跟随重定向。
- 解析 JSON 与 SSE initialize 响应，拒绝错误请求 ID、错误服务和非 200 响应。
- 仅从当前 cloudflared 的日志解析 loopback metrics 地址并读取 `/ready`。默认端口不硬编码为唯一实例；HTTP readiness 超时为 2 秒。
- 本机 TUN/VPN 仍可能影响请求。关闭显式 HTTP 代理并不等于绕过 TUN。此修订没有全局修改代理、防火墙、DNS 或证书检查。

## 地址文件的边界：它不能救活已经离线的客户端

服务把本机当前地址/状态原子写入工作区根目录 `current_mcp_url.json`，秘密 URL 仍等同于凭据，**禁止公开或提交版本库**。

- 本地启动：记录 `RUNNING_LOCAL` 及本机地址。
- 公网通过验证：发布当前公网地址。
- 状态变化但地址不变：也更新落盘状态。
- `tunnel_generation`：统计本进程内发布的不同非空公网 origin；同一地址撤销后重新验证不再虚增。**它不是全生命周期的 cloudflared 启动次数**。
- 秘密路径重置：同步新路径；停止：地址清空。
- 原子替换失败只记日志，不让磁盘问题杀死隧道。
- `updated_at` 是地址/状态发布时刻，不是每一次健康探测的心跳。日志的“验证通过”通常也只在状态/地址转换时出现，安静一段时间本身不能证明线程卡死。

**本机程序或已有正常入口的客户端可以读这个文件；拿着失效 Quick Tunnel URL 的远端客户端，无法通过同一个失效入口读它。** 旧随机域名不会重定向到新域名。

发生旧地址 1033 时：只做少量只读核验，停止对同一旧地址持续轮询。请本机用户核对 GUI 最新“公网已验证”状态，再重新分享秘密地址；或使用预先配置的稳定入口。

## 根治地址漂移的架构方案

Quick Tunnel 是随机主机名的开发/测试服务，不承诺 SLA。应用补丁可以减少误重建、改善故障识别和恢复，但不能让旧随机域名复活，也无法让离线客户端自动获知新域名。

长期使用建议采用 **Cloudflare 受管理隧道 + 用户控制的固定主机名**。这需要用户自己的账户/域名配置，不属于本补丁已完成的工作；没有代购、登录账户或发布秘密 URL。需要按 Cloudflare 官方指引配置固定入口，同时保留原有 MCP 秘密路径和本机 GUI 权限开关。固定主机名减少地址变化，但仍不能承诺云服务永不故障。

## /health 与权限

`/health` 无鉴权，仅提供非秘密诊断：本地服务状态、工具数、公网 origin、状态、连接器 readiness、generation 和 `transport_revision`。**不包含 `/mcp/<secret>`**。

`status=healthy` 表示本地 HTTP handler 能响应，不等于公网已经通过验证；公网信息看 `public_state` 并最终做实际 MCP 握手。

Computer Use 的本机开关、危险操作确认、桌面/浏览器/Blender 启停方式均未改变。源码升级仍需完整重启 GUI；如果开关重启后关闭，应由本机用户在 GUI 重新授权，不能修改配置绕过。

## 验证与验收

隔离回归（不会创建真实公网隧道）：

```bat
python -m py_compile bridge.py gui.py connection_health.py connection_probe.py
python -m unittest -v test_connection_regression test_connection_health test_url_file test_tunnel_resilience
```

修复目录的 `run_staged_tests.py` 使用暂存代码和临时工作目录运行；不接触生产监听器和生产地址文件。覆盖：模拟 28 分钟公网活跃但自检失败、本地调用不能掩盖连续 1033、连接器 readiness 分层、真实 loopback HTTP、真实探测子进程截止时间、鉴权与秘密不泄露、地址发布、监督和既有 GUI 控制回归。模拟时间测试不等同于线上运行 28 分钟，也不等于复现用户历史故障。

升级激活后还必须：

1. `/health` 返回 `transport_revision=1033-repair-r1`。
2. 实际公网 initialize → notifications/initialized → tools/list → list_directory → computer_use_status 成功。
3. 核对权限，没有自动启用或绕过本地 GUI 开关。
4. 记录日志并继续观察，不能因一次握手成功就承诺长期无断线。

`smoke_tunnel_recovery.py` 会创建一个真实公网隧道并终止它自己的 cloudflared；本次补丁不自动运行它。不要对正在承担控制通道的隧道直接做 kill/故障注入。

## 回滚

本次修改前原件保存在 `connection_repair_20260921_1033_01/original/`，附 SHA-256 清单。先关闭 GUI，核对部署后文件未被其他人修改，再恢复本次改变的原件并重启；不要覆盖无关项目。新增模块/测试可留在磁盘上，旧入口不会导入它们，不必为了回滚删除任何用户文件。详细执行记录见修复目录的交接文件。

## 官方资料

- [Cloudflare Error 1033](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1033/)
- [Tunnel 故障排查](https://developers.cloudflare.com/tunnel/troubleshooting/)
- [Quick Tunnel 限制](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)
- [受管理隧道](https://developers.cloudflare.com/tunnel/get-started/)
- [连接器 readiness](https://developers.cloudflare.com/tunnel/guides/kubernetes/)
