# chlorine — notes

## concept

An empty motel pool at 3am, seen straight down from above. The underwater lamp is on;
the surface throws a slow caustic web across the plaster floor and the lane line wobbles
through the water. Near-black deck, one accent (pool aqua), at most one warm support.
Format bet: real optics in a terminal — caustic intensity from the surface Hessian
(1/|det(I + c·H)|), floor displacement from the surface gradient — and the first top-down
/ aerial framing in the catalog. Canvas: 80×66 cells, half-block ▀ → 80×132 square pixels.

## iteration log

### iter-1
- stats: loop 30 s / 300 steps · motion median 11.4% · seam 1.0x CLEAN · void 49% · 10.8 ms/frame build.
- works: the pool reads in 200 ms — a lit rectangle, rounded coping, teal glow bleeding onto
  the deck. Lamp falloff gives the water a volume (bright left-middle, deep blue toward the bottom).
- biggest problem: **there are no caustics.** Per-wave Hessian gain is ~0.03 (a·k²·c with
  a=0.12, k²≈0.11, c=2.2); folds need ~0.4–0.6 per wave. The defining feature is absent, so
  this reads as a still diagram of a pool with a wobbly line.
- also: the 6-px tile grid reads as graph paper (too regular, too strong); the lane line
  stair-steps because albedo is sampled with hard 1-px edges.
- next: parameterize each wave by hessian gain η and displacement δ directly (η≈0.45, δ≈0.4 px),
  anti-alias the lane line with coverage, drop the grid, push lamp saturation.

### iter-2
- stats: loop 30 s · motion median 35.6% · seam 1.0x CLEAN · void 49% · 16.5 ms/frame build.
- change: waves parameterized by Hessian gain η and displacement δ; 7 waves, λ 14–19 px, directions
  spread evenly + jitter, static domain warp folded into the phase tables (free irregularity);
  anti-aliased lane line; grid removed. Probed 4+5 variants side by side (gain 0.55–0.85,
  wave count 5/7/9) — 7 waves of similar λ gave the most "pool floor" web (ring cells, thin bright rims).
- works: at phone scale (sheet thumbnails) it is unmistakably light on a pool floor; the lane line
  bends with the surface; deep end goes electric blue, shallow end stays teal.
- biggest problem: **composition** — a rectangle centered on an empty black field is a diagram,
  not a place. Nothing says motel / 3am / somebody was here, no warm support, no drift layer and
  no rare event; the entire pool churns uniformly (every pool cell changes every step, waves up to
  3 cycles/loop → cells re-form every ~3 s, busier than "held").
- also: at full res the caustic reads as blobs more than web — wants thinner, brighter rims (contrast).
- next: bleed the pool off the top edge (shallow end out of frame, deep end + lamp in the lower third),
  ladder at the deep end, two chaises on the right deck lit teal on their pool side, a warm window
  spill on the deck as the single support hue, one leaf on a slow closed orbit (drift layer),
  one drip ring per loop (rare event), slow the waves to 1–2 cycles/loop.

### iter-3
- stats: loop 30 s · motion median 38.1% · seam 1.0x CLEAN (seam.png: identical) · void 47% ·
  18 ms/frame median, 26 ms max.
- change: recomposed — pool bleeds off the top edge (shallow end out of frame), deep end + lamp in
  the lower third; ladder rails at the deep end with an underwater tread that refracts; two loungers
  on the right deck; warm door spill entering from the right edge (the one support hue); a backlit
  leaf on a slow closed orbit (one revolution per loop, drift layer); one drip ring at t=14 s
  (Hessian of a radial wave packet, rare event); waves slowed to 1–2 cycles/loop; bleach shoulder so
  highlights go pale aqua instead of neon cyan.
- perf bug found + fixed: unbounded Style cache → GC gen-2 pauses growing 54→135 ms; capped at 6000.
- works: at phone scale it is now a place, not a diagram — slab of light, loungers, door left ajar,
  a leaf. The ring at 15 s is the best moment in the piece. Half-second frames confirm the caustic
  drift is slow and held, not churn.
- biggest problem: **no focal hierarchy.** The caustic texture is equally bright and busy from the
  top edge to the deep end; the lamp that explains the light is invisible (its hot spot reads as one
  more caustic blob). The eye has nowhere to land.
- also: deep end still drifts to neon cyan (R≈0); the lane line is a hard black bar bisecting the
  subject; loungers read as two-segment "battery" icons (crease too dark, no height cues); spill's
  inner edge is muddy brown.
- next: draw the lamp lens on the wall with a halo, steepen the falloff so the upper pool sinks to
  deep teal, add red to the deep tint, soften/thin the lane line, give the loungers height (soft
  crease, brighter inclined back, longer cast shadow from the back).

### iter-4
(studio/tac was updated mid-iteration — stats now count only >6-level changes and judge the seam
against p90; all iter-4 numbers are from the new renderer.)
- stats: loop 30 s · motion median 0.3% · p90 2.1% · 14% of cells ever move · seam 0.1x p90 CLEAN ·
  void 49% · 17.8 ms/frame median, 36.6 ms max.
- change: lamp lens drawn on the left wall (white-hot, the brightest pixel in the piece) with a
  steeper falloff; shallow tint darkened so the upper pool sinks to deep teal; deep tint given red
  (32,118,175) so the lamp zone is steel-aqua, not neon; lane line thinned + softened to 48%.
  Loungers: tried pixel straps (read as vents), cushions (read as pills/batteries), light and heavy
  box-drawing line art (read as UI buttons) — **cut them**. The deck is now negative space. Door
  spill reshaped into a fan with a bright threshold strip, so it reads as a door left ajar.
- works: clear hierarchy now — lamp → glowing deep end → dim shallow water off the top edge;
  warm wedge bottom-right balances the aqua slab; the frame is two lights in the dark.
- biggest problem: **time.** Waves at 1–2 cycles/loop move ~0.5 px/s; per step almost nothing
  crosses the 6-level threshold (median 0.3%). The caustic — the reason the piece exists — reads as
  slowly morphing blobs, closer to a lava lamp than water. The drip ring (the best moment) is gone
  in ~6 s and reads as a small donut/target at its start.
- also: leaf is olive-yellow while the door light is amber — two warm hues where there should be one.
- next: waves to 1–3 cycles/loop (~1 px/s), ring lives 18 s and decays slower, leaf to amber;
  try specular glints of the door light on the water surface (a second, surface plane above the
  floor caustics — and the warm hue enters the pool).

### iter-5
- stats: loop 30 s · motion median 1.0% · p90 3.4% · 25% of cells ever move · seam 0.2x p90 CLEAN ·
  void 49%.
- change: waves 1–3 cycles/loop (~1 px/s), drip ring now lives 18.5 s (t=10.5→29 s) with slower
  decay; leaf moved from olive to amber (same family as the door light — one support hue).
  Tried specular glints of the door light on the surface (target slope at the p90 of |∇h|, warm
  additive) — on a bright pool they read as grey dirt specks, not light. Cut.
- works: the ring is now the piece's rare event — a thin circle that crosses the whole pool over
  ~10 s, bending the caustic cells as it passes (t=15 s frame). Motion is still calm (median 1%).
- biggest problem: **full-res legibility of the caustic.** At 1040×1848 the web dissolves into
  soft 3-px blobs: λ 14–19 px makes cells ~8 px with rims as wide as the cells' interiors. It reads
  as caustics only at thumbnail scale.
- next: bigger cells (λ 17–23) + more contrast (CPOW 1.6, LO 0.28) so rims are thin relative to the
  cell; slightly longer leaf so it isn't a 3×2 orange block.

### iter-6
- stats: loop 30 s · motion median 3.4% · p90 6.5% · 35% of cells ever move · seam 0.5x p90 CLEAN ·
  void 50%.
- change: λ 17–23 px, CPOW 1.6, LO 0.28, gain 0.75 (picked from a 5-way probe); leaf 9 px long.
- works: full-res now shows real cells — dark rounded floors with bright rims around the lamp
  (t=0 lower half), dappled spots in the dim shallow water. The drip (probe frames 10.4→13.5 s) is
  gentle: a caustic spot swells into a 2–3-ring wave packet and walks outward, not a pop.
- biggest problem: **the lamp zone clips to flat white** (a 3×10-px slab under the lens at
  t=0/15 s), so the lens is no longer the single brightest thing and the glare reads as an artifact.
- also: the ladder's in-water rail segments are pasted on top of the water (they don't refract,
  while the lane line and tread do); the right deck is 24×100 px of nothing above the door light.
- next: soften the lamp falloff/hot spot, route the underwater rails through the refracted
  albedo, test wet footprints from the ladder to the door (a trace, not a beat).

### iter-7
- stats: loop 30 s · motion median 2.5% · p90 5.6% · 35% of cells ever move · seam 0.4x p90 CLEAN ·
  void 49% · 18.4 ms/frame median, 26.3 ms max.
- change: the white slab was not the lamp hot spot (HOT 1.6→0.7 and a near-field knee changed
  nothing) and not the caustic cap (CMAX 8→3 just flattened every rim into plateaus) — it was the
  product lamp×caustic overrunning all three channels. Fix: hue-preserving luminance knee before
  the tone curve (above L=240, slope 0.25). Lens is the brightest pixel again, the slab is pale aqua
  with shape. Underwater rail segments now go through the refracted albedo (they wobble with the
  lane line and tread); only the above-water handrail is drawn flat. Wet footprints: 6 small
  ellipses, alternating sides, ladder → door, catching teal near the pool and amber in the spill.
- works: the trail bridges the two lights (ladder → door) and tells "someone swam and went in"
  without a beat; every frame has a clear read order: lens → lit deep end → ring/leaf → trail → door.
- biggest problem: **the top edge.** Caustic spots near y=0 are as bright as mid-pool ones
  (t=0 top-right blob), pulling the eye to the frame edge; the shallow end should recede into the
  dark the way the light physically would 80 px from the lamp.
- next: a falloff toward the top edge so the pool dissolves into the dark off-frame.

### iter-8
- stats: loop 30 s · motion median 2.3% · p90 4.7% · 33% of cells ever move · seam 0.5x p90 CLEAN ·
  void 55%.
- change: light falls to 25% over the top 55 px (probed 1.0/0.45/0.3/0.2) — the shallow end now
  dissolves into the dark off-frame; void 49% → 55%.
- works: the t=15 s frame is the strongest image so far — the drip ring is a clean circle in dark
  water, the pool reads as a vertical shaft of light rising from the lamp. Coping lines still run
  off the top as structure.
- biggest problem: **no rest beat.** The caustic runs at the same intensity for all 30 s; the DNA
  wants a near-still moment every 15–30 s, and the drip ring lands on full-strength caustics,
  competing with them instead of owning the moment.
- next: a once-per-loop "settle" — surface amplitude dips to ~65% around the drip, so the water
  goes glassy, the ring crosses calm water, and the web returns. Strip dead lounger code.

### iter-9 (final → chlorine.py)
(studio/tac updated again: motion is now cells changed per 0.5 s window. Calibration on the same
renderer: rooftop-skyline 0.4%, coffee-shop 4.7%, aurora 27.1%.)
- stats: loop 30.00 s / 300 steps @ 10 fps · motion median 18.0%, max 21.5% per 0.5 s · 43% of cells
  ever move · seam 1.8% (0.7x p90 step) CLEAN · void 55% · build 18.1 ms median / 29.5 ms max,
  57 ms first frame incl. setup.
- change: a once-per-loop settle — surface amplitude dips to 58% centred on t=12.5 s (the drip lands
  at 10.5 s). The web is at full strength at t=0 and t≈27 s, goes glassy through the middle, and
  the ring crosses calm water and owns the frame (t=15 s). Dead lounger/knee code stripped;
  chlorine.py renders pixel-identical to iter-9 (cmp on frame-015).
- robustness: renders without error at 120×40, 60×30, 80×24, 200×60, 100×80 (landscape 120×40 holds
  the composition).

## self-review

1. **First frame** — t=0 is the strongest web: a vertical shaft of aqua light with a white-hot lamp
   on the left wall, an amber leaf, a fan of door light bottom-right. Reads as "pool from above at
   night" in 200 ms; the slab-of-light silhouette carries even as a thumbnail (sheet thumbs).
2. **One subject** — the pool. Read order is fixed by luminance: lens → lit deep end → leaf/ring →
   footprint trail → door. Right deck and bottom 21% are near-black volume; void 55%.
3. **Palette** — deck (9,10,15); one accent, pool aqua (teal shallow → steel-blue deep, highlights
   bleach to pale aqua, never flat white thanks to the luminance knee); one support, amber (door
   light, leaf, wet prints in the spill). No third hue.
4. **Motion** — three timescales: caustic drift ~1 px/s (medium); leaf orbit + settle breath, one
   cycle per 30 s (slow); drip ring once per loop (rare). Rest beat = the glassy stretch 7–18 s.
   18% per 0.5 s sits between coffee-shop (4.7%) and aurora (27.1%).
5. **Seam** — every periodic term uses integer cycles per 300 frames; ring lives frames 105–290;
   seam.png last|first indistinguishable; 0.7x p90.
6. **TAC-ness** — 3am, motel, door left ajar, wet footprints drying toward it: someone swam and
   went inside. Quiet, late, held; no climax, the drip is a breath not a beat.
7. **Novelty** — the catalog's first computed optics: caustic intensity is 1/|det(I + c·H)| of the
   live surface Hessian, the floor (lane line, ladder tread, underwater rails) is sampled through
   the surface gradient so it wobbles physically, and the drip is a radial wave packet whose
   Hessian is added analytically. Also the first top-down / aerial framing.

## renderer / pipeline notes
- (pre-revision) preview.gif was 26.4 MB at the default 560 px (17.8 MB at 400 px): smooth per-frame colour change
  over ~40% of the frame is GIF's worst case. Catalog previews top out at 8.9 MB. reel.mp4 is 5.5 MB.
  Publishing may want an mp4/webm preview or a lower-fps gif for this piece.
- mid-session the renderer briefly raised `NameError: clock` inside capture() while being edited;
  resolved on its own, no workaround needed.

## catalog description
An empty motel pool at 3am, seen from straight above — the underwater lamp throws a slow caustic web
across the floor, a leaf circles, wet footprints lead to a door left ajar, and once a loop a single
drop rings out across water gone glassy.

---

## revision 1 — the float (operator request: replace the leaf with a red inflatable ring)

### scale assumption
- Pool width **5.5 m** (motel pools run 4.5–6 m). The pool is pw = 44 px wide at 80 cols →
  **8.0 px/m**. Pixels are treated as square (half-block: 13×14 px on screen, 7.7% taller than
  wide — under the threshold where a circle reads as an oval; everything else in the piece uses the
  same unit).
- Float: outer Ø 1.1 m → **8.8 px** (R_o 4.4 px); hole Ø 0.4 m → **3.2 px** (R_i 1.6 px);
  tube 0.35 m → **2.8 px**; 8.8 / 44 = 1/5 of the pool width. All derived in code from
  POOL_M / FLOAT_OD / FLOAT_ID, not drawn by eye.
- Depth: 1.0 m shallow → 2.0 m deep, same smoothstep as the water tint.

### light model (decided once, used everywhere)
- Two sources. (1) The underwater lamp: diffuse fill of the volume (the lamp field), lights the
  float only from below/side — so no floor shadow from it. (2) A high moon, up-left, 37° off
  vertical: it is what the caustic web is made of. The float blocks it → its shadow removes the
  caustic term on the floor (c → LO), offset down-right by depth · tan(θ_water) · PPM with
  θ_water from Snell (sin 37°/1.33 → tan 0.43): ~4.8 px at the float's 1.4 m depth. Penumbra
  grows with depth; the shadow is sampled through the surface gradient so it wobbles like the
  lane line.
- Float shading: torus normals; moon diffuse + Blinn specular (cool), door light on the right
  flank, aqua rim where the tube's normal turns horizontal (reflected pool light). Red vinyl over
  cyan pool light transmits almost nothing, so the float is a dark crimson silhouette, not a glow.

### behaviour
- Drift: ellipse 3.5×6 px around (px0+0.34·pw, 0.30·H), one revolution per loop (~1 px/s
  ≈ 0.12 m/s), exact at the seam.
- Rotation: one turn per loop + ±0.25 rad wobble at 2/loop; a pale valve on the tube marks it.
- Rides the surface: position slides 0.35× the surface tilt averaged over 5 points under the
  tube; normals tilt with it. The drip ring's analytic slope at the float centre is added with
  gain 2.2 → a visible rock as the ring passes (t≈17–19 s).

### iter-10
- stats: loop 30 s / 300 · motion median 18.3% · max 21.5% per 0.5 s · 43% ever move ·
  seam 0.8x p90 CLEAN · void 55% · 18.1 ms median / 28.9 ms max per frame.
- works: the ring with its hole reads at thumbnail scale at true size — no inflation needed. The
  offset shadow sits down-right of it and tells you the water is deep. The rock is visible:
  shading flips side as the ring passes under (crop t=16.5→19.5 s).
- biggest problem: **the tube shades pink, not red.** Specular (pow 24, ×150) spreads over the
  whole lit half and adds near-equal RGB, so the upper-left of the ring is salmon; the valve
  sinks into that and rotation is barely visible.
- also: amber door light + crimson float are two different hot hues (30° vs 355°).
- next: tight glossy specular (pow 60), saturated vinyl albedo (205,22,30), white valve on red;
  door light muted and shifted toward the red family.

### iter-11
- stats: loop 30 s · motion median 18.3% · max 21.5% per 0.5 s · seam 0.8x p90 CLEAN · void 55%.
- change: vinyl albedo (205,22,30), Blinn specular pow 60 (a tight cool glint top-left, which
  stays put while the float turns — that's what gloss looks like), white valve (232,226,216).
  Door light: amber (255,175,95) at 0.30 → muted coral-tan (240,160,128) at 0.27, picked from a
  4-way probe (amber / pale peach / coral / muted). Amber next to crimson read as two hot hues;
  coral-tan is a desaturated relative of the float, so the warm side is one family — saturated
  red on the float, a dusty red-tan in the door light and on the wet prints. Teal stays dominant
  (the float is ~60 px of red against ~4,500 px of water).
- works: the float is red, not pink; the tube reads round (glint top-left, crimson body, aqua rim
  on the outer and inner edges); the hole shows whatever the water is doing under it. The valve
  walks around the ring once per loop, so the turn reads frame to frame.
- biggest problem: **the valve lights 2 pixels** when it sits between pixel centres (t=10 s:
  two white dots at the bottom of the tube), which reads as two features, not one valve.

### iter-12 (final → chlorine.py)
- stats (80×66): loop 30.00 s / 300 steps · motion median 18.3%, max 21.5% per 0.5 s · 43% of
  cells ever move · seam 2.2% (0.8x p90) CLEAN · void 55% · build 18.0 ms median / 25.2 ms max.
- stats (80×81): motion median 17.6%, max 21.7% · seam 2.1% (0.9x p90) CLEAN · void 56% ·
  build 22.5 ms median / 30.7 ms max. Composition holds at 81 rows: the pool is taller, the
  float sits in the upper third, the drip ring lands closer to it (t=15 s).
- change: valve radius 1.0 → 0.8 px with a steeper falloff, so it lights one pixel.

### self-review (revision)
1. **First frame** — t=0: aqua shaft, white lamp, and now a red ring floating in the upper third.
   The float + hole + offset shadow reads in 200 ms ("pool float") at true scale.
2. **One subject** — still the pool; the float is the occupant. Read order: lens → red float →
   lit deep end → drip ring → prints → door.
3. **Palette** — teal dominant; red is the warm accent; the door light is muted into the same
   family. No amber any more.
4. **Motion** — float drift and turn (slow, one cycle/loop), caustic drift (medium), drip + rock
   (rare). The rock is the drip's consequence, so the rare event now has a cause and an effect.
5. **Seam** — float path, turn and wobble are all integer cycles/loop; seam CLEAN at both heights.
6. **TAC-ness** — someone swam, left the float, went inside. Still quiet, late, held.
7. **Novelty** — on top of the optics: the float obeys the same light model as the floor (its
   shadow cuts only the overhead caustic term, offset by Snell-refracted depth), and it rides the
   computed surface (slide + tilt from the averaged gradient, analytic ring slope → rock).

## catalog description (revised)
An empty motel pool at 3am, seen from straight above — the underwater lamp throws a slow caustic web
across the floor, a red pool float turns on the current with its shadow on the bottom, wet
footprints lead to a door left ajar, and once a loop a single drop rings out and rocks the float.

## deliverables (revision)
- chlorine.py = iter-12.py · preview.gif 17.7 MB at --width 400 (still ~2x the largest catalog
  preview, 8.9 MB) · reel.mp4 1080×1920, 900 frames, 5.6 MB · chlorine-iphone.mp4 1206×2622,
  --rows 81, 900 frames, 7.7 MB.
