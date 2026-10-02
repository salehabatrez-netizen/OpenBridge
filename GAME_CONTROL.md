# 受控游戏输入：第一阶段实现

## 2026-09-20：受信游戏窗口自动批准（游戏 broker 0.3.0）

目的：本机用户可预先信任特定游戏（默认规则：Minecraft Java，进程 javaw.exe/java.exe + 标题前缀 "Minecraft" + 窗口类 GLFW30/LWJGL），此后 `GameRequestControl` 命中规则时不再弹出确认框，直接返回 `state=approved` 与 `lease_id`。

- 开关：GUI「03 Computer Use」卡片新增复选框「自动批准受信游戏窗口」，记忆在 HKCU\Software\OpenBridge\LocalConsent\GameTrustAutoApproveV1。勾选时弹一次确认并自动生成规则文件。
- 规则文件：`%LOCALAPPDATA%\OpenBridge\game_trust.json`（用户配置目录，不在 MCP 工作区内，MCP 工具无法写入）。规则的 process / title_prefix / class 三项凡列出者必须全部匹配；空规则不匹配任何窗口。
- 传递路径：GUI → `ComputerUseManager.game_trust` → 启动 broker 子进程时的环境变量 `OPENBRIDGE_GAME_TRUST=1` → `game_control.trust.policy()` → `Controller(policy=...)`。切换复选框会撤销当前租约并重启 broker。
- 未改变：仍只绑定一个窗口、租约闲置 600 秒/最长 3600 秒/1024 次动作、失焦中止松键、F8 与 Computer Use OFF 立即撤权、MCP 工具列表仍无任何 approve/trust 工具（`test_no_approval_tool_is_exposed`）。
- 客户端注意：`GameRequestControl` 返回值可能已含 `lease_id`（自动批准），否则仍按旧流程轮询 `GameControlStatus(request_id)`。
- 生效条件：需要完整退出并重开 OpenBridge GUI（父进程重新导入 gui.py / computer_use.py），重开后 URL/密钥可能变化。
- 验证：新增 `test_game_trust.py` 13 项；全量 182 项 unittest 通过；静态诊断 0 问题。


## 状态（2026-09-18）

已新增独立 stdio 输入工作进程、8 个桌面扩展工具、GUI 本机窗口确认、总开关联动及离线回归测试。原网关仍为 17 个顶层工具，target 仍是 desktop/browser/blender，客户端无需新增网关类型。

**写入源代码不等于已升级正在运行的 GUI。** 需要退出并重新打开 OpenBridge 应用（只在旧 GUI 中停止/启动 Bridge 不会重新导入这些模块），重新启动服务并复制新的连接提示词。不要假设重启后 URL/密钥不变。日常 Computer Use OFF/ON 无需重启。

真实窗口测试入口：`python smoke_game_control.py`。它只批准该测试进程自己创建的两个窗口，并且等用户点击主测试窗口取得前台焦点后才发送输入。生产接口没有远程批准方法。本机测试夹具不用于批准 Minecraft 或其他现有窗口。

最终离线回归：**85 项通过**；静态检查 26 个代码文件、0 问题；编译通过。

真实 Windows 测试最终记录：`.input-lab-runs/20260918-130340/report.json`，进程 exit_code=0，success=true；`verified.png` 已下载、SHA-256 校验并实际查看。结果如下：

| 验收项 | 实际结果 |
|---|---|
| W+D 同时按住 | Windows 原生状态与应用收到的 W/D key-down 均确认；454 ms 后均 key-up |
| 鼠标 | 指定位置 (400,270) 点击一次；相对移动产生应用事件 |
| 动作去重 | 相同 action_id/参数不产生第二次输入 |
| 同实例跨适配器争用 | 浏览器调用被拒绝 |
| 失焦 | 批次中止，cleanup_confirmed=true |
| 原生 F8 | 批次中止，cleanup_confirmed=true |
| Computer Use OFF | 批次中止，cleanup_confirmed=true |
| 父端 stdin EOF | 批次中止，cleanup_confirmed=true |
| 最终状态与截图 | W、D、F8 均未按住；截图 Native held: NONE，无输入法候选窗 |

**测试过程中发现并修正了输入法干扰。** 早先截图虽然显示原生状态检查通过，但中文 IME 截获了应用层 key-down，并显示候选窗。因此最终测试增加了应用实际收到 W/D 的断言，并只对测试进程自己创建的窗口解除 IME 关联；不修改全局输入语言，不改任何现有应用的输入法上下文。生产输入工具不会自动关闭目标应用输入法；进入 Minecraft 测试前应手动切换英文输入，继续核对画面与动作响应。

早先未获得焦点的测试在零输入状态停止，失败/中间结果均保留；不把它们改写成成功。最终测试的 454 ms 是这一次组合动作的持续时间，不是全链路模型延迟，也不是 FPS 指标。F8/失焦/EOF 用例的 elapsed_ms 是从动作开始计算，不应冒充取消响应延迟。

**尚未完成：** 运行中的正式 GUI 重新加载、真实 GUI 本机确认弹窗验收、Java Minecraft 实机试验。Minecraft 未启动测试，未打开或修改存档。

## 使用流程

1. 本机开启统一 Computer Use 总开关。
2. 客户端重新执行 `computer_use_status` 和 `computer_use_tools(target="desktop")`。确认工具列表含 GameWindows 等新工具。
3. `GameWindows` 查看可见顶层窗口，选定窗口后 `GameRequestControl(window_id=...)`。
4. 本机 GUI 显示窗口标题、PID、窗口 ID，由用户批准。请求 60 秒过期。客户端用返回的 request_id 调用 GameControlStatus，只有匹配请求才能取得 lease_id。
5. 必要时 GameFocus 尝试激活已批准窗口。系统拒绝时请用户手动切换；不要通过额外快捷键绕过。
6. GameObserve 获取 PNG 和 frame_id。只使用最新且未消费、年龄不超过 5 秒的截图。
7. GameAct 提交唯一 action_id、lease_id、frame_id、steps；成功执行后自动松键并返回新截图及坐标元数据。若截图失败，动作收据与 observation_error 分开报告，不能因此重复动作。
8. 不确定结果先 GameActionStatus，再观察。`unknown` 不代表“未执行”；不能改个 action_id 重发。
9. 完成后 GameRelease。按 F8 或关闭 Computer Use 可以中止任何当前租约。

动作示例（指令说明，非已经执行过的游戏操作）：

```json
{
  "lease_id": "本机批准后取得",
  "frame_id": "最新截图 ID",
  "action_id": "trial-001",
  "steps": [
    {"keys": ["W", "D"], "buttons": [], "dx": 60, "dy": 0, "duration_ms": 250},
    {"keys": ["SPACE"], "buttons": [], "duration_ms": 100}
  ]
}
```

每一步 keys/buttons 表示该步骤**完整的按住状态**，不是仅追加。dx/dy 是该步骤总相对位移，随持续时间分段发送；pointer 是截图像素坐标 `[x,y]`，不能和相对位移混用。pointer 先定位再按键/鼠标，可用于菜单点击；不保证平滑拖动。

## 安全边界

- 仅匹配本机批准的顶层 HWND、PID、进程启动时间和窗口类；执行时核对前台窗口与客户区物理像素矩形。失焦、窗口消失/替换/移动/缩放会中止当前批次并释放输入，不自动继续。
- 一个租约占用同一 OpenBridge 实例的共享 Computer Use 操作通道；现有桌面、浏览器和 Blender 动作在此期间被拒绝，避免多适配器争用。网关的文件/命令开发权限不是桌面沙箱，仍只应给可信客户端。
- 每批 1–16 步，每步 20–5000 ms，请求持续时间合计最多 10000 ms（2026-09-18 起上调，参数集中在 engine.py 顶部 MAX_STEP_MS/MAX_BATCH_MS）；运行额外预算 300 ms 用于调度开销。操作循环约每 10 ms 检查，独立工作进程看门狗约每 20 ms 检查 F8/租约过期。这是软件尽力时限，不是硬实时保证。
- 租约闲置 600 秒失效；一次本机批准绝对最多 60 分钟/1024 次动作（LEASE_IDLE_S/LEASE_ABSOLUTE_S/MAX_ACTIONS）。收据不通过驱逐旧 ID 腾出空间，达到限额要重新批准。
- 所有步骤先验证后执行；不开放 WIN/ALT/F8 注入，禁止 CTRL+ESC，参数有范围限制。不能靠此保证所有应用内操作无害，支付/删除/外发/覆盖仍需用户确认。
- 动作结束、异常、F8、总开关关闭、正常退出和父端 stdin EOF 均走松键路径。总开关撤权通知不等待正常工具请求锁。
- 松键失败会锁定当前控制器并报告 fault。GUI 日志在关停未收到释放确认时明确警告。不要把故障当成安全成功；先本机检查。
- **不能保证工作进程被强杀、系统崩溃、驱动卡死、完整性级别阻止 SendInput 时仍成功松键。** F8 也依赖工作进程存活。出现未知状态，应人工释放按键并检查目标，不盲目重放。
- Windows 前台变化与 SendInput 无法组成原子事务；失焦检测是尽力保护，不是操作系统隔离。测试期间不要同时手动操作同一桌面。

## 截图、依赖与性能

输入使用 Win32 SendInput 扫描码和相对鼠标，截图使用已经存在的 Pillow ImageGrab，线程采用 per-monitor DPI 坐标。PNG 元数据包含客户区物理 rect、图像尺寸、frame_id、单调时钟捕获时间和后端名称；可按 rect 与 image_width/image_height 映射坐标。

本阶段不安装付费 API、模型权重、驱动或大框架。截图为前台客户区对应的屏幕像素，**不是引擎 framebuffer**：其他置顶窗口可能遮挡，受保护内容可能黑屏，独占全屏/OpenGL/虚拟显示器兼容性必须实测。优先用窗口化/无边框的 Java Minecraft 做下一阶段测试。

没有宣称 30/60 FPS，也没有实现持续视频捕获或游戏智能策略。当前先验证输入/观察闭环；后续依据测量决定是否引入 DXcam/WGC/DXGI。截图频率、工具调用速度、模型决策频率是三件不同的事。

## 检查与下一步

- 离线：`python -m unittest discover -v`；新测试 `test_game_control.py`。
- 静态：网关 get_diagnostics；编译 `python -m compileall -q game_control computer_use.py gui.py test_game_control.py smoke_game_control.py`。
- 真实输入：运行 smoke_game_control.py，点击测试窗口后暂时不触碰输入，检查 report.json、before.png、after-action.png、verified.png。程序只控制自己的测试窗，保存到新时间目录，不覆盖旧记录。
- 测试顺序：组合键/点击/相对移动 → 去重 → 跨适配器排他 → 失焦松键 → 原生 F8 → 总开关 OFF → stdin EOF。
- 然后重载正式 GUI，验证本机确认弹窗和新工具发现，再进入 **新建、可丢弃的 Java Minecraft 测试世界**。不得自动打开现有存档或加入多人服务器。

本工具读取屏幕和公开窗口元数据，不读取游戏内存/网络包/隐藏世界状态，不绕过反作弊，不破解软件。游戏是验收环境，不以“能移动鼠标”冒充“会玩所有游戏”。

## 2026-09-18：截图衔接与长会话状态改进

本轮是兼容的小范围改进，不是持续视频代理，也没有新增无人监督的游戏循环。

- GameAct 新增 observation_max_size（320–1920，默认 1280）与 observation_settle_ms（0–1000，默认 100）。动作先自动松键，再等待应用渲染、截图；降低“截图还没反映最后一次按键”的概率，但不能保证画面稳定，也不保证更高 FPS。
- GameObserve 新增 settle_ms（默认 0）。等待期间不按住任何键，并持续检查授权、窗口身份、客户区和焦点；原看门狗仍处理 F8/撤权。截图有效期仍为捕获起 5 秒，不因等待而放宽。
- 新截图元数据包含 capture_duration_ms 和 settle_ms，便于测量，而不是凭感觉宣称提速。
- GameControlStatus 新增 lease_absolute_remaining_seconds 与 actions_remaining。原闲置 600 秒、批准后绝对 3600 秒、最多 1024 次动作的上限均未改变。
- 新增 test_game_fluency.py：连续步骤相同按键只按下一次、最终松开一次；长批次/schema 一致；截图参数预验证；去重不重放；截图失败保留动作收据；等待期间失焦/撤权停止；授权上限不延长。
- 实际离线验证：95 项 unittest 通过；静态诊断扫描 27 个代码文件，0 问题。未宣称完成本次改动后的 Minecraft 实机性能验收。

### 推荐调用方式

确认环境安全时，把连续动作合并为一个不超过 10 秒的批次（每步不超过 5 秒）。相邻步骤相同 keys/buttons 不会中间松开；短暂释放请显式插入空状态步骤。菜单操作建议先用 pointer 定位的空状态步骤，再点击，避免游戏尚未处理移动事件。危险地形和战斗仍用短动作。

优先复用 GameAct 返回的 observation.frame_id；只能在看过画面、该帧仍有效时使用。过期则重新观察，不放宽检查；遇到结果不确定先查 GameActionStatus，不能重放。

### 启用与回退

源码写入不等于正在运行的进程已加载。完整重载 OpenBridge GUI 才能确保父进程公布的新 schema 与子进程一致；重启会中断连接，URL/密钥可能变化，请由用户本机完成并提供新的连接说明。单独热开关可能重载子进程，但不能假设父进程内已导入的 schema 同时更新。重载后重新发现工具并取得本机游戏窗口授权。

若要关闭新增的截图等待，可传 observation_settle_ms=0；不影响安全边界。不要为了“持续运作”取消每批松键、F8、失焦、授权或截图过期检查。本次没有改变原存档、账号或 Arena 实例。


## 2026-09-18：流畅度与控制通道改进（游戏 broker 0.2.0）

### 本轮已实现

1. **运行时 schema 为准。** 桌面工具发现向当前游戏子进程执行 tools/list，不再把 GUI 启动时导入的旧 GAME_TOOLS 当成当前接口。仍过滤固定的 8 个已审查游戏工具；工具缺失、结构错误或发现期间子进程更换时明确失败，不回退到陈旧定义。工具发现返回 game_runtime，GameControlStatus 返回 runtime，包括 version、protocol_revision、schema_revision、当前时间上限和 input_tick_ms。
2. **独立控制通道。** 仅游戏 stdio 通道按 request_id 分发响应；GameRelease、GameControlStatus、GameActionStatus 不再等待长动作持有的操作锁。输入动作仍在父进程串行，子进程 action_lock 仍拒绝重入。工作进程为释放保留独立名额，慢截图、普通请求或状态查询不会占用它。远程 GameRelease 仍验证匹配的 lease_id；本机 F8/OFF 的原有撤权路径保留。
3. **绝对时间线调度。** 保持原有名义 10ms 输入周期，不提高线程优先级、不忙等、不修改系统定时器。按单调时钟和累计步骤截止时间执行；迟到时直接按实际时间插值，不突发补发过期 tick。完全错过的步骤在发送新按键前以 SCHEDULE_LATE 中止，不把过期步骤压成瞬时点击。输入步骤仍是尽力的计划时间段，不是硬实时按住时长承诺；系统迟到可缩短剩余步骤时间。短按是否被游戏接收仍须实机核对。
4. **组合键次序。** CTRL/SHIFT 先按下、后松开；相邻步骤相同按住状态仍不重复按下/松开。完成、取消、异常和截止时间超限仍走原有清理路径。
5. **并发撤权保护。** 校验租约与撤权在同一状态锁中完成，但等待输入清理时不占状态锁；旧输入/截图未退出或清理未完成时拒绝新授权请求。迟到的旧租约到期检查不会撤销替代租约。状态查询不在状态锁内执行较慢的 Win32 窗口查询。
6. **分段性能数据。** 动作收据包含 timings；截图包含 capture_metrics；游戏 stdio 结果的 _meta 包含 openbridge/transport。默认 PNG、默认 1280 尺寸、默认 100ms 渲染等待、5 秒帧有效期、5 秒单步/10 秒批次上限全部不变。

### 指标含义（不要混作 FPS）

- timings.requested_duration_ms：请求的累计计划时间。
- timings.first_input_ms：批次开始至首次调用输入后端之前的时间；不是游戏接收或画面响应时间，无输入时为 null。
- timings.tick_lateness / step_start_lateness：计划时刻到实际执行时刻的迟到统计（samples、p50_ms、p95_ms、max_ms，nearest-rank）。
- timings.window_check：取消、租约和窗口完整核验的耗时统计；本轮没有删减窗口身份、客户区或失焦检查。
- timings.missed_ticks / execution_overshoot_ms / cleanup_ms：错过的周期数、计划结束后的额外执行时间、末尾清理时间。
- observation.capture_metrics：grab_ms、resize_ms、png_encode_ms、base64_ms、total_ms、png_bytes、base64_bytes。不会记录图像内容或键盘文本。
- release_elapsed_ms / last_release_ms：撤权处理开始到清理完成的耗时，不包含 F8 从物理按下到被轮询发现的时间，也不是 OS 崩溃时的保证。
- tool_elapsed_ms：游戏 worker 本次 GameAct 调用耗时，含动作及后续观察，不含结果序列化和回传。
- _meta["openbridge/transport"].broker_round_trip_ms：父子进程往返，含动作与截图等待；不含公网 HTTP 传输、客户端解码或模型思考时间。

### 验证与范围

新增 test_game_scheduling.py、test_game_transport.py，并更新原有工具发现测试，使其使用明确的假游戏进程。覆盖绝对时间线、浮点 tick 边界、延迟/过期步骤、精确相对位移、组合键顺序、实时 schema、乱序响应、EOF/超时、错误租约、独立释放名额、慢截图中释放、HTTP 长动作期间释放，以及动作继续串行。

隔离 Linux 副本：131 项 unittest 完成，无失败；1 项 Windows 专属 socket 测试跳过。已有 95 项回归作为改动前基线，新增加 36 项。这里所有游戏输入来自 FakeBackend，不调用真实 SendInput，不打开游戏、不接管现有窗口。

本机 Windows 实际离线验证：compileall 成功，131/131 项 unittest 通过，无失败、无跳过，退出码 0；静态诊断扫描 29 个代码文件，0 个问题。新增 36 项在隔离副本中另重复通过 3 轮。测试包含假后端的 HTTP→父进程→stdio→worker 取消通道，不是正式 GUI 重载或真实游戏输入验收。写入后再次发现，当前未重启的 GUI 仍公布旧版 1000ms 单步上限；因此新版正式启用仍需要下面的本机重载步骤。

确定性假时钟示例：8 个连续 100ms 步骤、窗口核验 3ms、发送 2/4ms、唤醒晚 2ms 的设定下，旧实现累计 888ms，新实现 811ms；位移总量和最终松键均保持正确。这是人为注入成本的回归示例，**不是本机游戏提速百分比，也不是实机延迟或 FPS 测量**。

本轮不安装模型、驱动、抓屏库，不改变游戏存档，不自动重启 GUI。尚未实施进程句柄缓存、WGC/DXGI、视频流或本机视觉策略；需要先从真实指标定位瓶颈，不通过取消安全检查获得表面加速。跨批次仍会松键、观察并等待下一次决策，远程聊天回合的停顿并未消失。

### 加载新版

必须由本机用户完整退出并重新打开 OpenBridge GUI，使父进程及游戏 worker 一起加载新版；单独 Computer Use OFF/ON 不能重新导入已经运行的父进程代码。完整重开后 URL/密钥可能变化，请使用新连接提示词；日常热开关不改变 URL 的行为未修改。

重新发现工具后，应看到游戏 runtime.version=0.2.0、protocol_revision=2，GameAct 的单步上限 5000ms、批次上限 10000ms，以及 observation_max_size / observation_settle_ms。正式游戏仍须本机窗口确认。没有完成实机验收前，不宣称实时、固定 FPS 或硬实时停止保证。
