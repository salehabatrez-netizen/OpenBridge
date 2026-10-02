"""Launch a separate Blender GUI with the community addon. No shell interpolation."""
import os
from pathlib import Path
import shutil
import socket
import subprocess

ROOT = Path(__file__).resolve().parent

def find_blender():
    configured = os.environ.get('BLENDER_EXE')
    candidates = [Path(configured)] if configured else []
    found = shutil.which('blender')
    if found:
        candidates.append(Path(found))
    base = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Blender Foundation'
    candidates.extend(sorted(base.glob('Blender */blender.exe'),reverse=True))
    return next((p for p in candidates if p.is_file()), None)

def main():
    exe=find_blender()
    if not exe:
        raise SystemExit('Blender not found. Install official Blender or set BLENDER_EXE to blender.exe')
    with socket.socket() as sock:
        sock.settimeout(1)
        if sock.connect_ex(('127.0.0.1',9876))==0:
            raise SystemExit('Port 9876 is already in use. Use the existing Blender instance or stop its MCP server first.')
    if not (ROOT/'.computer-use/blender-addon/blender_mcp.py').is_file():
        raise SystemExit('Addon missing: run install_blender_mcp.cmd')
    env=os.environ.copy()
    env.update({'BLENDER_MCP_DISABLE_TELEMETRY':'1','DISABLE_TELEMETRY':'1'})
    logdir=ROOT/'.computer-use'
    logdir.mkdir(exist_ok=True)
    with (logdir/'blender-startup.log').open('ab') as log:
        process=subprocess.Popen([str(exe),'--python',str(ROOT/'blender_bootstrap.py')],
            cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    print('Blender launched. PID=%d. Wait for the viewport, then enable Blender in OpenBridge.' % process.pid)

if __name__=='__main__':main()
