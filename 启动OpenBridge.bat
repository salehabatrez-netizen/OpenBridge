@echo off
chcp 65001 > nul
title OpenBridge 本地 MCP 免费桥接服务
cd /d "%~dp0"
echo 正在启动 OpenBridge 本地 MCP 服务...
python bridge.py
pause
