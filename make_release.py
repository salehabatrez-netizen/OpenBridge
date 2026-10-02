#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_release.py — build a clean, shareable OpenBridge one-click package.

Produces  dist/OpenBridge-<version>.zip  containing only the files a stranger
needs, with every secret and machine-specific artefact excluded.

Why this script exists rather than "just zip the folder":
  The working directory accumulates 170+ scratch probe scripts, a live tunnel
  credential (current_mcp_url.json), a 40k-file mc_agent tree, and unrelated
  third-party projects. Zipping it naively would leak the credential and ship
  ~1 GB of noise.

Safety guarantees, all enforced at build time and verified after packing:
  1. DENY list is checked against the final archive, not just the copy step.
  2. The archive is re-scanned for live credential patterns; a hit aborts the
     build with a non-zero exit code.
  3. Nothing is deleted or modified in the source tree — read-only operation.

Usage:
    python make_release.py                 # build into dist/
    python make_release.py --outdir X      # custom output directory
    python make_release.py --check-only    # audit without writing the zip
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import sys
import zipfile
from datetime import datetime, timezone

VERSION = "2.0.0"

# ── What ships ────────────────────────────────────────────────────────────────
CORE_FILES = [
    "bridge.py",
    "gui.py",
    "computer_use.py",
    "connection_health.py",
    "connection_probe.py",
    "floating_status.py",
    "handoff.py",
    "handoff_cli.py",
    "registry.py",
    "local_cu_preferences.py",
    "blender_bootstrap.py",
    "launch_blender_mcp.py",
    "make_release.py",
    "ui_kit.py",          # imported by gui.py / floating_status.py
    "project_board.py",   # hot-loaded by floating_status.py
]

CORE_DIRS = [
    "game_control",
    "background_control",
]

DOCS = [
    "README.md",
    "GETTING_STARTED.md",
    "LICENSE",
    "SECURITY.md",
    "requirements.txt",
    "THIRD_PARTY_NOTICES.md",
    "CONTRIBUTING.md",
    "COMPUTER_USE.md",
    "BLENDER_MCP.md",
    "BACKGROUND_CONTROL.md",
    "GAME_CONTROL.md",
    "CROSS_CHAT.md",
    "RELIABILITY.md",
    ".gitignore",
    ".gitattributes",
]

LAUNCHERS = [
    "OpenBridge-一键启动.cmd",
    "启动OpenBridge.bat",
    "启动OpenBridge-GUI.bat",
    "install_computer_use.cmd",
    "install_blender_mcp.cmd",
    "launch_blender_mcp.cmd",
]

TEST_PREFIX = "test_"
SMOKE_PREFIX = "smoke_"

# ── What must never ship ──────────────────────────────────────────────────────
# Exact filenames that are credentials or machine state.
DENY_NAMES = {
    "current_mcp_url.json",
    "local_cu_prefs.json",
    ".env",
    ".env.local",
    "bridge.v1.backup.py",
    "test_skymission.py",   # tests a private scratch module (_skymission.py)
}

# Directory names excluded anywhere in the tree.
DENY_DIRS = {
    ".git", ".computer-use", ".playwright-mcp", ".input-lab-runs",
    "__pycache__", "node_modules", "logs", "test-artifacts",
    "HANDOFF", "mc_agent", "watch", "artworks", "_agent_tools",
    ".venv", "venv", "build", "dist", ".pytest_cache",
}

# Patterns that indicate a real leaked credential in the packed output.
# Test fixtures deliberately use obvious placeholders, which these tolerate.
LIVE_SECRET_PATTERNS = [
    # A real Quick Tunnel host: 3+ hyphenated words. Placeholders like
    # "xxxx.trycloudflare.com" or "a.trycloudflare.com" have too few parts.
    (re.compile(r"https://[a-z0-9]+(?:-[a-z0-9]+){2,}\.trycloudflare\.com"),
     "live trycloudflare hostname"),
]

# Known-safe fixtures: obvious dummies used by the test suite.
SECRET_ALLOWLIST = {
    "0123456789abcdef0123456789abcdef",
    "e4197451d0ddd573020087681c21d5e8",   # test_url_file.py fixture
    "00000000000000000000000000000000",
}

TEXT_EXT = {".py", ".md", ".txt", ".json", ".cmd", ".bat", ".cfg", ".toml", ".yml", ".yaml"}


def is_denied(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if any(p in DENY_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    if name in DENY_NAMES:
        return True
    # Scratch convention: _name.ext  (throwaway probe scripts and their output).
    # Dunder files are NOT scratch: __init__.py makes a directory an importable
    # package, and excluding it silently ships a broken distribution.
    if name.startswith("_") and not name.startswith("__"):
        return True
    # Pre-change copies kept for local rollback (e.g. gui.v2_before_apple.backup.py).
    if name.endswith((".backup.py", ".bak")):
        return True
    return False


def collect(src: str, include_tests: bool) -> list[str]:
    """Return repo-relative paths to include, in deterministic order."""
    out: list[str] = []

    def add(rel: str) -> None:
        if is_denied(rel):
            return
        if os.path.isfile(os.path.join(src, rel)) and rel not in out:
            out.append(rel)

    for f in CORE_FILES + DOCS + LAUNCHERS:
        add(f)

    for d in CORE_DIRS:
        base = os.path.join(src, d)
        if not os.path.isdir(base):
            continue
        for dp, dn, fn in os.walk(base):
            dn[:] = [x for x in dn if x not in DENY_DIRS]
            for f in sorted(fn):
                rel = os.path.relpath(os.path.join(dp, f), src).replace("\\", "/")
                add(rel)

    if include_tests:
        for f in sorted(os.listdir(src)):
            if f.startswith((TEST_PREFIX, SMOKE_PREFIX)) and f.endswith(".py"):
                add(f)

    return out


def unshipped_local_imports(src: str, rels: list[str]) -> list[str]:
    """Imports that resolve to a module in the source tree that is NOT shipped.

    A module that exists next to bridge.py but is missing from the package
    imports fine on the developer machine and breaks for everyone else (this
    happened with ui_kit.py / project_board.py). Covers `import x`,
    `from x import y` and importlib-by-filename ('x.py' string literals)."""
    import ast
    shipped = {os.path.splitext(r)[0].split("/")[0] for r in rels if r.endswith(".py")}
    shipped |= {r.split("/")[0] for r in rels if "/" in r}
    local = {os.path.splitext(n)[0] for n in os.listdir(src) if n.endswith(".py")}
    local |= {n for n in os.listdir(src) if os.path.isfile(os.path.join(src, n, "__init__.py"))}
    problems: list[str] = []
    for rel in rels:
        if not rel.endswith(".py"):
            continue
        try:
            tree = ast.parse(io.open(os.path.join(src, rel), encoding="utf-8-sig").read())
        except (OSError, SyntaxError) as exc:
            problems.append(f"{rel}: cannot parse ({exc})")
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            elif rel != "make_release.py" and isinstance(node, ast.Constant) \
                    and isinstance(node.value, str) and re.fullmatch(r"[A-Za-z_]\w*\.py", node.value):
                names = [node.value[:-3]]
            for name in names:
                top = name.split(".")[0]
                if top in local and top not in shipped:
                    problems.append(f"{rel}: imports '{top}', which is not in the package")
    return sorted(set(problems))


def scan_for_secrets(src: str, rels: list[str]) -> list[str]:
    """Return human-readable findings for anything that looks like a live secret."""
    findings: list[str] = []
    hex32 = re.compile(r"\b[0-9a-f]{32}\b")
    for rel in rels:
        p = os.path.join(src, rel)
        if os.path.splitext(rel)[1].lower() not in TEXT_EXT:
            continue
        try:
            text = io.open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for rx, label in LIVE_SECRET_PATTERNS:
            for hit in rx.findall(text):
                findings.append(f"{rel}: {label}: {hit}")
        for h in hex32.findall(text):
            if h not in SECRET_ALLOWLIST:
                findings.append(f"{rel}: unrecognised 32-hex string: {h}")
    return findings


QUICKSTART = """OpenBridge — 一键启动包 / One-click package
================================================

【中文】
1. 安装 Python 3.11 或更新版本，安装时务必勾选 "Add Python to PATH"。
   下载：https://www.python.org/downloads/
2. 双击 `启动OpenBridge-GUI.bat`
3. 在打开的窗口里点「启动服务」，复制显示出来的 MCP 地址。
4. 把该地址粘贴给支持 MCP 的 AI 客户端即可。

⚠️ 安全警告
   那条 MCP 地址等同于你这台电脑的密码。任何拿到它的人都能读写你的文件。
   不要截图、不要发到公开群、不要提交到仓库。关闭程序即失效。

   「Computer Use」（桌面/浏览器/Blender 控制）默认关闭，只能由你本机点按钮开启，
   远程客户端无法自行授权。开启后自动化可以触及你登录用户能触及的一切。

【English】
1. Install Python 3.11+ and tick "Add Python to PATH".
2. Double-click `启动OpenBridge-GUI.bat` (or run `pythonw gui.py`).
3. Click Start in the window, then copy the MCP URL it shows.
4. Paste that URL into any MCP-capable AI client.

⚠️ The MCP URL is a credential — it grants full workspace access.
   Never post it publicly. Computer Use is off by default and can only be
   enabled from the local GUI.

No pip install is required: the core is pure Python standard library.

想用 Computer Use（控制桌面/浏览器）或 Blender 建模？
  -> 打开 GETTING_STARTED.md，第 2、3 节有完整的安装与启用步骤。

Want desktop/browser control or Blender modelling?
  -> See GETTING_STARTED.md sections 2 and 3.

文档导航 / Docs: GETTING_STARTED.md (上手) · SECURITY.md (权限边界)
                 COMPUTER_USE.md (桌面/浏览器) · BLENDER_MCP.md (建模)
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the OpenBridge release package.")
    ap.add_argument("--src", default=".", help="source directory (default: cwd)")
    ap.add_argument("--outdir", default="dist", help="output directory")
    ap.add_argument("--no-tests", action="store_true", help="exclude the test suite")
    ap.add_argument("--check-only", action="store_true", help="audit only, write nothing")
    args = ap.parse_args()

    # Windows consoles default to a legacy codepage (GBK/cp936) that cannot
    # encode box-drawing or check characters. Force UTF-8 where supported and
    # keep all console output ASCII-only as a second line of defence.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    src = os.path.abspath(args.src)
    rels = collect(src, include_tests=not args.no_tests)

    print(f"OpenBridge {VERSION} - release builder")
    print(f"source : {src}")
    print(f"files  : {len(rels)}")

    missing = [f for f in CORE_FILES if not os.path.exists(os.path.join(src, f))]
    if missing:
        print("ERROR: missing core files: " + ", ".join(missing))
        return 2

    # Safety gate 1: nothing denied slipped through.
    leaked = [r for r in rels if is_denied(r)]
    if leaked:
        print("ERROR: denied paths in file list: " + ", ".join(leaked))
        return 3

    # Safety gate 1b: the package is self-contained.
    unshipped = unshipped_local_imports(src, rels)
    if unshipped:
        print(f"ERROR: {len(unshipped)} import(s) of local modules that would not ship:")
        for u in unshipped[:20]:
            print("   " + u)
        return 7

    # Safety gate 2: no live credentials in the content.
    findings = scan_for_secrets(src, rels)
    if findings:
        print(f"ERROR: {len(findings)} possible secret(s) would be published:")
        for f in findings[:20]:
            print("   " + f)
        return 4
    print("secrets: none detected [OK]")

    total = sum(os.path.getsize(os.path.join(src, r)) for r in rels)
    print(f"size   : {total/1024:.1f} KiB uncompressed")

    if args.check_only:
        print("check-only: no archive written")
        return 0

    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)
    zpath = os.path.join(outdir, f"OpenBridge-{VERSION}.zip")
    root = f"OpenBridge-{VERSION}"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in rels:
            z.write(os.path.join(src, rel), f"{root}/{rel}")
        z.writestr(f"{root}/快速开始-QUICKSTART.txt", QUICKSTART)
        z.writestr(f"{root}/BUILD_INFO.txt",
                   f"OpenBridge {VERSION}\nbuilt: {stamp}\nfiles: {len(rels) + 2}\n")

    # Safety gate 3: verify the archive we actually produced.
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        bad = [n for n in names
               if os.path.basename(n) in DENY_NAMES
               or any(part in DENY_DIRS for part in n.split("/")[1:-1])]
        if bad:
            os.remove(zpath)
            print("ERROR: archive contained denied entries, deleted: " + ", ".join(bad))
            return 5
        if z.testzip() is not None:
            print("ERROR: archive failed integrity check")
            return 6

    digest = hashlib.sha256(open(zpath, "rb").read()).hexdigest()
    print(f"\nBUILT: {zpath}")
    print(f"  entries : {len(names)}")
    print(f"  zip size: {os.path.getsize(zpath)/1024:.1f} KiB")
    print(f"  sha256  : {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
