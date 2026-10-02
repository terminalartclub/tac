# wake — notes

## concept

A water tunnel left running overnight in a dark lab. A laser sheet cuts the test section; fluorescein
dye seeps from a port at the front of a round rod, coats it, peels off both shoulders and rolls up into
a Kármán vortex street — a staircase of scrolls that climbs the frame and fades out of the light. Systems
lane: an instrument doing its one job at 3am with nobody watching.

Format bet: the catalog's first computed fluid flow. The velocity field is a periodic vortex-street
model: uniform flow + potential flow around the rod + alternately shed Lamb–Oseen vortices (with
Milne-Thomson images inside the rod) + a Gaussian cut-off source that separates the near wake. Dye is
a set of streaklines: particles released from ports on the rod every 2 frames, integrated (RK2, 2-frame
step) through that field, and drawn as mass-conserving Catmull-Rom filaments, so stretched dye dims and
folded dye brightens, the way it does in a real LIF photo. The shedding period is the loop: every
position is a function of (release time mod T), so the seam is exact by construction.

Second format bet: sub-cell rendering. Each cell picks the best 2-colour split of its 2×2 sub-pixels
and draws it with a quadrant glyph (▘▝▖▗▚▞▛▜▙▟▌▐▀▄), falling back to ▀ where the split doesn't matter.
That doubles horizontal resolution (160 × 132 sub-pixels at 80 × 66 cells), so the thin shear-layer
filaments render at half a cell's width.

## physical scale (decided before sizing anything)

- Rod: 25 mm diameter = D = 0.13·W px → 10.4 px at 80 cols → **2.40 mm/px**. Visible window
  (the laser-sheet plane seen through the side glass): 192 mm × 317 mm at 80 × 66.
- Water at 20 °C, ν = 1.0 mm²/s. Freestream U = 5.8 mm/s (0.24 px/frame) → **Re = U·D/ν = 145**
  (laminar periodic shedding regime, 47 < Re < 190).
- Strouhal St = 0.18 at Re 145 (Roshko: 0.212·(1 − 21.2/Re)) → shedding f = St·U/D = 0.042 Hz,
  **period T = 24 s**. Loop = 2 periods = 48 s (480 frames @ 10 fps).
- Vortex convection 0.85·U → spacing λ = 0.85·U·T = 118 mm = 4.7 D (49 px). Circulation 3.0·U·D at
  formation, slow decay. Core radius grows by real viscous diffusion r_c² = r_c0² + 4νt:
  1.6 px → 6.0 px over the 48 s climb.
- Everything is derived in px/frame from D and T, so every element scales with the rod, not by eye.
  On the iPhone canvas (81 rows) the window is 192 × 389 mm — same rod, more street.

## iteration log

### iter-1
- stats: loop 48 s / 480 steps · motion median 11.9% per 0.5 s · 27% of cells ever move ·
  seam 0.9x p90 CLEAN · void 83% · 11 ms/frame median, 103 ms first frame incl. table build.
- works: it reads as a vortex street in 200 ms — alternating scrolls joined by S-braids, rising
  from a dark disc; the prototype physics produced real roll-up on the first try.
- biggest problem: **tone** — cores clip to flat neon lime; the spiral structure (the point of the
  piece) disappears inside saturated blobs, and the hue is a default neon green.
- also: the near wake (the two shear layers meeting just behind the rod) reads as a little stick
  figure; scrolls are small (D = 10 px); the green haze tints the void.

### iter-2 (probe round, frame-level)
- tone: LUT ramp deep green → mid → pale yellow-white; blue laser haze as the support hue.
- 5-way probes: rod size D 10/12/14 px; port angle; formation length; separated-wake model.
  - Potential flow around a cylinder has no separation, so both shear layers hug the rod and meet
    at the rear stagnation point → the "stick figure" neck. A source + sink (Rankine oval) separated
    the layers but the sink scattered particles into noise; a Gaussian cut-off source (no sink)
    separates them cleanly. Too strong (QS 6–10) pushes the dye outside the vortices and the
    scrolls never form; QS ≈ 2 with formation at 0.8–1.7 D and X0F 0.4 separates the shear layers
    and still rolls them up.

### iter-3
- stats: loop 48 s · motion median 19.2% · 39% ever move · seam 0.9x p90 CLEAN · void 79% ·
  20 ms/frame median (2 ports).
- change: two dye ports per side (inner at the shoulder, outer at 1.5a) → double filaments that wrap
  into each vortex as two striations; age-split diffusion (young dye sharp, old dye blurred, mass
  conserved) so the street softens as it climbs; cross-pass cache for the trajectory tables.
- tried 3 ports incl. a "coat" port at −65° to outline the rod: the three filaments merged into one
  thick neon tube and the coat hid under the rod disc. Cut (for now).
- biggest problem: **no anchor** — the rod is a black disc in black water, invisible at full res;
  the composition's base is a hole. Also the blue haze turns the whole void navy.
- also: dye accumulations at the vortex centres read as eyes (at D = 14 the frame holds only
  ~2 scrolls and the S-curve + eye reads as a seahorse).

### iter-4
- stats: motion median 23.4% · seam 0.9x CLEAN · void 75%.
- change: laser fan moved to the right wall: the rod gets a blue crescent where the sheet hits its
  surface (the real artefact of an LIF rig) and casts a shadow left through the haze. D 14 → 12 px,
  rod lowered to 0.86 H, so the frame holds 3 scrolls + the forming one (a street, not a creature).
  28 seeding specks advected through the same field (the water's own motion layer).
- biggest problem: **no focal hierarchy** — the street is one uniform-brightness column from the rod
  to the top edge; the eye has nowhere to land. Also rod + shadow fuse into a dark horizontal
  "pipe".

### iter-5
- stats: motion median 18.6% · seam 0.9x CLEAN · void 79% · 21.5 ms/frame.
- change: the sheet's upper edge fades the light out over 0.6–1.4 rad above the rod line (probed 5
  edge/attenuation variants): brightest at the rod, the street dims as it leaves the light. D 12 →
  10.4 px so 4 scrolls survive the fade — with only 1.5 visible, it read as a sprout, not a street.
- biggest problem: **full-res mush** — at 13×14 px per pixel the filaments are 2–3 px blurred
  bands; the scrolls read as rings with eyes.

### iter-6 (renderer)
- quadrant renderer (2×2 sub-pixels per cell, 2 colours each). Side-by-side crop vs iter-5: the
  shear layers drop from ~26 to ~13 screen px wide; no cost (21.8 vs 21.5 ms) because empty water
  takes a precomputed-background fast path. Low-contrast haze cells fall back to ▀ (QMIN) — without
  that, the haze gradient broke into a checkerboard.

### iter-7
- stats: motion median 19.1% · seam 1.0x CLEAN · void 81%.
- change: single crisp port (+ a faint outer one), gentle upward fade, no haze rays (fine angular
  rays aliased into moiré in quadrant cells; cut).
- works: the first frame that reads as a vortex street at every scale.
- biggest problem: **the left ribbon floats** — its root sits in the rod's shadow, so the left
  shear layer starts a diameter above the rod while the right one is attached.

### iter-8
- stats: motion median 19.6% · seam 1.0x CLEAN · void 82%.
- change: shadow lifted to 45% (scattered light) and the coat port came back, this time at the
  front stagnation point (−89°) with the rod drawn 0.2 px inside the clamp radius: dye now wraps the
  whole front of the rod and peels off both shoulders. The rod reads as a solid disc in the flow —
  green outline below, blue laser crescent on the right.
- biggest problem: chords — long Catmull-Rom segments across stretched gaps drew faint straight lines
  through the separation bubble; 32 ms/frame under load.

### iter-9
- change: segments longer than 9 px are cut (a sheet stretched that far is invisible anyway); blur and
  tone limited to the dye's bounding box; per-age and per-release tables. G0 2.9, formation 0.8–1.7 D,
  QS 1.8 (5-way probe) — shorter bubble, more winding.
- biggest problem: dull — mid-street sits in dark greens; nothing glows.

### iter-10
- stats: motion median 21.4% · seam 1.0x CLEAN · void 80% · 18 ms CPU/frame.
- change: DYE_K 0.65 → 0.9 (probed 0.65/0.9/1.2 + bloom): luminous without losing the fade. Dye rate
  breathes ±25% once per loop. iPhone sheet (81 rows) holds: 5 scrolls, same composition. Robustness:
  renders at 120×40, 200×60, 60×30, 80×24, 100×80 (landscape keeps the street at the left third).

### iter-11
- change: aged (diffused) dye shifts slightly toward teal (AGEHUE 0.5; 1.0 turned the whole street
  aurora-teal and lost the fluorescein green).
- preview gif at 400 px: 20.4 MB. Probed causes: bloom off saves only 6%; the half-block renderer is
  the same size (4.7 vs 5.2 MB per 12 s). The cost is the moving street itself × 480 frames.

### iter-12
- change: dye diffusion time 300 → 800 frames, bloom 0.2 → 0.12, G0 3.0 (4-way full-res probe):
  old scrolls stay crisp, so the inner turns read as a wound spiral instead of a ring around a dot.

### iter-13
- change: the one-sided dye bolus (rare event) was invisible — the tone curve saturates young dye,
  so 2.4× dye barely brightened it (probed bolus 1.4/2.5/3.5). Replaced by its opposite: once per
  loop the dye pump pauses (rate → 3% for ~4 s around t = 6 s). The rod's coat thins and dims at
  t ≈ 7–9 s, then the gap travels into the bubble and leaves one scroll half-drawn as it climbs.
  Absence reads where extra brightness couldn't.
- biggest problem (found in the iPhone mp4, not the frames): the dim blue haze gradient bands into
  a faint purple arc after the yuv420p/BT.709 encode — the source PNG is smooth, the h.264 is not.

### iter-14 (final → wake.py)
- stats: loop 48.00 s / 480 steps @ 10 fps · motion median 20.0%, max 21.2% per 0.5 s · 48% of cells
  ever move · seam 10.3% (0.9x p90 step) CLEAN · void 83% · 18 ms CPU/frame median (section
  profile), 27 ms median / 45 ms max CPU under load avg 10–18 from the parallel artists ·
  first frame incl. trajectory build 260–370 ms, cached afterwards. iPhone canvas (81 rows): motion
  18.9%, seam 1.0x CLEAN, void 84%.
- change: static ±0.5-level dither baked into the haze (seeded, so identical every frame). Probed
  amplitude 1.6 vs 1.0: both kill the arc in the mp4; 1.0 costs +10% gif (5.4 vs 4.9 MB per 12 s),
  1.6 costs +20%. Kept 1.0. Invisible at normal brightness.

## self-review

1. **First frame** — t = 0: a column of four green scrolls rising from a dark disc with a blue
   crescent, fading upward into black. Reads as "smoke / ink curling off something" in 200 ms, and as
   a vortex street to anyone who has seen one; the S-chain silhouette carries at thumbnail size.
2. **One subject** — the wake. Read order is fixed by luminance: the dye-coated rod (brightest, the
   front stagnation point) → the separation bubble → the scrolls dimming as they climb → dark. Right
   55% of the frame is water lit only by a faint blue fan; void 83%.
3. **Palette** — ground (8, 9, 14); one accent, fluorescein green (deep green → mint → pale
   yellow-white highlights, aged dye slightly teal); one support, the laser blue (rod crescent, haze,
   specks). No third hue.
4. **Motion** — slow: the street climbs at 2 px/s (vortex convection 0.85·U); medium: the near wake
   swings left/right every 12 s as each vortex sheds; rare: the dye pause once per 48 s; breath: dye
   rate ±25% over the loop. Seeding specks drift through the empty water. 20% of cells change per
   0.5 s, between coffee-shop (4.7%) and aurora (27.1%).
5. **Seam** — the flow is periodic in T = 24 s and N = 2T; every particle position, the breath and the
   gap are functions of release time mod N; seam.png last|first indistinguishable; 0.9x p90.
6. **TAC-ness** — a machine left running in a dark room, doing the same slow thing forever; quiet,
   held, no climax. Weakest point: nothing in the frame beyond darkness says *3am* — the time is in
   the title and description, not the image.
7. **Novelty** — the catalog's first fluid dynamics: dye streaklines integrated through a vortex-street
   velocity field with viscous cores, a separated near wake and the rod's laser shadow; and the first
   piece rendered at quadrant-glyph resolution (2× horizontal detail inside the cell grid).

## renderer / pipeline notes

- Quadrant glyphs render as exact geometry in vscreen (_BLOCKS), so what the sheet shows is what
  ships. Real terminals draw them as block elements too.
- preview.gif (400 px): **21.9 MB** (480 frames; 19.9 MB before the haze dither). The street
  changes ~20% of cells every half second for 48 s; neither bloom nor the quadrant renderer is the
  cost. Lever if the site needs it: N = 240 (one shedding period, ~11 MB), at the cost of the
  once-per-48 s dye pause becoming a once-per-period event. reel.mp4 12.2 MB (1080×1920),
  wake-iphone.mp4 15.3 MB (1206×2622, 81 rows).
- The vscreen mp4 path (yuv420p, tv range) bands very dim gradients into chroma-shifted arcs that
  the PNG frames don't show — worth knowing for any dark-gradient piece; worked around in-piece with
  a static dither rather than touching the renderer.
- The trajectory tables (2 ports × 120 release phases × ~245 steps + 28 specks, ≈ 60k RK2 steps)
  take 260–370 ms. The live viewer re-runs the script every loop, so the tables are cached in a
  module stored in sys.modules (keyed by canvas size and flow parameters): first pass pays once, every
  later pass starts in < 30 ms.
- No per-frame Style cache growth: capped at 6000 entries as in chlorine.
- Wall-clock timings were noisy (load average 8–10 from parallel renders); budgets are quoted in
  process CPU time.
- Shared scratchpad: another session overwrote my timing.py mid-run; moved my tools to a private
  subfolder. No renderer bugs found.

## catalog description

A water tunnel left running in a dark lab — green dye coats a small rod, peels off both sides and rolls
into a slow staircase of vortices that climbs out of the laser light; once a loop the dye pump pauses and
one scroll goes up half-drawn.
