# terminal art club: the tac plugin

Use the Claude capacity you'd otherwise let expire to have Claude make a looping terminal-art
animation, then share it in the Terminal Art Club community gallery. You need no git and no GitHub.

```
install plugin → /tac:login (once) → /tac:create → /tac:play → /tac:submit
```

## For contributors

**Install** (needs Claude Code, `uv` and Python 3.10+; `ffmpeg` only if you want mp4 reels):

```bash
claude plugin marketplace add terminalartclub/tac   # or a local checkout: ./tac-community
claude plugin install tac@terminalartclub
```

**Make and share a piece** from any project directory. Work lands in `./tac-work/<name>/`.

| command | what it does |
|---|---|
| `/tac:login` | Device-code login to the TAC platform in your browser. The token goes in `~/.config/tac/credentials.json` (mode 600). It never touches your Claude credentials. |
| `/tac:create [--sketch] [idea]` | Claude sketches 3 concepts, iterates on the virtual screen (renders, looks, critiques) and finishes `<name>.py` + `notes.md`. |
| `/tac:play <name>` | Prints the `tac play …` command to watch it live in your terminal, and opens `tac-work/index.html`, a local review page. |
| `/tac:submit <name>` | Assembles `tac-work/<name>/submission/`, lints it locally (rejects never leave your machine), asks you to confirm you have the right to share it and that it copies no one else's characters, brands or logos, then uploads it and polls until the platform has rendered and reviewed it. |
| `/tac:mine` | Your submitted pieces: status, total and 7-day views, a 28-day sparkline, the critique, and the reasons for any rejection. View totals are public on the site (anonymous, one per IP per piece per day). Unpublishing or deleting your account happens on the web only (terminalart.club/me). |
| `/tac:logout` | Deletes the token. |
| `/tac:style` | Creates/opens `~/.config/tac/style.md` (optional standing taste; `tacctl style`). |

**Steering is optional.** Give an idea (`/tac:create rain on a skylight`), pick one of the 3 concepts, or
drop notes like "too busy" or "warmer" between iterations. Or do none of it: with no input, Claude picks.
Your inputs are logged under `## direction` in `notes.md`, and the gallery credit is computed from that log,
so it can't be declared:

| `human_role` | when | credit |
|---|---|---|
| `none` | no input | "made by Claude Opus 5.5 for @you" |
| `seeded` | only an idea / seed / theme | "from an idea by @you · made by Claude Opus 5.5" |
| `directed` | picked a concept and/or gave ≥1 note | "directed by @you · made by Claude Opus 5.5" |

**Standing style (optional).** Put your lasting taste in `~/.config/tac/style.md`: palette, subjects you
love, things to avoid. `/tac:style` (`tacctl style`) creates it from a short commented template and opens it.
- Every `/tac:create` reads it; with no file, nothing changes.
- A per-run idea or note wins over it when they conflict.
- TAC's DNA rules win over both: no franchise IP, real-world scale, and so on.
- Its use is logged under `## direction` with the file's first line. It doesn't affect `human_role`: a style
  file alone is still `none`.

**Sketch vs full.** `/tac:create --sketch` is at most 3 iterations, with the same quality bar. It's recorded as `size: sketch`.
Before a run starts, `tacctl fit` checks the spare weekly window (when the usage cache exists). If the run
won't fit, Claude warns you and doesn't start unless you override with `--force`.

### Optional: the spare-capacity nudge

A SessionStart hook prints one line only when your weekly window resets within 24 h and is under 80% used:

```
tac: room for a piece before your weekly reset (~58% used, resets in 11h, fits ~2 full pieces). Make something for the wall: /tac:create
tac: room for a sketch before your weekly reset (~79% used, resets in 1h, fits a sketch). Make something for the wall: /tac:create --sketch
```

It reads `~/.cache/tac/usage.json`, which only an **opt-in** statusline helper writes. The helper
wraps your existing statusline rather than replacing it. In `~/.claude/settings.json`:

```json
"statusLine": {
  "type": "command",
  "command": "python3 ~/.claude/plugins/data/tac-terminalartclub/statusline_cache.py -- <your current statusline command>"
}
```

The hook copies the helper to that path (`${CLAUDE_PLUGIN_DATA}`, which survives plugin updates) only
if no copy is there. It never overwrites your copy: when a plugin update ships a different helper, the hook
prints a one-line notice and you copy it over yourself after a look. With nothing after `--` the statusline prints nothing. `rate_limits` only exists for claude.ai
Pro/Max, after the session's first response. Without the cache, the hook stays silent.

**Calibrate `piece_pct`**: the share of your weekly window one full piece costs. The default is **15**
(conservative). The house pieces took 0.27–0.53 M tokens each (subagent totals). A sketch is costed at 40% of
a piece. After your first measured run (note the window % before and after), set:

```bash
mkdir -p ~/.config/tac && echo '{"piece_pct": 6}' > ~/.config/tac/config.json   # or: export TAC_PIECE_PCT=6
```

A Max plan typically needs a much smaller value than Pro. Measure, don't guess.

### Token counts are honest

`tokens` in the meta is an int or `null` ("unknown"). `/tac:submit` uses your number if you give one
(`tokens_source: user`). Otherwise `--estimate-tokens` sums input + cache-write + output tokens from this
project's Claude Code transcripts since the work dir was created (`transcript-estimate`; cache reads
excluded). `/usage` has no machine-readable output, so nothing is read from it.

## Repo layout

```
.claude-plugin/marketplace.json     marketplace "terminalartclub" → plugins/tac-studio (plugin name "tac")
plugins/tac-studio/
  .claude-plugin/plugin.json
  bin/tac  bin/tacctl               launchers: uv run --no-project, rich/Pillow/fonttools pinned
  lib/vscreen.py                    bundled virtual screen (copy of TAC studio/vscreen.py + Linux fonts)
  lib/check_piece.py                lint (stdlib only)       ← tools/check_piece.py runs this
  lib/render_piece.py               preview/og/stats/process ← tools/render_piece.py runs this
  lib/tacctl.py  meta.py  notes.py  review.py
  skills/tac-studio/SKILL.md, DNA.md
  commands/{create,play,login,logout,submit}.md
  hooks/hooks.json  scripts/nudge.py  scripts/statusline_cache.py
pieces/<handle>/<slug>/             piece.py · meta.yaml · notes.md · process/≤4 PNG (≤600 KB each)
tools/                              check_piece · render_piece · build_site · import_seeds
curation.json                       week, theme, picks
.github/workflows/                  publish · tests
platform/                           the submissions API (separate component, own README)
```

The plugin refers to its own files only through `${CLAUDE_PLUGIN_ROOT}`, which Claude Code substitutes
in skill, command and hook content. Persistent files go under `${CLAUDE_PLUGIN_DATA}`
(<https://code.claude.com/docs/en/plugins/components.md>, "Path variables and persistent data").

## CLI contract (the platform and CI depend on this)

```
python tools/check_piece.py <piece_dir>
    stdout: {"ok": bool, "reasons": [str]} · exit 0 ok, 1 rejected
python tools/render_piece.py <piece_dir> --out <dir> [--timeout 120]
    writes preview.webp (540x960, native fps, q70), og.jpg, stats.json, process/NN.webp
    exit 0 ok · non-zero on failure · 124 = exceeded the wall-clock timeout (default 120 s)
```

- `<piece_dir>` holds `piece.py` and `meta.yaml` or `meta.json` (yaml wins if both exist). `notes.md` and `process/*.png` are optional.
- `render_piece` streams: capture, rasterize, downscale and encode one frame at a time, so peak RSS is ~160 MB whatever the loop length (wake, 480 frames: 2.38 GB → 160 MB, byte-identical output).
- `render_piece` needs `rich`, `Pillow` and `fonttools`: it uses the current interpreter if they import, else `uv run --with …`.
- Fonts: Menlo on macOS, or `fonts-dejavu-core` on Linux.
- `stats.json` carries `motion_median`, `seam` (`CLEAN|CHECK|JUMP`), `void`, `loop_s`, `fps`, `frames`, and `raw` (vscreen's stats).

**meta** (flat `key: value` YAML, every value a YAML scalar or JSON flow list):

| key | rule |
|---|---|
| `title` | required, ≤80 chars |
| `model` | required, `claude-…`, e.g. `claude-opus-5-5` |
| `handle` | in `pieces/<handle>/`, must match the folder; the platform takes it from the token |
| `description` | ≤400 chars |
| `tokens` | whole number 0–2,000,000, or `null`; the platform flags > 1,000,000 for review |
| `tokens_source` | `user`, `transcript-estimate`, `subagent-total` or `unknown` |
| `iterations` | int |
| `loop_s` | number of seconds, 0–600 |
| `license` | default `CC-BY-4.0 art / MIT code` |
| `created` | `YYYY-MM-DD` |
| `size` | `sketch` or `full`; a sketch is ≤3 iterations |
| `human_role` | `none`, `seeded` or `directed`; must match notes.md `## direction` |
| `house_artist` | bool |
| `process_notes` | ≤4 strings, one caption per process PNG, in order |

**check_piece is lint, not a sandbox.** It runs a static AST allowlist:

- Imports allowed: `math`, `random`, `colorsys`, `functools`, `itertools`, `bisect`, `cmath`, `rich.*` (common submodules), `sys` (only `sys.modules['_tac_*']`) and `types` (only `ModuleType` / `SimpleNamespace`).
- Rejected:
  - `open`, `exec`, `eval`, `compile`, `__import__`, `globals`, `vars`
  - `getattr`/`hasattr` anywhere except a direct call with a literal public name; no aliasing, indirection, passing as an argument or shadowing
  - `type`, `object`, `super`, `dir`, `setattr`, `delattr`
  - classes with bases, metaclasses or decorators, and dunder methods other than `__init__`
  - any dunder or `_private` attribute
  - dunder strings
  - file/process attributes (`.os`, `save_html`, `from_path`, …) and any attribute named like a top-level module (`.sys`, `.threading`, `.re`, …; only `.random` is exempt)
  - top-level `while True`
- Size and layout: >1500 lines is rejected, as are bad process images and unexpected files.

Rendering still executes the piece, so CI and the platform run it with no secrets, a read-only
token and a 120 s wall clock.

This repo is the plugin's install source, so it takes no outside PRs. Community pieces go through the
platform upload (`/tac:submit`) only. `pieces/` holds the house pieces, and `publish.yml` renders them on
push to `main`.

### Open TODOs

- **Network-isolate renders.** Uploaded piece code renders on the platform with network. Wrap the
  render step in `unshare -n` or a `--network none` container.
- **Runtime builtins.** vscreen `exec`s pieces with full builtins. Pass a minimal `__builtins__` with a
  gated `__import__` as defence in depth. This is not a sandbox; isolation is the real control.
- **Deploy.** `publish.yml` uploads `build/` as an artifact; pushing it to the gallery host is not built yet.

## Site data

`python tools/build_site.py --render` renders stale pieces and writes `build/community.json`. All its paths are
relative to `build/`; `piece.py`, `preview.webp`, `og.jpg` and `process/NN.webp` are copied there.
Each piece carries `human_role` and `size`, in addition to the original contract fields.

## Seeds

The 10 house-artist pieces (`studio-opus`, `studio-fable`) are openly labelled AI house artists
(`house_artist: true`, `human_role: none`), imported by `tools/import_seeds.py` from Terminal Art Club's
studio. Their token counts are subagent totals including revision passes.

## Development

```bash
uv run --with pytest --with rich --with Pillow --with fonttools python3 -m pytest -q
claude plugin validate . && claude plugin validate ./plugins/tac-studio
```
