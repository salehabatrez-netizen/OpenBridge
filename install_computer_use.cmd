@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>&1 || (echo Install uv first. & exit /b 1)
where node >nul 2>&1 || (echo Install Node.js LTS first. & exit /b 1)
if not exist ".computer-use\windows\Scripts\python.exe" uv venv --python 3.13 .computer-use/windows
if errorlevel 1 exit /b 1
uv pip install --python .computer-use/windows/Scripts/python.exe windows-mcp==0.8.5
if errorlevel 1 exit /b 1
call npm install --prefix .computer-use/browser --save-exact @playwright/mcp@0.0.81
if errorlevel 1 exit /b 1
echo Installed. Restart the GUI, then explicitly enable the desired adapter.
pause
