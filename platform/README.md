# spare cycles platform API

The gallery backend for Terminal Art Club community pieces. The `tac-studio` plugin logs in with a device code and uploads a piece. Each upload goes through a static check, a render, optional automod and human review, and is then published to `community.json`.

```
plugin ──POST /v1/submissions──▶ queued ──▶ rendering ──▶ rejected
                                                │
               tools/check_piece.py ◀───────────┤ (sandboxed subprocess, scrubbed env, rlimits)
               tools/render_piece.py ◀──────────┤
               Claude automod (if key) ◀────────┘
                                                ▼
                         in_review ──admin approve──▶ published ──3 reports──▶ hidden
                             │  (trusted + clean automod: auto-publish)          │
                             └──admin reject──▶ rejected        admin unhide ◀───┘ / delete ▶ rejected
```

## Run locally

```bash
cd platform
uv sync
TAC_ADMIN_TOKEN=pick-a-local-value uv run tac-platform        # http://127.0.0.1:8790
# or: uv run uvicorn tac_platform.app:create_app --factory --port 8790
```

- Admin queue: open `http://127.0.0.1:8790/admin/login?token=<TAC_ADMIN_TOKEN>` once (it sets a cookie), then use `/admin`.
- Device login (dev mode): the plugin prints a code. Open `/device`, enter the code and pick a handle.
- State: `data/tac.sqlite3` and `data/submissions/...` (private), plus `data/public/...`, served at `/media/...`.
- Reset: stop the server and `rm -rf data/`.

## Tests

```bash
uv run pytest -q                                   # unit + in-process pipeline (stand-in tools)
TAC_ADMIN_TOKEN=t uv run tac-platform &            # then, against the live server + real tools/:
TAC_E2E_URL=http://127.0.0.1:8790 TAC_E2E_ADMIN_TOKEN=t uv run pytest tests/test_e2e_live.py -s
```

- The unit tests use `tests/fixtures/tools/` (stand-ins with the same CLI contract), so they don't depend on `tools/`.
- The e2e test submits `terminal-art-club/studio/work/laps` (override with `TAC_E2E_PIECE`).

## Endpoints

| method | path | auth | notes |
|---|---|---|---|
| POST | `/v1/auth/device` | none | `{device_code, user_code, verification_uri, interval: 3, expires_in: 600}`; 30/h per IP |
| POST | `/v1/auth/token` | none | `{device_code}`. 428 pending, 200 `{access_token, handle}`, 410 expired/used, 400 unknown |
| GET/POST | `/device` | none | dev: code + handle form (10 tries / 10 min per IP); github: code, then OAuth |
| GET | `/v1/me` | Bearer | `{handle}` |
| POST | `/v1/submissions` | Bearer | multipart `piece`, `meta`, `notes?`, `process[≤4]`. 202 `{id, status, url}` |
| GET | `/v1/submissions/{id}` | Bearer, owner | `{id, status, reasons, preview_url, critique}`; others get 404 |
| GET | `/v1/submissions/{id}/preview.webp` | signed URL | pre-publish preview (HMAC, 7-day expiry) |
| GET | `/v1/community.json` | none | gallery feed (CORS `*`) |
| GET | `/media/{handle}/{slug}/...` | none | preview.webp, og.jpg, piece.py, process/NN.webp |
| POST | `/v1/pieces/{handle}/{slug}/report` | none | `{reason}`; 5/h per IP; 3 distinct IPs hide the piece |
| POST | `/v1/pieces/{handle}/{slug}/view` | none | private view count; 204 always (see below) |
| GET | `/v1/me/pieces` | Bearer | own pieces: `{pieces: [{id: "handle/slug", slug, title, status (+ "hidden"), views_total, views_7d, views_28d[28 ints, oldest→newest, last = today UTC], url}]}` (pinned with the plugin) |
| GET | `/admin`, `/admin/login?token=` | admin | HTML queue: in review, hidden, published, audit log |
| GET | `/v1/admin/queue` | admin | JSON version of the queue |
| POST | `/v1/admin/submissions/{id}/approve` · `/reject {reason}` | admin | from `in_review` only (409 otherwise) |
| POST | `/v1/admin/pieces/{handle}/{slug}/unhide` · `/delete {reason}` | admin | delete makes the piece `rejected` and removes its media |
| POST | `/v1/admin/users/{handle}/trust {trusted}` | admin | trusted + clean automod means auto-publish |

Errors are always `{"error": "<code>", "detail"?: ...}`. Admin auth is the `X-Admin-Token` header or the `tac_admin` cookie. Cookie-authenticated POSTs also need `X-TAC-Admin-CSRF: 1`.

## Submission meta

`meta` is a JSON string.
- Required: `title` (≤ 80), `model` (`claude-…` id).
- Optional: `description` (≤ 400), `tokens`, `iterations`, `loop_s`, `license`, `process_notes` (≤ 4 strings, one per process image), `human_role` (`none` default | `seeded` | `directed`, i.e. how much the person steered the piece).
- Unknown keys are ignored.
- `human_role` is passed through to `community.json` and shown in `/admin`.

## View counts (private, v0)

```
site ──POST /v1/pieces/h/s/view──▶ day_salt(UTC day) ─▶ hash = sha256(salt|ip)
        (no cookie, no body)           │                   │
                                       │   rate 120/h per hash ── over → 204, dropped
                                       ▼
                     published && !hidden ? INSERT OR IGNORE views(piece, day, hash)
                                              └─ new row → view_days(piece, day) += 1
        ◀── 204 always (unknown / hidden / duplicate / limited all look the same)
```

- One view per piece, per IP, per UTC day.
- Counts are private:
  - only the owner sees them (`GET /v1/me/pieces`: total, last 7 days, a 28-day series from oldest to newest);
  - admins see `views_7d` in the queue;
  - nothing goes into `community.json`, and there are no rankings.
- CORS: `Access-Control-Allow-Origin` is echoed only for `TAC_SITE_ORIGINS`.
- Retention: per-hash `views` rows are purged after 30 days (hourly job); the `view_days` rollup is kept.

## Limits

- `piece.py` ≤ 200 KB, UTF-8.
- Process images: ≤ 4 PNGs, ≤ 600 KB each (magic bytes checked).
- `notes` ≤ 64 KB.
- Whole request ≤ 3 MB, enforced on Content-Length and on the streamed bytes. Other routes are capped at 64 KB.
- 3 submissions per handle per rolling 24 h. The limit check and the insert are one SQL statement.
- Render: 240 s wall clock, 180 s CPU, 3 GB address space, 256 fds, 200 MB max file size. Each render output must be ≤ 25 MB.

## Environment

| var | default | meaning |
|---|---|---|
| `TAC_HOST` / `TAC_PORT` | `127.0.0.1` / `8790` | bind |
| `TAC_PUBLIC_BASE_URL` | `http://127.0.0.1:8790` | used in `url`, `preview_url`, `verification_uri` |
| `TAC_DATA_DIR` | `platform/data` | SQLite + media root |
| `TAC_DB_PATH` | `$TAC_DATA_DIR/tac.sqlite3` | |
| `TAC_ENV` | `dev` | `prod` refuses to start unless `TAC_RENDERER` is an implemented isolated backend (none yet) |
| `TAC_RENDERER` | `local` | `local` = `run_limited` subprocess (not a sandbox; dev only) |
| `TAC_AUTH` | `dev` | `dev` (handle form) or `github` (OAuth; see TODOs in `auth.py`) |
| `TAC_GITHUB_CLIENT_ID` / `TAC_GITHUB_CLIENT_SECRET` | | github mode only |
| `TAC_ADMIN_TOKEN` | unset | admin disabled (403) when unset |
| `TAC_TOOLS_DIR` | `<repo>/tools` | where `check_piece.py` / `render_piece.py` live |
| `TAC_TOOLS_PYTHON` | this venv's python | interpreter for the tools (needs rich, Pillow, fonttools) |
| `TAC_RENDER_TIMEOUT_S` | `240` | wall clock per render |
| `TAC_RENDER_CONCURRENCY` | `1` | parallel renders (each is ~1 CPU-bound core) |
| `TAC_THEMES_FILE` | `platform/themes.json` | `{"2026-W40": {"title", "blurb"}}`, upserted at startup |
| `ANTHROPIC_API_KEY` | unset | enables automod; never passed to renders |
| `TAC_AUTOMOD_MODEL` / `TAC_AUTOMOD_EFFORT` | `claude-opus-5-5` / `low` | |
| `TAC_SITE_ORIGINS` | `http://localhost:5181,https://terminalart.club` | origins allowed to POST views cross-origin |
| `TAC_TRUST_PROXY` | `0` | `1` = take the client IP from `Fly-Client-IP` / `X-Forwarded-For` (only behind Fly's proxy) |
| `TAC_WORKER` | `1` | `0` = don't start the in-process pipeline worker |

## Layout

```
src/tac_platform/
  app.py          wiring, body-size limit, error shape, /media
  auth.py         device code, bearer tokens, /device (dev form + GitHub OAuth path)
  submissions.py  upload + owner status + signed preview
  pipeline.py     worker: claim -> check -> render -> automod -> decide
  sandbox.py      subprocess with scrubbed env + rlimits (NOT a real sandbox, see SECURITY.md)
  automod.py      Claude structured-output moderation + critique
  publish.py      state transitions, public media copy, community.json
  moderation.py   public reports
  admin.py        /admin HTML + /v1/admin JSON
  storage.py      MediaStore interface + LocalStore (S3/Tigris swap point)
  db.py           SQLite schema + tiny async wrapper (Postgres swap point)
```
