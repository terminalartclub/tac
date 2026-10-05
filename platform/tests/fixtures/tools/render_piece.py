"""Stand-in for builder-community's tools/render_piece.py (same CLI contract).

python render_piece.py <piece_dir> --out <dir> -> preview.webp, og.jpg, stats.json, process/*.webp.
Draws a synthetic 12-frame animation instead of running the piece. A piece containing
"TEST:render-fail" exits 3; "TEST:render-hang" sleeps forever (for timeout tests); "TEST:render-inner-timeout" sleeps for the
--timeout it was given and exits 124 like the real one.
"""

import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw


def main() -> int:
    piece_dir, out = Path(sys.argv[1]), Path(sys.argv[sys.argv.index("--out") + 1])
    src = (piece_dir / "piece.py").read_text()
    if "TEST:render-fail" in src:
        print("Traceback: boom", file=sys.stderr)
        return 3
    if "TEST:render-hang" in src:
        time.sleep(3600)
    if "TEST:render-inner-timeout" in src:
        timeout = float(sys.argv[sys.argv.index("--timeout") + 1])
        time.sleep(timeout)
        print(f"error: render exceeded {timeout:.0f}s wall clock", file=sys.stderr)
        return 124
    frames = []
    for i in range(12):
        im = Image.new("RGB", (160, 132), (8, 8, 15))
        d = ImageDraw.Draw(im)
        x = 20 + i * 10
        d.ellipse((x, 50, x + 24, 74), fill=(230, 120, 30))
        frames.append(im)
    out.mkdir(parents=True, exist_ok=True)
    frames[0].save(out / "preview.webp", save_all=True, append_images=frames[1:], duration=100, loop=0)
    frames[0].resize((1200, 630)).save(out / "og.jpg", quality=85)
    procs = sorted((piece_dir / "process").glob("*.png")) if (piece_dir / "process").is_dir() else []
    if procs:
        (out / "process").mkdir(exist_ok=True)
        for i, p in enumerate(procs, 1):
            Image.open(p).convert("RGB").save(out / "process" / f"{i:02d}.webp")
    stats = {"motion_median": 0.027, "seam": "CLEAN", "void": 0.64, "loop_s": 30.0, "fps": 10, "frames": 300}
    (out / "stats.json").write_text(json.dumps(stats))
    write_frames(src, out)
    return 0


def write_frames(src: str, out: Path) -> None:
    """frames.cells.gz for the wall, from the real format code: 3 frames of an 8x4 screen whose first cell
    counts the frame. TEST:no-frames -> none; TEST:bad-frames -> garbage; TEST:bomb-frames -> a gzip bomb."""
    import zlib
    from types import SimpleNamespace

    from tac_platform import wallframes

    if "TEST:no-frames" in src:
        return
    if "TEST:bad-frames" in src:
        (out / "frames.cells.gz").write_bytes(b"not frames at all")
        return
    if "TEST:bomb-frames" in src:
        (out / "frames.cells.gz").write_bytes(zlib.compress(bytes(64 * 2**20), 9, wbits=31))  # 64 MiB of zeros
        return
    enc = wallframes.Encoder(8, 4, 10)
    for k in range(3):
        grid = [[SimpleNamespace(ch="0123456789"[k] if (x, y) == (0, 0) else "▀", fg=(230, 120, 30), bg=(8, 8, 15))
                 for x in range(8)] for y in range(4)]
        enc.sample(k / 10, grid)
    (out / "frames.cells.gz").write_bytes(enc.finish(0.3))


if __name__ == "__main__":
    sys.exit(main())
