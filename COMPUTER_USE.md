# OpenBridge Computer Use 接入

## 已接入的上游

- Windows: https://github.com/CursorTouch/Windows-MCP ，MIT，安装 PyPI 发布版 `windows-mcp==0.8.5`。
- Browser: https://github.com/microsoft/playwright-mcp ，Apache-2.0，安装 npm 发布版 `@playwright/mcp@0.0.81`。

使用独立 stdio 子进程集成，没有复制上游实现。上游及依赖许可证随安装包保留。主分支与发行版的 Python 要求可能不同，当前采用已实测的 Python 3.13 隔离环境，不替换 OpenBridge 的 Python 3.11。

## 已安装位置

- `.computer-use/windows/`：独立 Python 环境和 Windows-MCP。
- `.computer-use/browser/`：Playwright MCP 和 npm package-lock.json。
- 浏览器使用本机 Microsoft Edge，`--isolated` 新建独立上下文，不使用现有用户 profile/CDP 连接。
- 若移到另一台电脑：先安装 Node.js LTS、uv、Edge，再在本机执行 `install_computer_use.cmd`。启动过程中不自动安装或更新依赖。
- Windows-MCP 遥测通过 `ANONYMIZED_TELEMETRY=false` 关闭。上游其他行为以其许可证和隐私声明为准。

## 启用

1. 停止并关闭整个旧 OpenBridge GUI，再双击 `启动OpenBridge-GUI.bat`。必须重开窗口才能加载新代码。
2. 选择工作区，点击统一的“开启 Computer Use”按钮，授权桌面、浏览器和 Blender。默认关闭，运行期间也能切换。
3. 阅读本机权限提示后确认，启动 Bridge。第三方进程在首次调用时启动，不提前接管桌面。
4. 使用新的 URL 连接，`tools/list` 会返回原有 14 个工具加 3 个 Computer Use 入口（共 17 个）。
5. 点击“关闭 Computer Use”撤销权限、停止子进程；再次点击即可热启用，不停止文件 MCP、不重建隧道、不改变 URL。详见 [CROSS_CHAT.md](CROSS_CHAT.md)。

本版 GUI 是权限入口；命令行旧启动方式仍默认禁用各路能力，不读取能被远程工具修改的配置文件来自动启用。

## 调用协议

首先 `computer_use_status()`，再 `computer_use_tools(target="desktop")` 或 `target="browser"`。它们返回当次安装版本的精确参数定义。

然后调用统一入口，例如：

```json
{"target":"browser","name":"browser_navigate","arguments":{"url":"about:blank"}}
```

发送给 `computer_use_call`。截图的 MCP image 内容块直接转发，**不是本机路径字符串或图片的文字化表示**。工具名区分大小写，不能套用其他版本参数。当前实际发现 9 个桌面工具、19 个浏览器工具；不是把所有子工具平铺到顶层 tools/list。

桌面提供 App、Snapshot、Screenshot、Click、Type、Scroll、Move、Shortcut、Wait。浏览器提供导航、快照、点击、输入、选项、标签页、截图等。当前桌面发行版没有名称为 Drag 的工具，因此不声称桌面拖拽可用。一般先 Snapshot 识别目标，再调用动作工具，动作后重新检查状态。

## 活动指示与暂停（本机可见性）

Computer Use 卡片下方有一条实时状态条，回答“AI 现在是不是正在动我的电脑”：

- `🟢 AI 正在操作：desktop/Screenshot · 已用 3.2 秒`，多个请求排队时追加 `(+N 排队)`。
- `⚪ 空闲 · 上次 browser/browser_navigate 完成 1.4 秒 · 累计成功 12 / 失败 0`。
- `⏸ 已暂停 · AI 的操作请求会被拒绝，连接与子进程保留`。

运行日志同时给出成对的开始/结束行，含耗时与成败：
`[COMPUTER USE] > desktop/Click 开始` / `[COMPUTER USE] < desktop/Click 完成 0.8s`。
过去 `computer_use_status` 和 `computer_use_tools` 在 GUI 中完全不留痕迹，现在也会记为 `[TOOL]`。
审计仍只记录 target/工具名，**不写入输入文本和截图**。

「暂停 AI 操作」是本机软暂停：

- 后续 `computer_use_call` 直接被拒绝并返回明确原因，**动作不会到达桌面**。
- 不杀子进程、不断隧道、不改 URL，浏览器与 Blender 上下文保留，点「恢复 AI 操作」即可继续。
- **不会打断正在进行中的动作**。要立即制止请按 F8（游戏输入松键）或「关闭 Computer Use」（撤权并杀子进程）。
- 租约控制类工具（GameRelease/GameCancel 等）不受暂停拦截，避免暂停把游戏租约卡死。
- 暂停开关仅存在于本机 GUI，**没有对应的 MCP 工具**：远程对话无法暂停、恢复或隐藏自己的活动，也无法自行解除暂停。

`computer_use_status` 的返回中含 `activity` 字段（`paused`/`busy`/`current`/`pending`/`last`/`counters`），便于 AI 自述当前状态。

## 权限与限制（必读）

- 文件工作区边界**不约束桌面、浏览器或现有 run_command**。GUI 开关是操作保护，不是抵御持有完整文件/终端权限者的安全沙箱。
- 允许桌面控制即允许访问当前登录桌面，不是单应用授权。关闭敏感窗口，在测试账号/虚拟机中使用更安全。
- 白名单屏蔽额外 shell、剪贴板、任意 JS 执行等专门工具，但键盘、应用启动、浏览器导航本身仍是强权限，不能据此声称无法执行系统命令或访问敏感页面。
- 本版不自动识别付款、删除和对外发送；需要调用方遵守用户确认流程。不可绕过 UAC、登录或系统安全限制。
- 独立浏览器不复用日常登录信息，但用户在测试浏览器中的登录和页面数据仍可能被 AI 读取。
- 接口密钥在 URL 中，不能公开共享。Computer Use 要求启用原有 MCP 鉴权。
- 子调用串行执行；超时会停止该子 MCP，**不会自动重试动作**。超时前动作可能已发生，先人工检查，再决定是否重启。
- 每条子 MCP 消息上限 12 MiB，过大截图会导致调用失败。并非完整 MCP 代理：不支持子服务器 sampling/elicitation/resources 请求。
- 紧急停用是 best-effort：撤销后会杀死子进程树，但无法撤回已点击/已发送的操作，也不保证关闭通过独立应用启动产生的所有窗口。
- 审计只记录 target/tool，不把输入文本和截图写入 OpenBridge 的工具调用日志；上游可能创建输出文件，请自行管理 `.playwright-mcp`。

## 测试

```bat
python -m unittest -v test_computer_use test_connection_regression
python smoke_computer_use.py
```

活动指示与暂停相关：

```bat
python -m unittest -v test_activity_indicator
python smoke_activity_gui.py
```

`test_activity_indicator` 覆盖计数、并发排队、异常不卡死指示灯、暂停不下发动作、暂停不可远程触及；`smoke_activity_gui.py` 离屏构建真实 GUI 校验各状态文案与按钮，不启动 Bridge、不触碰桌面。

单元测试不操作桌面。实机 smoke 会启动子 MCP、列出工具、桌面 Wait（不点击/输入），并在独立 Edge 中打开 about:blank、截图并关闭测试浏览器。没有验证所有应用的点击、输入、滚动、UAC、多显示器和锁屏情况。最终公网加载新版需用户重启 GUI 后再验证。

升级时固定版本，重跑这些测试。当前是可运行的多路集成，不是全部桌面操作的兼容性保证。
