# Security

## Reporting a vulnerability

Use GitHub private vulnerability reporting on this repository: **Security → Report a vulnerability**.
Don't open a public issue or PR for a security bug. The platform has its own notes in
[`platform/SECURITY.md`](platform/SECURITY.md).

This repo is the plugin's install source, so it takes no outside PRs. Community pieces reach the
gallery only through the platform upload (`/tac:submit`).

## What the plugin runs on your machine

| what | when | does |
|---|---|---|
| `scripts/nudge.py` | SessionStart hook (`startup`), 5 s timeout | Reads `~/.cache/tac/usage.json` and `~/.config/tac/config.json`, and may print one line. Copies `statusline_cache.py` into `${CLAUDE_PLUGIN_DATA}` only if no copy is there, and never overwrites one. **No network calls, no subprocesses.** |
| `scripts/session_env.py` | SessionStart hook (every source), 5 s timeout | Reads the hook's stdin JSON and, if `session_id` is a plain `[A-Za-z0-9-]` id, appends `export TAC_SESSION_ID=<id>` to `$CLAUDE_ENV_FILE`, so `tacctl` can attribute token estimates to the sessions that built a piece. Prints nothing. **No network calls, no subprocesses.** |
| `bin/tac` | when you or Claude run it (`/tac:create`, `/tac:play`) | `uv run --no-project` with pinned `rich`, `Pillow`, `fonttools`; runs `lib/vscreen.py`, which executes the piece being made. |
| `bin/tacctl` | when you or Claude run it (`/tac:login`, `/tac:submit`, `/tac:mine`, `tacctl gallery`) | Same pinned `uv run --no-project`; talks to the TAC platform at `TAC_API`. That must be https unless it is localhost. It opens the browser only for https pages on that same host, and strips control characters from every server string before printing it. |
| `scripts/statusline_cache.py` | only if you point your `statusLine` at its copy | Caches `rate_limits.seven_day`, then runs your own statusline command (from your settings) through a shell. |

Nothing runs at install time itself; the two hooks run at each session start.

## Incident plan

1. Fix on `main` and ship a release.
2. Bump `version` in `plugins/tac-studio/.claude-plugin/plugin.json`. Users get a new copy only when the
   version changes, so a fix pushed without a bump never reaches them.
3. If the plugin must be pulled, remove its entry from `.claude-plugin/marketplace.json`, map the old
   name in a top-level `renames` map (`"tac": null`), and set `"forceRemoveDeletedPlugins": true` so
   Claude Code uninstalls it at the next session start. Use `renames` for a rename, too. Treat it as
   append-only history (<https://code.claude.com/docs/en/plugins/host-marketplace>, "Rename or remove a plugin").
4. Rotate anything the bug exposed. Device access tokens have no expiry or revoke endpoint yet
   (`platform/SECURITY.md`), so a token leak means deleting the token rows server-side; users then
   re-run `/tac:login`.
5. Publish a GitHub security advisory with the affected and fixed versions.
