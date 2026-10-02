# Security: threat model

**Bottom line:** the API surface is hardened for a small public gallery. Locally on macOS, the render step is not sandboxed. A malicious `piece.py` runs as your user, with your files and your network. Prod must not run renders in the API process's machine (see "Prod render isolation").

**Hard gate:** the platform MUST refuse to start with `TAC_ENV=prod` while the renderer is the local `run_limited` subprocess. This is enforced by `Settings.check_prod_safety()`, which runs in `create_app` before anything binds. `ISOLATED_RENDERERS` is empty until an isolated backend is implemented, so prod cannot start today. Public uploads stay blocked until isolated render machines exist.

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

### Prod render isolation (required before accepting public uploads)

Either option works. Option A fits Fly.

- **A. Ephemeral Fly Machine per job** (`tac-render` app, see DEPLOY.md):
  - Firecracker microVM, destroyed after the job (`--rm`).
  - No secrets in its env. Inputs are baked in or passed as files. Output goes out through presigned PUT URLs scoped to `submissions/<id>/render/*`, expiring in 10 min.
  - The piece process runs under `unshare --net` (loopback only) inside the VM. Only the trusted wrapper uploads, after the piece exits.
  - The app lives in a **separate Fly org**, so the API's 6PN private network is unreachable from it.
  - Machine size caps memory and CPU for real: shared-cpu-2x / 2 GB, with a kill after 240 s.
- **B. Same host, real sandbox:** nsjail or bubblewrap with a new user, mount, PID and net namespace (no network), a read-only root, a tmpfs work dir, seccomp, and cgroup memory/CPU limits. Run the API and the renderer under different uids.

The `Pipeline` only calls `run_limited(argv, cwd, timeout)`. Swapping in either option means replacing `sandbox.run_limited` plus `_materialize` / `_store_render`.

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
- Dev mode: whoever enters the code picks any free handle. There is no re-login into an existing handle. Dev mode is for local use only. Prod uses `TAC_AUTH=github`, where the handle is the GitHub login.
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
