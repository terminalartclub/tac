"""Assemble build/community.json (the gallery site's data contract) from pieces/ and build/.

    python3 tools/build_site.py [--render] [--build build]

--render renders every piece whose build is missing or older than its sources (needs rich,
Pillow, fonttools; see render_piece.py). Without it, pieces with no render fail the build.
Week, theme and picks come from curation.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "plugins" / "tac-studio" / "lib"
sys.path.insert(0, str(LIB))
import meta as metamod  # noqa: E402
from check_piece import check_dir  # noqa: E402
from notes import human_role  # noqa: E402


def _stale(piece: Path, out: Path) -> bool:
    stamp = out / "stats.json"
    if not (stamp.exists() and (out / "preview.webp").exists() and (out / "og.jpg").exists()):
        return True
    inputs = [piece / "piece.py", *sorted((piece / "process").glob("*.png"))]  # meta/notes don't affect pixels
    newest = max(p.stat().st_mtime for p in inputs)
    return newest > stamp.stat().st_mtime


def _label(png: Path) -> str:
    """process/02-iteration-4.png → 'iteration 4'."""
    return re.sub(r"^\d+[-_ ]*", "", png.stem).replace("-", " ").replace("_", " ").strip() or png.stem


def piece_entry(piece: Path, build: Path, picks: set[str]) -> dict:
    handle, slug = piece.parent.name, piece.name
    pid = f"{handle}/{slug}"
    m = metamod.load(piece)
    out = build / handle / slug
    stats = json.loads((out / "stats.json").read_text())
    shutil.copyfile(piece / "piece.py", out / "piece.py")
    pngs = sorted((piece / "process").glob("*.png"))[:4] if (piece / "process").is_dir() else []
    notes = m.get("process_notes") or []
    process = []
    for i, p in enumerate(pngs, 1):
        img = out / "process" / f"{i:02d}.webp"
        if not img.exists():
            raise SystemExit(f"{pid}: missing {img} (render it with --render)")
        process.append({"label": _label(p), "image": f"{pid}/process/{i:02d}.webp",
                        "note": notes[i - 1] if i - 1 < len(notes) and notes[i - 1] else ""})
    loop_s = m.get("loop_s") or stats.get("loop_s")
    return {
        "id": pid, "handle": handle, "slug": slug,
        "title": m.get("title") or slug,
        "description": m.get("description") or "",
        "model": m["model"], "model_label": metamod.model_label(m["model"]),
        "house_artist": bool(m.get("house_artist", False)),
        # computed from notes.md's direction log (check_piece rejects a declared role that disagrees)
        "human_role": human_role((piece / "notes.md").read_text(encoding="utf-8"))
        if (piece / "notes.md").exists() else "none",
        "tokens": m.get("tokens"),
        "iterations": m.get("iterations"),
        "size": m.get("size") or "full",
        "loop_s": float(loop_s) if loop_s is not None else None,
        "license": m.get("license") or metamod.DEFAULT_LICENSE,
        "created": m.get("created"),
        "pick": pid in picks,
        "preview": f"{pid}/preview.webp", "og": f"{pid}/og.jpg", "source": f"{pid}/piece.py",
        "process": process,
        "stats": {"motion_median": stats["motion_median"], "seam": stats["seam"], "void": stats["void"]},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--build", type=Path, default=ROOT / "build")
    ap.add_argument("--pieces", type=Path, default=ROOT / "pieces")
    ap.add_argument("--curation", type=Path, default=ROOT / "curation.json")
    a = ap.parse_args(argv)
    cur = json.loads(a.curation.read_text()) if a.curation.exists() else {}
    picks = set(cur.get("picks", []))
    pieces = sorted(p for p in a.pieces.glob("*/*") if p.is_dir() and (p / "piece.py").exists())
    bad = {f"{p.parent.name}/{p.name}": r for p in pieces if (r := check_dir(p))}
    if bad:
        for pid, reasons in bad.items():
            print(f"{pid}: {reasons}", file=sys.stderr)
        return 1
    for p in pieces:
        out = a.build / p.parent.name / p.name
        if a.render and _stale(p, out):
            if out.exists():
                shutil.rmtree(out)
            r = subprocess.run([sys.executable, str(LIB / "render_piece.py"), str(p), "--out", str(out)])
            if r.returncode != 0:
                print(f"render failed: {p} (exit {r.returncode})", file=sys.stderr)
                return 1
        elif not (out / "stats.json").exists():
            print(f"{p}: not rendered — run with --render", file=sys.stderr)
            return 1
    entries = [piece_entry(p, a.build, picks) for p in pieces]
    entries.sort(key=lambda e: (not e["pick"], -(dt.date.fromisoformat(e["created"]).toordinal()
                                                 if e["created"] else 0), e["id"]))
    now = dt.datetime.now(dt.timezone.utc)
    iso = now.isocalendar()
    doc = {
        "generated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "week": cur.get("week") or f"{iso.year}-W{iso.week:02d}",
        "theme": cur.get("theme") or {"title": "", "blurb": ""},
        "totals": {"pieces": len(entries), "artists": len({e["handle"] for e in entries}),
                   "tokens": sum(e["tokens"] for e in entries if isinstance(e["tokens"], int))},
        "pieces": entries,
    }
    a.build.mkdir(parents=True, exist_ok=True)
    (a.build / "community.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print(f"{a.build / 'community.json'} · {len(entries)} pieces · {doc['totals']['artists']} artists · "
          f"{doc['totals']['tokens']:,} tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
