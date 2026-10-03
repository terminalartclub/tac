# Security: threat model

**Bottom line:** the API surface is hardened for a small public gallery. Locally on macOS, the render step is not sandboxed. A malicious `piece.py` runs as your user, with your files and your network. Prod must not run renders in the API process's machine (see "Prod render isolation").

**Hard gate:** the platform MUST refuse to start with `TAC_ENV=prod` while the renderer is the local `run_limited` subprocess.
- This is enforced by `Settings.check_prod_safety()`, which runs in `create_app` before anything binds.
- `ISOLATED_RENDERERS` = `{"docker", "fly-machine"}`. Prod also refuses to start if the chosen backend isn't usable: the image is missing (docker), or `FLY_API_TOKEN` / `TAC_FLY_RENDER_IMAGE` are unset (fly-machine).

## Assets

| asset | where | impact if lost |
|---|---|---|
| `ANTHROPIC_API_KEY`, `TAC_ADMIN_TOKEN`, GitHub OAuth secret | API process env | spend, takeover of moderation |
| access tokens | DB, sha256 only | impersonate an artist (submit as them) |
| unpublished submissions | `data/submissions/` | leak of drafts |
| gallery integrity | `community.json`, `/media` | defacement, hosting abuse |
| host | wherever renders run | full compromise if the render escapes |

## 1. Untrusted Python (the big one)

Every submission is arbitrary Python that the renderer executes.

| control | local (macOS) | what it actually stops |
|---|---|---|
| `check_piece.py` static check | yes | lazy attacks only. Static analysis of Python is bypassable (`getattr(__builtins__, ...)`, `exec` of decoded strings). |
| scrubbed env: only PATH, HOME, LANG, LC_ALL pass (no `UV_*`: `UV_INDEX_URL` can embed credentials; the tools run as the interpreter directly) | yes (`sandbox.py`, tested) | reading secrets from `os.environ`. It does **not** stop reading `~/.config`, `~/.ssh`, the SQLite DB or the API's `/proc`-equivalent. |
| cwd = fresh temp dir, `TMPDIR` pointed there | yes | accidental writes into the repo |
| `RLIMIT_CPU` 180 s, `RLIMIT_NOFILE` 256, `RLIMIT_FSIZE` 200 MB | yes | CPU burn, fd exhaustion, disk fill via one file |
| `RLIMIT_AS` 3 GB | **set but not enforced by the macOS kernel** | nothing on macOS. It works on Linux. |
| 240 s wall clock, kill of the whole process group on every exit path (normal, timeout, cancellation on shutdown/reload) | yes | hangs, orphaned children |
| network isolation | **no** | nothing. The piece can exfiltrate and call any reachable host, including 127.0.0.1:8790. |
| filesystem isolation | **no** | nothing. Same uid as the API. |
| stdout/stderr | read into memory | a piece can print GBs within 240 s. Unbounded memory in the API process (accepted locally). |

**Local rule:** only run untrusted submissions locally on a machine or account with nothing to lose, or keep `TAC_WORKER=0` and review the code first.

### Prod render isolation: `FlyMachineRenderer` (`TAC_RENDERER=fly-machine`, the decided prod backend)

One throwaway Firecracker microVM per job, in the `tac-render` app (recommended: its own Fly org).
- The Machine has `auto_destroy`, restart `no`, no services and no IPs. Its env holds no secrets: only two presigned URLs and two timeouts.
- The API force-destroys the Machine on every exit path: success, error, 300 s timeout, cancellation.

**Which step has network** (`render-image/fly_bootstrap.py`):

| step | runs | network | identity |
|---|---|---|---|
| 1. GET input tar (presigned, 10 min, one key) | bootstrap (ours) | **yes** | root in the VM |
| 2. probe: no routes except kernel IPv6 reject routes; TCP to 1.1.1.1 fails | probe child | **no**: fresh netns | uid 65534 |
| 3. `check_piece.py` | child (parses untrusted source) | **no**: fresh netns | uid 65534, no_new_privs, rlimits |
| 4. `render_piece.py` (runs the piece) | child | **no**: fresh netns | uid 65534, no_new_privs, rlimits |
| 5. PUT output tar (presigned, 10 min, one key) | bootstrap, after all children exit | **yes** | root |

- **How the drop works:**
  - Each untrusted step starts via `preexec_fn`: `os.unshare(CLONE_NEWNET)`, setrlimit, `setgroups([])`, setgid/setuid 65534, `prctl(PR_SET_NO_NEW_PRIVS)`, then exec.
  - The new namespace has only a downed loopback and no routes.
  - Re-entering the Machine's namespace needs CAP_SYS_ADMIN (`setns`), which uid 65534 doesn't have.
- **Fail closed:** if the namespace can't be created, or the probe finds any usable route, the bootstrap uploads `{"error": "network isolation unavailable"}` and **never runs check or render**. The API rejects the job as "render backend unavailable".
- **What is verified, and what isn't (honest status):**
  - Verified locally: the real image + real `fly_bootstrap.py` + the real seed `laps` ran as the "Machine". Docker stood in for the VM as root + CAP_SYS_ADMIN with a normal network. (`tests/test_fly_machine.py::test_real_bootstrap_drops_network_for_piece`)
  - In that run the bootstrap reached the API over the presigned URLs. The untrusted child, as uid 65534, could not reach the same host ("Network is unreachable"), and the probe saw zero routes.
  - Without CAP_SYS_ADMIN the probe fails and the job fails closed.
  - **Not verified on a real Fly Machine:** no Fly resources were created. Fly Machines boot a full Linux kernel with the process as root, and network namespaces are standard there (Docker-in-Machine setups depend on them). This is expected to work, but it is unproven until the first smoke render (DEPLOY.md checklist).
  - Because the bootstrap fails closed, an unsupported kernel produces failed renders, never unisolated ones. That is why `fly-machine` is in `ISOLATED_RENDERERS`.
- **Fallback** if Fly's kernel refuses the namespace:
  - The closest equivalent is an egress firewall applied by the bootstrap before step 3: nftables/iptables drop all output except loopback, then drop CAP_NET_ADMIN by switching to uid 65534. That needs `nftables` in the image and has the same fail-closed probe.
  - A weaker alternative: give the render Machine no network at all, deliver inputs through Machine `files` (base64 in the create request, ≤ a few MB), and collect outputs by polling the Machine's exec API. That moves ~1 MB previews through the Machines API and is not built.
- **Residual risk:**
  - A kernel exploit from the piece gets root in an empty, disposable VM. From there it holds:
    - two presigned URLs scoped to this job (read its own input, write its own output);
    - the tac-render org's 6PN, so the separate org keeps tac-api unreachable.
  - The output bundle is attacker-controlled either way:
    - the API extracts it with tarfile's `data` filter;
    - it keeps only `out/*` regular files ≤ 25 MB plus `result.json`;
    - render outputs are then whitelisted by name;
    - a human reviews the result before publish.
- **API side:**
  - `FLY_API_TOKEN` is a deploy token scoped to the `tac-render` app (`fly tokens create deploy -a tac-render`). Never an org token.
  - Anyone holding it can read the presigned URLs in Machine configs. Those last 10 minutes and cover one job's I/O.
  - The `/v1/render-io/…` routes are public but capability-bound: HMAC over method, key and expiry, keys confined to `render-io/`, PUT ≤ 64 MB. Job I/O is deleted after each job.

### Implemented: `DockerRenderer` (`TAC_RENDERER=docker`)

Both `check_piece.py` and `render_piece.py` run inside a fresh container per call:

```
docker run --rm --name tac-render-<random> --network none --read-only --tmpfs /tmp:size=256m
  --memory 2g --memory-swap 2g --cpus 1 --pids-limit 256 --ulimit nofile=256 --ulimit fsize=200MB
  --cap-drop ALL --security-opt no-new-privileges --user 65534:65534
  -v <piece_dir>:/in:ro -v <out_dir>:/out:rw tac-render:local python /app/render_piece.py /in --out /out
```

- The docker CLI gets only PATH, HOME and `DOCKER_*`, and the container gets only the image's env. No API keys either way.
- On the 240 s wall-clock timeout, cancellation or any other exit, the container is `docker kill`ed and `docker rm -f`ed.
- Tested (`tests/test_docker_renderer.py`, skipped without Docker):
  - TCP, UDP and DNS fail;
  - writes to `/app`, `/etc`, `/in` and `/usr` fail, while `/out` and `/tmp` work;
  - uid is 65534;
  - no secrets in env;
  - timeout and cancel leave no container behind;
  - the real `laps` seed renders through the full pipeline.
- Remaining risk:
  - The boundary is the shared Linux kernel. On Docker Desktop that kernel is a VM, which adds a layer. On a Linux host, a kernel exploit reaches the host.
  - `/out` is a bind mount with no quota. `fsize` caps any single file at 200 MB, but many files could fill the disk within 240 s. Outputs are size-checked after the run (≤ 25 MB each).

`DockerRenderer` is the isolated backend for running renders locally or on a Docker host. Prod uses `FlyMachineRenderer` (above).

## 2. Uploads

- Size caps are enforced before parsing (Content-Length) and while streaming (`LimitsMiddleware`): 3 MB per submission, 64 KB on every other route.
- Per-file caps: piece ≤ 200 KB and UTF-8, process images ≤ 4 × 600 KB with the PNG magic checked, notes ≤ 64 KB.
- Uploaded files never reach the public dir as-is:
  - Process PNGs are re-encoded to webp by the renderer.
  - The public dir only gets whitelisted render outputs (`preview.webp`, `og.jpg`, `process/NN.webp`) plus `piece.py`.
  - Each output is ≤ 25 MB. Symlinks in render output are ignored.
- `piece.py` is served as `text/x-python` with `X-Content-Type-Options: nosniff`, so it can't be sniffed into HTML.
- Storage keys are built from server-generated ids and validated handles/slugs. `LocalStore` rejects `..`, empty and absolute segments.
- All HTML (admin, device page) escapes user content (title, description, notes, code, reasons).
- Automod reads frames with Pillow. Pillow's default `MAX_IMAGE_PIXELS` decompression-bomb guard applies.

## 3. Auth and tokens

- Device code: 32 random bytes, stored as sha256. The user code is 8 chars over the 20-letter RFC 8628 alphabet (20^8 ≈ 2.6e10), lives 10 min, and the form allows 10 attempts per 10 min per IP. Brute force is out of reach.
- A device code is consumed exactly once (compare-and-swap `approved → consumed`). Replays get 410.
- Access tokens: 32 random bytes (`secrets.token_urlsafe`). The DB keeps only sha256, so a DB leak does not leak usable tokens. They don't expire yet (TODO: expiry + revoke endpoint).
- Dev mode, device flow: whoever enters the code picks any free handle; there is no re-login into an existing handle.
- Dev mode, web login: signs in as any handle, existing or new.
- Dev mode is local-only, and `TAC_ENV=prod` refuses to start without `TAC_AUTH=github`, where the handle is the GitHub login.
- Inherent to device flow: a phished user code can approve an attacker's device. The page shows nothing about the requesting device yet (TODO: show request time/IP region).
- Admin:
  - The token is compared in constant time.
  - The `/admin/login?token=` cookie is `HttpOnly; SameSite=Strict`, plus `Secure` on https.
  - SameSite alone trusts sibling subdomains, so a cookie-authenticated POST also needs:
    - the `X-TAC-Admin-CSRF: 1` header. The admin page's `act()` sends it, and cross-origin it forces a CORS preflight we never answer;
    - and, when the browser sends `Sec-Fetch-Site`, the value `same-origin`.
  - Header-authenticated calls (`X-Admin-Token`) are exempt: a browser can't attach that header cross-site.
  - The access log redacts `token=` (`main.RedactTokens`).
  - Still: the token sits in browser history. Prefer the header from scripts. TODO(prod): replace with GitHub OAuth plus an admin allow-list.
- Owner-only status: another user's submission id returns 404, not 403. Ids are 72-bit random.
- Pre-publish preview links are HMAC-signed with a per-install secret (in the `kv` table) and expire after 7 days.
  - They are issued only while the status is queued, rendering or in_review.
  - On every fetch the status is re-checked: queued/rendering/in_review, or published and not hidden. A rejected, deleted or hidden piece stops serving even through a still-valid link.
  - Signatures are compared as bytes, so a non-ASCII `sig` gets 403, not 500.

### Web sessions (site sign-in)

- **Cookie:** `tac_session` = 32 random bytes; the DB stores only sha256.
  - 30-day expiry, enforced server-side. Expired rows are purged hourly.
  - Rotated on every login: a session presented at login is revoked.
  - Logout deletes the row.
  - Flags: `HttpOnly; SameSite=Lax; Path=/`, plus `Secure` in prod (or with an https base URL). `Domain` comes from `TAC_COOKIE_DOMAIN` (`.terminalart.club` in prod, host-only in dev).
- **Same user records as the plugin.** One account can be authenticated three ways:
  - plugin: a Bearer token;
  - site: the session cookie;
  - prod web login: GitHub id → handle, same as the device flow.
- **CSRF** for cookie-authenticated state changes (POST/PATCH/DELETE). Both checks are enforced centrally in `auth.current_user`:
  - `X-TAC-CSRF` must equal `HMAC(install secret, "csrf|" + sha256(session))`. The site reads it from `GET /v1/auth/web/csrf`, which needs the cookie and is CORS-readable only by `TAC_SITE_ORIGINS`.
  - `Sec-Fetch-Site`, when the browser sends it, must be `same-origin` or `same-site`.
  - SameSite=Lax alone would still let a same-site sibling (any `*.terminalart.club`) or a top-level GET through; the header closes that.
  - Bearer requests are exempt: a browser never attaches the header on its own.
- **Login CSRF:** a dev login POST with `Sec-Fetch-Site: cross-site` is refused. In GitHub mode, the OAuth `state` is HMAC-signed and bound to a nonce cookie scoped to `/v1/auth/web/`.
- **Open redirect:**
  - `return` must be a relative path: it starts with exactly one `/`, has no backslash, whitespace or control chars, no scheme or host, and is ≤ 512 chars. Anything else becomes `/`.
  - The redirect target is `TAC_SITE_URL` (config) + that path.
  - Tested against `//evil`, `/\evil`, `/\t/evil`, `https://evil` and `javascript:`.
- **CORS:** `Access-Control-Allow-Origin: <origin>` + `Allow-Credentials: true` is sent only when Origin is in `TAC_SITE_ORIGINS`, and only on `/v1/*` site paths. Never on `/v1/admin*` or `/v1/render-io/*`. `community.json` and `/media` stay `*` without credentials.
- **Login rate limits:** dev form 10 per 10 min per IP (salted hash). GitHub redirect 20 per 10 min per IP.
- **Profile fields** (`display_name` ≤ 40, `bio` ≤ 280, `link` https only, ≤ 200, no userinfo, no whitespace):
  - Control, zero-width and bidi-override characters are stripped; unknown keys are refused.
  - They are stored as typed. The admin HTML escapes them (tested with `<script>`).
  - `community.json` carries them as JSON strings in `artist`. The site must render them as text, never as HTML, and give the link `rel="nofollow noopener ugc"`.
- **Unpublish:**
  - Owner only; someone else's piece returns 404, not 403.
  - Published or in_review → `rejected` with the reason recorded. Public media and render outputs are deleted.
  - The private upload stays for the audit trail until the account is deleted. Audit-logged as the user.
- **Account deletion:**
  - Needs `{"confirm": "<handle>"}`.
  - In one transaction it deletes the user, submissions, reports, views and rollups, Bearer tokens, web sessions and device codes. Then it removes all media and regenerates `community.json`.
  - Refused with 409 while a render is in flight, so the worker can't write files for a deleted user.
  - The audit log keeps rows naming the handle: the operational record, no content.

## 4. Rate limits and abuse

| limit | key | mechanism |
|---|---|---|
| 3 submissions / 24 h | user | one `INSERT … SELECT … WHERE count < 3` statement, so it holds under concurrency |
| 5 reports / h | salted IP hash | `rate_events` table, atomic insert-if-under |
| 3 distinct reporters hides a piece | salted IP hash, `UNIQUE(submission, reporter)` | one person can't hide a piece alone without 3 IPs |
| 30 device codes / h, 10 device-form posts / 10 min | salted IP hash | same |

- Raw IPs are never stored: `sha256(secret | ip)`.
- The client IP is the socket peer. `Fly-Client-IP` / `X-Forwarded-For` are honoured only with `TAC_TRUST_PROXY=1`. Otherwise anyone could spoof them and dodge the limits.
- Known gap: 3 IPs (one phone, one VPN, one home connection) are enough to hide any piece. That's acceptable because hiding only puts the piece in the admin queue, and unhide clears its reports. Revisit if it gets abused.

## 5. View counts (privacy)

- **No cookies, no identifiers, no body.** The site fires `POST /v1/pieces/{h}/{s}/view`, and the response is 204 with an empty body in every case: unknown, hidden, duplicate or rate-limited. A probe learns nothing about state.
- **No raw IPs.**
  - The dedupe key is `sha256(day_salt | ip)`. `day_salt` is 32 random bytes minted per UTC day (in `kv`).
  - The salt is deleted once the day after it has ended, so a hash stays reversible by IPv4 brute force (2^32) for at most ~48 h. After that it is irreversible.
  - Different days use different salts, so a viewer can't be linked across days.
- **Retention:**
  - Per-hash rows (`views`) are purged after 30 days.
  - The rollup `view_days(piece, day, views)` holds no viewer data and is kept.
  - Rate-limit events (`rate_events`, keyed by the day hash) are GC'd after 2 days.
- **Abuse limits:**
  - 120 view POSTs per hash per hour. Beyond that they are silently dropped (still 204).
  - Dedupe caps inflation at 1 per IP per piece per day, so inflating by N takes N IPs.
  - Counts are private and unranked, which leaves little incentive to game them.
- **CORS** is echoed only for `TAC_SITE_ORIGINS`. The POST is a "simple" request, so CORS doesn't stop a third-party page firing it. Dedupe and the rate limit are what bound the effect.
- **Exposure:** only the owner (`/v1/me/pieces`, Bearer) and admins see counts. `community.json` carries none (tested).

## 6. Automod

- The submission (code, title, description) is untrusted. The system prompt marks it as data, and the reply is forced into a JSON schema (`output_config.format`).
- The untrusted blocks are wrapped in `<title>/<description>/<code>`, with every `</` inside them rewritten to `<\/`. A submitter can't close a wrapper and write text that looks like ours.
- A prompt-injected "safe" verdict only matters for trusted handles (auto-publish). Everyone else still gets human review.
- A refusal (`stop_reason == "refusal"`), an API error or invalid JSON goes to `in_review` with the reason, never to auto-reject or auto-publish.
- The API key is never passed to renders. Automod sends code to Anthropic, which the submit flow should disclose to artists.

## 7. Concurrency

- Public-media transitions (publish, hide, unhide, delete) are serialised by one in-process lock. Hide removes the media before regenerating the listing, so a concurrent unhide can't be wiped (regression test). Multiple API instances would need a DB lock instead.
- One shared SQLite connection. Every write goes through one `asyncio.Lock`, and every state change is a compare-and-swap on the current status inside a transaction, together with its audit row. Two admins can't double-publish, and a report-hide can't race an admin delete into an inconsistent state.
- The queue claim is a single `UPDATE … WHERE id = (SELECT … LIMIT 1) AND status='queued' RETURNING id`.
- `community.json` regeneration is serialised and reads the DB after the write. The file is written atomically (tmp + rename).
