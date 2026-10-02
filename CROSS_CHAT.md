# 热启停、统一按钮与跨对话使用

## 现在的行为

GUI 只有一个 **开启/关闭 Computer Use** 按钮，统一控制桌面、浏览器、Blender。默认关闭，可在启动 Bridge 前选择，也可在 Bridge 运行期间随时开启/关闭。开启需要本机确认；没有向远程 MCP 客户端提供自行授权的工具。

- 开启：授权所有已接入的适配器，首次调用时才启动子 MCP。不是立刻打开所有应用。
- 关闭：先撤销权限，再停止子 MCP、清空工具缓存；HTTP 服务、隧道和秘密 URL 不改变。
- 再开启：创建新的子 MCP 实例；无需关闭 GUI 或重启 Bridge，超时退出的子 MCP 也可这样恢复。
- 浏览器子进程停止可能关闭独立浏览器并丢失其临时会话；操作前保存需要的数据。
- Blender GUI 和其插件监听不会因适配器关闭而自动结束。若 Blender 没打开，先运行 `launch_blender_mcp.cmd`；总开关不替代软件安装和 Blender 插件启动。
- 这是**运行时权限/子服务热启停**，不是任意 Python 源码的热重载。本次升级和以后修改主程序代码仍需完整重开 GUI 一次。
- 停止整个 Bridge、重启 Quick Tunnel 或重置秘密路径，仍可能改变 URL；这与 Computer Use 热切换不同。

## 在其他对话使用

> **地址失效时先读文件。** Quick Tunnel 域名会轮换，旧地址会一直返回 Cloudflare 530 /
> Error 1033。当前有效地址始终保存在工作区根目录的 `current_mcp_url.json`，
> 新对话可直接读取该文件，不必人工重新复制；`status` 为 `STOPPED` 表示服务已停止，
> 此时应请用户在本机启动，而不是继续轮询旧域名。

1. 保持 Bridge 和网络在线，开启 Computer Use。
2. 在 GUI 点击 **复制开工提示词**，将完整提示词发给新的对话。
3. 新客户端独立执行 initialize → notifications/initialized → tools/list。
4. 调用 computer_use_status → computer_use_tools(target) → computer_use_call。
5. target 支持 `desktop`、`browser`、`blender`。签名以当次 tools/list 和子工具列表为准。

服务采用无状态 Streamable HTTP，不绑定本次 Arena 会话，不需要旧会话 ID、Cookie 或之前聊天历史。URL 中的秘密路径就是凭据。截图以 MCP image 块返回，由客户端渲染或将 base64 解码为图片文件。

### 两类可用客户端

- 原生支持远程 Streamable HTTP MCP 的客户端：在设置里添加完整 URL。
- 有 HTTPS 请求和工具执行能力的 agent：可主动 POST MCP JSON-RPC 请求。生成的提示词已包含请求头和调用流程。

**普通纯文本聊天不能凭一个链接获得工具权限。** 目标平台也可能不允许自定义 MCP、要求 OAuth、禁止临时隧道或不支持图片结果。这些平台限制不能由本地代码保证消除。新版不实现 OAuth/旧式独立 SSE endpoint，不能承诺所有聊天产品即插即用。

### 共享状态与风险

所有对话访问的是同一台电脑、同一工作区和应用状态，不是多个隔离桌面。不要同时让多个对话改同一文件或操作 UI。复制给其他对话等于授予其对应权限；不要公开 URL。付款、删除、对外发送及覆盖项目仍需用户确认。

## 已验证

- 无旧会话 ID/Cookie 的独立新 HTTP 客户端可初始化、发现 17 个工具并调用。
- 同一 HTTP 地址下关闭、拒绝调用、重新开启、恢复工具与图片结果。
- Windows 实机单按钮 OFF→ON→OFF→ON，URL 不变。
- 真实 Playwright MCP 经 HTTP 返回图片，热启停后另一新客户端仍可调用。
- 31 项自动化测试通过。没有替用户登录或测试每一种商业聊天平台。

复验：

```bat
python -m unittest -v test_hot_controls test_computer_use test_blender_adapter test_connection_regression
python smoke_hot_gui.py
python smoke_http_computer_use.py
```
