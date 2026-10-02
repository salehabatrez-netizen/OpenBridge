"""Run inside a NEW Blender GUI via launch_blender_mcp.py. Never clears or saves a scene."""
import os
from pathlib import Path
import bpy

root = Path(__file__).resolve().parent
addon = root / '.computer-use/blender-addon/blender_mcp.py'
if not addon.is_file():
    raise RuntimeError('Run install_blender_mcp.cmd first')
if 'blender_mcp' not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_install(filepath=str(addon), overwrite=False)
    bpy.ops.preferences.addon_enable(module='blender_mcp')
prefs = bpy.context.preferences.addons['blender_mcp'].preferences
if hasattr(prefs, 'telemetry_consent'):
    prefs.telemetry_consent = False
bpy.ops.wm.save_userpref()
bpy.context.scene.blendermcp_port = 9876
bpy.ops.blendermcp.start_server()
server = getattr(bpy.types, 'blendermcp_server', None)
if not server or not server.running:
    raise RuntimeError('Blender MCP could not start (port 9876 may already be occupied)')
if server.host not in ('localhost', '127.0.0.1', '::1'):
    server.stop()
    raise RuntimeError('Refusing non-loopback Blender listener')
print('OPENBRIDGE BLENDER READY: loopback:9876; telemetry off; no scene overwritten',flush=True)
