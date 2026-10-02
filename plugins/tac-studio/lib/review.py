"""Build tac-work/index.html: a local review page for your pieces (port of TAC's studio gallery).

Media paths are relative, so the page opens straight from disk (file://), no server.
"""

from __future__ import annotations

import html
import json
import shutil
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))
import notes as notesmod  # noqa: E402

CSS = """
:root { --void: #08080f; --ink: #d4d2cb; --dim: #8a8992; --faint: #2a2a36; --ok: #8fd3bf; --warn: #e3b663; --bad: #d9786a; }
@media (prefers-color-scheme: light) { :root:not([data-theme=dark]) { --void: #f3f1ec; --ink: #1d1c22; --dim: #5d5c66; --faint: #d8d5cc; } }
* { box-sizing: border-box; margin: 0; }
body { background: var(--void); color: var(--ink); font: 300 15px/1.55 Roboto, system-ui, sans-serif; padding: 48px 32px 96px; }
header { max-width: 1400px; margin: 0 auto 48px; display: flex; justify-content: space-between; align-items: baseline; gap: 24px; flex-wrap: wrap; }
h1 { font: 400 13px/1 "Roboto Mono", monospace; letter-spacing: .08em; color: var(--dim); }
h1 b { color: var(--ink); font-weight: 500; }
main { max-width: 1400px; margin: 0 auto; display: grid; gap: 64px 40px; grid-template-columns: repeat(auto-fill, minmax(260px, 320px)); justify-content: center; }
article { display: flex; flex-direction: column; gap: 12px; }
article.focus { outline: 1px solid var(--dim); outline-offset: 12px; }
img.media, video.media, .still { width: 100%; aspect-ratio: 9 / 16; background: #08080f; display: block; object-fit: contain; }
.still { display: grid; place-items: center; color: var(--dim); font: 400 12px "Roboto Mono", monospace; }
h2 { font: 500 26px/1.1 Roboto, sans-serif; letter-spacing: -.01em; margin-top: 4px; }
.line { font-style: italic; }
.meta { font: 400 12px/1.4 "Roboto Mono", monospace; color: var(--dim); }
pre { font: 400 11.5px/1.6 "Roboto Mono", monospace; color: var(--dim); white-space: pre-wrap; border-top: 1px solid var(--faint); padding-top: 10px; }
.ok { color: var(--ok); } .warn { color: var(--warn); } .bad { color: var(--bad); }
code { font: 400 12px "Roboto Mono", monospace; background: var(--faint); padding: 2px 6px; word-break: break-all; }
.links { font: 400 12px "Roboto Mono", monospace; display: flex; gap: 14px; flex-wrap: wrap; }
.links a { color: var(--dim); text-decoration: none; border-bottom: 1px solid var(--faint); }
.links a:hover, .links a:focus-visible { color: var(--ink); border-color: var(--ink); outline: none; }
@media (max-width: 640px) { body { padding: 28px 16px 64px; } }
"""


def _render_preview(wd: Path, src: Path) -> Path | None:
    """tac-work/<name>/preview/preview.webp, re-rendered when the source is newer."""
    out = wd / "preview"
    webp = out / "preview.webp"
    if webp.exists() and webp.stat().st_mtime >= src.stat().st_mtime:
        return webp
    stage = wd / ".play"
    stage.mkdir(exist_ok=True)
    shutil.copyfile(src, stage / "piece.py")
    r = subprocess.run([sys.executable, str(LIB / "render_piece.py"), str(stage), "--out", str(out)])
    return webp if r.returncode == 0 and webp.exists() else None


def _stats(wd: Path) -> str:
    sj = wd / "preview" / "stats.json"
    if sj.exists():
        s = json.loads(sj.read_text())
        return (f"loop {s['loop_s']:.1f}s · {s['fps']} fps\nmotion {s['motion_median']:.1%} · "
                f"seam {s['seam']} · void {s['void']:.0%}")
    st = sorted(wd.glob("iter-*/stats.txt"), key=lambda p: p.stat().st_mtime)
    return st[-1].read_text().strip() if st else ""


def _stats_html(text: str) -> str:
    e = html.escape(text)
    for word, cls in (("CLEAN", "ok"), ("CHECK", "warn"), ("JUMP", "bad")):
        e = e.replace(word, f'<span class="{cls}">{word}</span>')
    return e


def _card(wd: Path, src: Path, focus: bool, render: bool) -> str:
    name = wd.name
    notes = (wd / "notes.md").read_text(encoding="utf-8") if (wd / "notes.md").exists() else ""
    webp = _render_preview(wd, src) if render else (wd / "preview" / "preview.webp")
    if webp and webp.exists():
        media = f'<img class="media" src="{name}/preview/preview.webp" alt="{html.escape(name)} preview">'
    elif (wd / "reel.mp4").exists():
        media = f'<video class="media" src="{name}/reel.mp4" muted loop autoplay playsinline></video>'
    else:
        media = '<div class="still">no preview yet</div>'
    sub = wd / ".submission.json"
    status = ""
    if sub.exists():
        s = json.loads(sub.read_text())
        status = f'submitted · {html.escape(str(s.get("id")))}'
    n_iter = len(list(wd.glob("iter-*.py")))
    links = [(f, f"{name}/{f}") for f in (src.name, "notes.md", "preview.gif", "reel.mp4") if (wd / f).exists()]
    play = f"{LIB.parent / 'bin' / 'tac'} play {src.resolve()}"
    return f"""
<article{' class="focus" id="focus"' if focus else ''}>
  {media}
  <h2>{html.escape(name)}</h2>
  <div class="meta">{n_iter} iteration{'s' if n_iter != 1 else ''}{' · ' + status if status else ''}</div>
  <p class="line">{html.escape(notesmod.catalog_description(notes))}</p>
  <pre>{_stats_html(_stats(wd))}</pre>
  <div class="meta">live in a terminal:</div><code>{html.escape(play)}</code>
  <nav class="links">{''.join(f'<a href="{html.escape(h)}">{html.escape(t)}</a>' for t, h in links)}</nav>
</article>"""


def build(root: Path, *, render: bool = True, only: str | None = None) -> Path:
    import tacctl

    cards = []
    for wd in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        src = tacctl.piece_source(wd, wd.name)
        if src is None:
            continue
        cards.append(_card(wd, src, focus=wd.name == only, render=render and (only is None or wd.name == only)))
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>My TAC pieces</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Roboto:ital,wght@0,300;0,400;0,500;1,300&family=Roboto+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{CSS}</style></head>
<body>
<header><h1><b>terminal art club</b> / my pieces / {len(cards)}</h1></header>
<main>{''.join(cards)}</main>
<script>document.getElementById('focus')?.scrollIntoView({{block: 'center'}});</script>
</body></html>"""
    out = root / "index.html"
    out.write_text(page, encoding="utf-8")
    return out
