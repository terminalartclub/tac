"""Virtual terminal screen for TAC pieces.

Runs a piece (claude-panel script contract) on a virtual clock and rasterizes
each frame the way a real terminal does: near-black ground, Menlo glyphs,
block/box elements drawn as exact cell geometry. No window, no screencapture.

Bundled copy of terminal-art-club/studio/vscreen.py (plus Linux font fallbacks).

    tac sheet  piece.py --out work/x/            contact sheet + full-res frames + motion stats
    tac frame  piece.py --t 3.5 --out f.png      one frame at virtual time t
    tac gif    piece.py --out preview.gif        site preview
    tac mp4    piece.py --out vertical.mp4       1080x1920 reel (no audio)
    tac play   piece.py                          live, in this terminal
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import io
import math
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from rich.cells import cell_len
from rich.columns import Columns
from rich.console import Console, Group
from rich.highlighter import ReprHighlighter
from rich.measure import Measurement
from rich.pretty import Pretty
from rich.protocol import is_renderable
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.terminal_theme import MONOKAI
from rich.text import Text

# 80x66 cells at 13x28px = 1040x1848 — fills a 1080x1920 reel with a thin pad.
DEFAULT_COLS = 80
DEFAULT_ROWS = 66
CELL_W = 13
CELL_H = 28
FONT_SIZE = 22
BG = (8, 8, 15)
FG = (204, 204, 204)
REEL = (1080, 1920)
SIZES = {"reel": REEL, "iphone": (1206, 2622)}  # iphone = 17 Pro panel, 19.5:9
MAX_STEPS = 200_000
MIN_WIDTH = 78  # RichLog.min_width: non-Text renderables never render narrower
DIM = 0.66  # Textual's DIM_FACTOR

# macOS fonts first (what TAC's published renders use); Linux fallbacks so CI runners render too.
FONT_CHAIN = [
    ("/System/Library/Fonts/Menlo.ttc", 0),
    ("/System/Library/Fonts/Apple Symbols.ttf", 0),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ("/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf", 0),
]
BOLD_CHAIN = [
    ("/System/Library/Fonts/Menlo.ttc", 1),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 0),
]


# ── capture: run the script on a virtual clock ────────────────────────────


class _Done(BaseException):
    """BaseException so a piece's own `except Exception` can't swallow it."""


class _Runaway(BaseException):
    pass


class _Canvas:
    def __init__(self) -> None:
        self.buffer: list[Any] = []

    def clear(self) -> None:
        self.buffer.clear()

    def write(self, renderable: Any) -> None:
        # Live RichLog renders on write; snapshot so later in-place mutation can't leak back.
        if isinstance(renderable, Text):
            renderable = renderable.copy()
        elif not isinstance(renderable, str):
            try:
                renderable = copy.deepcopy(renderable)
            except Exception:
                pass
        self.buffer.append(renderable)


def _namespace(canvas: Any, sleep: Any, cols: int, rows: int) -> dict[str, Any]:
    return {
        "canvas": canvas, "sleep": sleep, "width": cols, "height": rows,
        "Text": Text, "Panel": Panel, "Table": Table, "Columns": Columns,
        "Syntax": Syntax,
    }


def _compile(code: str) -> Any:
    wrapped = "async def __script__():\n" + "".join(f"    {ln}\n" for ln in code.splitlines())
    return compile(wrapped, "<piece>", "exec")


@dataclass
class Capture:
    loop_s: float           # virtual length of one pass of the script
    steps: int              # sleep() calls in one pass
    samples: list[tuple[float, list[Any]]]


async def capture(code: str, cols: int, rows: int, times: list[float] | None = None,
                  *, seed: int = 42, on_sample: Any = None) -> Capture:
    """Sample the canvas at each virtual time in `times` (seconds, ascending).

    times=None → every sleep() step of exactly one pass (for gif/mp4/stats).
    Passes repeat like the live viewer's outer loop, so t may exceed one pass.
    on_sample(t, buffer) → stream each sample instead of keeping it (Capture.samples stays empty,
    so memory doesn't grow with loop length; tac-community addition).
    """
    compiled = _compile(code)
    canvas = _Canvas()
    clock = 0.0
    pending = sorted(times) if times is not None else None
    samples: list[tuple[float, list[Any]]] = []
    first_pass: tuple[float, int] | None = None
    steps = 0
    n_streamed = 0

    async def fake_sleep(dt: float = 0) -> None:
        nonlocal clock, steps, n_streamed
        dt = max(float(dt), 0.0)
        if dt == 0:
            return
        if steps > MAX_STEPS:
            raise _Runaway(f"no end after {MAX_STEPS} steps — top-level `while True`?")
        if pending is None:
            if on_sample is not None:
                on_sample(clock, list(canvas.buffer))
                n_streamed += 1
            else:
                samples.append((clock, list(canvas.buffer)))
        else:
            eps = 1e-6  # float drift: 0.1 summed 40x != 4.0; boundary targets belong to the next frame
            while pending and clock - eps <= pending[0] < clock + max(dt, 1e-9) - eps:
                samples.append((pending.pop(0), list(canvas.buffer)))
            if not pending:
                raise _Done
        clock += dt
        steps += 1

    real_seed = random.seed

    def fixed_seed(a: Any = None, *args: Any, **kw: Any) -> None:
        real_seed(seed if a is None else a, *args, **kw)  # random.seed() → deterministic previews

    random.seed = fixed_seed  # type: ignore[assignment]
    try:
        for _pass in range(10_000):
            random.seed(seed)
            canvas.clear()
            ns = _namespace(canvas, fake_sleep, cols, rows)
            start = clock
            exec(compiled, ns)
            try:
                await ns["__script__"]()
            except _Done:
                break
            if first_pass is None:
                first_pass = (clock - start, steps)
            if clock - start <= 0:
                raise RuntimeError("one pass of the script advanced 0s of virtual time — no sleep()?")
            if pending is None or not pending:
                break
    finally:
        random.seed = real_seed  # type: ignore[assignment]
    loop_s, n = first_pass if first_pass else (clock, steps)
    if not samples and not n_streamed:
        raise RuntimeError("piece produced no frames — did it call canvas.write() and sleep()?")
    return Capture(loop_s, n, samples)


# ── rasterize: segments → cells → pixels ──────────────────────────────────


@dataclass(frozen=True)
class Cell:
    ch: str
    fg: tuple[int, int, int]
    bg: tuple[int, int, int]
    bold: bool


def _rgb(color: Any, default: tuple[int, int, int], is_fg: bool) -> tuple[int, int, int]:
    if color is None or color.is_default:
        return default
    t = color.get_truecolor(MONOKAI, foreground=is_fg)
    rgb = (t.red, t.green, t.blue)
    # Published pipeline paints black grounds as the DNA near-black.
    ansi_black = color.triplet is None and color.number == 0
    if rgb == (0, 0, 0):
        return BG  # pure black is the ground on both halves of a ▀ cell — no alternate-row stripes
    return default if not is_fg and ansi_black else rgb


_HIGHLIGHT = ReprHighlighter()


def to_cells(buffer: list[Any], cols: int, rows: int) -> list[list[Cell]]:
    console = Console(width=cols, height=rows, file=io.StringIO(), force_terminal=True,
                      color_system="truecolor", legacy_windows=False)
    # Mirror RichLog.write: str → markup Text; render at measured width, no wrap,
    # crop at the viewport; auto_scroll keeps the tail visible.
    opts = console.options.update(width=cols, height=None)
    text_opts = opts.update(no_wrap=True, overflow="ignore")
    lines: list[list[Any]] = []
    for r in buffer:
        if isinstance(r, str):
            r = _HIGHLIGHT(Text.from_markup(r))
        elif not isinstance(r, Text) and not is_renderable(r):
            r = Pretty(r)
        o = text_opts if isinstance(r, Text) else opts
        w = max(MIN_WIDTH, min(cols, Measurement.get(console, o, r).maximum))
        lines.extend(console.render_lines(r, o.update_width(w), pad=False))
    grid: list[list[Cell]] = []
    blank = Cell(" ", FG, BG, False)
    for segs in lines[-rows:]:
        row: list[Cell] = []
        for seg in segs:
            if seg.control:
                continue
            st = seg.style
            fg = _rgb(st.color if st else None, FG, True)
            bg = _rgb(st.bgcolor if st else None, BG, False)
            if st and st.reverse:
                fg, bg = bg, fg
            if st and st.dim:
                fg = tuple(int(c * DIM + b * (1 - DIM)) for c, b in zip(fg, bg))  # type: ignore[assignment]
            bold = bool(st and st.bold)
            for ch in seg.text:
                w = cell_len(ch)
                if w == 0:
                    continue
                row.append(Cell(ch, fg, bg, bold))
                if w == 2:
                    row.append(Cell("", fg, bg, bold))
        row = row[:cols] + [blank] * max(0, cols - len(row))
        grid.append(row)
    grid += [[blank] * cols for _ in range(rows - len(grid))]
    return grid


# Block elements as (x0, y0, x1, y1) fractions of the cell, drawn in fg.
_BLOCKS: dict[str, list[tuple[float, float, float, float]]] = {
    "█": [(0, 0, 1, 1)], "▀": [(0, 0, 1, .5)], "▄": [(0, .5, 1, 1)],
    "▌": [(0, 0, .5, 1)], "▐": [(.5, 0, 1, 1)], "▔": [(0, 0, 1, .125)], "▕": [(.875, 0, 1, 1)],
    "▖": [(0, .5, .5, 1)], "▗": [(.5, .5, 1, 1)], "▘": [(0, 0, .5, .5)], "▝": [(.5, 0, 1, .5)],
    "▙": [(0, 0, .5, 1), (.5, .5, 1, 1)], "▛": [(0, 0, 1, .5), (0, .5, .5, 1)],
    "▜": [(0, 0, 1, .5), (.5, .5, 1, 1)], "▟": [(.5, 0, 1, 1), (0, .5, .5, 1)],
    "▚": [(0, 0, .5, .5), (.5, .5, 1, 1)], "▞": [(.5, 0, 1, .5), (0, .5, .5, 1)],
}
for _i, _c in enumerate("▁▂▃▄▅▆▇", start=1):
    _BLOCKS[_c] = [(0, 1 - _i / 8, 1, 1)]
for _i, _c in enumerate("▏▎▍▌▋▊▉", start=1):
    _BLOCKS[_c] = [(0, 0, _i / 8, 1)]
_SHADES = {"░": 0.25, "▒": 0.5, "▓": 0.75}

# Box drawing as arms (left, right, up, down) with weight 1=light 2=heavy 3=double.
_BOX: dict[str, tuple[int, int, int, int]] = {
    "─": (1, 1, 0, 0), "│": (0, 0, 1, 1), "┌": (0, 1, 0, 1), "┐": (1, 0, 0, 1),
    "└": (0, 1, 1, 0), "┘": (1, 0, 1, 0), "├": (0, 1, 1, 1), "┤": (1, 0, 1, 1),
    "┬": (1, 1, 0, 1), "┴": (1, 1, 1, 0), "┼": (1, 1, 1, 1),
    "━": (2, 2, 0, 0), "┃": (0, 0, 2, 2), "┏": (0, 2, 0, 2), "┓": (2, 0, 0, 2),
    "┗": (0, 2, 2, 0), "┛": (2, 0, 2, 0), "╋": (2, 2, 2, 2),
    "═": (3, 3, 0, 0), "║": (0, 0, 3, 3), "╔": (0, 3, 0, 3), "╗": (3, 0, 0, 3),
    "╚": (0, 3, 3, 0), "╝": (3, 0, 3, 0),
    "╴": (1, 0, 0, 0), "╶": (0, 1, 0, 0), "╵": (0, 0, 1, 0), "╷": (0, 0, 0, 1),
}
_ROUND = {"╭": (1, 1), "╮": (-1, 1), "╰": (1, -1), "╯": (-1, -1)}  # (h arm dir, v arm dir)


class Rasterizer:
    def __init__(self, cw: int = CELL_W, chh: int = CELL_H, size: int = FONT_SIZE,
                 shade: str = "blend") -> None:
        self.cw, self.ch, self.size, self.shade = cw, chh, size, shade
        self.fonts = []
        for path, idx in FONT_CHAIN:
            if Path(path).exists():
                self.fonts.append(ImageFont.truetype(path, size, index=idx))
        if not self.fonts:
            raise SystemExit("no monospace font found: install Menlo (macOS) or fonts-dejavu-core (Linux)")
        bold = next(((p, i) for p, i in BOLD_CHAIN if Path(p).exists()), None)
        self.bold = ImageFont.truetype(bold[0], size, index=bold[1]) if bold else self.fonts[0]
        self._cmaps = [self._cmap(p, i) for p, i in FONT_CHAIN if Path(p).exists()]
        asc, desc = self.fonts[0].getmetrics()
        self.baseline = (chh - (asc + desc)) // 2 + asc
        self._masks: dict[tuple[str, bool], Any] = {}

    @staticmethod
    def _cmap(path: str, idx: int) -> set[int]:
        try:
            from fontTools.ttLib import TTCollection, TTFont
            f = TTCollection(path).fonts[idx] if path.endswith(".ttc") else TTFont(path)
            return set(f.getBestCmap())
        except Exception:
            return set()

    def _font_for(self, ch: str, bold: bool) -> Any:
        cp = ord(ch[0])
        for font, cmap in zip(self.fonts, self._cmaps):
            if not cmap or cp in cmap:
                return self.bold if bold and font is self.fonts[0] else font
        return self.fonts[0]

    def _mask(self, ch: str, bold: bool) -> tuple[Any, tuple[int, int]]:
        key = (ch, bold)
        if key not in self._masks:
            font = self._font_for(ch, bold)
            m = Image.new("L", (self.cw * 2, self.ch), 0)
            ImageDraw.Draw(m).text((0, self.baseline), ch, fill=255, font=font, anchor="ls")
            self._masks[key] = m
        return self._masks[key], (0, 0)

    def frame(self, grid: list[list[Cell]]) -> Image.Image:
        rows, cols = len(grid), len(grid[0]) if grid else 0
        img = Image.new("RGB", (cols * self.cw, rows * self.ch), BG)
        draw = ImageDraw.Draw(img)
        cw, chh = self.cw, self.ch
        for r, row in enumerate(grid):
            y = r * chh
            for c, cell in enumerate(row):
                x = c * cw
                if cell.bg != BG:
                    draw.rectangle((x, y, x + cw - 1, y + chh - 1), fill=cell.bg)
                ch = cell.ch
                if not ch or ch == " ":
                    continue
                if ch in _BLOCKS:
                    for fx0, fy0, fx1, fy1 in _BLOCKS[ch]:
                        draw.rectangle((x + round(fx0 * cw), y + round(fy0 * chh),
                                        x + round(fx1 * cw) - 1, y + round(fy1 * chh) - 1), fill=cell.fg)
                elif ch in _SHADES and self.shade == "blend":
                    a = _SHADES[ch]
                    col = tuple(int(f * a + b * (1 - a)) for f, b in zip(cell.fg, cell.bg))
                    draw.rectangle((x, y, x + cw - 1, y + chh - 1), fill=col)
                elif ch in _BOX or ch in _ROUND:
                    self._box(draw, x, y, ch, cell.fg)
                else:
                    mask, _ = self._mask(ch, cell.bold)
                    w = min(mask.width, img.width - x)
                    img.paste(cell.fg, (x, y, x + w, y + chh), mask.crop((0, 0, w, chh)))
        return img

    def _box(self, draw: Any, x: int, y: int, ch: str, fg: Any) -> None:
        cw, chh = self.cw, self.ch
        cx, cy, x1, y1 = x + cw // 2, y + chh // 2, x + cw - 1, y + chh - 1
        if ch in _ROUND:
            sh, sv = _ROUND[ch]
            rr = cw // 2
            ccx, ccy = cx + sh * rr, cy + sv * rr
            dx, dy = (-1 if sh > 0 else 0), (-1 if sv > 0 else 0)
            start = {(1, 1): 180, (-1, 1): 270, (1, -1): 90, (-1, -1): 0}[(sh, sv)]
            draw.arc((ccx - rr + dx, ccy - rr + dy, ccx + rr + dx, ccy + rr + dy), start, start + 90,
                     fill=fg, width=2)
            draw.rectangle((ccx, cy - 1, x1, cy) if sh > 0 else (x, cy - 1, ccx, cy), fill=fg)
            draw.rectangle((cx - 1, ccy, cx, y1) if sv > 0 else (cx - 1, y, cx, ccy), fill=fg)
            return
        l, r, u, d = arms = _BOX[ch]
        if 3 in arms:
            off = 2
            if l and r and not (u or d):
                for oy in (-off, off):
                    draw.line((x, cy + oy, x1, cy + oy), fill=fg)
            elif u and d and not (l or r):
                for ox in (-off, off):
                    draw.line((cx + ox, y, cx + ox, y1), fill=fg)
            else:
                sh, sv = (1 if r else -1), (1 if d else -1)
                for k in (-1, 1):
                    px, py = cx + k * off * sh, cy + k * off * sv
                    draw.line((px, py, x1 if sh > 0 else x, py), fill=fg)
                    draw.line((px, py, px, y1 if sv > 0 else y), fill=fg)
            return
        # Each arm runs edge → center and covers the center square, so joins have no notches.
        for weight, box in (
            (l, lambda lo, hi: (x, cy + lo, cx + hi, cy + hi)),
            (r, lambda lo, hi: (cx + lo, cy + lo, x1, cy + hi)),
            (u, lambda lo, hi: (cx + lo, y, cx + hi, cy + hi)),
            (d, lambda lo, hi: (cx + lo, cy + lo, cx + hi, y1)),
        ):
            if weight:
                t = 2 if weight == 1 else 4
                draw.rectangle(box(-(t // 2), (t - 1) // 2), fill=fg)


# ── stats: is it moving, is the seam clean, how much void ─────────────────


def _lum(rgb: tuple[int, int, int]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _cell_lum(cell: Cell) -> float:
    if cell.ch in ("", " "):
        return _lum(cell.bg)
    if cell.ch in _BLOCKS:
        return max(_lum(cell.fg), _lum(cell.bg))
    return max(_lum(cell.bg), _lum(cell.fg) * 0.5)


MOVE_DELTA = 6  # rgb levels; smaller per-step changes are invisible


def _vis(cell: Cell) -> tuple[Any, ...]:
    if cell.ch in ("", " "):
        return ("solid", cell.bg)
    if cell.ch == "█":
        return ("solid", cell.fg)
    return (cell.ch, cell.fg, cell.bg)


def _moved(a: Cell, b: Cell) -> bool:
    va, vb = _vis(a), _vis(b)
    if va[0] != vb[0]:
        return True
    return any(abs(p - q) > MOVE_DELTA for ca, cb in zip(va[1:], vb[1:]) for p, q in zip(ca, cb))


def _changed(a: list[list[Cell]], b: list[list[Cell]], ever: set | None = None) -> float:
    n = 0
    for r, (ra, rb) in enumerate(zip(a, b)):
        for c, (ca, cb) in enumerate(zip(ra, rb)):
            if ca != cb and _moved(ca, cb):
                n += 1
                if ever is not None:
                    ever.add((r, c))
    return n / max(1, len(a) * len(a[0]))


def stats(grids: list[list[list[Cell]]], window: int = 1) -> dict[str, float]:
    """motion/ever: change across `window` steps (≈0.5 s, so slow fades register);
    seam: one step, judged against the p90 single step."""
    ever: set = set()
    k = max(1, min(window, len(grids) - 1))
    motion = [_changed(grids[i], grids[i + k], ever) for i in range(len(grids) - k)]
    steps = [_changed(grids[i], grids[i + 1]) for i in range(len(grids) - 1)]
    seam = _changed(grids[-1], grids[0])
    total = len(grids[0]) * len(grids[0][0])
    dark = [sum(1 for row in g for c in row if _cell_lum(c) < 24) / total for g in grids]
    p90 = sorted(steps)[int(0.9 * (len(steps) - 1))] if steps else 0.0
    return {
        "motion_median": statistics.median(motion) if motion else 0.0,
        "motion_max": max(motion) if motion else 0.0,
        "step_change_p90": p90,
        "seam_change": seam,
        "dark_fraction_mean": statistics.mean(dark),
        "cells_ever_moving": len(ever) / total,
    }


def fmt_stats(cap: Capture, s: dict[str, float]) -> str:
    p90, seam = s["step_change_p90"], s["seam_change"]
    ratio = seam / p90 if p90 else (float("inf") if seam else 0.0)
    verdict = "CLEAN" if ratio <= 1.5 else "CHECK" if ratio <= 3.0 else "JUMP"
    return "\n".join([
        f"loop        {cap.loop_s:.2f}s virtual, {cap.steps} steps ({cap.steps / cap.loop_s:.1f} fps)",
        f"motion      median {s['motion_median']:.1%} · max {s['motion_max']:.1%} cells changed per 0.5 s "
        f"(>{MOVE_DELTA} rgb levels) · {s['cells_ever_moving']:.0%} of cells ever move",
        f"seam        {seam:.1%} cells change last→first ({ratio:.1f}x p90 step) → {verdict}",
        f"void        {s['dark_fraction_mean']:.0%} of cells near-black (mean over loop)",
    ])


# ── outputs ───────────────────────────────────────────────────────────────


def _label(img: Image.Image, text: str, font: Any) -> Image.Image:
    out = Image.new("RGB", (img.width, img.height + 30), (22, 22, 30))
    ImageDraw.Draw(out).text((6, 4), text, fill=(190, 190, 200), font=font)
    out.paste(img, (0, 30))
    return out


async def cmd_sheet(code: str, out: Path, cols: int, rows: int, n: int, shade: str) -> str:
    full = await capture(code, cols, rows)
    grids_all = [to_cells(b, cols, rows) for _, b in full.samples]
    report = fmt_stats(full, stats(grids_all, window=max(1, round(0.5 * full.steps / full.loop_s))))
    idx = [round(i * (len(full.samples) - 1) / (n - 1)) for i in range(n - 1)] + [len(full.samples) - 1]
    picks = [(full.samples[i][0], grids_all[i]) for i in idx]
    ras = Rasterizer(shade=shade)
    out.mkdir(parents=True, exist_ok=True)
    label_font = ras.fonts[0].font_variant(size=18)
    thumbs = []
    for t, g in picks:
        img = ras.frame(g)
        img.save(out / f"frame-{t:06.2f}s.png")
        thumbs.append(_label(img.resize((img.width // 2, img.height // 2), Image.Resampling.LANCZOS),
                             f"t={t:.2f}s", label_font))
    seam = [ras.frame(grids_all[-1]), ras.frame(grids_all[0])]
    seam_t = [_label(s.resize((s.width // 2, s.height // 2), Image.Resampling.LANCZOS), lbl, label_font)
                for s, lbl in zip(seam, ("seam: last", "seam: first"))]
    gap = 10
    sheet_w = sum(t.width for t in thumbs) + gap * (len(thumbs) - 1)
    sheet = Image.new("RGB", (sheet_w, thumbs[0].height), (22, 22, 30))
    x = 0
    for t in thumbs:
        sheet.paste(t, (x, 0))
        x += t.width + gap
    sheet.save(out / "sheet.png")
    seam_img = Image.new("RGB", (seam_t[0].width * 2 + gap, seam_t[0].height), (22, 22, 30))
    seam_img.paste(seam_t[0], (0, 0))
    seam_img.paste(seam_t[1], (seam_t[0].width + gap, 0))
    seam_img.save(out / "seam.png")
    (out / "stats.txt").write_text(report + "\n")
    return f"{report}\nsheet       {out / 'sheet.png'}\nseam        {out / 'seam.png'}\nframes      {out}/frame-*.png"


async def cmd_frame(code: str, out: Path, cols: int, rows: int, t: float, shade: str) -> str:
    if t < 0:
        raise SystemExit("--t must be >= 0")
    cap = await capture(code, cols, rows, [t])
    Rasterizer(shade=shade).frame(to_cells(cap.samples[0][1], cols, rows)).save(out)
    return str(out)


def _step_durations(cap: Capture) -> list[float]:
    ts = [t for t, _ in cap.samples] + [cap.loop_s]
    return [b - a for a, b in zip(ts, ts[1:])]


def _pick_fps(dts: list[float]) -> int:
    for f in (30, 60, 25, 50, 24):
        if all(round(d * f) >= 1 and abs(d * f - round(d * f)) < 0.02 for d in dts):
            return f
    return 60


def _gif_delays(durs: list[float]) -> list[int]:
    """Error-diffused 10 ms delays (GIF centisecond grid; browsers clamp <20 ms)."""
    out, target, emitted = [], 0.0, 0
    for d in durs:
        target += d * 1000
        q = max(20, int(round((target - emitted) / 10)) * 10)
        out.append(q)
        emitted += q
    return out


async def cmd_gif(code: str, out: Path, cols: int, rows: int, seconds: float | None,
                  width: int, shade: str) -> str:
    cap = await capture(code, cols, rows)
    durs = _step_durations(cap)
    keep = [i for i, (t, _) in enumerate(cap.samples) if seconds is None or t < seconds]
    ras = Rasterizer(shade=shade)
    small = []
    for i in keep:  # downscale as we go: full-res frames for every step would cost GBs
        f = ras.frame(to_cells(cap.samples[i][1], cols, rows))
        small.append(f.resize((width, round(f.height * width / f.width)), Image.Resampling.LANCZOS))
    h = small[0].height
    # One global palette so identical pixels stay identical across frames.
    picks = small[:: max(1, math.ceil(len(small) / 12))]
    montage = Image.new("RGB", (width, h * len(picks)))
    for k, f in enumerate(picks):
        montage.paste(f, (0, k * h))
    palette = montage.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in small]
    out.parent.mkdir(parents=True, exist_ok=True)
    pal[0].save(out, save_all=True, append_images=pal[1:], duration=_gif_delays([durs[i] for i in keep]),
                loop=0, optimize=True, disposal=2)
    return f"{out} · {len(pal)} frames · {out.stat().st_size / 1e6:.1f}MB · loop {cap.loop_s:.2f}s"


async def cmd_mp4(code: str, out: Path, cols: int, rows: int, fps: int | None, seconds: float | None,
                  shade: str, size: tuple[int, int] = REEL) -> str:
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found")
    probe = await capture(code, cols, rows)
    fps = fps or _pick_fps(_step_durations(probe))
    dur = seconds or probe.loop_s
    n = max(2, round(dur * fps))
    cap = await capture(code, cols, rows, [i / fps for i in range(n)])
    W, H = size
    # Scale the cell (not the bitmap) so glyph edges stay crisp at any output size.
    if W * rows * CELL_H <= H * cols * CELL_W:
        cw, ch = W // cols, (W * CELL_H) // (cols * CELL_W)
    else:
        cw, ch = (H * CELL_W) // (rows * CELL_H), H // rows
    ras = Rasterizer(cw, ch, (FONT_SIZE * cw) // CELL_W, shade=shade)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        prev_key, prev_path = None, None
        for i, (_, buf) in enumerate(cap.samples):
            grid = to_cells(buf, cols, rows)
            key = hash(tuple(tuple(r) for r in grid))
            path = Path(td) / f"f{i:05d}.png"
            if key == prev_key and prev_path is not None:
                shutil.copyfile(prev_path, path)
                continue
            f = ras.frame(grid)
            canvas = Image.new("RGB", size, BG)
            canvas.paste(f, ((W - f.width) // 2, (H - f.height) // 2))
            canvas.save(path)
            prev_key, prev_path = key, path
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(Path(td) / "f%05d.png"),
            "-vf", "scale=in_range=full:out_range=tv:out_color_matrix=bt709,format=yuv420p",
            "-c:v", "libx264", "-preset", "slow", "-crf", "16",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-movflags", "+faststart", str(out),
        ], check=True)
    return (f"{out} · {W}x{H} · cell {ras.cw}x{ras.ch} · {n} frames @ {fps}fps · "
            f"{out.stat().st_size / 1e6:.1f}MB · loop {probe.loop_s:.2f}s")


async def cmd_play(code: str, cols: int | None, rows: int | None) -> None:
    from rich.live import Live

    term = shutil.get_terminal_size()
    cols, rows = cols or term.columns, rows or term.lines
    canvas = _Canvas()
    compiled = _compile(code)
    console = Console()
    with Live(console=console, screen=True, auto_refresh=False) as live:
        async def real_sleep(dt: float = 0) -> None:
            live.update(Group(*canvas.buffer), refresh=True)
            await asyncio.sleep(max(float(dt), 0.0))
        while True:
            canvas.clear()
            ns = _namespace(canvas, real_sleep, cols, rows)
            exec(compiled, ns)
            await ns["__script__"]()
            await asyncio.sleep(0.1)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tac", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser, out: bool = True) -> None:
        sp.add_argument("piece", type=Path)
        if out:
            sp.add_argument("--out", type=Path, required=True)
        sp.add_argument("--cols", type=int, default=DEFAULT_COLS)
        sp.add_argument("--rows", type=int, default=DEFAULT_ROWS)
        sp.add_argument("--shade", choices=("blend", "glyph"), default="blend",
                        help="░▒▓ as alpha blend (default) or Menlo dither glyph")

    s = sub.add_parser("sheet", help="contact sheet + seam pair + full-res frames + stats")
    common(s)
    s.add_argument("--n", type=int, default=5, help="frames across one loop (default 5)")
    f = sub.add_parser("frame", help="one full-res frame at virtual time t")
    common(f)
    f.add_argument("--t", type=float, default=0.0)
    g = sub.add_parser("gif", help="site preview gif — one frame per script step, native timing")
    common(g)
    g.add_argument("--seconds", type=float, default=None, help="truncate; default: one natural loop")
    g.add_argument("--width", type=int, default=560)
    m = sub.add_parser("mp4", help="vertical video, no audio")
    common(m)
    m.add_argument("--size", default="reel",
                   help="reel (1080x1920, 9:16) | iphone (1206x2622; pair with --rows 81) | WxH")
    m.add_argument("--fps", type=int, default=None, help="default: smallest of 30/60/25/50/24 that holds every step evenly")
    m.add_argument("--seconds", type=float, default=None, help="default: one natural loop")
    pl = sub.add_parser("play", help="play live in this terminal (Ctrl-C to quit)")
    pl.add_argument("piece", type=Path)
    pl.add_argument("--cols", type=int, default=None)
    pl.add_argument("--rows", type=int, default=None)

    a = p.parse_args(argv)
    code = a.piece.read_text(encoding="utf-8")
    try:
        if a.cmd == "sheet":
            print(asyncio.run(cmd_sheet(code, a.out, a.cols, a.rows, a.n, a.shade)))
        elif a.cmd == "frame":
            print(asyncio.run(cmd_frame(code, a.out, a.cols, a.rows, a.t, a.shade)))
        elif a.cmd == "gif":
            print(asyncio.run(cmd_gif(code, a.out, a.cols, a.rows, a.seconds, a.width, a.shade)))
        elif a.cmd == "mp4":
            size = SIZES.get(a.size) or tuple(int(v) for v in a.size.lower().split("x"))
            print(asyncio.run(cmd_mp4(code, a.out, a.cols, a.rows, a.fps, a.seconds, a.shade, size)))
        elif a.cmd == "play":
            asyncio.run(cmd_play(code, a.cols, a.rows))
    except KeyboardInterrupt:
        pass
    except _Runaway as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
