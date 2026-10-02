# Security Policy

## The MCP URL is a credential

OpenBridge exposes a local HTTP server through a Cloudflare Quick Tunnel. Access is
gated by a random secret path, e.g.

```
https://<random>.trycloudflare.com/mcp/<32-hex-secret>
```

**Anyone holding that full URL can read and write every file in your workspace and,
if Computer Use is enabled, control your desktop.** Treat it exactly like a password:

- Never paste it into a public issue, screenshot, commit, or chat log you will share.
- `current_mcp_url.json` is git-ignored by default. Keep it that way.
- The URL rotates whenever the tunnel restarts. An old URL becoming invalid is
  expected behaviour, not a bug.

## Trust boundary

- The bridge serves a **stateless** HTTP MCP endpoint. Any client presenting the
  secret path is fully authorised; there is no per-client identity or audit trail.
- All connected chats share one workspace, one desktop, and one application state.
  They are not isolated sandboxes.
- Computer Use is **off by default** and can only be enabled from the local GUI.
  A remote client cannot grant itself permission. This is deliberate.
- The GUI kill switch stops the adapter processes. It is *not* an OS-level sandbox:
  while enabled, desktop automation can reach anything the logged-in user can reach.

## Recommended usage

- Run it only on a machine you own, for sessions you are actively supervising.
- Close the tunnel when you are done.
- Do not run it on a machine holding credentials you cannot afford to expose.

## Reporting a vulnerability

Open a GitHub issue describing the problem. **Do not include your MCP URL, tunnel
hostname, or any secret path** in the report. If a report requires sensitive detail,
say so in the issue and request a private channel first.
