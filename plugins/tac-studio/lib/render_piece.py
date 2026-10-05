"""Render a TAC piece directory with the bundled virtual screen.

    python render_piece.py <piece_dir> --out <dir> [--timeout 120]

Writes into <dir>:
    preview.webp    animated, 540x960 (9:16 reel framing), the piece's native fps, quality 70
    og.jpg          still at t≈1 s, 540x960, for link unfurls
    stats.json      motion_median / seam / void (+ loop_s, fps, frames, raw vscreen stats)
    process/NN.webp each process/*.png, ≤540 px wide
    frames.cells.gz the frames as terminal cells for /tac:wall (wallframes.py): the piece's fps (≤30), or
                    15, 10, 5 when that passes 32 MiB; left out (the piece still renders) when even 5 fps does,
                    or there's no time left for another pass

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
UV_DEPS = ("rich==15.0.0", "Pillow==12.3.0", "fonttools==4.66.1")  # same pins as bin/tac, bin/tacctl


def _deps_ok() -> bool:
    return all(importlib.util.find_spec(m) for m in ("rich", "PIL", "fontTools"))


class NoUv(RuntimeError):
    pass


def _worker_cmd() -> list[str]:
    if _deps_ok():
        return [sys.executable, str(Path(__file__).resolve())]
    import uvfind

    uv = uvfind.find_uv()
    if uv is None:
        raise NoUv(uvfind.MISSING)
    # --isolated, --no-config: no .venv, uv.toml or [tool.uv] from the cwd's ancestors (see bin/tacctl)
    return [uv, "run", "-q", "--no-project", "--isolated", "--no-config", "--python", uvfind.PYTHON,
            *(a for d in UV_DEPS for a in ("--with", d)),
            "python3", str(Path(__file__).resolve())]


def run_with_timeout(piece_dir: Path, out: Path, timeout: float, cols: int, rows: int) -> int:
    try:
        cmd = _worker_cmd()
    except NoUv as e:
        print(str(e), file=sys.stderr)
        return 127
    cmd = cmd + ["--worker", str(piece_dir), "--out", str(out), "--cols", str(cols), "--rows", str(rows),
                 "--timeout", f"{timeout:g}"]
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
# Streams: capture → cells → stats window → rasterize → downscale → WebP encoder, one frame at a
# time. Peak memory no longer grows with loop length (wake: 480 frames held 2.4 GB before).


def _ms_durations(durs: list[float]) -> list[int]:
    """Error-diffused integer milliseconds so the loop length stays exact."""
    out, target, emitted = [], 0.0, 0
    for d in durs:
        target += d * 1000
        q = max(1, round(target - emitted))
        out.append(q)
        emitted += q
    return out


class WebPStream:
    """Incremental animated-WebP writer, byte-identical to
    `frames[0].save(path, save_all=True, append_images=frames[1:], duration=_ms_durations(durs),
    loop=0, quality=WEBP_QUALITY, method=4)` but holding one frame at a time. It mirrors Pillow's
    WebPImagePlugin._save_all on Pillow's own WebPAnimEncoder; without it, frames are buffered."""

    def __init__(self, path: Path, quality: int = WEBP_QUALITY, method: int = 4) -> None:
        try:
            from PIL import _webp
            anim = hasattr(_webp, "WebPAnimEncoder")
        except ImportError:
            anim = False
        self.path, self.quality, self.method = path, quality, method
        self._first = None          # held until a 2nd frame proves it's an animation
        self._enc = None
        self._target = 0.0
        self._emitted = 0
        self._buffered: list | None = None if anim else []
        self._durs: list[float] = []

    def _encoder(self):  # type: ignore[no-untyped-def]
        from PIL import _webp

        # Pillow defaults for lossy RGB: background (0,0,0,0) → 0, loop 0, kmin 3, kmax 5.
        return _webp.WebPAnimEncoder(self._first.size, 0, 0, False, 3, 5, False, False)

    def _add(self, img) -> None:  # type: ignore[no-untyped-def]
        self._enc.add(img.getim(), self._emitted, False, self.quality, 100, self.method)

    def _advance(self, duration_s: float) -> None:
        self._target += duration_s * 1000
        q = max(1, round(self._target - self._emitted))
        self._emitted += q

    def add(self, img, duration_s: float) -> None:  # type: ignore[no-untyped-def]
        """Append one frame shown for duration_s. The image is not retained after the call
        (except the very first frame, until a second one arrives)."""
        if img.mode not in ("RGB", "RGBA", "RGBX"):
            img = img.convert("RGB")
        if self._buffered is not None:
            self._buffered.append(img)
            self._durs.append(duration_s)
            return
        if self._enc is None and self._first is None:
            self._first, self._first_d = img, duration_s
            return
        if self._enc is None:
            self._enc = self._encoder()
            self._add(self._first)
            self._advance(self._first_d)
            self._first = None
        self._add(img)
        self._advance(duration_s)

    def close(self) -> None:
        if self._buffered is not None:
            fr = self._buffered
            fr[0].save(self.path, save_all=True, append_images=fr[1:], duration=_ms_durations(self._durs),
                       loop=0, quality=self.quality, method=self.method)
            return
        if self._enc is None:  # a single frame: Pillow saves it as a still WebP
            self._first.save(self.path, save_all=True, append_images=[], duration=_ms_durations([self._first_d]),
                             loop=0, quality=self.quality, method=self.method)
            return
        self._enc.add(None, self._emitted, False, self.quality, 100, 0)
        data = self._enc.assemble("", "", "")
        if data is None:
            raise OSError("cannot write file as WebP (encoder returned None)")
        self.path.write_bytes(data)


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


class StreamStats:
    """vscreen.stats(grids, window) computed over a stream, holding only window+1 grids."""

    def __init__(self, window: int, n: int) -> None:
        from collections import deque

        self.k = max(1, min(window, n - 1))
        self.win: deque = deque(maxlen=self.k + 1)
        self.first = None
        self.prev = None
        self.ever: set = set()
        self.motion: list[float] = []
        self.steps: list[float] = []
        self.dark: list[float] = []

    def add(self, grid) -> None:  # type: ignore[no-untyped-def]
        import vscreen

        self.win.append(grid)
        if len(self.win) == self.k + 1:
            self.motion.append(vscreen._changed(self.win[0], grid, self.ever))
        if self.prev is not None:
            self.steps.append(vscreen._changed(self.prev, grid))
        if self.first is None:
            self.first = grid
        self.prev = grid
        total = len(grid) * len(grid[0])
        self.dark.append(sum(1 for row in grid for c in row if vscreen._cell_lum(c) < 24) / total)

    def result(self) -> dict[str, float]:
        import statistics

        import vscreen
        total = len(self.first) * len(self.first[0])
        steps = self.steps
        p90 = sorted(steps)[int(0.9 * (len(steps) - 1))] if steps else 0.0
        return {
            "motion_median": statistics.median(self.motion) if self.motion else 0.0,
            "motion_max": max(self.motion) if self.motion else 0.0,
            "step_change_p90": p90,
            "seam_change": vscreen._changed(self.prev, self.first),
            "dark_fraction_mean": statistics.mean(self.dark),
            "cells_ever_moving": len(self.ever) / total,
        }


def _frames_pass(vscreen, code: str, cols: int, rows: int, fps: int) -> bytes:  # type: ignore[no-untyped-def]
    """One more capture, for frames.cells.gz only, at `fps` (raises wallframes.TooBig)."""
    import asyncio

    import wallframes

    enc = wallframes.Encoder(cols, rows, fps)
    cap = asyncio.run(vscreen.capture(code, cols, rows, on_sample=lambda t, b: enc.sample(t, vscreen.to_cells(b, cols, rows))))
    return enc.finish(cap.loop_s)


def worker(piece_dir: Path, out: Path, cols: int, rows: int, timeout: float = TIMEOUT_S) -> int:
    import asyncio
    import time

    from PIL import Image

    sys.path.insert(0, str(HERE))
    import vscreen
    import wallframes

    started = time.monotonic()
    code = (piece_dir / "piece.py").read_text(encoding="utf-8")
    # Pass 1: loop length and step count only (the stats window depends on fps). Nothing is kept.
    probe = asyncio.run(vscreen.capture(code, cols, rows, on_sample=lambda t, b: None))
    loop_s, n_steps = probe.loop_s, probe.steps
    out.mkdir(parents=True, exist_ok=True)

    st = StreamStats(max(1, round(0.5 * n_steps / loop_s)), n_steps)
    native_fps = max(1, min(wallframes.MAX_FPS, round(n_steps / loop_s)))
    wall: dict = {"enc": wallframes.Encoder(cols, rows, native_fps), "why": ""}
    ras = vscreen.Rasterizer()
    webp = WebPStream(out / "preview.webp")
    state = {"t": None, "key": None, "img": None, "d": 0.0, "frames": 0, "og": None}

    def flush() -> None:
        if state["img"] is not None:
            webp.add(state["img"], state["d"])
            state["img"] = None

    def on_sample(t: float, buf: list) -> None:
        if state["t"] is not None:
            state["d"] += t - state["t"]  # the previous sample's step duration is now known
        grid = vscreen.to_cells(buf, cols, rows)
        st.add(grid)
        if wall["enc"] is not None:  # the wall's frames ride along on this pass, at the piece's own fps
            try:
                wall["enc"].sample(t, grid)
            except wallframes.TooBig as e:
                wall.update(enc=None, why=str(e))
        key = hash(tuple(tuple(r) for r in grid))
        if key != state["key"]:
            flush()
            state.update(key=key, img=_reel_frame(ras.frame(grid), (PREVIEW_W, PREVIEW_H)), d=0.0)
            state["frames"] += 1
        if t <= OG_T + 1e-6:
            state["og"] = state["img"]
        state["t"] = t

    main_started = time.monotonic()
    cap = asyncio.run(vscreen.capture(code, cols, rows, on_sample=on_sample))
    main_s = time.monotonic() - main_started
    state["d"] += cap.loop_s - state["t"]
    flush()
    webp.close()
    state["og"].save(out / "og.jpg", quality=82, optimize=True, progressive=True)

    raw = st.result()
    p90, seam = raw["step_change_p90"], raw["seam_change"]
    ratio = seam / p90 if p90 else (float("inf") if seam else 0.0)
    verdict = "CLEAN" if ratio <= 1.5 else "CHECK" if ratio <= 3.0 else "JUMP"
    stats = {
        "motion_median": round(raw["motion_median"], 4),
        "seam": verdict,
        "void": round(raw["dark_fraction_mean"], 4),
        "loop_s": round(cap.loop_s, 3),
        "fps": round(cap.steps / cap.loop_s, 2),
        "steps": cap.steps,
        "frames": state["frames"],
        "seam_ratio": None if ratio == float("inf") else round(ratio, 3),
        "raw": {k: round(v, 5) for k, v in raw.items()},
    }
    (out / "stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    _write_frames(vscreen, wallframes, wall, code, cols, rows, cap.loop_s, native_fps, main_s, started, timeout, out)

    pngs = sorted((piece_dir / "process").glob("*.png")) if (piece_dir / "process").is_dir() else []
    if pngs:
        (out / "process").mkdir(exist_ok=True)
    for i, p in enumerate(pngs[:4], 1):
        im = Image.open(p).convert("RGB")
        if im.width > PROCESS_W:
            im = im.resize((PROCESS_W, round(im.height * PROCESS_W / im.width)), Image.Resampling.LANCZOS)
        im.save(out / "process" / f"{i:02d}.webp", quality=80, method=4)
    print(f"{out} · {state['frames']} frames · loop {cap.loop_s:.2f}s · {stats['fps']} fps · "
          f"motion {stats['motion_median']:.1%} · seam {verdict} · void {stats['void']:.0%} · "
          f"webp {(out / 'preview.webp').stat().st_size / 1e6:.1f} MB")
    return 0


def _write_frames(vscreen, wallframes, wall: dict, code: str, cols: int, rows: int, loop_s: float,  # type: ignore[no-untyped-def]
                  native_fps: int, main_s: float, started: float, timeout: float, out: Path) -> None:
    """frames.cells.gz, or nothing: the wall is optional, the render is not. Over the cap at the piece's fps,
    another capture at each lower step, only while it fits in half the render's time (each pass costs about
    the main pass, scaled by fps)."""
    import time

    try:
        data = wall["enc"].finish(loop_s) if wall["enc"] is not None else None
    except wallframes.TooBig as e:
        data, wall["why"] = None, str(e)
    for fps in (f for f in wallframes.FPS_STEPS if f < native_fps):
        if data is not None:
            break
        if time.monotonic() - started + main_s * fps / native_fps > timeout / 2:
            print(f"frames.cells.gz skipped: {wall['why']}; no time for a {fps} fps pass", file=sys.stderr)
            return
        try:
            data = _frames_pass(vscreen, code, cols, rows, fps)
        except wallframes.TooBig as e:
            wall["why"] = str(e)
    if data is None:
        print(f"frames.cells.gz skipped: {wall['why']}", file=sys.stderr)
        return
    if len(data) > wallframes.MAX_GZ:
        print(f"frames.cells.gz skipped: {len(data)} bytes gzipped, over {wallframes.MAX_GZ}", file=sys.stderr)
        return
    (out / "frames.cells.gz").write_bytes(data)


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
        return worker(a.piece_dir, a.out, a.cols, a.rows, a.timeout)
    return run_with_timeout(a.piece_dir, a.out, a.timeout, a.cols, a.rows)


if __name__ == "__main__":
    sys.exit(main())
