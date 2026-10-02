# Contributing to OpenBridge

## Ground rules

1. **Never commit a live MCP URL.** `current_mcp_url.json` is git-ignored. The
   secret path in that URL is a credential — see [SECURITY.md](SECURITY.md).
   Use obvious placeholders in tests, e.g. `https://xxxx.trycloudflare.com/mcp/<secret>`.
2. **Keep the core dependency-free.** Every module in the core must import only
   the Python 3.11+ standard library or a local package. Optional adapters
   (Windows-MCP, Playwright, Blender) live in their own virtualenvs and are
   launched as separate processes.
3. **Read before you edit.** Open the surrounding code first; make small,
   targeted patches that preserve existing formatting and comments.

## Development workflow

```bash
# run the whole suite (324 tests, no network or GUI required)
python -m unittest discover -p "test_*.py"

# run one area
python -m unittest -v test_connection_regression test_computer_use
```

`smoke_*.py` scripts are **manual** checks that need a real GUI, browser, or
tunnel. They are intentionally excluded from `unittest discover`.

Reloading matters: editing Python does not affect a running GUI. Stop the
bridge, close the GUI window completely, then relaunch. Confirm your build is
live via `/health` → `transport_revision`, not by checking the file on disk.

## Before opening a pull request

- [ ] `python -m unittest discover -p "test_*.py"` passes.
- [ ] `python make_release.py --check-only` passes (this is the secret scanner).
- [ ] No new third-party runtime dependency in the core.
- [ ] Docs updated if behaviour or tool surface changed.

## Building a release

```bash
python make_release.py            # -> dist/OpenBridge-<version>.zip
```

The builder refuses to produce an archive if it detects a live tunnel hostname
or an unrecognised 32-hex string in any file it is about to ship. If it stops
with `ERROR: possible secret(s) would be published`, that is the safety net
working — remove the secret rather than adding it to the allowlist, unless you
are certain it is a dummy fixture.

## Reporting bugs

Include OS, Python version, the `transport_revision` from `/health`, and the
relevant log excerpt. **Redact your tunnel hostname and secret path.**
