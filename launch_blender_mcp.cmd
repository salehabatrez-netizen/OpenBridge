@echo off
cd /d "%~dp0"
python launch_blender_mcp.py
if errorlevel 1 pause
