# OpenBridge — a free, local MCP bridge for web-based AI chats

[中文](README.md) | **English**

OpenBridge lets an AI assistant running in a **web chat** (Arena, ChatGPT, Claude and
other MCP-capable clients) work on **your own Windows PC**: read and edit files, run
commands and tests, and — only if you switch it on locally — operate the desktop, a
browser or Blender.

It runs a small MCP (Model Context Protocol) server on your machine and publishes it
through a Cloudflare Quick Tunnel behind a random secret path. The core is pure Python
standard library: no subscription, no account, no required `pip install`.

> **The MCP URL is a password for your computer.** Anyone holding the full URL can read
> and write your workspace and run commands. Never post it, screenshot it, or commit it.
> See [SECURITY.md](SECURITY.md).

---

## Features

- **File bridge (core, 14 tools)** — browse, search, batch-read, Codex-style multi-file
  patches with stale-write protection, terminal commands (foreground and background,
  with stdin), static diagnostics, symbol navigation, TODO and progress reporting.
- **Computer Use (optional, off by default)** — one local switch enables three gateway
  tools that drive:
  - the Windows desktop via [Windows-MCP](https://github.com/CursorTouch/Windows-MCP),
  - an isolated Microsoft Edge via [Playwright MCP](https://github.com/microsoft/playwright-mcp),
  - Blender via [blender-mcp](https://github.com/ahujasid/blender-mcp) (8 modelling sub-tools).
  Remote clients cannot grant themselves this permission; it can only be enabled in the
  local GUI, and can be hot-toggled without changing the URL.
- **Controlled game input and background window control** — leased, per-window,
  locally approved input (F8 releases all keys), and UI-Automation-based actions on
  background windows without stealing focus.
- **Tunnel reliability** — HTTP/2 Quick Tunnel, public end-to-end verification,
  supervised reconnects, and diagnostics for Cloudflare 530 / Error 1033.
  See [RELIABILITY.md](RELIABILITY.md).
- **Multi-agent handoff** — `handoff_cli.py` keeps per-project state (focus, claims
  with expiry, decisions, facts, asset review) so several chats can share one machine
  without stepping on each other.
- **Release builder** — `make_release.py` packs a clean zip and refuses to build if a
  denied file, a live credential, or an import of an unshipped local module slips in.

## Quick start

1. Install **Python 3.11+** from <https://www.python.org/downloads/> and tick
   *Add Python to PATH*.
2. Double-click **`OpenBridge-一键启动.cmd`** (it checks Python and tkinter first), or
   run `pythonw gui.py`.
3. In the window: choose a workspace folder → **启动 Bridge** (Start) → wait until it
   shows **公网已验证** (public endpoint verified).
4. Click **复制开工提示词** (copy start prompt) and paste it into your AI chat.
5. Check it works: ask the AI to call `list_directory`.

The GUI is currently in Chinese; the button names above are given in both languages.

Command line alternative:

```bash
python bridge.py --dir "C:\path\to\your\project"
```

`--no-tunnel` runs without the public tunnel. `--no-auth` disables the secret path for
local debugging only; it must be combined with `--no-tunnel` and then listens on
`127.0.0.1` only.

### Optional layers

| Layer | What you get | Requires |
|---|---|---|
| A. File bridge (core) | 14 tools | Python 3.11+ only |
| B. Computer Use | desktop + browser control (3 gateway tools, 17 in total) | `uv`, Node.js LTS, Edge; run `install_computer_use.cmd` |
| C. Blender | 8 modelling sub-tools | `uv`, Blender; run `install_blender_mcp.cmd`, then `launch_blender_mcp.cmd` |

Adapters are installed into isolated environments under `.computer-use/` and never
into your main Python or global npm. Optional Python packages (Pillow for smoother GUI
widgets and window screenshots, pywin32 + comtypes for background window control) are
listed in `requirements.txt`.

After changing Python code, **close the whole GUI and reopen it** — Stop/Start inside
the window does not reload modules.

## Tools

| Group | Tools |
|---|---|
| Discover | `list_directory`, `find_files`, `search_files` |
| Read | `read_files`, `read_file` |
| Edit | `apply_patch`, `write_file` |
| Terminal | `run_command`, `get_command_output`, `send_command_input` |
| Diagnose | `get_diagnostics`, `lsp` |
| Coordinate | `set_todos`, `report_progress` |
| Computer Use (opt-in) | `computer_use_status`, `computer_use_tools`, `computer_use_call` |

Computer Use sub-tools are not flattened into `tools/list`: call
`computer_use_tools(target="desktop" | "browser" | "blender")` to get the exact
signatures of the installed version, then `computer_use_call`.

## Security model

- The MCP endpoint lives at `/mcp/<32-hex-secret>`; any other path returns 404.
  The secret can be reset from the GUI at any time, which invalidates the old URL.
- `/health` is unauthenticated and returns only non-secret diagnostics.
- File tools refuse paths outside the workspace (`PATH_OUTSIDE_WORKSPACE`).
  **`run_command`, the desktop and the browser are not limited by the workspace.**
- Computer Use is off by default and can be enabled only from the local GUI. The GUI
  switch is an operational guard, **not a sandbox**.
- The random tunnel hostname hides the service; it does not lock it. Close the tunnel
  when you are done and keep the workspace narrow.

Full details: [SECURITY.md](SECURITY.md).

## Responsible use

- Run it only on a machine you own, during sessions you are supervising.
- Game input and background control are intended for local, single-player automation.
  Many online games and services prohibit automated input and may ban accounts; check
  their terms first.
- Treat the AI's actions as your own: confirm payments, deletions and outgoing messages
  yourself.

## Development

```bash
git clone <repo-url>
cd OpenBridge
python -m unittest discover -p "test_*.py"   # 400+ tests, offline, no GUI needed
pythonw gui.py
```

`smoke_*.py` are manual real-machine checks (real GUI / browser / Blender) and are
deliberately excluded from discovery.

```bash
python make_release.py --check-only   # audit only (credentials, denied files, imports)
python make_release.py                # build dist/OpenBridge-<version>.zip
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Most detailed design notes are currently in
Chinese: [GETTING_STARTED.md](GETTING_STARTED.md), [COMPUTER_USE.md](COMPUTER_USE.md),
[BLENDER_MCP.md](BLENDER_MCP.md), [GAME_CONTROL.md](GAME_CONTROL.md),
[BACKGROUND_CONTROL.md](BACKGROUND_CONTROL.md), [CROSS_CHAT.md](CROSS_CHAT.md),
[RELIABILITY.md](RELIABILITY.md).

## License

MIT — see [LICENSE](LICENSE). Third-party adapters run as separate processes under
their own licenses (MIT / Apache-2.0); see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
OpenBridge is an independent project and is not affiliated with Arena, OpenAI,
Anthropic, Cloudflare or Microsoft.
