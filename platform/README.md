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

## Isolated renders (Docker)

```bash
render-image/build.sh                                  # tac-render:local from plugins/tac-studio/lib
TAC_RENDERER=docker TAC_ADMIN_TOKEN=… uv run tac-platform
```

See SECURITY.md for the exact `docker run` flags.

## Tests

```bash
uv run pytest -q                                   # unit + in-process pipeline (stand-in tools)
TAC_ADMIN_TOKEN=t uv run tac-platform &            # then, against the live server + real tools/:
TAC_E2E_URL=http://127.0.0.1:8790 TAC_E2E_ADMIN_TOKEN=t uv run pytest tests/test_e2e_live.py -s
```

- The unit tests use `tests/fixtures/tools/` (stand-ins with the same CLI contract), so they don't depend on `tools/`.
- `tests/test_docker_renderer.py` needs Docker and the image, and skips otherwise.
- The e2e test submits `terminal-art-club/studio/work/laps` (override with `TAC_E2E_PIECE`).

## Endpoints

| method | path | auth | notes |
|---|---|---|---|
| POST | `/v1/auth/device` | none | `{device_code, user_code, verification_uri, interval: 3, expires_in: 600}`; 30/h per IP |
| POST | `/v1/auth/token` | none | `{device_code}`. 428 pending, 200 `{access_token, handle}`, 410 expired/used, 400 unknown |
| GET/POST | `/device` | none | dev: code + handle form (10 tries / 10 min per IP); github: code, then OAuth |
| GET | `/v1/auth/web/login?return=/path` | none | site sign-in. dev: handle form · github: OAuth redirect. Sets the session cookie (`__Host-tac_session` in prod, `tac_session` in dev) and 303s to `TAC_SITE_URL + return` (relative paths only) |
| POST | `/v1/auth/web/login` | none | dev form submit (10 per 10 min per IP) |
| GET | `/v1/auth/web/github/callback` | none | github mode OAuth return |
| GET | `/v1/auth/web/csrf` | cookie | `{csrf, header: "X-TAC-CSRF"}` for cookie-authenticated state changes |
| POST | `/v1/auth/web/logout` | cookie + CSRF | revokes the session, clears the cookie |
| GET | `/v1/me` | cookie or Bearer | `{handle, display_name, bio, link, created}` |
| PATCH | `/v1/me` | cookie+CSRF or Bearer | `{display_name ≤ 40, bio ≤ 280, link: https ≤ 200}`. Omitted = unchanged, `""`/null = cleared, unknown keys = 400 |
| DELETE | `/v1/me` | cookie+CSRF or Bearer | `{"confirm": "<handle>"}`. Deletes the account, pieces, media, sessions and tokens; 409 while a render is running |
| POST | `/v1/me/pieces/{id or handle/slug}/unpublish` | cookie+CSRF or Bearer, owner | published or in_review → rejected ("unpublished/withdrawn by the artist"); public + render media deleted |
| POST | `/v1/submissions` | Bearer | multipart `piece`, `meta`, `notes?`, `process[≤4]`. 202 `{id, status, url}` |
| GET | `/v1/submissions/{id}` | Bearer, owner | `{id, status, reasons, preview_url, critique}`; others get 404 |
| GET | `/v1/submissions/{id}/preview.webp` | signed URL | pre-publish preview (HMAC, 7-day expiry) |
| GET | `/v1/community.json` | none | gallery feed (CORS `*`) |
| GET | `/media/{handle}/{slug}/...` | none | preview.webp, og.jpg, piece.py, process/NN.webp |
| POST | `/v1/pieces/{handle}/{slug}/report` | none | `{reason}`; 5/h per IP; 3 distinct IPs hide the piece |
| POST | `/v1/pieces/{handle}/{slug}/view` | none | private view count; 204 always (see below) |
| GET | `/v1/me/pieces` | cookie or Bearer | own pieces: `{pieces: [{id: "handle/slug", slug, title, status (+ "hidden"), views_total, views_7d, views_28d[28 ints, oldest→newest, last = today UTC], url, critique, reasons}]}` (pinned with the plugin) |
| GET | `/admin`, `/admin/login?token=` | admin | HTML queue: in review, hidden, published, audit log |
| GET | `/v1/admin/queue` | admin | JSON version of the queue |
| POST | `/v1/admin/submissions/{id}/approve` · `/reject {reason}` | admin | from `in_review` only (409 otherwise) |
| POST | `/v1/admin/pieces/{handle}/{slug}/unhide` · `/delete {reason}` | admin | delete makes the piece `rejected` and removes its media |
| POST | `/v1/admin/users/{handle}/trust {trusted}` | admin | trusted + clean automod means auto-publish |
| POST | `/v1/admin/users/{handle}/house {house}` | admin | sets `house_artist` on all of that handle's pieces in community.json; clients can't set it (ignored in meta) |

Errors are always `{"error": "<code>", "detail"?: ...}`. Admin auth is the `X-Admin-Token` header or the `tac_admin` cookie. Cookie-authenticated POSTs also need `X-TAC-Admin-CSRF: 1`.

## Web sign-in (site)

```
site ──GET /api/v1/auth/web/login?return=/me──▶ dev: handle form ─POST─┐   prod: GitHub OAuth ─callback─┐
                                                                        ▼                                ▼
                          user (same row as the plugin's device login) ─▶ session: 32 random bytes in cookie,
                          sha256 in DB, 30 days, rotated on login ─▶ Set-Cookie __Host-tac_session (prod) / tac_session (dev)
                          (HttpOnly, SameSite=Lax, Path=/, Secure in prod, never a Domain) ─▶ 303 TAC_SITE_URL + return
site JS: GET /api/v1/auth/web/csrf → X-TAC-CSRF on every PATCH/POST/DELETE made with the cookie
```

- **Dev:** the site's vite dev server proxies `/api` → `http://127.0.0.1:8790` (the site builder adds the proxy). So the cookie is first-party on `localhost:5181`, and `TAC_SITE_URL` stays unset.
- **Prod:** the cookie is host-only on the API host. `terminalart.club` → `api.terminalart.club` is same-site, so the site's credentialed fetches carry it without a `Domain`; a `Domain=.terminalart.club` cookie would reach (and could be overwritten from) every subdomain.
- Dev login lets anyone sign in as any handle, existing or new; it is for local use only. `TAC_ENV=prod` refuses to start unless `TAC_AUTH=github`.
- **GitHub OAuth app:** set the authorization callback URL to `TAC_PUBLIC_BASE_URL`. Both `/device/github/callback` and `/v1/auth/web/github/callback` are sub-paths of it, which GitHub accepts.

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
| `TAC_RENDERER` | `local` | `local` = `run_limited` subprocess (not a sandbox; dev only); `docker` = one isolated container per check/render; `fly-machine` = one throwaway Fly Machine per job (prod) |
| `TAC_RENDER_IMAGE` | `tac-render:local` | image for `docker`; build with `render-image/build.sh` |
| `TAC_FLY_RENDER_APP` / `TAC_FLY_RENDER_IMAGE` / `TAC_FLY_RENDER_REGION` | `tac-render` / unset / unset | `fly-machine` backend: app, `registry.fly.io/tac-render:<tag>`, region |
| `FLY_API_TOKEN` | unset | `fly-machine` only: deploy token scoped to the tac-render app (see DEPLOY.md) |
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
| `TAC_SITE_ORIGINS` | `http://localhost:5181,https://terminalart.club` | the only origins that get credentialed CORS on `/v1/*` (never `/v1/admin`, `/v1/render-io`) |
| `TAC_SITE_URL` | unset | web login redirects to `TAC_SITE_URL + return`; unset = relative (dev proxy). Prod: `https://terminalart.club` |
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
