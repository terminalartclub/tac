"""FlyMachineRenderer: one throwaway Fly Machine per job, via the Machines API.

Endpoints and fields per the Machines API OpenAPI spec (https://docs.fly.io/api/machines/openapi.json,
fetched 2026-10-02) and https://docs.fly.io/machines/api/machines-resource:
  POST   /v1/apps/{app}/machines                       create (config.auto_destroy, restart.policy, guest, ...)
  GET    /v1/apps/{app}/machines/{id}/wait?state=...   blocks <= 60 s per call (we loop to 300 s total)
  GET    /v1/apps/{app}/machines/{id}                  events[].request.exit_event (diagnostics only)
  DELETE /v1/apps/{app}/machines/{id}?force=true       always, on every exit path

  API ──put──▶ store render-io/<job>/in.tar.gz ──presign GET──┐
      ──presign PUT render-io/<job>/out.tar.gz ───────────────┤ env TAC_IN_URL / TAC_OUT_URL
      ──POST create (image, auto_destroy, restart=no, no services, no secrets) ──▶ Machine
                                Machine: fly_bootstrap.py  GET in → [netns: check → render] → PUT out
      ──wait stopped (≤300 s) ──get out.tar.gz ──safe-extract ──▶ out_dir + result.json
      ──finally: DELETE ?force=true, delete render-io/<job>/
"""

import asyncio
import io
import json
import logging
import os
import secrets
import tarfile
import time
from pathlib import Path

import httpx

from .config import Settings
from .renderer import JobResult
from .sandbox import RunResult
from .storage import MediaStore

log = logging.getLogger("tac.fly")

API = "https://api.machines.dev"
WAIT_STEP_S = 60  # the wait endpoint blocks at most 60 s per call
MAX_OUT_TAR = 64 * 1024 * 1024
MAX_IN_TAR = 8 * 1024 * 1024
PRESIGN_TTL_S = 600


class FlyApiError(Exception):
    pass


def bundle(piece_dir: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in sorted(piece_dir.rglob("*")):
            if p.is_file() and not p.is_symlink():
                tar.add(p, arcname=str(p.relative_to(piece_dir)), recursive=False)
    data = buf.getvalue()
    if len(data) > MAX_IN_TAR:
        raise ValueError("input bundle over 8 MB")
    return data


def unpack(data: bytes, out_dir: Path) -> dict:
    """Extract out/* into out_dir (data filter: no links out, no absolute paths, no devices); return result.json."""
    result: dict = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.name == "result.json" and m.isfile():
                result = json.loads(tar.extractfile(m).read(1024 * 1024))
            elif m.name.startswith("out/") and m.isfile() and m.size <= 25 * 1024 * 1024:
                m = m.replace(name=m.name[4:])
                tar.extract(m, out_dir, filter="data")
    return result


def _run_result(d: dict | None) -> RunResult | None:
    if not d:
        return None
    return RunResult(returncode=int(d.get("returncode", -1)), stdout=str(d.get("stdout", "")),
                     stderr=str(d.get("stderr", "")), timed_out=bool(d.get("timed_out")))


class FlyMachineRenderer:
    def __init__(self, settings: Settings, store: MediaStore, transport: httpx.AsyncBaseTransport | None = None,
                 api_base: str = API, wait_total_s: float = 300.0) -> None:
        self.s = settings
        self.store = store
        self.transport = transport
        self.api_base = api_base
        self.wait_total_s = wait_total_s

    def _client(self) -> httpx.AsyncClient:
        token = os.environ.get("FLY_API_TOKEN", "")
        return httpx.AsyncClient(
            base_url=f"{self.api_base}/v1/apps/{self.s.fly_render_app}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(90.0, connect=10.0),  # wait calls block up to 60 s
            transport=self.transport,
        )

    async def available(self) -> str | None:
        if not os.environ.get("FLY_API_TOKEN"):
            return "FLY_API_TOKEN is not set (deploy token for the tac-render app)"
        if not self.s.fly_render_app or not self.s.fly_render_image:
            return "TAC_FLY_RENDER_APP / TAC_FLY_RENDER_IMAGE not set"
        return None

    def machine_config(self, in_url: str, out_url: str, job: str) -> dict:
        return {
            "name": f"tac-render-{job}",
            **({"region": self.s.fly_render_region} if self.s.fly_render_region else {}),
            "skip_service_registration": True,
            "config": {
                "image": self.s.fly_render_image,
                "auto_destroy": True,
                "restart": {"policy": "no"},
                "guest": {"cpu_kind": "shared", "cpus": 1, "memory_mb": 2048},
                "init": {"exec": ["/usr/local/bin/python", "/app/fly_bootstrap.py"]},
                # no secrets: two presigned URLs (one object each, 10 min) and timeouts
                "env": {
                    "TAC_IN_URL": in_url,
                    "TAC_OUT_URL": out_url,
                    "TAC_CHECK_TIMEOUT": str(int(self.s.check_timeout_s)),
                    "TAC_RENDER_TIMEOUT": str(int(self.s.render_timeout_s)),
                },
                "services": [],  # nothing listens; no public IP is allocated to the tac-render app
                "dns": {"skip_registration": True},
                "metadata": {"tac_job": job},
            },
        }

    async def run_job(self, piece_dir: Path, out_dir: Path, work: Path) -> JobResult:
        job = secrets.token_hex(8)
        in_key, out_key = f"render-io/{job}/in.tar.gz", f"render-io/{job}/out.tar.gz"
        machine_id: str | None = None
        async with self._client() as api:
            try:
                await self.store.put(in_key, await asyncio.to_thread(bundle, piece_dir))
                body = self.machine_config(
                    self.store.presign(in_key, "GET", PRESIGN_TTL_S), self.store.presign(out_key, "PUT", PRESIGN_TTL_S), job
                )
                r = await api.post("/machines", json=body)
                if r.status_code != 200:
                    return JobResult(check=None, backend_error=f"create machine: HTTP {r.status_code} {r.text[:200]}")
                m = r.json()
                machine_id, instance_id = m["id"], m.get("instance_id", "")
                stopped = await self._wait_stopped(api, machine_id, instance_id)
                data = await self.store.get(out_key)
                if not stopped and data is None:
                    return JobResult(check=None, timed_out=True)
                if data is None or len(data) > MAX_OUT_TAR:
                    return JobResult(check=None, backend_error=f"machine {machine_id} produced no output"
                                     f"{await self._exit_hint(api, machine_id)}")
                result = await asyncio.to_thread(unpack, data, out_dir)
                iso = result.get("isolation") if isinstance(result.get("isolation"), dict) else None
                if result.get("error"):
                    return JobResult(check=None, backend_error=f"machine {machine_id}: {result['error']}", isolation=iso)
                return JobResult(check=_run_result(result.get("check")), render=_run_result(result.get("render")),
                                 isolation=iso)
            except (httpx.HTTPError, FlyApiError, KeyError, ValueError, tarfile.TarError) as exc:
                return JobResult(check=None, backend_error=f"{type(exc).__name__}: {exc}"[:300])
            finally:
                if machine_id:
                    await asyncio.shield(self._destroy(api, machine_id))
                await asyncio.shield(self.store.delete_prefix(f"render-io/{job}"))

    async def _wait_stopped(self, api: httpx.AsyncClient, machine_id: str, instance_id: str) -> bool:
        """True once the Machine is stopped or destroyed; False after wait_total_s."""
        deadline = time.monotonic() + self.wait_total_s
        while (left := deadline - time.monotonic()) > 0:
            params = {"state": "stopped", "timeout": max(1, min(WAIT_STEP_S, int(left)))}
            if instance_id:
                params["instance_id"] = instance_id  # required when waiting for "stopped" (docs)
            r = await api.get(f"/machines/{machine_id}/wait", params=params)
            if r.status_code == 200:
                return True
            if r.status_code == 404:  # auto_destroy already removed it: it has stopped
                return True
            if r.status_code == 408:  # this wait call timed out; keep waiting
                continue
            raise FlyApiError(f"wait: HTTP {r.status_code} {r.text[:200]}")
        return False

    async def _exit_hint(self, api: httpx.AsyncClient, machine_id: str) -> str:
        try:
            r = await api.get(f"/machines/{machine_id}")
            for ev in (r.json().get("events") or []) if r.status_code == 200 else []:
                exit_ev = ((ev.get("request") or {}).get("exit_event")) or {}
                if ev.get("type") == "exit" and exit_ev:
                    return f" (exit_code={exit_ev.get('exit_code')}, oom_killed={exit_ev.get('oom_killed', False)})"
        except (httpx.HTTPError, ValueError):
            pass
        return ""

    async def _destroy(self, api: httpx.AsyncClient, machine_id: str) -> None:
        try:
            r = await api.delete(f"/machines/{machine_id}", params={"force": "true"})
            if r.status_code not in (200, 404):
                log.error("destroy %s: HTTP %s %s", machine_id, r.status_code, r.text[:200])
        except httpx.HTTPError:
            log.exception("destroy %s failed; auto_destroy should still remove it after stop", machine_id)
