"""One-off: import the 10 TAC studio house-artist pieces as community seed data.

    uv run --with rich --with Pillow --with fonttools python3 tools/import_seeds.py [--tac ../terminal-art-club]

Reads terminal-art-club/studio/work/<slug>/ and collection.js (read-only) and writes
pieces/<handle>/<slug>/ {piece.py, meta.yaml, notes.md, process/*.png}. Process frames are
rendered at t=0 with the bundled renderer: iteration 1, the v1 (when one was kept), the final.
Captions come from tools/seed_captions.json (tight paraphrases of each notes.md critique); the
notes.md heuristics are the fallback.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "plugins" / "tac-studio" / "lib"
sys.path.insert(0, str(LIB))
import meta as metamod  # noqa: E402
import notes as notesmod  # noqa: E402
import vscreen  # noqa: E402
from check_piece import check_dir  # noqa: E402
from tacctl import save_process_png  # noqa: E402

OPUS, FABLE = ("studio-opus", "claude-opus-5-5"), ("studio-fable", "claude-fable-5-1")
# slug: (artist, subagent tokens incl. revision passes, iterations, v1 script or None, v1's final iter)
SEEDS = {
    "chlorine": (OPUS, 391831, 12, "iter-9.py", 9),  # pre-revision final (notes: "iter-9 (final → chlorine.py)")
    "laps": (FABLE, 403746, 14, "laps-v1.py", None),
    "sodium": (FABLE, 403269, 13, "sodium-v1.py", None),
    "eastbound": (OPUS, 366925, 10, None, None),
    "insomnia": (OPUS, 430650, 9, None, None),
    "seeing": (FABLE, 265133, 6, None, None),
    "hollow": (FABLE, 318430, 8, None, None),
    "wake": (OPUS, 500646, 14, None, None),
    "airshaft": (OPUS, 528061, 10, "airshaft-v1.py", None),
    "hush": (FABLE, 380892, 13, "hush-v1.py", None),
}
CREATED = "2026-10-02"


def descriptions(collection_js: str) -> dict[str, str]:
    out = {}
    for slug in SEEDS:
        m = re.search(r"id: '%s',.*?description: '((?:[^'\\]|\\.)*)'" % slug, collection_js, re.S)
        out[slug] = m.group(1).replace("\\'", "'") if m else ""
    return out


def frame(src: Path, t: float = 0.0):  # type: ignore[no-untyped-def]
    cap = asyncio.run(vscreen.capture(src.read_text(encoding="utf-8"), 80, 66, [t]))
    return vscreen.Rasterizer().frame(vscreen.to_cells(cap.samples[0][1], 80, 66))


def v1_final_iter(text: str) -> int:
    """The iteration a '(final → x.py)' heading marks first — the pre-revision final."""
    m = re.search(r"^###\s+iter-(\d+)\s+\(final", text, re.M)
    return int(m.group(1)) if m else notesmod.last_iter(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tac", type=Path, default=ROOT.parent / "terminal-art-club")
    a = ap.parse_args()
    work = a.tac / "studio" / "work"
    desc = descriptions((a.tac / "collection.js").read_text(encoding="utf-8"))
    for slug, ((handle, model), tokens, iters, v1, v1_iter) in SEEDS.items():
        src, dst = work / slug, ROOT / "pieces" / handle / slug
        if dst.exists():
            shutil.rmtree(dst)
        (dst / "process").mkdir(parents=True)
        shutil.copyfile(src / f"{slug}.py", dst / "piece.py")
        shutil.copyfile(src / "notes.md", dst / "notes.md")
        text = (src / "notes.md").read_text(encoding="utf-8")
        loop_s = asyncio.run(vscreen.capture((src / f"{slug}.py").read_text(encoding="utf-8"), 80, 66)).loop_s

        steps = [("iteration-1", src / "iter-1.py", notesmod.critique(text, 1))]
        if v1:
            k = v1_iter or v1_final_iter(text)
            steps.append(("v1", src / v1, notesmod.critique(text, k, ("biggest problem", "works"))))
        steps.append(("final", src / f"{slug}.py",
                      notesmod.critique(text, notesmod.last_iter(text), ("works", "biggest problem"))))
        curated = json.loads((ROOT / "tools" / "seed_captions.json").read_text()).get(slug, {})
        captions = []
        for i, (label, script, note) in enumerate(steps, 1):
            note = curated.get(label.replace("-", " "), note)
            if not save_process_png(frame(script), dst / "process" / f"{i:02d}-{label}.png"):
                raise SystemExit(f"{slug}: process frame {label} over the size cap")
            captions.append(note)
        m = {"title": slug, "description": desc[slug], "handle": handle, "model": model,
             "tokens": tokens, "iterations": iters, "loop_s": round(loop_s, 3),
             "license": metamod.DEFAULT_LICENSE, "created": CREATED, "size": "full", "human_role": "none", "house_artist": True,
             "tokens_source": "subagent-total", "process_notes": captions}
        (dst / "meta.yaml").write_text(metamod.dumps_yaml(m), encoding="utf-8")
        reasons = check_dir(dst)
        print(f"{handle}/{slug}: loop {loop_s:.1f}s · {len(steps)} process frames · "
              f"{'ok' if not reasons else reasons}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
