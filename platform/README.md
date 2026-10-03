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
| GET | `/v1/me` | cookie or Bearer | `{handle, display_name, bio, link, instagram, instagram_confirmed, created, terms_version, terms_current}` |
| PATCH | `/v1/me` | cookie+CSRF or Bearer | `{display_name ≤ 40, bio ≤ 280, link: https ≤ 200, instagram: bare handle (leading @ stripped, URLs refused, IG rules; a changed handle resets `instagram_confirmed`)}`. Omitted = unchanged, `""`/null = cleared, unknown keys = 400 |
| DELETE | `/v1/me` | cookie+CSRF or Bearer | `{"confirm": "<handle>"}`. Deletes the account, pieces, media, sessions and tokens; 409 while a render is running |
| POST | `/v1/me/pieces/{id or handle/slug}/unpublish` | cookie+CSRF or Bearer, owner | published or in_review → rejected ("unpublished/withdrawn by the artist"); public + render media deleted |
| POST | `/v1/submissions` | Bearer | multipart `piece`, `meta`, `notes?`, `process[≤4]`. 202 `{id, status, url, piece_url}` (`piece_url` = `TAC_SITE_URL/@<handle>/<slug>`, null without `TAC_SITE_URL`). 403 `terms_not_accepted` `{terms_url, terms_version}` until the current terms are accepted; 400 `rights_not_confirmed` unless `meta.rights_confirmed` is `true` |
| GET | `/v1/submissions/{id}` | Bearer, owner | `{id, status, reasons, preview_url, critique}`; others get 404 |
| GET | `/v1/submissions/{id}/preview.webp` | signed URL | pre-publish preview (HMAC, 7-day expiry) |
| GET | `/v1/community.json` | none | gallery feed (CORS `*`): pieces with `views`, `artists{handle: {views, instagram? (admin-confirmed only)}}`, `week{label, views}`; rebuilt on every publish change and hourly |
| GET | `/media/{handle}/{slug}/...` | none | preview.webp, og.jpg, share.jpg, card.jpg, piece.py, process/NN.webp |
| GET | `/v1/og?path=/gallery \| /@handle \| /@handle/slug` (legacy `/night-shift[/@handle \| /handle/slug]`) | none | the site's index.html with link-preview `<head>` tags for that path; 404 if unknown (see below) |
| POST | `/v1/pieces/{handle}/{slug}/report` | none | `{reason}`; 5/h per IP; 3 distinct IPs hide the piece |
| POST | `/v1/pieces/{handle}/{slug}/view` | none | count a view; 204 always (see below) |
| POST | `/v1/events` `{"name": "piece_share"\|"install_copy"\|"install_send"}` | none | per-day event counter; 204, or 400 for an unknown name |
| GET | `/v1/me/pieces` | cookie or Bearer | own pieces: `{pieces: [{id: "handle/slug", slug, title, status (+ "hidden"), views_total, views_7d, views_28d[28 ints, oldest→newest, last = today UTC], url, critique, reasons}]}` (pinned with the plugin) |
| GET | `/admin`, `/admin/login?token=` | admin | HTML queue: in review, hidden, published, audit log |
| GET | `/v1/admin/queue` | admin | JSON version of the queue |
| POST | `/v1/admin/submissions/{id}/approve` · `/reject {reason}` | admin | from `in_review` only (409 otherwise) |
| POST | `/v1/admin/pieces/{handle}/{slug}/unhide` · `/delete {reason}` | admin | delete makes the piece `rejected` and removes its media |
| POST | `/v1/admin/users/{handle}/trust {trusted}` | admin | trusted + clean automod means auto-publish |
| POST | `/v1/admin/users/{handle}/instagram-confirm {instagram}` | admin | confirms the user's current IG handle (compare-and-set: 409 if it changed); only confirmed handles are public and tagged |
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
- Required: `title` (≤ 80), `model` (`claude-…` id), `rights_confirmed: true` (see Terms acceptance).
- Optional: `description` (≤ 400), `tokens` (whole number 0–2,000,000 or null; > 1,000,000 shows a `high_tokens` badge in `/admin` and is never auto-published, even for trusted handles), `iterations`, `loop_s`, `license`, `process_notes` (≤ 4 strings, one per process image), `human_role` (`none` default | `seeded` | `directed`, i.e. how much the person steered the piece).
- Unknown keys are ignored.
- `human_role` is passed through to `community.json` and shown in `/admin`.

## Terms acceptance

```
sign-in ─▶ identity known? ─ dev /device, dev web form: no ─▶ box on the form (always shown; required for a new
        │                                                    handle or one behind the current version)
        └─ GitHub device / GitHub web: yes, after the OAuth callback
              users.terms_version >= TAC_TERMS_VERSION ? ─ yes ─▶ signed in, no box
                                                          └ no ─▶ interstitial: the box + a signed 10-min token
                                                                  bound to an httpOnly nonce cookie
box ticked ─▶ users.terms_version = TAC_TERMS_VERSION, terms_accepted_at = now, audit "terms_accepted vN"
box missing ─▶ 400, the same form re-rendered with an inline error; nothing created, no code approved
```

- The box: `I agree to the Terms and the Content policy`, linking `TAC_SITE_URL/terms` and `/content-policy`
  (`https://terminalart.club` when `TAC_SITE_URL` is unset). Required and unticked.
- `POST /v1/submissions` answers 403 `terms_not_accepted` while `terms_version < TAC_TERMS_VERSION` (the plugin says
  `accept the updated terms: run /tac:login`), and 400 `rights_not_confirmed` unless `meta.rights_confirmed` is the
  JSON `true`: the submitter's statement that they may share the piece and that it copies no one else's characters,
  brands or logos. The attestation is stored with the piece's meta.
- `GET /v1/me` returns `terms_version` (last accepted, null = never) and `terms_current`.
- Seeding house pieces through the upload sets `rights_confirmed: true`; the operator attests for them.

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

crawler ─▶ site nginx /@<…>, /gallery, /night-shift/<…> (no static file) ─▶ GET /v1/og?path=<uri>
           ─▶ path matches HANDLE_RE/SLUG_RE and names a published, visible piece/artist? else 404
           ─▶ TAC_SITE_URL/index.html (cached 2 min; stale copy kept if the site is down; a failed first fetch → 503 for 30 s)
           ─▶ <head> tags replaced ─▶ 200 text/html, Cache-Control: public, max-age=300
```

- Piece: `og:title` = `<title> · @handle · <model>` (the author is always in the text messengers print, with no
  site suffix), `og:site_name` = `terminal art club`, `og:description` =
  `animated terminal art made by @handle's Claude. terminal art club`, `og:image` = `share.jpg` with its real
  width/height, `twitter:card` = `summary_large_image`, `twitter:image` = `card.jpg`, and `og:url` + canonical =
  `TAC_SITE_URL/@<handle>/<slug>`.
- Canonical paths: piece `/@<handle>/<slug>`, artist `/@<handle>`, feed `/gallery`. The legacy `/night-shift`,
  `/night-shift/@<handle>` and `/night-shift/<handle>/<slug>` still resolve during the transition, and their
  `og:url` is the new canonical URL.
- Artist: `@handle on terminal art club · N pieces`, with their newest piece's images. Feed: `the wall · terminal art club`
  (keeps the site's own image and description).
- Every value is HTML-escaped, and `og:url` is rebuilt from the validated parts; the raw path is never echoed.
- Without `TAC_SITE_URL` the endpoint is a 404. A card render failure is logged and never blocks a publish; the
  tags then fall back to `og.jpg`.
- Fonts: JetBrains Mono 2.211 (`src/tac_platform/fonts/`, SIL OFL 1.1, license alongside).

## Automod budget

Automod is the launch review gate, with a hard monthly budget (default $10, `TAC_AUTOMOD_BUDGET_USD`).

```
submission rendered ─▶ this month's automod_spend >= budget ? ─ yes ─▶ no API call; in_review, reason
                                                             │          "automod budget reached" (a human decides)
                                                             └ no ──▶ one call ─▶ cost from response.usage
                                                                                ─▶ automod_spend[YYYY-MM] += cost
```

- The call: Claude Sonnet 5.5, effort `low`, thinking off (`between_tools`), `max_tokens` 300, three frames at
  ≤ 512 px a side, the first 20,000 characters of code. The output is only `{safe, on_brief, flags}`; the artist's
  own Claude writes the critique.
- Cost = input × $2/M + output × $10/M (cache writes 1.25× input, cache reads 0.1×; the prices table lives in
  `Settings.automod_prices`). Every call that returns usage is charged, including refusals and truncated answers.
  Each call is logged: `automod call: model=… in=… out=… cost=$…`.
- Estimate, not yet measured on live traffic: ~600 image tokens + ~4–6k code tokens + ~60 output tokens ≈
  $0.010–0.014 per typical submission, so $10 covers roughly 700–1,000 submissions a month.
- Worst case per call (`automod_budget.worst_case_call_usd`): 20,000 code chars at a dense 2 chars/token = 10,000
  tokens, plus 3 frames × 512² px / 750 ≈ 1,050 image tokens, plus ~1,000 tokens of prompt, title and description =
  ~12,050 input tokens × $2/M ≈ $0.024, plus 300 output tokens × $10/M = $0.003. That is about **$0.027 per call**.
- When the budget is reached, submissions keep flowing: each one goes to `in_review` for a human, and trusted
  handles stop auto-publishing until the next month or a higher budget. `/admin` shows
  `automod: $X.XX of $10 this month` and a badge once the budget is reached.
- To raise it, set `TAC_AUTOMOD_BUDGET_USD` (e.g. `fly secrets set TAC_AUTOMOD_BUDGET_USD=25`) and restart. The
  ledger isn't reset; the new cap applies to the same month at once.
- Overshoot: the check is a read before each call and the charge an atomic UPSERT after it, so concurrent reviews
  can pass the cap by at most `TAC_RENDER_CONCURRENCY − 1` calls, each at most the worst case above (~$0.03).
  With the default concurrency of 1 there is no overshoot.

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
| `TAC_AUTOMOD_MODEL` / `TAC_AUTOMOD_EFFORT` | `claude-sonnet-5-5` / `low` (`low`, `medium` or `high`; `xhigh`/`max` 400 with thinking off, so startup refuses them) | the model must have a price in `Settings.automod_prices` (startup fails otherwise) |
| `TAC_AUTOMOD_BUDGET_USD` | `10` | hard monthly automod spend cap in USD (UTC calendar month); see Automod budget |
| `TAC_SITE_ORIGINS` | dev: `http://localhost:5181`; prod: required | the only origins that get credentialed CORS on `/v1/*` (never `/v1/admin`, `/v1/render-io`). Prod refuses to start when unset or when any origin is not `https://` |
| `TAC_TERMS_VERSION` | `1` | mirrors `TERMS_VERSION` in the site's `legal.js`; raising it makes every user re-accept at the next sign-in, and uploads 403 until they do |
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
  automod.py      Claude structured-output safety verdict (no critique)
  automod_budget.py  per-call cost from usage, monthly ledger, budget cutoff
  publish.py      state transitions, public media copy, community.json
  moderation.py   public reports
  admin.py        /admin HTML + /v1/admin JSON
  storage.py      MediaStore interface + LocalStore (S3/Tigris swap point)
  db.py           SQLite schema + tiny async wrapper (Postgres swap point)
```
