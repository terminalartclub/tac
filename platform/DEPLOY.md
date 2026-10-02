# Deploy: Fly.io

**Decided (operator):** prod renders with `FlyMachineRenderer`, one throwaway Fly Machine per job (option b). The code is built and tested against a mocked Machines API. **No Fly resources exist yet.** Everything under "Operator runs these" costs money and is for the operator to run.

> **Gate:** with `TAC_ENV=prod` the API refuses to start unless `TAC_RENDERER` is an implemented isolated backend (`config.ISOLATED_RENDERERS` = `docker`, `fly-machine`).
> - With `fly-machine`, it also refuses to start without `FLY_API_TOKEN` and `TAC_FLY_RENDER_IMAGE`.
> - The local `run_limited` subprocess renderer is never allowed in prod.

```
                    org A                                          org B (recommended: separate org)
 ┌───────────────────────────────────────────┐           ┌────────────────────────────────────────────┐
 │ app tac-api (1 Machine, shared-cpu-1x 512MB)│  Machines │ app tac-render: no IPs, no services, no    │
 │  FastAPI + worker, SQLite + media on volume │───API────▶│ secrets; one Machine per job, auto_destroy │
 │  secrets: FLY_API_TOKEN (deploy token,      │  create/  │                                            │
 │   tac-render only), ANTHROPIC, ADMIN, GH     │  wait/    │ fly_bootstrap.py (root, has network):      │
 │                                              │  destroy  │  1 GET  in.tar.gz  ◀─presigned (10 min)    │
 │  /v1/render-io/… presigned GET/PUT ◀────────┼───────────┤  2-4 netns, uid 65534, NO network:         │
 └──────────────────────────────────────────────┘  https   │     probe → check_piece → render_piece      │
                                                            │  5 PUT  out.tar.gz ─▶presigned (10 min)    │
                                                            └────────────────────────────────────────────┘
```

## Job lifecycle (`src/tac_platform/fly_machine.py`)

Endpoints are from the Machines API OpenAPI spec (`https://docs.fly.io/api/machines/openapi.json`, fetched 2026-10-02) and docs.fly.io/machines/api/machines-resource.

```
tar piece_dir ─▶ store.put render-io/<job>/in.tar.gz ─▶ presign GET in, PUT out (10 min each)
  ─▶ POST https://api.machines.dev/v1/apps/tac-render/machines
       {name, skip_service_registration: true, config: {image, auto_destroy: true, restart: {policy: "no"},
        guest: {cpu_kind: "shared", cpus: 1, memory_mb: 2048}, init: {exec: [python, /app/fly_bootstrap.py]},
        env: {TAC_IN_URL, TAC_OUT_URL, TAC_CHECK_TIMEOUT, TAC_RENDER_TIMEOUT}, services: [],
        dns: {skip_registration: true}, metadata: {tac_job}}}
  ─▶ GET …/machines/<id>/wait?state=stopped&instance_id=…&timeout≤60, looped up to 300 s (408 → keep waiting, 404 → auto-destroyed = stopped)
  ─▶ store.get out.tar.gz ─▶ safe extract (tarfile "data" filter, out/* regular files ≤ 25 MB, result.json)
  ─▶ finally, on every path incl. cancel: DELETE …/machines/<id>?force=true ; delete render-io/<job>/
```

- On timeout the job is rejected with "render timed out". If no output arrives, the API reads `events[].request.exit_event` (exit_code, oom_killed) for the log.
- The presigned URLs live on the API itself (`/v1/render-io/…`, HMAC over method, key and expiry, keys confined to `render-io/`), so v0 needs no object store. The Machine must reach `TAC_PUBLIC_BASE_URL` over https.
- When media moves to Tigris, `TigrisStore.presign()` returns S3 presigned URLs instead. The renderer is unchanged.
- Machine create is rate-limited to 1 req/s per action, with bursts to 3 req/s (docs). `TAC_RENDER_CONCURRENCY` (default 1) stays well inside that.

## Operator runs these (costs ~$4/mo + cents per render)

Prerequisites: `flyctl` logged in, Docker, and the tac-community repo.

```bash
# 0. (recommended) a separate org for renders, so a render VM can't reach tac-api over 6PN
fly orgs create tac-render                                   # or reuse an org; renders still have no secrets

# 1. render app: no IPs, no services, never `fly deploy`ed (Machines are created per job by the API)
fly apps create tac-render --org tac-render
fly ips list -a tac-render                                   # must be empty; never allocate one

# 2. render image (x86_64 for Fly hosts), pushed to Fly's registry
fly auth docker
TAC_RENDER_IMAGE=registry.fly.io/tac-render:$(git rev-parse --short HEAD) TAC_RENDER_PLATFORM=linux/amd64 \
  platform/render-image/build.sh
docker push registry.fly.io/tac-render:$(git rev-parse --short HEAD)

# 3. the API's token: a DEPLOY token scoped to tac-render only (never an org or personal token)
fly tokens create deploy -a tac-render --name tac-api-renders --expiry 2160h   # 90 days, then rotate
#    -> paste straight into step 5; don't save it to a file

# 4. API app + 1 GB volume (SQLite + media)
fly apps create tac-api
fly volumes create tac_data --size 1 -a tac-api --region <region>

# 5. API secrets (values typed or piped from the password manager, never written to disk)
fly secrets set -a tac-api FLY_API_TOKEN=<token from step 3> TAC_ADMIN_TOKEN=<random> \
  ANTHROPIC_API_KEY=<key> TAC_GITHUB_CLIENT_ID=<id> TAC_GITHUB_CLIENT_SECRET=<secret>

# 6. deploy the API (needs a Dockerfile + fly.toml for tac-api; not written yet, see checklist)
#    fly.toml [env]: TAC_ENV=prod TAC_RENDERER=fly-machine TAC_FLY_RENDER_APP=tac-render
#      TAC_FLY_RENDER_IMAGE=registry.fly.io/tac-render:<sha> TAC_FLY_RENDER_REGION=<region>
#      TAC_PUBLIC_BASE_URL=https://<api host> TAC_AUTH=github TAC_TRUST_PROXY=1 TAC_DATA_DIR=/data
#      TAC_HOST=0.0.0.0
fly deploy -a tac-api
```

- **Smoke test before opening uploads:**
  1. Submit one piece and confirm it reaches `in_review`.
  2. Look at the job's `result.json` `isolation` field in the API log: it must be `{"routes": [], "tcp": "OSError"}`.
  3. If it shows `network isolation unavailable`, the Fly kernel refused the namespace. Renders then fail closed (no untrusted code runs). Stop there and see SECURITY.md.
- **Rotation:** run step 3 again, then `fly secrets set` the new token and `fly tokens revoke <old id>` (`fly tokens list -a tac-render`).

## Cost

Rates verified from docs.fly.io/about/pricing:
- shared-cpu-1x 512 MB: $3.69/mo
- shared-cpu-1x 2 GB: $13.39/mo
- volumes: $0.15/GB-mo
- egress: $0.02/GB (North America / Europe)

Machines bill per second while running.

| line | assumption | USD/mo |
|---|---|---|
| tac-api Machine | shared-cpu-1x 512 MB, always on | 3.69 |
| tac-api volume | 1 GB | 0.15 |
| render Machines | shared-cpu-1x 2 GB = $13.39/730 h = $0.0183/h. ~40 s per job incl. boot means ~$0.0002 per render | 0.20 per 1,000 renders |
| egress | gallery media. 300 pieces × ~0.9 MB preview × 50 views = ~13.5 GB | ~0.27 |
| **Fly total** | 1,000 renders/mo | **~$4.3** |
| automod (Anthropic, not Fly) | Opus 5.5 at effort low, ~$0.08–0.10 per submission | ~80–100 per 1,000 |

- Registry storage and image pulls are not in the verified price list above. Check them on the pricing page.
- At this volume, automod is ~95% of the bill. If it needs trimming, switch `TAC_AUTOMOD_MODEL` to a Sonnet.

## Not built (by decision)

- **Option (a), a Docker host:** `DockerRenderer` exists and is tested. It is useful for local isolated renders. Prod doesn't use it.
- **Tigris media store:** not needed for v0, since the volume holds media and presigned URLs are served by the API. Revisit when the volume or the single API Machine becomes a limit. `MediaStore` is the swap point; `db.py` is the swap point for Postgres or LiteFS.

## Before going public: checklist

- [ ] Steps 0–6 above. Smoke test shows `isolation: {"routes": [], "tcp": "OSError"}` from a real Fly Machine.
- [ ] Dockerfile + fly.toml for `tac-api` (`uv sync --frozen`, `tac-platform`, `/healthz` check, `auto_stop_machines = "off"`, volume at `/data`).
- [ ] GitHub OAuth tested end to end; pick-a-handle step for invalid or taken logins.
- [ ] Token expiry + revoke; admin via a GitHub allow-list instead of a shared token.
- [ ] Backups: daily volume snapshots + `sqlite3 .backup`.
- [ ] The plugin discloses that code and frames go to Anthropic for moderation.
