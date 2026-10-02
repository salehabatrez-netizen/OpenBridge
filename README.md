# OpenBridge - 本地免费 MCP 桥接控制台

**最新操作方式：一个 Computer Use 按钮统一热启停桌面、浏览器、Blender，切换不改变 MCP 地址。跨对话连接说明见 [CROSS_CHAT.md](CROSS_CHAT.md)。本次代码升级需完整重开 GUI 一次。**

Blender 专用建模已接入：见 [BLENDER_MCP.md](BLENDER_MCP.md)。使用 `target="blender"`，需在本机开启统一的 Computer Use 按钮。

## 新增：Windows 桌面与浏览器 Computer Use

已集成可选 Windows-MCP 与 Playwright MCP。安装位置、启用方式、调用参数和权限限制详见 [COMPUTER_USE.md](COMPUTER_USE.md)。原有 14 个工具保留，新增 3 个接入入口；各路默认关闭，由本机统一按钮明确启用。首次加载请关闭整个旧 GUI 后重新打开。

## 📚 文档导航

| 文档 | 读它如果你想… |
|---|---|
| **[GETTING_STARTED.md](GETTING_STARTED.md)** | **第一次使用 —— 从零到能用（含 Computer Use 与 Blender 安装）** |
| [SECURITY.md](SECURITY.md) | 搞清楚权限边界和 MCP 地址为什么是凭据 |
| [COMPUTER_USE.md](COMPUTER_USE.md) | 桌面/浏览器控制的完整参数与限制 |
| [BLENDER_MCP.md](BLENDER_MCP.md) | Blender 8 个建模子工具的细节 |
| [CROSS_CHAT.md](CROSS_CHAT.md) | 多个对话共用一个桥接 |
| [RELIABILITY.md](RELIABILITY.md) | 隧道稳定性与 530/1033 排障 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 参与开发、提 PR |

---

OpenBridge 是专为网页端大模型（如 **Arena (LMSYS)**、**ChatGPT**、**Claude** 等）打造的本地 MCP（Model Context Protocol）桥接工具。

无需购买第三方收费 Bridge 订阅，不限单日使用时长，**100% 免费开源、零外部 pip 依赖**，基于 Python 3.11 原生标准库开发，随时自由掌控本地代码。

---

## 🚀 极速启动方式

### 方式一：图形界面启动（推荐 ⭐⭐⭐⭐⭐）
双击运行当前目录下的：
```text
启动OpenBridge-GUI.bat
```
或在终端执行：
```bash
pythonw gui.py
```
即刻打开现代化的图形化控制台，无黑色黑框残留。

### 方式二：命令行运行
双击运行：
```text
启动OpenBridge.bat
```
或执行：
```bash
python bridge.py --dir "你的项目路径"
```

---

## 🖥 图形界面使用步骤（极简 3 步）

1. **选择工作区 (Workspace)**：
   - 点击 `[ 📂 浏览... ]` 选择你想要让 AI 查看与编辑的项目文件夹。
2. **启动 Bridge**：
   - 点击绿色按钮 `[ ▷ 启动 Bridge ]`。
   - 程序将自动启动本地 MCP 服务，并通过 Cloudflare HTTP/2 极速隧道建立公网链接（通常 3~4 秒内就绪）。
3. **一键复制提示词并开工**：
   - 点击蓝色按钮 `[ 📋 复制开工提示词 ]`。
   - 将剪贴板内容直接粘贴到 **Arena / ChatGPT / Claude** 网页对话框中发送给 AI 即可！

---

## 📋 开工提示词设计与规则

点击 `[ 📋 复制开工提示词 ]` 时复制的模板已内置了严格的开发工作流与纪律要求：

1. **快速连接 MCP**：直接注入专属的 `https://xxxx.trycloudflare.com/mcp/<secret>` 地址（含随机秘密路径，见下方安全说明）。
2. **操作规范与纪律**：
   - **阅读优先**：AI 动工修改任何代码前必须先用 `read_files` 查看目标代码上下文，严禁凭空盲猜。
   - **精准补丁**：优先使用 `apply_patch` 进行小颗粒度替换，保留原有代码格式与注释。
   - **验证闭环**：代码改动完成后，必须主动调用 `get_diagnostics`，并用 `run_command` 运行测试用例或编译。
   - **任务协同**：复杂任务必须先拆解 TODO 计划再分步骤实施。
3. **14 个本地工具**（全量对标 shuncode-bridge）：

   **发现**
   - `list_directory`: 查看目录树与文件列表（`depth` 控制层级）。
   - `find_files`: 按文件名/路径 glob 模式查找（不检索内容）。
   - `search_files`: 全文检索关键词、函数名或正则（返回路径/行号/片段）。

   **读取**
   - `read_files`: 批量读取多个文件，支持按行切片与版本号。
   - `read_file`: 读取单个文件（`read_files` 的便捷别名）。

   **编辑**
   - `apply_patch`: 主力编辑工具，Codex 式多文件补丁，支持 Update / Add / Delete / Move File，并可用 `expected_versions` 防陈旧覆盖。
   - `write_file`: 新建文件或整体覆盖写入。

   **终端**
   - `run_command`: 在本地终端执行命令（测试、构建、安装包等），`background=true` 可跑常驻服务。
   - `get_command_output`: 按 `command_id` 增量读取后台命令的新输出与状态。
   - `send_command_input`: 向运行中的命令写入 stdin（交互式提示、REPL）。

   **诊断**
   - `get_diagnostics`: 工作区静态语法检查（Python ast / JSON / `node --check`）。
   - `lsp`: 基于符号索引的代码导航，支持 workspace_symbols、document_symbols、definition、references、implementation、hover。

   **协同**
   - `set_todos`: 维护多步任务的完整 TODO 列表（至多一项 in_progress）。
   - `report_progress`: 向 GUI 汇报当前任务的实时进度。
4. **就绪确认**：引导 AI 首先调用 `list_directory` 审查工程并汇报概况，做好处理后续一系列开发工作的准备。

---

## 🛠 高级网络优化说明

- 隧道启动命令显式使用 `--protocol http2`，可避免部分 UDP / QUIC 限制，但不能保证消除 530 / 1033 或代理故障。
- 程序持续读取隧道日志，并通过公网 MCP 初始化响应验证可用性；只分配到域名不会显示公网就绪。
- 传输修订 `1033-repair-r1`：独立子进程对公网自检设置 12 秒总截止时间（socket 8 秒）；区分当前域名真实公网请求、本地调用、TLS EOF、连接器 readiness 和明确的 530/1033，避免误杀仍可用的隧道，也避免本地轮询拖住真实故障恢复。详见 [RELIABILITY.md](RELIABILITY.md)。
- Quick Tunnel 换域名后，旧地址不会跳转到新地址；失联客户端无法通过失效入口读取本机地址文件。长期使用需固定主机名/受管理隧道。源码升级要完整重启 GUI，并以实际 `/health` 的 `transport_revision` 和公网 MCP 握手确认加载；不能把源码落盘当作运行已修复。
- Windows 使用独占、原子端口绑定，避免多实例争抢同一监听端口。GUI 后台日志经消息队列更新，避免阻塞 Tk 或隧道日志读取。
- 深色控制台提供“修复连接”按钮和连接日志目录。详细策略、限制与测试见 [RELIABILITY.md](RELIABILITY.md)。
- 公网验证由本机发起：本机代理或 HTTPS 访问受限也会导致验证失败，不代表所有外部客户端都不可达。

## 工作区切换与本次修复生效

1. 本次修改后请停止 Bridge，关闭整个 GUI，再重新运行 `启动OpenBridge-GUI.bat`。仅在旧窗口点停止/启动不会重新加载 Python 代码。
2. 运行期间禁止选择其他工作区，避免提示词与后台实际目录不一致；请先停止，再选择目录，再启动。
3. 等待“公网已验证”后，复制新的提示词和完整 URL。首次未验证和连续失败期间禁用复制；已验证地址的短暂波动显示黄色警告并暂时保留复制能力。
4. 重启会生成新的秘密路径，Quick Tunnel 域名通常也会变化；旧地址失效是预期行为。Computer Use 热开关不重建隧道，开关本身不改变 URL；重建隧道则不能保证保留域名。
5. 界面会丢弃上一轮服务的延迟回调，避免旧地址在停止/重启后重新出现。

完整回归：`python -m unittest -v test_connection_regression test_connection_health test_hot_controls test_computer_use test_blender_adapter`（53 项）。另有真实 GUI、浏览器图片及独立隧道恢复测试，见 `RELIABILITY.md`。

---

## 🔐 安全说明

- **秘密路径认证**：服务不监听裸 `/mcp`，而是挂在 `/mcp/<secret>`（32 位随机十六进制）。不带或带错 secret 一律返回 `404`，不泄露服务存在性。
- **随时重置**：在 GUI 中重置 secret 后，旧地址立即失效，新地址同步刷新到提示词中。泄露了就重置一次即可。
- **健康检查免鉴权**：`/health` 可匿名访问，但不会回显工作区路径等敏感信息。
- **工作区边界**：文件类工具（读/写/补丁）会拦截越界路径并返回 `PATH_OUTSIDE_WORKSPACE`。
- **注意**：`run_command` 是真实终端，**不受工作区边界限制**。随机隧道域名属于「藏起来」而非「锁起来」，因此建议：用完即关隧道、工作区尽量选窄、不要把地址贴到公开场合。

---

## 📦 开源信息

### 许可证

本项目以 **MIT License** 发布，见 [LICENSE](LICENSE)。

第三方组件（Windows-MCP、Playwright MCP、blender-mcp）以独立进程运行，各自保留
其上游许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

### 环境要求

| 项目 | 要求 | 说明 |
|---|---|---|
| Python | **3.11+** | 核心**零第三方依赖**，仅用标准库 |
| tkinter | 随 Python 安装 | GUI 必需；python.org 安装包默认包含 |
| cloudflared | 可选 | 仅公网隧道模式需要 |
| uv + Node.js LTS | 可选 | 仅 Computer Use 适配器需要 |

`requirements.txt` 中没有任何 pip 包——这不是遗漏，而是设计目标。

### 从源码运行

```bash
git clone <repo-url>
cd OpenBridge
python -m unittest discover -p "test_*.py"   # 324 项，无需联网
pythonw gui.py                                # 启动控制台
```

### 打包发布

```bash
python make_release.py                # 生成 dist/OpenBridge-<版本>.zip
python make_release.py --check-only   # 只做安全审计，不产出文件
```

打包器会**拒绝**把任何疑似真实凭据打进压缩包（真实隧道域名、未登记的 32 位
十六进制串），并在产出后重新扫描一遍压缩包本体做二次确认。

### ⚠️ 分享前必读

`current_mcp_url.json` 含有**真实可用的访问凭据**，已被 `.gitignore` 屏蔽。
任何拿到完整 MCP 地址的人都能读写你的工作区；若 Computer Use 处于开启状态，
还能操作你的桌面。**请勿截图、勿发群、勿提交。**

详见 [SECURITY.md](SECURITY.md)；参与开发见 [CONTRIBUTING.md](CONTRIBUTING.md)。
