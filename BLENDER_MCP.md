# Blender 高效建模接入

## 已配置

- 官方 Blender **5.2.1 LTS**，通过 WinGet 的 `BlenderFoundation.Blender` 安装，下载来自 download.blender.org，安装器哈希通过 WinGet 校验。
- 社区项目 **ahujasid/mcp-for-blender**（原 blender-mcp）：https://github.com/ahujasid/blender-mcp ，MIT 许可证。
- PyPI 发布包固定 `mcp-for-blender==2.0.0`，运行于 `.computer-use/blender/` 的独立 Python 3.11 环境，不修改原 OpenBridge Python。
- 插件来自该发行包的 bundled addon，安装包中保留上游许可证。不是 Blender Foundation 官方 MCP。
- OpenBridge 三个统一入口现在支持 `target="blender"`，顶层 tools/list 仍为 17 个入口。

## 第一次使用

1. Blender 已在配置验收期间打开，插件正在本机端口 9876 运行。不要重复启动第二个占用相同端口的实例。
2. 停止并完全关闭旧 OpenBridge GUI，再重开 `启动OpenBridge-GUI.bat`。
3. 点击统一的 **开启 Computer Use** 按钮。按用户要求统一授权桌面、浏览器和 Blender，不再提供分开的勾选框；子服务按需启动。
4. 启动 Bridge，将新的 MCP URL 发给 AI。
5. AI 先 `computer_use_tools(target="blender")` 获取签名，然后调用 `get_addon_status` 和 `get_scene_info`。

日后 Blender 关闭了，可双击 **launch_blender_mcp.cmd**：自动定位官方安装目录中的 blender.exe，打开一个新的 Blender GUI，启用已安装插件、关闭遥测并启动本机连接。不会读取/覆盖某个项目文件，也不会清空你另一个 Blender 窗口的场景。

也可自行打开 Blender：Edit → Preferences → Add-ons，确认 MCP for Blender 已启用；3D 视口按 N，打开 MCP 面板并启动服务。一个端口只对应一个 Blender 实例。面板和插件名称以实际发行版为准。

## 8 个建模子工具

| 子工具 | 功能 |
|---|---|
| get_addon_status | 检查插件协议与 Blender 版本 |
| get_scene_info | 场景对象清单 |
| get_object_info | 对象属性与包围盒 |
| get_viewport_screenshot | 返回当前视口图片 |
| execute_blender_code | 用 Blender Python 批量建模、材质、灯光等 |
| describe_node_type | 查询节点及插槽定义 |
| bpy_api_lookup | 查询当前 Blender API |
| export_scene | 导出场景（具体支持参数以 tools/list 为准） |

例如调用 `computer_use_call`：

```json
{"target":"blender","name":"get_scene_info","arguments":{"user_prompt":"查看当前场景，不做修改"}}
```

高效工作流：先读场景和版本 → 按需求批量执行短小 bpy 脚本 → 检查对象信息与视口截图 → 再迭代。优先用数据/API 操作而不是桌面坐标点击。写版本敏感代码时查询 bpy_api_lookup；不硬编码本地化界面的节点名称。

建模前先确认单位、尺寸、用途、面数和导出格式。修改已有项目先保存副本，不默认删除所有对象。保存/导出路径需用户授权。大规模建模和渲染分步骤执行，当前适配器调用超时为 60 秒；长渲染不要直接堵塞一个同步调用。超时可能已执行部分操作，不自动重试。

## 安全

- `BLENDER_HOST=127.0.0.1`，`BLENDER_PORT=9876`；不将插件端口映射到公网。
- 上游插件 socket 本身没有认证或加密，本机其他程序仍可能访问。远程访问应走 OpenBridge 的秘密路径鉴权，不直接暴露 9876。
- `BLENDER_MCP_SAFE_MODE=1`，上游脚本检查会限制系统/网络等危险操作；这不是操作系统隔离，正常 bpy 保存、导出和场景删除仍具备破坏性。
- 禁用遥测环境变量，并将插件遥测同意项设为 false。
- 未接入 Hyper3D/Hunyuan 等付费生成、第三方素材下载/API Key。8 个核心建模工具足够本地参数化建模，不需要模型生成平台账号。
- OpenBridge 的停用按钮停止 MCP 适配器，但不会关闭 Blender 或插件监听；彻底停止本机监听请在 Blender 面板点 Stop，或关闭 Blender。
- 桌面/终端工具仍是另外的高权限通道；开关不是完整安全沙箱。

## 安装和验证文件

- `install_blender_mcp.cmd`：重建隔离环境与提取插件（不负责安装 Blender 软件）。
- `launch_blender_mcp.cmd` / `.py`：定位并启动 GUI。
- `blender_bootstrap.py`：在 Blender 内启用插件、保存插件偏好、启动回环监听；不保存场景。
- `.computer-use/blender-startup.log`：Blender 启动日志。
- `test_blender_adapter.py`：离线权限与配置测试。
- `smoke_blender.py`：实机验证，创建并删除一个唯一命名的测试立方体、获取视口图片；不保存 blend 文件。会改变临时选择/撤销历史，已有项目先保存后再运行。

验收记录：Blender 5.2.1 LTS；插件协议 7 与客户端匹配；遥测 false；场景读取成功；临时立方体倒角及清理成功；视口返回图片成功。基础接入及热启停等共 31 项离线测试通过。未验证全部修改器、渲染引擎、复杂插件或所有导出格式。
