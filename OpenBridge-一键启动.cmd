@echo off
chcp 65001 > nul
title OpenBridge - 一键启动
cd /d "%~dp0"

echo(
echo   OpenBridge - 本地免费 MCP 桥接控制台
echo   ======================================
echo(

REM ---- Preflight: Python present? -------------------------------------
where pythonw >nul 2>&1
if errorlevel 1 goto NOPYTHON
where python  >nul 2>&1
if errorlevel 1 goto NOPYTHON

REM ---- Preflight: version >= 3.11 -------------------------------------
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 (
    echo   [X] 需要 Python 3.11 或更高版本。当前版本：
    python -c "import sys; print('       ' + sys.version)"
    echo(
    echo   请从 https://www.python.org/downloads/ 安装新版本，
    echo   安装时务必勾选 "Add Python to PATH"。
    echo(
    pause
    exit /b 1
)

REM ---- Preflight: tkinter available? ----------------------------------
python -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo   [X] 缺少 tkinter 图形库。
    echo       Windows 请重新运行 Python 安装程序，
    echo       勾选 "tcl/tk and IDLE" 组件后修复安装。
    echo(
    pause
    exit /b 1
)

echo   [OK] 环境检查通过，正在打开控制台窗口...
echo(
echo   接下来请在窗口中：
echo     1. 选择工作区文件夹
echo     2. 点击 [启动 Bridge]
echo     3. 等待“公网已验证”后点击 [复制开工提示词]
echo(
echo   安全提醒：复制出来的地址等同于本机密码，请勿公开分享。
echo(

start "" pythonw gui.py
timeout /t 3 >nul
exit /b 0

:NOPYTHON
echo   [X] 未检测到 Python。
echo(
echo   请先安装 Python 3.11 或更高版本：
echo       https://www.python.org/downloads/
echo(
echo   安装时务必勾选 "Add Python to PATH"，
echo   然后重新运行本文件。
echo(
pause
exit /b 1
