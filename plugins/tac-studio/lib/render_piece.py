"""Render a TAC piece directory with the bundled virtual screen.

    python render_piece.py <piece_dir> --out <dir> [--timeout 120]

Writes into <dir>:
    preview.webp    animated, 540x960 (9:16 reel framing), the piece's native fps, quality 70
    og.jpg          still at t≈1 s, 540x960, for link unfurls
    stats.json      motion_median / seam / void (+ loop_s, fps, frames, raw vscreen stats)
    process/NN.webp each process/*.png, ≤540 px wide

The piece runs in a child process (its own process group) killed after --timeout
seconds of wall clock (exit 124). Needs rich, Pillow and fonttools: uses the current
interpreter when they import, else `uv run --with ...`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIMEOUT_S = 120
PREVIEW_W, PREVIEW_H = 540, 960
WEBP_QUALITY = 70
OG_T = 1.0
PROCESS_W = 540


def _deps_ok() -> bool:
    return all(importlib.util.find_spec(m) for m in ("rich", "PIL", "fontTools"))


def _worker_cmd() -> list[str]:
    if _deps_ok():
        return [sys.executable, str(Path(__file__).resolve())]
    return ["uv", "run", "-q", "--with", "rich", "--with", "Pillow", "--with", "fonttools",
            "python3", str(Path(__file__).resolve())]


def run_with_timeout(piece_dir: Path, out: Path, timeout: float, cols: int, rows: int) -> int:
    cmd = _worker_cmd() + ["--worker", str(piece_dir), "--out", str(out), "--cols", str(cols), "--rows", str(rows)]
    proc = subprocess.Popen(cmd, start_new_session=True)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Kill only the process group we created (the worker and anything it spawned).
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        print(f"error: render exceeded {timeout:.0f}s wall clock", file=sys.stderr)
        return 124


# ── worker (needs rich + Pillow) ───────────────────────────────────────────


def _ms_durations(durs: list[float]) -> list[int]:
    """Error-diffused integer milliseconds so the loop length stays exact."""
    out, target, emitted = [], 0.0, 0
    for d in durs:
        target += d * 1000
        q = max(1, round(target - emitted))
        out.append(q)
        emitted += q
    return out


def _reel_frame(img, size):  # type: ignore[no-untyped-def]
    from PIL import Image

    import vscreen
    W, H = size
    canvas = Image.new("RGB", (round(W * 2), round(H * 2)), vscreen.BG)  # 1080x1920 reel framing
    scale = min(canvas.width / img.width, canvas.height / img.height, 1.0)
    if scale < 1.0:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS)
    canvas.paste(img, ((canvas.width - img.width) // 2, (canvas.height - img.height) // 2))
    return canvas.resize((W, H), Image.Resampling.LANCZOS)


def worker(piece_dir: Path, out: Path, cols: int, rows: int) -> int:
    import asyncio

    from PIL import Image

    sys.path.insert(0, str(HERE))
    import vscreen

    code = (piece_dir / "piece.py").read_text(encoding="utf-8")
    cap = asyncio.run(vscreen.capture(code, cols, rows))
    grids = [vscreen.to_cells(b, cols, rows) for _, b in cap.samples]
    raw = vscreen.stats(grids, window=max(1, round(0.5 * cap.steps / cap.loop_s)))
    p90, seam = raw["step_change_p90"], raw["seam_change"]
    ratio = seam / p90 if p90 else (float("inf") if seam else 0.0)
    verdict = "CLEAN" if ratio <= 1.5 else "CHECK" if ratio <= 3.0 else "JUMP"

    durs = vscreen._step_durations(cap)
    ras = vscreen.Rasterizer()
    frames, frame_durs, og = [], [], None
    prev_key = None
    for (t, _), grid, d in zip(cap.samples, grids, durs):
        key = hash(tuple(tuple(r) for r in grid))
        if key == prev_key:
            frame_durs[-1] += d  # identical frame: extend the previous one
        else:
            frames.append(_reel_frame(ras.frame(grid), (PREVIEW_W, PREVIEW_H)))
            frame_durs.append(d)
            prev_key = key
        if t <= OG_T + 1e-6:
            og = frames[-1]
    out.mkdir(parents=True, exist_ok=True)
    frames[0].save(out / "preview.webp", save_all=True, append_images=frames[1:],
                   duration=_ms_durations(frame_durs), loop=0, quality=WEBP_QUALITY, method=4)
    (og or frames[0]).save(out / "og.jpg", quality=82, optimize=True, progressive=True)

    stats = {
        "motion_median": round(raw["motion_median"], 4),
        "seam": verdict,
        "void": round(raw["dark_fraction_mean"], 4),
        "loop_s": round(cap.loop_s, 3),
        "fps": round(cap.steps / cap.loop_s, 2),
        "steps": cap.steps,
        "frames": len(frames),
        "seam_ratio": None if ratio == float("inf") else round(ratio, 3),
        "raw": {k: round(v, 5) for k, v in raw.items()},
    }
    (out / "stats.json").write_text(json.dumps(stats, indent=2) + "\n")

    pngs = sorted((piece_dir / "process").glob("*.png")) if (piece_dir / "process").is_dir() else []
    if pngs:
        (out / "process").mkdir(exist_ok=True)
    for i, p in enumerate(pngs[:4], 1):
        im = Image.open(p).convert("RGB")
        if im.width > PROCESS_W:
            im = im.resize((PROCESS_W, round(im.height * PROCESS_W / im.width)), Image.Resampling.LANCZOS)
        im.save(out / "process" / f"{i:02d}.webp", quality=80, method=4)
    print(f"{out} · {len(frames)} frames · loop {cap.loop_s:.2f}s · {stats['fps']} fps · "
          f"motion {stats['motion_median']:.1%} · seam {verdict} · void {stats['void']:.0%} · "
          f"webp {(out / 'preview.webp').stat().st_size / 1e6:.1f} MB")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("piece_dir", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--timeout", type=float, default=TIMEOUT_S)
    ap.add_argument("--cols", type=int, default=80)
    ap.add_argument("--rows", type=int, default=66)
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if not (a.piece_dir / "piece.py").is_file():
        print(f"error: {a.piece_dir}/piece.py not found", file=sys.stderr)
        return 2
    if a.worker:
        return worker(a.piece_dir, a.out, a.cols, a.rows)
    return run_with_timeout(a.piece_dir, a.out, a.timeout, a.cols, a.rows)


if __name__ == "__main__":
    sys.exit(main())
