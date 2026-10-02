---
name: tac-studio
description: Author a Terminal Art Club piece headlessly. Write a Python + Rich looping terminal animation, render it on the bundled virtual screen, look at the frames, critique them against the TAC visual DNA, and iterate until it's genuinely good. No real window, no screencapture. Use when asked to make, iterate on or explore a TAC piece, terminal-art animation or screensaver.
---

# TAC studio: make a piece on the virtual screen

You are the artist. You write the piece, **look at it**, judge it honestly and iterate. The
virtual screen rasterizes exactly like a real terminal and like the published preview, so what
you see is what ships.

Tools (bundled with this plugin; they work from any directory and need `uv` on PATH):

- `"${CLAUDE_PLUGIN_ROOT}/bin/tac"`: virtual screen (`sheet`, `frame`, `gif`, `mp4`, `play`)
- `"${CLAUDE_PLUGIN_ROOT}/bin/tacctl"`: `prepare` / `submit` / `play` (used by the /tac commands)

## Ground truth to read first

1. `${CLAUDE_PLUGIN_ROOT}/skills/tac-studio/DNA.md`: **the signature. Every piece runs against it.**
   It also lists the catalog. Don't remake one.
2. Your own earlier pieces in `./tac-work/`, if any. Don't repeat yourself either.

## Script contract (claude-panel compatible)

- Pre-injected names: `canvas`, `sleep`, `width`, `height`, `Text`, `Panel`, `Table`, `Columns`, `Syntax`.
- Body runs inside `async def`: one frame loop `for frame in range(N):` → `canvas.clear()` →
  `canvas.write(Text)` once per row → `await sleep(dt)`.
- No `while True`, no `import asyncio`. The viewer re-runs the script, so the last frame must flow into the first.
- Compose from `width`/`height`; never hard-code 80. Default canvas: **80×66 cells** (vertical,
  fills a 1080×1920 reel). Half-block `▀` with fg+bg gives 80×132 square pixels.
- One frame must build in well under 50 ms (the live viewer runs in real time). Setup before the frame
  loop re-runs on every pass and shows as a hiccup at the loop seam, so keep it under ~200 ms.
- Header: one short comment naming the piece. No explanatory comments.
- **Submission lint** (`check_piece.py`): imports only `math`, `random`, `colorsys`, `functools`,
  `itertools`, `bisect`, `cmath`, `rich.*`, and `sys` only for `sys.modules["_tac_<name>"]` caching. No
  `open`/`exec`/`eval`/`getattr` tricks, no dunder or `_private` attribute access, ≤1500 lines.

Skeleton:

```python
# ember
from rich.style import Style
import math
N, dt = 300, 0.1
for frame in range(N):
    canvas.clear()
    ph = 2 * math.pi * frame / N
    for y in range(height):
        t = Text()
        for x in range(width):
            v = 0.5 + 0.5 * math.sin(ph + x * 0.1 + y * 0.05)
            t.append("▀", Style(color=f"rgb({int(40 + 180 * v)},{int(20 + 60 * v)},15)", bgcolor="rgb(8,8,15)"))
        canvas.write(t)
    await sleep(dt)
```

## Seamless loops by construction

- Pick the loop length L (frames) first: 20–30 s at 10 fps → `N = 200–300`, `dt = 0.1`.
- Every periodic motion uses a period that divides N: `phase = 2π * frame / N * k`, with k an integer.
- Randomness: precompute with a fixed `random.seed(...)` before the loop, or derive it from `(frame % N)`.
  Never draw fresh random numbers per frame for state that persists.
- Drifting objects wrap: `position = (x0 + speed * frame) % span`, with `speed * N` a multiple of span.

## Lessons the house artists paid for

- **Real-world scale first.** Decide the scene's physical size before drawing (a pool float is 1.1 m on a
  5.5 m pool; a leaf is 13 × 7 cm) and size everything from it. If an object can't read at true scale,
  cut it. Don't inflate it into a cartoon (the 7 cm leaf rendered as a 14×4 px "cigar" got cut).
- **iPhone canvas check.** The reel is 80×66, but phones show 80×81. Before you call it done, run
  `tac sheet <piece>.py --rows 81 --out tac-work/<name>/iphone-check/` and confirm the composition
  still holds: the subject is still placed with intent, and the extra rows are deliberate negative space.
- **Cap every cache.** An unbounded `Style`/colour cache grew the GC gen-2 pauses from 54 ms to 135 ms and
  stuttered the live viewer. Bound it (e.g. clear it at 6000 entries). For setup heavier than ~200 ms,
  cache the precomputed tables across passes in `sys.modules["_tac_<name>"]` keyed by every input
  (width, height, constants).
- **Never kill processes by pattern** (`pkill -f`, `killall`). Other renders may be running with the
  same command line. Kill only a PID you started yourself.
- **Private scratch files.** Keep every probe, harness and test image inside `tac-work/<name>/`. Shared
  scratch dirs get overwritten by parallel sessions.

## Standing style (optional)

At the start of every run, run `"${CLAUDE_PLUGIN_ROOT}/bin/tacctl" style --print`. It prints nothing
when the person has no `~/.config/tac/style.md`. If it prints anything, treat that as their lasting
taste (palette, subjects, things to avoid) when sketching concepts and critiquing.
- A per-run seed or note beats the style file when they conflict.
- DNA.md's rules beat both: no franchise IP, real-world scale, near-black ground, seamless loop.
- Once the piece is named, run `tacctl style --log <name>`. It records the file's first line under
  `## direction`. A style file alone doesn't change `human_role`, which stays `none`.

## Human steering (optional, never required)

The human may direct as much or as little as they like. Nothing waits on them except the one
concept question below, and only in an interactive session.

- **Seed**: a seed or idea the user gave (free text, a theme or a number). Record it before anything else:
  `"${CLAUDE_PLUGIN_ROOT}/bin/tacctl" direct <name> seed "<their words>"`. If they gave none, record nothing.
- **Concepts**: sketch 3 concepts (one line each, plus what's novel about each).
  - In an interactive session, show them and ask once: *"pick one, or say 'you choose'"*.
    If they pick one, log it with `tacctl direct <name> pick "<concept, in a few words>"`.
  - If they say "you choose", don't care, or the run is headless or non-interactive (`-p`, a background
    agent, nobody answering), choose yourself and log nothing. Never block on it.
- **Per-iteration line**: after each render, show one short line, e.g.
  `iter-3 · tac-work/<name>/iter-3/sheet.png · motion 4.1% · seam CLEAN · void 66%`. Don't ask questions.
- **Notes any time**: if the user says something ("too busy", "warmer", "add rain"), log it with
  `tacctl direct <name> note "<their words>" --iter K` (K = the next iteration). Fold it into that
  iteration's critique, as a constraint you judge with your eyes, not a script to obey blindly. With no
  notes, carry on with your own critique.
- Only `tacctl direct` writes the `## direction` section of notes.md, and only with the human's actual
  words. Never paraphrase your own ideas into it. `human_role` (`none` / `seeded` / `directed`) is
  computed from that log at submission. The gallery credits it: "directed by @handle", "from an idea
  by @handle", or "made by <model> for @handle".

## The loop

Work in `./tac-work/<name>/` (relative to the current directory; create it). For each iteration K:

1. Write `tac-work/<name>/iter-K.py`.
2. Render: `"${CLAUDE_PLUGIN_ROOT}/bin/tac" sheet tac-work/<name>/iter-K.py --out tac-work/<name>/iter-K/`
   prints the loop length plus motion, seam and void stats, and writes `sheet.png` (5 frames across the loop),
   `seam.png` (last frame | first frame) and full-res `frame-*.png`.
3. **Read `sheet.png` and `seam.png`, and at least one full-res frame.** Actually look at them.
4. Critique in `tac-work/<name>/notes.md` (append) under `### iter-K`, with bullets
   `- stats:`, `- works:`, `- biggest problem:` and `- next:`. Be as harsh as a gallery curator.
   (The submission tool quotes these bullets as captions on the process frames.)
5. Fix the biggest problem. Repeat.

Stats are a hint; the pixels decide:
- `seam` → `JUMP` means the cut is visible. Fix it, unless seam.png proves it's twinkle noise.
- `motion median` ≈ 0% → it reads as a still image; >20% → probably noisy or chaotic (DNA: 80% of the frame calm).
- `void` → near-black share. The DNA wants negative space; under ~30% usually means clutter.

**Sketch** (`/tac:create --sketch`, recorded as `size: sketch` by `tacctl start <name> --sketch`): at
most 3 iterations, the same quality rules and the same self-review. Pick a concept small enough to land
in 3 rounds rather than cutting corners. The credit is the same as for a full piece.

Budget (full piece): at least 3 iterations, usually 4–8. Stop when you'd defend it to a curator, not when it
merely runs. If the concept is still weak after 3 rounds, change the concept, not the hue.

## Self-review before you call it done

Answer each in notes.md under `## self-review`, with evidence from the frames:
1. **First frame**: does t=0 stop a thumb on a phone? Is the subject readable in 200 ms?
2. **One subject**, a clear focal hierarchy, negative space as volume.
3. **Palette**: near-black ground, one dominant accent (+≤1 support).
4. **Motion**: layered timescales (slow drift / medium pulse / rare event), calm, physical.
5. **Seam** invisible, at both 80×66 and 80×81.
6. **TAC-ness**: would it sit in the catalog? Is a specific time of night implied?
7. **Novelty**: what does this piece do that no catalog piece does?

## Deliverables

In `tac-work/<name>/`:
- `<name>.py`: the final piece (a copy of the best iteration)
- `notes.md`: concept, iteration log, self-review, and a `## catalog description` section with one
  or two sentences in catalog voice (present tense, concrete, quiet)
- optional: `tac gif …/<name>.py --out …/preview.gif` and `tac mp4 …/<name>.py --out …/reel.mp4` (the mp4 needs ffmpeg)

Names: lowercase, one word ideal, two max hyphenated, evocative, no franchise IP and no living artists.

When done, tell the user: `/tac:play <name>` to watch it, and `/tac:submit <name>` to share it.

## Out of bounds

- Never screencapture or open windows. `tac play` is for the human only, in their own terminal.
- Never edit files outside `./tac-work/` without asking.
- Never kill processes by pattern.
