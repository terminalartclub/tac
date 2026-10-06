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
  - **Verified on a real Fly Machine (3 Oct 2026):** the prod smoke render logged `isolation {"routes": [], "tcp": "OSError"}` from a `tac-render` Machine. Prod runs `TAC_RENDERER=fly-machine`, and every render repeats the same probe before any submitted code runs; a failed probe rejects the submission (`pipeline.py`, `backend_error`).
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

### Terms acceptance

- Every sign-in path ends with the current `TAC_TERMS_VERSION` accepted, or it doesn't end: the dev `/device` form
  and the dev web form require the box server-side (a missing box is a 400 and creates nothing); the GitHub device
  and web flows stop after the OAuth callback at an interstitial when the user is behind, and only then approve the
  device code or set the session.
- The interstitial token is `kind.user_id.extra.exp.nonce` + HMAC(secret), valid 10 minutes, and must match an
  httpOnly `SameSite=Lax` nonce cookie set on the same response. A leaked form token alone (no cookie) can't finish
  a sign-in; a device token can't finish a web sign-in (the kind is signed); the web step keeps the login-CSRF check.
- Acceptance is stored per user (`terms_version`, `terms_accepted_at`) with an audit row per acceptance. Uploads
  are refused (403 `terms_not_accepted`) until the current version is accepted, and need `meta.rights_confirmed: true`.

### Rights attestation

- It's agent-mediated. `/tac:submit` tells the artist's own Claude to show the line *"You have the right to share
  this, and it doesn't copy anyone else's characters, brands or logos."* and to pass `--confirm-rights` only after
  the person confirms it in the conversation. `tacctl` refuses to upload without the flag.
- The server records `rights_confirmed: true` in the piece's stored meta. With the uploader's accepted
  `terms_version` (`terms_accepted_at`, and the `terms_accepted` audit row from sign-in), that says who uploaded
  what under which terms.
- It's a good-faith attestation, not proof. Nothing technical stops a client from setting the flag itself, and an
  agent can misreport what the person said. It doesn't replace review, automod's `franchise_ip` flag, the report
  flow or takedowns; it records that the uploader made the statement.


- Device code: 32 random bytes, stored as sha256. The user code is 8 chars over the 20-letter RFC 8628 alphabet (20^8 ≈ 2.6e10), lives 10 min, and the form allows 10 attempts per 10 min per IP. Brute force is out of reach.
- A device code is consumed exactly once (compare-and-swap `approved → consumed`). Replays get 410.
- Access tokens: 32 random bytes (`secrets.token_urlsafe`). The DB keeps only sha256, so a DB leak does not leak usable tokens. They don't expire yet (TODO: expiry + revoke endpoint).
- Dev mode, device flow: whoever enters the code picks any free handle; there is no re-login into an existing handle.
- Dev mode, web login: signs in as any handle, existing or new.
- Dev mode is local-only, and `TAC_ENV=prod` refuses to start without `TAC_AUTH=github`, where the handle is the GitHub login.
  - `TAC_ENV` is normalized (strip + lowercase) and must be `dev` or `prod`; `production`, `staging` or an empty value refuse to start instead of falling back to dev.
  - `TAC_AUTH=dev` refuses to start unless the bind `TAC_HOST`, `TAC_PUBLIC_BASE_URL` and `TAC_SITE_URL` (when set) are loopback. `TAC_HOST=0.0.0.0`, `::` or a LAN address refuses, so a deploy that sets only the bind host can't serve dev login.
  - Prod refuses to start without `TAC_SITE_ORIGINS`, or with any non-`https://` origin.
- Inherent to device flow: a phished user code can approve an attacker's device. The page shows nothing about the requesting device yet (TODO: show request time/IP region).
- Admin:
  - The token is compared in constant time.
  - The `/admin/login?token=` cookie is `HttpOnly; SameSite=Strict`, plus `Secure` on https.
  - SameSite alone trusts sibling subdomains, so a cookie-authenticated POST also needs:
    - the `X-TAC-Admin-CSRF: 1` header. The admin page's `act()` sends it, and cross-origin it forces a CORS preflight we never answer;
    - and, when the browser sends `Sec-Fetch-Site`, the value `same-origin`.
  - Header-authenticated calls (`X-Admin-Token`) are exempt: a browser can't attach that header cross-site.
  - uvicorn's request lines (`uvicorn.access`, and `uvicorn.error` for WebSocket) redact the `token`, `code`,
    `state` and `sig` query values (`main.RedactTokens`), matched on the decoded, case-folded key (`%63ode=`,
    `CODE=`), plus any pair hiding one inside it (`x=1;code=…`, `code%3D…`). The admin token, the OAuth code +
    state on both GitHub callbacks and the signed preview / render-io links never reach the logs.
  - Still: the token sits in browser history. Prefer the header from scripts. TODO(prod): replace with GitHub OAuth plus an admin allow-list.
- Owner-only status: another user's submission id returns 404, not 403. Ids are 72-bit random.
- Pre-publish preview links are HMAC-signed with a per-install secret (in the `kv` table) and expire after 7 days.
  - They are issued only while the status is queued, rendering or in_review.
  - On every fetch the status is re-checked: queued/rendering/in_review, or published and not hidden. A rejected, deleted or hidden piece stops serving even through a still-valid link.
  - Signatures are compared as bytes, so a non-ASCII `sig` gets 403, not 500.

### Web sessions (site sign-in)

- **Cookie:** `__Host-tac_session` in prod (`tac_session` in dev) = 32 random bytes; the DB stores only sha256.
  - 30-day expiry, enforced server-side. Expired rows are purged hourly.
  - Rotated on every login: a session presented at login is revoked.
  - Logout deletes the row. `POST /v1/auth/web/logout-all` (CSRF-checked) deletes every web session of the user, e.g. after a lost device.
  - Flags: `HttpOnly; SameSite=Lax; Path=/`, plus `Secure` in prod (or with an https base URL). Never a `Domain`: the cookie is host-only on the API host, and the prod `__Host-` prefix makes the browser enforce Secure, Path=/ and no Domain. A sibling subdomain can neither read it nor plant (toss) one. There is no cookie-domain setting.
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
  - C0/C1 control, zero-width, bidi mark/override/isolate, invisible-operator, BOM and tag characters (`me._CTRL`) are stripped from `display_name` and `bio`, except ZWNJ/ZWJ (U+200C/U+200D), which shape Persian/Indic text and join compound emoji. A `link` containing any of them, ZWNJ/ZWJ included (`me._LINK_CTRL`), is refused (never rewritten). Unknown keys are refused.
  - `PATCH /v1/me` is limited to 30 accepted calls per user per hour (429 after), since each one by a published artist regenerates `community.json`.
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
  - Deleting an account keeps the log lines that name the handle and record what happened. Moderation
    actions also keep a short moderator reason, such as a complaint reference, so we can answer takedown
    claims and enforce the repeat-infringer rule. No content is kept.
  - A suspended account can't sign in to delete itself: email hello@terminalart.club and we delete it.
  - Deleted rows are gone from the file, not only from queries: `PRAGMA secure_delete=ON` zeroes freed cells,
    `delete_account` truncates the WAL (`wal_checkpoint(TRUNCATE)`), and the first boot with this setting
    VACUUMs once to clear rows deleted before it. A reader in another connection (operator shell,
    `sqlite3 .backup`) can block the truncate: it is never waited for (busy_timeout 0 for the checkpoint, the
    write and media locks free in milliseconds; the checkpoint runs on its own short-lived connection, so the
    shared connection's 5 s busy timeout is never lowered); one warning when first blocked, then one per 10
    retries and one on success, while a background task retries every 30 s until it succeeds. A checkpoint
    that raises (I/O error, "database is locked" during another process's recovery) is logged and treated
    the same way: the delete still answers 204 and the boot still starts. The VACUUM is
    a cleanup, never a boot blocker: a failure (disk full, I/O) is logged and retried next boot, and its done-flag is set only after a truncate that wasn't blocked.
  - Fly's daily volume snapshots (5-day retention) keep pre-delete copies of the whole DB until they rotate
    out: a deletion is complete everywhere after 5 days.
  - Handles in the audit log after a delete (`Publisher._pseudonymise`, same transaction as the delete):
    - self-deletion, or an admin deleting an ACTIVE account, is an erasure request: every audit row of that
      account gets `deleted:<12 hex>` = sha256(blocklist salt | account id, created_at, first audit row id),
      and each of its piece slugs becomes `deleted:<…>/<12 hex>` (submit details, `<handle>/<slug>` targets,
      the delete_account IG list). What happened and when stays; who it was, and which piece, doesn't.
      Pseudonymous, not anonymous: the salt derives from the kv secret in the same SQLite file and slugs have
      low entropy (lowercased title words), so anyone with a DB dump can dictionary-attack the slug aliases,
      and the account pseudonym by brute force over ids and timestamps. A leaked DB file defeats them.
    - an admin deleting a SUSPENDED account keeps handle and slugs on the moderation rows (suspend, unsuspend,
      hide, unhide, delete, delete_account, block, unblock, reject, ig_posted, ig_removed) and in
      `blocked_identities.ref`: the repeat-infringer evidence and the takedown log. Every other row is
      pseudonymised the same way.
    - Rows are chosen by ownership, never by matching the handle as a value: actor `user:<handle>`; the
      user-level actions whose target is the handle (suspend, unsuspend, delete_account, account_deleted,
      block, unblock); the details that are exactly the handle (trust, untrust, house, unhouse) or start with
      `<handle>: ` (instagram_confirm, whose Instagram handle goes too); and rows whose submission_id is one of
      the account's pieces. Another person's slug, profile field or reason equal to the handle is untouched.
    - Only this account's rows: they start after the previous owner's end row still naming the handle
      (`delete_account` or `account_deleted`); block/unblock rows (always about a deleted, banned holder) are
      never rewritten. Prod launched after this rule, so no legacy self-deletion rows needed a backfill.
    - Caveats: the same handle string can reappear later if someone else claims it. Free-text moderator reasons
      are not scanned (the runbook says reference IDs only). Access-log lines on admin paths
      (`/v1/admin/users/<handle>/…`) carry the handle in stdout logs, which age out with Fly's log retention.
  - No raw GitHub id outlives the account: it lives only in `users.github_id` (deleted with the row). The
    `user_created` audit row says just `github`; older rows that carried `github:<id>` are scrubbed on delete
    and, once, at startup. The GitHub login is kept only as the handle it became (already public).

### Takedowns and suspension

- **Hide first, within minutes, then decide.** "Hide now" (`POST /v1/admin/pieces/{h}/{s}/hide {reason}`)
  is the same transition as a report auto-hide (`Publisher.hide`): public media deleted, `community.json`
  regenerated, og/share 404. Reversible with unhide. Then delete, unhide, or suspend.
- **Suspend** is one `BEGIN IMMEDIATE` transaction under the DB write lock: set `suspended_at`, delete every
  `access_tokens`, `web_sessions` and `device_codes` row of the user, set `hidden=1` on every published
  piece and reject every `queued` one (one audit row each), write the suspend audit row. The worker's claim
  skips suspended owners. No request
  authenticated by a revoked credential can start after it commits. Every path that creates a credential
  re-checks `suspended_at` inside its own transaction (web session insert, device approval, token mint), and
  `current_user` refuses a suspended user (403 `suspended`) as a second line. The submission `INSERT` carries
  the same guard. Publish and unhide are compare-and-swaps that require an unsuspended owner, so neither the
  admin nor trusted auto-publish can put a suspended artist's piece back up. After the commit, each
  `public/<handle>/<slug>` prefix is deleted in its own try (failures listed in the response) and
  `community.json` is regenerated once. Suspend is idempotent: calling it again re-sweeps (the first reason is kept). The Re-sweep button posts to
  `/resweep`, which never suspends (409 `not_suspended` from a stale page after an unsuspend), so a retry
  finishes a media delete that failed.
- The moderator's reason is never shown to the suspended user; sign-in pages and the API show a fixed message.
- Admin account deletion (`POST /v1/admin/users/{h}/delete {reason}`) reuses `delete_account`; it is how a
  suspended artist's erasure request is honoured.
- Deleting an account that is suspended at that moment (admin only) writes `blocked_identities` in the same
  transaction: a pseudonymous `sha256(salt | "github:<id>")` (`"dev:<handle>"` in dev auth), salt =
  `sha256("tac-blocked-identities|" + kv 'secret')`, the per-install secret. GitHub and dev sign-up refuse a
  blocked identity with the suspended message, so a delete can't lift a ban. No raw id or login is kept; `ref` =
  the deleted handle, shown in /admin. Unblock targets the row's own `id`, never `ref`: a handle can be
  reused, so several blocks can share one `ref`. Rotating the kv secret would void every block.
  Pseudonymous, not anonymous: the salt derives from the kv secret in the same SQLite file, so anyone with a DB
  dump can brute-force the ~2.6e8 GitHub ids and reverse every hash.
- Every hide, unhide, delete, suspend, unsuspend and admin account deletion writes an audit row with its reason and a `target`
  (`handle/slug` or `handle`) that survives row deletion. `/admin/takedowns` lists the last 100.
- All new endpoints sit under `/v1/admin/` behind `require_admin` (header token, or cookie + CSRF header +
  same-origin), so the admin listener's path guard covers them. Reason prompts use a `data-prompt`
  attribute read by the one delegated listener: no inline JS, no value in code.

## 4. Rate limits and abuse

| limit | key | mechanism |
|---|---|---|
| 5 submissions / 24 h (`TAC_SUBMISSIONS_PER_DAY`) | user | one `INSERT … SELECT … WHERE count < 5` statement, so it holds under concurrency. Rejections where the platform failed (render budget, backend, tooling: `platform_fault = 1`) don't count; check failures and other rejections do |
| 5 reports / h | salted IP hash | `rate_events` table, atomic insert-if-under |
| 3 distinct reporters hides a piece | salted IP hash, `UNIQUE(submission, reporter)` | one person can't hide a piece alone without 3 IPs |
| 30 device codes / h, 10 device-form posts / 10 min | salted IP hash | same |

- Raw IPs are never stored: `sha256(secret | ip)`.
- IPv6 clients are counted per /64 (`web.ip_bucket`) in every hash and rate key: views dedupe, events,
  rate limits and report dedupe. One subscriber usually holds a whole /64, so rotating addresses inside it
  can't inflate counts or dodge limits. IPv4 is unchanged.
- The client IP is the socket peer. `Fly-Client-IP` / `X-Forwarded-For` are honoured only with `TAC_TRUST_PROXY=1`. Otherwise anyone could spoof them and dodge the limits.
- Known gap: 3 IPs (one phone, one VPN, one home connection) are enough to hide any piece. That's acceptable because hiding only puts the piece in the admin queue, and unhide clears its reports. Revisit if it gets abused.

## 5. View counts and site events (privacy)

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
  - Counts are public but unranked; the site shows a piece's count only at ≥ 25 and aggregates only at ≥ 100.
- **CORS** is echoed only for `TAC_SITE_ORIGINS`. The POST is a "simple" request, so CORS doesn't stop a third-party page firing it. Dedupe and the rate limit are what bound the effect.
- **Exposure:** view counts are public, aggregated and anonymous. `community.json` carries per-piece totals,
  per-artist totals and the current ISO week's sum, rebuilt hourly. It carries no hashes, no per-day series and nothing
  per viewer. The owner (`/v1/me/pieces`, Bearer) also sees the 7-day count and the 28-day series.
- **Instagram handle** (`PATCH /v1/me` `instagram`): opt-in, a bare handle only (URLs refused), validated to
  Instagram's own rules. A user can claim any handle, so a claim is **unverified** until an admin confirms it
  (`instagram-confirm`, compare-and-set on the exact handle). Only a confirmed handle is public in `community.json`
  (`artists.<handle>.instagram`) and only a confirmed handle is tagged in our posts. Changing the handle resets the
  confirmation in the same UPDATE. `/v1/me` returns the claim with `instagram_confirmed` so the site can show
  "pending confirmation"; `/admin` shows it as unconfirmed with a confirm button.
- **Site events** (`POST /v1/events`): an allowlist of three names (`piece_share`, `install_copy`, `install_send`);
  anything else gets a 400. Only `event_days(name, day, n)` is stored, with no IP, hash, piece or cookie. The limit is
  60 per IP-day hash per hour, keyed with the same daily salt as views; over it the event is dropped with a 204.

## 5b. Link-preview shell (`GET /v1/og`)

- Public, unauthenticated HTML. The input is one `path` parameter, matched against fixed shapes (`/gallery`, `/@handle`, `/@handle/slug`, plus the legacy `/night-shift` ones) with the
  same `HANDLE_RE` / `SLUG_RE` as uploads. Anything else is a 404, and so are unknown, hidden and unpublished pieces.
- The page is the site's own `index.html`. It is fetched only from the configured `TAC_SITE_URL` (never a
  request-supplied URL, so there is no SSRF), with no redirects, streamed with a 512 KB cap, and cached for 2 minutes. A failed first fetch is remembered for
  30 s (immediate 503s), so a down site can't turn crawler traffic into upstream request load.
- Every interpolated value (title, handle, model, URLs) goes through `html.escape(quote=True)`. The raw path is never
  reflected: `og:url` is rebuilt from the validated handle and slug. A title such as `"><script>` comes out as text (tested).

## 6. Automod

- The submission (code, title, description) is untrusted. The system prompt marks it as data, and the reply is forced into a JSON schema (`output_config.format`).
- The untrusted blocks are wrapped in `<title>/<description>/<code>`, with every `</` inside them rewritten to `<\/`. A submitter can't close a wrapper and write text that looks like ours.
- A prompt-injected "safe" verdict only matters for trusted handles (auto-publish). Everyone else still gets human review.
- A refusal (`stop_reason == "refusal"`), an API error or invalid JSON goes to `in_review` with the reason, never to auto-reject or auto-publish.
- The API key is never passed to renders. Automod sends code to Anthropic, which the submit flow should disclose to artists.
- Spend is capped: at or above `TAC_AUTOMOD_BUDGET_USD` for the month, no call is made and the piece goes to a human
  (`automod budget reached`). A flood of submissions can exhaust the budget (bounded by the 3-per-day per-user limit),
  which degrades to human review and never to auto-publish.

## 7. Concurrency

- Public-media transitions (publish, hide, unhide, delete) are serialised by one in-process lock. Hide removes the media before regenerating the listing, so a concurrent unhide can't be wiped (regression test). Multiple API instances would need a DB lock instead.
- One shared SQLite connection. Every write goes through one `asyncio.Lock`, and every state change is a compare-and-swap on the current status inside a transaction, together with its audit row. Two admins can't double-publish, and a report-hide can't race an admin delete into an inconsistent state.
- The queue claim is a single `UPDATE … WHERE id = (SELECT … LIMIT 1) AND status='queued' RETURNING id`.
- `community.json` regeneration is serialised and reads the DB after the write. The file is written atomically (tmp + rename).
