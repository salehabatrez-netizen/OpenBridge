@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>&1 || (echo Install uv first. & exit /b 1)
if not exist ".computer-use\blender\Scripts\python.exe" uv venv --python 3.11 .computer-use/blender
if errorlevel 1 exit /b 1
uv pip install --python .computer-use/blender/Scripts/python.exe mcp-for-blender==2.0.0
if errorlevel 1 exit /b 1
.computer-use\blender\Scripts\mcp-for-blender.exe install-addon --addons-dir .computer-use/blender-addon
if errorlevel 1 exit /b 1
echo Ready. Run launch_blender_mcp.cmd after installing official Blender.
pause
