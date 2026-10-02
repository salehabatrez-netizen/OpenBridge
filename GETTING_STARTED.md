# OpenBridge 上手指南（从零到能用）

本文面向**第一次拿到这个包的人**。已有的 [COMPUTER_USE.md](COMPUTER_USE.md) 和
[BLENDER_MCP.md](BLENDER_MCP.md) 是按「本机已配置完成」写的参考手册；本文补上
**前置安装**和**端到端流程**。

阅读顺序建议：本文 → [SECURITY.md](SECURITY.md) → 需要哪块能力再看对应专题文档。

---

## 0. 三层能力，按需安装

OpenBridge 分三层，**下层不装也能用上层**：

| 层 | 能力 | 需要装什么 | 不装的后果 |
|---|---|---|---|
| **A. 文件桥接**（核心） | 14 个工具：读写文件、搜索、跑命令、诊断 | **只要 Python 3.11+** | — |
| **B. Computer Use** | 控制桌面 / 浏览器 | `uv` + Node.js LTS + Edge | 少 3 个入口工具，A 层照常 |
| **C. Blender** | 8 个建模子工具 | `uv` + Blender 官方版 | Blender 目标不可用，A/B 照常 |

**核心层零第三方 Python 依赖**，不需要 `pip install` 任何东西。

---

## 1. 最小可用（A 层，2 分钟）

1. 装 **Python 3.11 或更新**，安装时务必勾选 `Add Python to PATH`
   → https://www.python.org/downloads/
2. 解压本包，双击 **`OpenBridge-一键启动.cmd`**
   （它会先检查 Python 版本和 tkinter，缺什么会明确告诉你）
3. 在窗口里：选工作区文件夹 → 点 `启动 Bridge` → 等「公网已验证」
4. 点 `复制开工提示词`，粘贴给 AI（Arena / ChatGPT / Claude 等）

> ⚠️ **复制出来的地址等同于你这台电脑的密码。** 任何拿到它的人都能读写你的
> 工作区。别截图、别发群、别提交进仓库。详见 [SECURITY.md](SECURITY.md)。

验证是否正常：让 AI 调用 `list_directory`，能列出你选的目录就通了。

---

## 2. 开启 Computer Use（B 层）

### 2.1 先装前置

```bat
winget install astral-sh.uv
winget install OpenJS.NodeJS.LTS
```
Edge 一般 Windows 自带。装完**重开一个命令行窗口**让 PATH 生效。

### 2.2 装适配器

在包目录里双击 **`install_computer_use.cmd`**。它会：
- 建独立环境 `.computer-use/windows/`，装 `windows-mcp==0.8.5`
- 建 `.computer-use/browser/`，装 `@playwright/mcp@0.0.81`

装到**隔离目录**，不污染你的 Python 和全局 npm。

### 2.3 启用

1. **完全关闭** OpenBridge GUI 再重开 —— 只点停止/启动不会重新加载 Python 代码
2. 点 **「开启 Computer Use」** 按钮（统一授权桌面/浏览器/Blender）
3. 启动 Bridge，把新 URL 给 AI

此时 `tools/list` 从 14 个变成 **17 个**（多出 3 个 Computer Use 入口）。

> 权限只能从**本机 GUI** 开启。远程对话无法自行授权，也无法暂停或隐藏自己的活动。

---

## 3. 开启 Blender（C 层）

### 3.1 装 Blender 官方版

```bat
winget install BlenderFoundation.Blender
```
装在非标准位置的话，设环境变量 `BLENDER_EXE` 指向 `blender.exe`。
（启动器会依次探测 `BLENDER_EXE` → PATH → `C:\Program Files\Blender Foundation\Blender */`）

### 3.2 装 Blender MCP 适配器

双击 **`install_blender_mcp.cmd`** → 建 `.computer-use/blender/`，装
`mcp-for-blender==2.0.0` 并提取插件。

> 这一步**不负责安装 Blender 软件本身**，只装适配器。

### 3.3 启动

双击 **`launch_blender_mcp.cmd`**。它会自动定位 blender.exe、开一个新 Blender
窗口、启用插件、关遥测、在 `127.0.0.1:9876` 起监听。

常见拦截（都是正常保护，不是 bug）：

| 提示 | 含义 | 处理 |
|---|---|---|
| `Blender not found` | 没装或路径非标准 | 装官方版，或设 `BLENDER_EXE` |
| `Port 9876 is already in use` | 已有 Blender 实例在跑 | 直接用那个，或先在它的 MCP 面板点 Stop |
| `Addon missing: run install_blender_mcp.cmd` | 3.2 没做 | 先跑安装脚本 |

也可手动：Blender → Edit → Preferences → Add-ons 确认 `MCP for Blender` 已启用
→ 3D 视口按 `N` → MCP 面板 → Start。**一个端口只能对应一个实例。**

---

## 4. Blender 建模标准流程

让 AI 按这个顺序走（写进你的提示词里效果最好）：

```
1. computer_use_status()                      # 确认 blender 已 enabled
2. computer_use_tools(target="blender")       # 取当前版本的精确签名
3. get_addon_status                           # 校验插件协议版本
4. get_scene_info                             # 先读场景，别上来就改
5. execute_blender_code                       # 短小 bpy 脚本，分步执行
6. get_object_info / get_viewport_screenshot  # 验证结果
7. 回到 5 迭代
```

调用长这样：

```json
{"target":"blender","name":"get_scene_info",
 "arguments":{"user_prompt":"查看当前场景，不做修改"}}
```

**八个子工具**：`get_addon_status`、`get_scene_info`、`get_object_info`、
`get_viewport_screenshot`、`execute_blender_code`、`describe_node_type`、
`bpy_api_lookup`、`export_scene`。

### 实操要点

- **优先用 bpy 数据/API，别用桌面坐标点击** —— 稳定得多
- 写版本敏感代码前先 `bpy_api_lookup`，别硬编码本地化的节点名
- **适配器调用超时 60 秒**。长渲染别堵在一个同步调用里，拆步骤
- **超时不会自动重试**，但操作可能已部分执行 —— 先人工检查再决定
- 改已有项目**先存副本**。`export_scene` 的保存路径需要你授权
- 建模前先定清楚：单位、尺寸、面数、导出格式

---

## 5. 安全边界（务必读）

- **工作区边界不约束桌面、浏览器和 `run_command`。** 文件工具会拦越界路径，
  但终端和桌面是全权限通道。
- GUI 开关是**操作保护，不是安全沙箱**。允许桌面控制 = 允许访问整个当前登录桌面。
- **「暂停 AI 操作」** 是软暂停：拒绝后续请求，但**不会打断正在执行的动作**。
  要立即制止 → 按 **F8**（游戏输入松键）或 **「关闭 Computer Use」**（撤权杀子进程）。
- Blender 插件的 9876 端口**本身无认证**，别映射到公网；远程访问走 OpenBridge 的秘密路径鉴权。
- `BLENDER_MCP_SAFE_MODE=1` 会拦系统/网络类危险操作，但**正常的 bpy 保存、导出、
  删除场景依然具备破坏性**。
- 建议在测试账号或虚拟机里用，用完即关隧道。

---

## 6. 排障速查

| 现象 | 原因 | 处理 |
|---|---|---|
| 双击 bat 黑框一闪 | 没装 Python 或没进 PATH | 用 `OpenBridge-一键启动.cmd`，它会明确报错 |
| 改了代码没生效 | 只点了停止/启动 | **完全关闭 GUI 窗口**再重开 |
| 旧 URL 失效了 | 重启会换域名和密钥，这是预期行为 | 从 GUI 重新复制 |
| `tools/list` 只有 14 个 | Computer Use 没开 | 本机点「开启 Computer Use」，然后**重开 GUI** |
| Blender 目标报依赖缺失 | 没跑 `install_blender_mcp.cmd` | 见 3.2 |
| 截图调用失败 | 单条消息上限 12 MiB | 缩小截图范围或降分辨率 |
| 想确认跑的是不是新代码 | — | 看 `/health` 的 `transport_revision`，别看磁盘文件 |

---

## 7. 自检

```bat
python -m unittest discover -p "test_*.py"    # 324 项，不需要联网和 GUI
```

全绿说明包完整。`smoke_*.py` 是**手动**实机检查（需要真实 GUI/浏览器/Blender），
故意不纳入自动发现。其中 `smoke_blender.py` 会建一个临时立方体再删掉，
**会影响撤销历史，已有项目请先保存**。

打包／审计：

```bat
python make_release.py --check-only    # 只扫凭据，不出包
python make_release.py                 # 生成 dist/OpenBridge-<版本>.zip
```
