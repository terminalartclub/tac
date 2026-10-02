# Deploy: Fly.io (plan, not built)

Nothing here is provisioned. These are the target shape and the code changes it needs.

> **Gate:** with `TAC_ENV=prod` the API refuses to start unless `TAC_RENDERER` names an implemented isolated backend (`config.ISOLATED_RENDERERS`, empty today). The local `run_limited` subprocess renderer is never allowed in prod. Public uploads stay blocked until the `tac-render` Machine backend below is built, and its name is added to that set in the same change.

```
                         ┌──────────── org: tac (6PN A) ────────────┐
artist plugin ──https──▶ │ app tac-api  (1 Machine, shared-cpu-1x)   │──presign──▶ Tigris bucket tac-media
site / browsers ───────▶ │  FastAPI + worker, SQLite on volume       │                 ├ public/…  (public read)
                         │  secrets: ANTHROPIC_API_KEY, ADMIN, GH,   │                 └ submissions/… (private)
                         │           FLY_API_TOKEN (tac-render only) │
                         └──────────────┬────────────────────────────┘
                                        │ Machines API: run --rm, files=piece_dir, env=presigned URLs
                         ┌──────────────▼──── org: tac-render (6PN B, isolated) ──┐
                         │ app tac-render  (ephemeral Machine per job)            │
                         │  wrapper: fetch nothing, run piece under unshare --net │
                         │  → PUT outputs to presigned URLs → exit → destroyed    │
                         └────────────────────────────────────────────────────────┘
```

## App 1: `tac-api`

- Image: `python:3.12-slim` + `uv sync --frozen`. Run `tac-platform` with `TAC_ENV=prod`, `TAC_RENDERER=fly-machine` (once implemented), `TAC_HOST=0.0.0.0` and `TAC_TRUST_PROXY=1`.
- One Machine (shared-cpu-1x, 512 MB) with a 1 GB volume at `/data` (`TAC_DATA_DIR=/data`) for SQLite.
  - Stay on one Machine while SQLite is the DB. Scale out means LiteFS (one primary, read replicas) or Fly Postgres. `db.py` is the swap point; the SQL already uses `RETURNING` / `ON CONFLICT`.
- `fly secrets set ANTHROPIC_API_KEY=… TAC_ADMIN_TOKEN=… TAC_GITHUB_CLIENT_ID=… TAC_GITHUB_CLIENT_SECRET=… FLY_RENDER_TOKEN=…`
  - `FLY_RENDER_TOKEN` is a deploy token scoped to the `tac-render` app only (`fly tokens create deploy -a tac-render`).
- `TAC_AUTH=github`. Set `TAC_PUBLIC_BASE_URL` to the public hostname.
- `fly.toml`:
  - `[http_service] force_https = true`, `auto_stop_machines = "off"` (the worker lives in-process), `min_machines_running = 1`.
  - `[[http_service.checks]] path = "/healthz"`.
- Media: a `TigrisStore` implementing `storage.MediaStore` with S3 calls (`put`/`get`/`list`/`delete_prefix`/`copy_prefix`).
  - Use aioboto3 or plain `httpx` + SigV4.
  - `public/` is a public-read prefix. `/media` and `community.json` URLs then point at the bucket (or a CDN in front) instead of the API. Keep `GET /v1/community.json` as a redirect.

## App 2: `tac-render` (ephemeral, no secrets)

- **Separate Fly org**, so its 6PN private network can't reach `tac-api` or anything else we own.
- Image: `python:3.12-slim` + `rich pillow fonttools`, `fonts-dejavu-core` (render_piece needs a monospace font on Linux), `util-linux` (`unshare`), and builder-community's `tools/` baked in.
- Per job, the API calls the Machines API (equivalent to `fly machine run --rm`):
  ```
  fly machine run registry.fly.io/tac-render:<sha> --app tac-render --rm \
      --vm-size shared-cpu-2x --vm-memory 2048 --region <api region> \
      --file-local /job/piece/piece.py=… --file-local /job/piece/meta.json=… \
      --env PUT_PREVIEW=<presigned> --env PUT_OG=<presigned> --env PUT_STATS=<presigned> --env PUT_PROC_01=…
  ```
  - Inputs travel in the Machine config `files`, so the VM needs no inbound fetch.
  - Outputs go out through presigned PUT URLs: 10 min expiry, one key each, under `submissions/<id>/render/`. No credentials in the VM.
  - Wrapper entrypoint: `unshare --net --map-root-user timeout 240 python tools/render_piece.py /job/piece --out /job/out`. The piece process gets loopback only, so it has **no egress**. After it exits, the trusted wrapper PUTs the outputs and exits, and `--rm` destroys the Machine.
  - The API polls the Machines API for exit (or waits for the PUTs to land in Tigris), then continues the pipeline as today.
- Code change: `Pipeline._process` calls a `Renderer` interface. `LocalRenderer` = today's `run_limited` path. `FlyRenderer` = the flow above. The static check is pure AST, so it can stay in the API, but it is cheap to also run it in the render VM.
- Concurrency: cap jobs with `TAC_RENDER_CONCURRENCY`. Each job is its own VM, so 4 parallel jobs means 4 VMs.

## Rough monthly cost (verify on fly.io/pricing)

| line | assumption | ~USD/mo |
|---|---|---|
| tac-api Machine | shared-cpu-1x 512 MB, always on | ~3.2 |
| volume | 1 GB + snapshots | ~0.15–0.5 |
| tac-render Machines | shared-cpu-2x 2 GB, ~30 s/job incl. boot, 1,000 jobs/mo = 8.3 h | ~0.2–0.5 |
| Tigris storage | 300 pieces × ~2 MB = 0.6 GB (+ private submissions ~2 GB) | ~0.05–0.1 |
| Tigris requests/egress | gallery reads; Tigris has no egress fee, per-request pricing | ~0–2 |
| IPv4 | dedicated v4 optional (shared v4 is free) | 0–2 |
| automod (Anthropic, not Fly) | Opus 5.5 at effort low: ~15k input tokens (code + 3 frames) + ~1–2k output ≈ $0.08–0.10 each; 1,000/mo | ~80–100 |
| **total Fly** | | **~4–9** |

- Automod dominates the bill.
- Lever: switch `TAC_AUTOMOD_MODEL` to a Sonnet. That is ~2× cheaper per token, at some loss of judgment on the "malicious-looking code" flag.
- These numbers are from memory of Fly's published rates. Verify every line on fly.io/pricing and tigrisdata.com/pricing before budgeting.

## Before going public: checklist

- [ ] `FlyRenderer` implemented and `"fly-machine"` added to `ISOLATED_RENDERERS`. Until then `TAC_ENV=prod` refuses to start.
- [ ] GitHub OAuth tested end to end; pick-a-handle step for invalid/taken logins.
- [ ] Token expiry + revoke endpoint; admin via GitHub allow-list instead of a shared token.
- [ ] `TigrisStore` + public URLs; `/media` static mount removed.
- [ ] Backups: volume snapshots daily + `sqlite3 .backup` to Tigris (or LiteFS).
- [ ] Disclose in the plugin that code and frames go to Anthropic for moderation.
