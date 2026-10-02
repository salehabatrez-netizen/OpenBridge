# Background Computer Use 0.1.0

## 范围与限制
新增 `target=desktop` 下四个工具：BackgroundWindows、BackgroundSnapshot、BackgroundAction、BackgroundActionStatus。必须完整重载 GUI 后重新发现 schema；源码落盘不等于当前运行进程已加载。不要在动作结果未知时切换新 action_id 重放。

- UI Automation：Invoke、Value.SetValue、SelectionItem、Toggle、ExpandCollapse、Scroll（上下小幅滚动）。按控件提供的 pattern 报告能力。不支持的控件不回退到前台。
- 显式 message_click：左/右/中键，准确 HWND 客户区物理像素，不移动鼠标、不发全局按键、不调用聚焦。PostMessage 返回值逐条检查；成功仅为 dispatched_unverified，不代表应用执行成功。若 down 成功 up 失败，可能留下应用内部按下状态，需本机检查。无自动重试。
- 使用 UIA 控件树和可选 PrintWindow 客户区图像；覆盖窗口时不使用屏幕区域截图冒充目标画面。PrintWindow 在 Chromium/GPU/受保护内容可能黑屏、空白或陈旧，即便 API 返回成功也不代表图像可信。
- PNG/JPEG（JPEG quality 85）和最多16个红色标点，仅在图像副本显示。marks 使用原始客户区像素，输出 scale 将其映射到缩放图像。标点不是动作。
- 本版明确拒绝最小化/隐藏窗口；游戏 Raw Input、DirectInput、抗作弊、UAC、锁屏及高权限窗口不保证支持，不提权、不规避系统限制。Minecraft 保留原有 Game* 前台要求。无后台鼠标拖拽/全局快捷键，不假装完整桌面虚拟化。
- 工具不主动聚焦，但应用响应 Invoke/消息时仍可能自行激活窗口；返回 foreground_before/after，不尝试抢回焦点。
- 每次请求采用可终止的独立进程，8秒预算；进程创建/系统故障不属于硬实时保证。OFF 会撤销权限、终止活动 worker，但不能撤回已被应用接收的操作。
- 原始桌面 Click/Type 仍可能需要前台；需调用 Background* 或独立浏览器 browser_* 接口，不能把原工具全局当后台工具。浏览器接口不连接日常 profile，本次未改变其启动方式。

## 调用步骤
1. computer_use_status 确认本机已开启，computer_use_tools(target=desktop) 取得新工具。
2. BackgroundWindows 选择准确 window_id（不自动选择标题匹配的第一项）。
3. BackgroundSnapshot(window_id=..., capture=true, format="jpeg") 看树和图像。图像不可靠时不能据此进行坐标点击。
4. 从 elements 选 element_id，仅使用其 actions。UIA 优先。message_click 的 x/y 是对应 HWND 客户区坐标，不是整张根窗口截图坐标（除非选根 HWND）。
5. action_id = 返回的 session_prefix + 唯一后缀；同一个动作相同 ID 重发只返回已有回执，不再次输入。不同参数复用 ID 拒绝；旧进程前缀也拒绝。一次观察只消费一次动作。
6. UIA 观察30秒有效，坐标消息5秒有效，均从观察开始计时。目标身份/尺寸/位置变化拒绝操作。过期可以重新观察，但已发出的未知动作不能重放。
7. 每次动作后重新观察核验业务状态。失败/超时用 BackgroundActionStatus；unknown 不等于未执行。

回执最多1024条，不淘汰以避免重复；OFF/ON 保留回执。完整 GUI 重启后回执不持久化，旧编号不接受；重启后更不能据 unknown 推断旧动作未发生。原有付款、删除、对外发送需用户确认，代码不会自动识别业务危险性。

## 本机启动授权
GUI 增加“记住授权：启动 GUI 时默认开启（本机确认）”。用户必须在新 GUI 本机勾选并确认风险后保存，之后启动 GUI 默认开启。该选项不会热开启正在运行但已停用的服务；现有开关负责当前权限。关闭 Computer Use 会同时尝试取消记忆；取消勾选只撤销未来启动授权、不影响当前已启用状态。

授权偏好保存在当前 Windows 用户 HKCU/Software/OpenBridge/LocalConsent 中；只由本机 GUI 读取/写入。命令行与 MCP 不读取此偏好自授权限。它不是对完整终端权限持有者的安全沙箱。本次部署不写入偏好、不改变正在运行的权限、不自动重启。

## 测试和回退
`python -m unittest discover -q` 为离线回归；`smoke_background_control.py`（需指定 --run）仅操作它自己新建的 Win32 测试窗口，不使用当前业务窗口。实机测试并不证明所有第三方应用兼容。

回退用 git 恢复 gui.py 和 computer_use.py 的旧版本：先本机关闭 GUI，再恢复；不要覆盖之后的新修改。新增模块可保留但不会由旧入口加载。回退后再启动 GUI，MCP 地址可能变化。
