# Third-party notices

This integration runs the following unmodified upstream packages as separate processes:

- CursorTouch Windows-MCP 0.8.5: https://github.com/CursorTouch/Windows-MCP
  MIT License, copyright (c) 2025 JEOMON GEORGE. License is included in its installed distribution; upstream reference: https://github.com/CursorTouch/Windows-MCP/blob/main/LICENSE.md
- Microsoft Playwright MCP 0.0.81: https://github.com/microsoft/playwright-mcp
  Apache License 2.0. License/notice files are included in its npm distribution; upstream reference: https://github.com/microsoft/playwright-mcp/blob/main/LICENSE

Blender integration additionally runs `mcp-for-blender==2.0.0` (ahujasid, MIT), https://github.com/ahujasid/blender-mcp . Its bundled addon and installed package retain their upstream notices. Blender itself is distributed separately by Blender Foundation under GPL-3.0-or-later.

Transitive dependencies retain their individual licenses. Installing these packages does not grant rights to bypass licenses of applications being automated. No proprietary Bridge code is copied by this adapter.
