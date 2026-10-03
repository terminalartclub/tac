"""End-to-end against a RUNNING server, with the real tools/ and a real studio piece.

    TAC_ADMIN_TOKEN=<t> uv run tac-platform &
    TAC_E2E_URL=http://127.0.0.1:8790 TAC_E2E_ADMIN_TOKEN=<t> uv run pytest tests/test_e2e_live.py -s

device login (dev form) -> submit laps -> poll to in_review -> admin approve -> community.json + media.
Skipped unless TAC_E2E_URL is set.
"""

import asyncio
import json
import os
import re
import secrets
import time
from pathlib import Path

import httpx
import pytest

URL = os.environ.get("TAC_E2E_URL", "")
ADMIN = os.environ.get("TAC_E2E_ADMIN_TOKEN", "")
LAPS = Path(os.environ.get("TAC_E2E_PIECE", "/Users/aradaev/Desktop/Projects/terminal-art-club/studio/work/laps"))

pytestmark = pytest.mark.skipif(not URL or not LAPS.is_dir(), reason="needs TAC_E2E_URL and the laps studio dir")


def meta_from_notes(notes: str) -> dict:
    """Meta derived from laps/notes.md: title, catalog description, iteration count, loop length."""
    title = re.search(r"^# (\S+)", notes, re.M).group(1)
    desc = re.search(r"## catalog description\n(.+?)\n\n", notes, re.S).group(1)
    iterations = max(int(n) for h in re.findall(r"^### (.+)$", notes, re.M) for n in re.findall(r"iter-(\d+)", h))
    loop_s = float(re.search(r"loop ([\d.]+) s", notes).group(1))
    return {
        "title": title,
        "description": " ".join(desc.split()),
        # review.json lists laps as made by Fable; tools/import_seeds.py maps that to claude-fable-5-1
        "model": "claude-fable-5-1",
        "tokens": None,
        "iterations": iterations,
        "loop_s": loop_s,
        "license": "CC-BY-4.0 art / MIT code",
        "human_role": "none",  # studio piece: no person steered it
        "process_notes": ["iteration 1: the fish is a bun", "final: window light projected on the table"],
    }


async def test_live_end_to_end():
    notes = (LAPS / "notes.md").read_text()
    meta = meta_from_notes(notes)
    process = [LAPS / "iter-1" / "frame-000.00s.png", LAPS / "final" / "frame-000.00s.png"]
    process = [p for p in process if p.exists() and p.stat().st_size <= 600 * 1024]
    handle = f"e2e-{secrets.token_hex(3)}"
    t0 = time.monotonic()

    async with httpx.AsyncClient(base_url=URL, timeout=30) as c:
        # 1. device login, the human half done by posting the dev form
        d = (await c.post("/v1/auth/device", json={})).json()
        assert (await c.post("/v1/auth/token", json={"device_code": d["device_code"]})).status_code == 428
        r = await c.post("/device", data={"user_code": d["user_code"], "handle": handle})
        assert r.status_code == 200, r.text
        tok = await c.post("/v1/auth/token", json={"device_code": d["device_code"]})
        assert tok.status_code == 200, tok.text
        auth = {"authorization": f"Bearer {tok.json()['access_token']}"}
        assert (await c.get("/v1/me", headers=auth)).json() == {"handle": handle}

        # 2. submit the real piece
        files = [("piece", ("piece.py", (LAPS / "laps.py").read_bytes(), "text/x-python"))]
        files += [("process", (p.name, p.read_bytes(), "image/png")) for p in process]
        r = await c.post("/v1/submissions", headers=auth, files=files,
                         data={"meta": json.dumps(meta), "notes": notes})
        assert r.status_code == 202, r.text
        sub = r.json()
        print(f"\nsubmitted {sub['id']} as {handle}: {meta['title']} ({len(process)} process images)")

        # 3. poll until the pipeline is done (real render of laps ~16 s)
        seen = []
        for _ in range(150):
            st = (await c.get(f"/v1/submissions/{sub['id']}", headers=auth)).json()
            if not seen or seen[-1] != st["status"]:
                seen.append(st["status"])
            if st["status"] in ("in_review", "rejected", "published"):
                break
            await asyncio.sleep(2)
        print(f"statuses: {' -> '.join(seen)} reasons={st['reasons']} after {time.monotonic() - t0:.1f}s")
        assert st["status"] == "in_review", st
        preview = await c.get(st["preview_url"].replace(URL, ""))
        assert preview.status_code == 200 and preview.headers["content-type"] == "image/webp"

        # 4. human review
        assert ADMIN, "set TAC_E2E_ADMIN_TOKEN"
        r = await c.post(f"/v1/admin/submissions/{sub['id']}/approve", headers={"x-admin-token": ADMIN})
        assert r.status_code == 200, r.text

        # 5. the gallery feed and the media
        doc = (await c.get("/v1/community.json")).json()
        piece = next(p for p in doc["pieces"] if p["handle"] == handle)
        print(f"community.json: {piece['id']} model_label={piece['model_label']} stats={piece['stats']} "
              f"process={len(piece['process'])} totals={doc['totals']} week={doc['week']['label']} theme={doc['theme']['title']}")
        assert piece["human_role"] == "none" and piece["iterations"] == meta["iterations"]
        for key in (piece["preview"], piece["og"], piece["source"], *[p["image"] for p in piece["process"]]):
            m = await c.get(f"/media/{key}")
            assert m.status_code == 200, key
            print(f"  /media/{key}: {m.status_code} {m.headers['content-type']} {len(m.content)} B")
        assert (await c.get(f"/media/{piece['source']}")).content == (LAPS / "laps.py").read_bytes()
        print(f"e2e total {time.monotonic() - t0:.1f}s")

