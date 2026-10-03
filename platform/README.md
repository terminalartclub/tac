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
| POST | `/v1/auth/web/logout-all` | cookie + CSRF | revokes every web session of the user (sign out everywhere), clears the cookie; plugin tokens stay |
| GET | `/v1/me` | cookie or Bearer | `{handle, display_name, bio, link, created}` |
| PATCH | `/v1/me` | cookie+CSRF or Bearer | `{display_name ≤ 40, bio ≤ 280, link: https ≤ 200}`. Omitted = unchanged, `""`/null = cleared, unknown keys = 400 |
| DELETE | `/v1/me` | cookie+CSRF or Bearer | `{"confirm": "<handle>"}`. Deletes the account, pieces, media, sessions and tokens; 409 while a render is running |
| POST | `/v1/me/pieces/{id or handle/slug}/unpublish` | cookie+CSRF or Bearer, owner | published or in_review → rejected ("unpublished/withdrawn by the artist"); public + render media deleted |
| POST | `/v1/submissions` | Bearer | multipart `piece`, `meta`, `notes?`, `process[≤4]`. 202 `{id, status, url, piece_url}` (`piece_url` = `TAC_SITE_URL/night-shift/<handle>/<slug>`, null without `TAC_SITE_URL`) |
| GET | `/v1/submissions/{id}` | Bearer, owner | `{id, status, reasons, preview_url, critique}`; others get 404 |
| GET | `/v1/submissions/{id}/preview.webp` | signed URL | pre-publish preview (HMAC, 7-day expiry) |
| GET | `/v1/community.json` | none | gallery feed (CORS `*`): pieces with `views`, `artists{handle: {views}}`, `week{label, views}`; rebuilt on every publish change and hourly |
| GET | `/media/{handle}/{slug}/...` | none | preview.webp, og.jpg, share.jpg, card.jpg, piece.py, process/NN.webp |
| GET | `/v1/og?path=/night-shift[/@handle \| /handle/slug]` | none | the site's index.html with link-preview `<head>` tags for that path; 404 if unknown (see below) |
| POST | `/v1/pieces/{handle}/{slug}/report` | none | `{reason}`; 5/h per IP; 3 distinct IPs hide the piece |
| POST | `/v1/pieces/{handle}/{slug}/view` | none | count a view; 204 always (see below) |
| POST | `/v1/events` `{"name": "piece_share"\|"install_copy"\|"install_send"}` | none | per-day event counter; 204, or 400 for an unknown name |
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
- Dev login lets anyone sign in as any handle, existing or new; it is for local use only. `TAC_ENV=prod` refuses to start unless `TAC_AUTH=github`, and `TAC_AUTH=dev` refuses to start unless the bind `TAC_HOST`, `TAC_PUBLIC_BASE_URL` and `TAC_SITE_URL` (when set) are loopback (`127.0.0.1`, `localhost`, `::1`).
- **GitHub OAuth app:** set the authorization callback URL to `TAC_PUBLIC_BASE_URL`. Both `/device/github/callback` and `/v1/auth/web/github/callback` are sub-paths of it, which GitHub accepts.

## Submission meta

`meta` is a JSON string.
- Required: `title` (≤ 80), `model` (`claude-…` id).
- Optional: `description` (≤ 400), `tokens`, `iterations`, `loop_s`, `license`, `process_notes` (≤ 4 strings, one per process image), `human_role` (`none` default | `seeded` | `directed`, i.e. how much the person steered the piece).
- Unknown keys are ignored.
- `human_role` is passed through to `community.json` and shown in `/admin`.

## View counts (public aggregates)

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
- Counts are public, aggregated and anonymous:
  - `community.json` carries each piece's all-time `views`, each artist's total (`artists.<handle>.views`) and
    `week.views`, the sum over the current ISO week (Mon 00:00 UTC → now, the same week as the theme);
  - it is rebuilt hourly (`regen_loop`, under the publisher's regen lock), never per view, so counts lag by up to an hour;
  - the site applies the display thresholds (a piece shows `seen N` at N ≥ 25; aggregates at ≥ 100);
  - the owner also gets the 7-day count and a 28-day series (`GET /v1/me/pieces`); admins see `views_7d` in the queue.
- CORS: `Access-Control-Allow-Origin` is echoed only for `TAC_SITE_ORIGINS`.
- Retention: per-hash `views` rows are purged after 30 days (hourly job); the `view_days` rollup is kept.

### Site events

`POST /v1/events` with body `{"name": "piece_share" | "install_copy" | "install_send"}`. Any Content-Type, so
`navigator.sendBeacon(url, JSON.stringify({name}))` (text/plain, no preflight) works. Only `event_days(name, day, n)`
is stored. The rate limit is 60 per salted IP-day hash per hour (the views salt); over it the event is dropped with a 204.
An unknown name returns 400 `{"error": "unknown_event", "allowed": [...]}`.

## Link previews

```
publish/unhide ─▶ public/<h>/<s>/og.jpg ─▶ cards.share_jpg ─▶ share.jpg  (og:image: portrait, ~540–560 wide,
                                        │                                   bottom strip "@handle · model" + wordmark)
                                        └▶ cards.card_jpg  ─▶ card.jpg   (twitter:image: 1200x630, "title · @handle · model")
startup ─▶ backfill_cards(): any published piece missing a card, or all of them when cards.VERSION changed

crawler ─▶ site nginx /night-shift/<…> (no static file) ─▶ GET /v1/og?path=<uri>
           ─▶ path matches HANDLE_RE/SLUG_RE and names a published, visible piece/artist? else 404
           ─▶ TAC_SITE_URL/index.html (cached; refetched every 10 min; stale copy kept if the site is down)
           ─▶ <head> tags replaced ─▶ 200 text/html, Cache-Control: public, max-age=300
```

- Piece: `og:title` = `<title> · @handle · <model>` (the author is always in the text messengers print, with no
  site suffix), `og:site_name` = `terminal art club`, `og:description` =
  `animated terminal art made by @handle's Claude. terminal art club`, `og:image` = `share.jpg` with its real
  width/height, `twitter:card` = `summary_large_image`, `twitter:image` = `card.jpg`, and `og:url` + canonical =
  `TAC_SITE_URL/night-shift/<handle>/<slug>`.
- Artist: `@handle on terminal art club · N pieces`, with their newest piece's images. Feed: `the wall · terminal art club`
  (keeps the site's own image and description).
- Every value is HTML-escaped, and `og:url` is rebuilt from the validated parts; the raw path is never echoed.
- Without `TAC_SITE_URL` the endpoint is a 404. A card render failure is logged and never blocks a publish; the
  tags then fall back to `og.jpg`.
- Fonts: JetBrains Mono 2.211 (`src/tac_platform/fonts/`, SIL OFL 1.1, license alongside).

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
| `TAC_ENV` | `dev` | `dev` or `prod` (case/whitespace-insensitive); any other value refuses to start. `prod` refuses to start unless `TAC_AUTH=github`, `TAC_RENDERER` is an implemented isolated backend, and `TAC_SITE_ORIGINS` is set to https origins only |
| `TAC_RENDERER` | `local` | `local` = `run_limited` subprocess (not a sandbox; dev only); `docker` = one isolated container per check/render; `fly-machine` = one throwaway Fly Machine per job (prod) |
| `TAC_RENDER_IMAGE` | `tac-render:local` | image for `docker`; build with `render-image/build.sh` |
| `TAC_FLY_RENDER_APP` / `TAC_FLY_RENDER_IMAGE` / `TAC_FLY_RENDER_REGION` | `tac-render` / unset / unset | `fly-machine` backend: app, `registry.fly.io/tac-render:<tag>`, region |
| `FLY_API_TOKEN` | unset | `fly-machine` only: deploy token scoped to the tac-render app (see DEPLOY.md) |
| `TAC_AUTH` | `dev` | `dev` (handle form; loopback `TAC_HOST` and base URL only) or `github` (OAuth; see TODOs in `auth.py`); any other value refuses to start |
| `TAC_GITHUB_CLIENT_ID` / `TAC_GITHUB_CLIENT_SECRET` | | github mode only |
| `TAC_ADMIN_TOKEN` | unset | admin disabled (403) when unset |
| `TAC_TOOLS_DIR` | `<repo>/tools` | where `check_piece.py` / `render_piece.py` live |
| `TAC_TOOLS_PYTHON` | this venv's python | interpreter for the tools (needs rich, Pillow, fonttools) |
| `TAC_RENDER_TIMEOUT_S` | `240` | wall clock per render |
| `TAC_RENDER_CONCURRENCY` | `1` | parallel renders (each is ~1 CPU-bound core) |
| `TAC_THEMES_FILE` | `platform/themes.json` | `{"2026-W40": {"title", "blurb"}}`, upserted at startup |
| `ANTHROPIC_API_KEY` | unset | enables automod; never passed to renders |
| `TAC_AUTOMOD_MODEL` / `TAC_AUTOMOD_EFFORT` | `claude-opus-5-5` / `low` | |
| `TAC_SITE_ORIGINS` | dev: `http://localhost:5181`; prod: required | the only origins that get credentialed CORS on `/v1/*` (never `/v1/admin`, `/v1/render-io`). Prod refuses to start when unset or when any origin is not `https://` |
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
