# sodium — notes

## concept

First person, crawling through a long highway tunnel at 2am. Two strings of sodium lamps converge
into a gentle left bend that never shows the exit; wet asphalt mirrors them as long streaks; once a
loop a run of three dead lamps drifts overhead and the tunnel goes dark for a breath. Sodium light
is physically monochrome, so the one-accent rule is the optics, not a palette choice.
Format bet: the catalog's first first-person interior in true perspective — a sphere-traced curved
tunnel (torus-like horseshoe section), inverse-square lamp irradiance baked per surface sample,
glossy reflections on the wet road from reflected rays, screen-space fixtures + haze halos.
Canvas 80×66 cells, half-block ▀ → 80×132 pixels (13×14 px each; aspect 14/13 corrected).

## physical scale (everything is sized from this, not by eye)

- Two-lane road tunnel: road 9.0 m wide (two 3.5 m lanes + 1.0 m shoulders), vertical tiled walls
  to 3.0 m (tile band to 2.4 m, concrete above), elliptical arch with the apex at 6.0 m. Constant
  left bend, radius 700 m — the outer wall closes the view ~130 m out, so there is never an exit.
- Camera: driver's eye, 1.2 m above the road, centred in the right lane (u = +1.75 m), pitched up
  4.6° (0.08 rad). Focal length 94 px on the 80×132 half-block canvas → vertical FOV ±35°,
  horizontal ±23°; vanishing point at 57% of the frame height.
- Lamps: two rows on the arch at u = ±2.8 m, h = 5.35 m, hung 0.25 m below the surface, fixtures
  1.0 × 0.3 m, spacing 12 m per row, the left row staggered 6 m (standard staggered luminaire
  layout — v2). Arch ribs every 4 m (0.2 m face + 0.2 m groove), a 0.12 m trim at the tile /
  concrete junction (2.4 m), tile courses every 0.4 m on the lower walls (v2).
- Lane paint: centre dashes 2 m on / 6 m off (8 m period, so lamps and dashes beat every 24 m
  instead of locking), solid edge lines at ±3.5 m, 0.10 m wide, worn (albedo 0.22).
- Speed: 5 m/s (18 km/h, a crawl) → 0.4 m per frame at 12.5 fps; loop 360 frames = 28.8 s =
  144 m = 12 lamps = 18 dash periods. Two dead lamps at 70% of the loop (84 m and 96 m).
- Wet road: Fresnel F0 = 0.03, wetness 0.3 base → 0.75 in the four wheel tracks (u = ±0.95,
  ±2.55, σ 0.5 m); lobe 0.4 rad at steep view → 0.04 rad at grazing, floored at the asphalt
  macro-texture scale (1.0 m along, 0.5 m across).
- Car ahead: 14 m (v2; v1 had 28 m), same lane, 1.8 × 1.45 m body from 0.28 m up, rear window =
  top 30%; brake lights 0.18 m at ±0.62 m, 0.9 m high; breathes ±1.5 m / ±0.12 m over the loop
  (1 + 3 cycles).
- Sizes at that scale: nearest fixture 1.0 m at 6 m ≈ 16 half-block px tall; car 1.8 m at 14 m
  ≈ 12 px wide, 10 px tall; brake lights 0.18 m at 14 m ≈ 1.2 px (drawn as coverage) with a
  bloom — nothing is inflated past physical size. (v1's car at 28 m had 0.6 px lights: the
  coordinator's cross-check caught that it could not anchor the frame.)

## iteration log

### iter-1
- stats: loop 24.0 s / 300 steps @ 12.5 fps · motion median 51.6% per 0.5 s · 100% of cells ever move ·
  seam 0.6x p90 CLEAN · void 75% · build 5.4 ms median / 12 ms max, 434 ms first frame incl. setup.
- works: it is a tunnel in 200 ms — two lamp strings bending left into haze, a near pair overhead,
  the right wall closer than the left (right-lane camera), the far string curling into the bend.
  Seam clean by construction (loop = integer lamps, integer texture steps per frame).
- biggest problem: **underlit by ~5×.** Wall irradiance ≈ 0.22 per lamp × albedo 0.55 → tone 0.15:
  the tiled walls, the arch and the road are all near-black, so the frame is blobs on nothing
  (void 75%). Fix: LAMP_I 9 → ~40, probe 25/40/60.
- also: (a) crisp "lamp pairs" on the near road are the fixture reflections — blur is ROUGH·t₂ =
  0.35 m at 5 m, so the mirror image is sharp; real wet asphalt smears it into a soft blob/streak
  (ROUGH 0.07 → 0.3, more blur levels). (b) 12 m lamp spacing reads as isolated blobs, not a
  rhythm — try 8 m. (c) halo kernels cut off at 3.5σ (Lorentzian still 8% there) → visible ring;
  add a smooth window. (d) edge lines are the brightest lines in the frame (paint 0.65 vs asphalt
  0.09) → runway; worn paint 0.35, 0.10 m. (e) tile band top at exactly 3.0 m gives the right wall a
  hard polygon edge → 2.4 m with a 0.3 m fade. (f) fog 110 m leaves 150 m lamps as crisp dots → 70 m.
- next: all of the above, probed side by side (LAMP_I × spacing × speed).

### iter-2
- stats: loop 24.0 s · motion median 81.3% per 0.5 s · seam 1.0x p90 CLEAN · void 15%.
- change: LAMP_I 9 → 40, EMIT 300, spacing 8 m, ROUGH 0.3, fog 70 m, halo window, worn paint, tile
  band fade. Probed LAMP_I 20/40/70 (+AMB 0.3) and spacing 8 vs 12 / V 5 vs 10 / R 600 vs 2000.
- works: unmistakably a tunnel at thumbnail size; the lamp strings bend left; the right wall is
  closer than the left (right-lane camera) — the asymmetry reads.
- biggest problem: **daylit.** Void 15% — walls are flat bright planes, the ceiling is a brown slab,
  the dashes are the loudest element. Not "quiet, late, held"; a brown tunnel at noon.
- probe verdicts: LAMP_I 20 is nearest the register; 12 m spacing leaves real dark between lamps
  (8 m is a busy string); R 600 makes the bend legible, 2000 is a straight pipe → R 700.
- next: LAMP_I 20, albedos down (tile 0.42, arch 0.22, apex soot 0.05 so the ceiling is void),
  12 m lamps / 7.5 m/s (lamp every 1.6 s), dash period 9 m (LCM 36 divides the 180 m loop, so dashes
  and lamps drift against each other instead of locking), fog 60 / haze 0.25, 2 dead lamps.

### iter-3
- stats: loop 24.0 s / 300 steps · motion median 63.1% · seam 0.5x p90 CLEAN · void 66%.
- change: as planned above; reflection source gets a ±2-row lateral blur; ROUGH 0.5, EMIT 100.
  Probed PITCH 0 / 0.08 / 0.16 and haze 0.12@90 m / 0.25@60 / 0.4@45.
- works: first frame in the register — lamps float in a dark arch, the ceiling is void, the dead-lamp
  stretch (t=18 s) is a real rest beat with only the far string glowing.
- biggest problem: **the orange slabs at the bottom are not dashes — they are the lamp reflections
  in the wheel ruts.** EMIT 100 × Fresnel 0.06 × wet 0.9 ≈ 5 → saturates; ROUGH 0.5 × 6 m = 3 m of
  blur against 12 m spacing leaves them as segments instead of streaks. Lateral edges are hard
  (wetness σ 0.38 m).
- pitch verdict: 0.08 rad (4.6° up) → vanishing point at 57% height, ceiling becomes negative space,
  road shrinks to the bottom third. 0.16 made the road a sliver.
- also: walls are blurry planes with no structure; nothing for the eye to land on at the far end.
- next: EMIT 25, ROUGH 0.9, wetness σ 0.5 / peak 0.75; concrete joints halfway between lamps on the
  walls (0.2 m, −50%, faded with distance so they don't alias); a car 42 m ahead in our lane with
  red brake lights + their streaks on the wet road (the one support hue, the focal anchor); it
  breathes ±1.5 m / ±0.12 m over the loop (integer cycles).

### iter-4
- stats: loop 24.0 s · motion median 60.0% · seam 0.5x p90 CLEAN · void 63% · build 9.9 ms median.
- change: EMIT 25, ROUGH 0.9, lateral blur ±4 rows, wetness σ 0.5; wall joints (0.2 m, −50%, faded
  with distance); a car 42 m ahead with brake lights + streaks; PITCH 0.08. Probed EMIT/ROUGH and
  car on/off, joints on/off, car at 28 m.
- works: the car reads — the two red streaks on the wet road sell it more than the 4×3 px body;
  it is the focal anchor and the only second hue. Joints add a thin vertical structure to the walls.
- biggest problem: **murk.** The reflections spread to nothing (EMIT 25 over a 0.9·t₂ blur), the
  lower two-thirds is a flat dull brown, the far end is mush. Too much mid-dark brown, not enough
  near-black and not enough bright structure. Car at 42 m is a dot; 28–32 m reads.
- next: V 5 m/s (lamp every 2.4 s), loop 360 frames = 28.8 s = 144 m = 12 lamps, dash 8 m; stronger
  lamp directionality, lower ambient, gamma 1.3; EMIT 150 / ROUGH 1.6 / lateral ±8; car at 32 m.

### iter-5
- stats: loop 28.8 s / 360 steps · motion median 23.6% · seam 0.4x p90 CLEAN · void 92%.
- works: 5 m/s is the right pace (motion 60% → 24%); the loop now passes 12 lamps.
- biggest problem: **overshot into black.** cl^1.5 killed the wall light, lateral ±8 rows (±1.6 m)
  divided the fixture reflection by ~11, gamma 1.3 crushed the rest. Probe: gamma 1.0 beats 1.3.
- next: back to iter-4 light (cl^0.8, AMB 0.035, haze 0.26, gamma 1.1) and fix the reflection model
  properly instead of scaling it.

### iter-6
- stats: loop 28.8 s · motion median 46.4% · seam 0.7x p90 CLEAN · void 68%.
- change: reflection stretch law — along-tunnel blur ROUGH·t₂/sin(elev), lateral ROUGH·t₂, blur
  levels chosen per pixel in both axes.
- biggest problem: **terracing.** Blur levels 2× apart chosen per pixel → iso-level contours become
  hard-edged "fan" slabs on the near road (sheet, all frames). Also still too bright near.
- also found the real modelling error: I was diluting the streak energy by the stretch factor.
  The stretch is foreshortening of the same radiance (ds = t₂·dθ/sin elev), so the gain must be
  kept — that is exactly why real wet roads show bright streaks at grazing angles.
- next: gain × 1/cos v; facet roughness rising at steep view angles (thin water film sits in the
  asphalt texture, so steep views see facets, grazing views see the film); bilinear interpolation
  across blur levels, prefix-sum lateral averages.

### iter-7
- stats: loop 28.8 s · motion median 45.2% · seam 0.6x p90 CLEAN · void 59% · setup 376 ms,
  7.8 ms/frame.
- works: **the tunnel image.** Converging bright streaks on wet asphalt, lamps floating above, the
  far bend — at thumbnail size this is the piece.
- biggest problem: the road reflection is a flood, not streaks — lateral lobe 0.15·t₂ spreads the
  0.3 m fixture over ±3 m at distance, so the whole right lane is a bright slab; faint arcs where
  2×-apart blurs blend near the dead lamps.
- next: tight lobe (0.03) with steep-angle roughening ×8.

### iter-8
- stats: motion median 52.8% · seam 0.8x p90 CLEAN · void 64%.
- biggest problem: wrong direction — 0.03 rad makes the near reflections mirror-sharp saturated
  blocks and brings the arcs back hard. ROUGH 0.05 / ×16 variants were the best of the probe.
- next: parametrize the lobe as near/far roughness (0.3 / 0.04), floor the blur at the asphalt
  macro-texture scale (1.0 m along, 0.5 m lateral), 1.5×-spaced levels with a triangle filter.

### iter-9
- stats: loop 28.8 s / 360 steps · motion median 49.3% · seam 0.9x p90 CLEAN · void 68% ·
  setup 518 ms, 9.4 ms/frame median, 22 ms max.
- works: structure finally right — each lamp pair lays down a pair of soft elongated reflections
  that converge down the road; no terracing; the dead-lamp stretch (t=14.4 s) leaves the far string
  and the brake lights alone in the dark.
- biggest problem: magnitude — the nearest reflections at the frame bottom saturate to pale
  slabs while a hand calculation for that pixel gives tone ≈ 0.3. Instrumenting before tuning.

### iter-10
- stats: loop 28.8 s / 360 steps · motion median 49.8% · seam 0.7x p90 CLEAN · void 69%.
- change: wet-asphalt sheen term (1 − cos v)^1.5 on the specular gain — at steep views the dry
  texture peaks poke through the water film, at grazing the film hides them. ×0.27 at the frame
  bottom, ×0.93 on the far road. ROUGH_NEAR 0.4. Probed GRAZE 0 / 1 / 1.5 / 2.5.
- works: hierarchy is right at last — lamps → mid-road reflection pairs → far streaks → walls;
  the bottom edge is quiet. seam.png last|first indistinguishable.
- biggest problem: streaks are ~2.4:1 where the physics says ~4:1 — I had halved the triangle
  filter's box, so the along-tunnel blur was 0.6× its intended width. And the brake-light streaks,
  the piece's only second hue, are too faint to count at 400 px.

### iter-11 (final → sodium.py)
- stats: loop 28.80 s / 360 steps @ 12.5 fps · motion median 46.0%, max 59.5% per 0.5 s · 90% of
  cells ever move · seam 3.9% (0.4x p90 step) CLEAN · void 68% · build 7.5 ms median / 18 ms max,
  511 ms first frame incl. setup (81 rows: 8.5 ms / 19 ms / 542 ms).
- change: full-width triangle filter along the tunnel (streaks now elongated and continuous into
  the convergence); brake-light streaks 0.55 → 0.9, lights 0.14 → 0.18 m; car 32 → 28 m
  (probed 22 / 28 / 34 / none: 22 shows too much body, 34 is a dot).
- robustness: renders without error at 120×40, 60×30, 80×24, 200×60, 100×80 (setup 0.34–0.76 s,
  4–15 ms/frame); the composition is derived from width/height, F is fixed at 94 px.
- checks: four consecutive frames (t = 3.00–3.24 s) tiled — lamps step evenly, far dots and
  fixtures hold without flicker. iPhone canvas (81 rows): vanishing point at ~55%, more road and
  ceiling, composition holds. sodium.py renders pixel-identical to iter-11 (cmp on t=7.2 s).

## v2 pass (coordinator cross-check: tunnel legibility, face pareidolia at t=0, lost car)

v1 kept as sodium-v1.py / preview-v1.gif / sodium-iphone-v1.mp4.

### iter-12
- stats: loop 28.8 s / 360 steps · motion median 30.8% · seam 0.4x p90 CLEAN · void 78% ·
  19 ms/frame median, 38 ms max.
- change: (1) structure — uniform haze 0.26 → 0.08 and ambient 0.035 → 0.015 so blacks go black
  away from the lamps; in-scatter only at the lamps (halos; far halos floored at 2.5 px so they
  overlap into the end-glow); geometry for the lamps to catch — arch ribs every 4 m, a trim line
  at the tile/concrete junction (anti-aliased per pixel), tile courses every 0.4 m on the lower
  walls, all lit by the baked inverse-square irradiance so they brighten under each fixture and
  fade between; courses and ribs fade with distance so they cannot alias. (2) eyes — the two lamp
  rows staggered by 6 m (real staggered luminaire layout), so no pair ever sits at one height; the
  bloom now hugs the capsule segment instead of being a round disc. (3) car 28 → 14 m: lights
  0.18 m → 1.2 px, body 12 px wide, red bloom; the body also covers the busy convergence knot.
- t=0 check: right lamp at 12 m (upright, right), left at 18 m (smaller, lower-left), the 6 m left
  lamp is off-frame — no eye pair, no V under a pair. Probed HALO_K / HALO_MIN, RIB 0.9, haze 0.
- biggest problem: the red bloom is heavier than the 1.2 px source warrants; the car's window band
  is invisible; frame max 38 ms from the larger halo windows.

### iter-13 (final → sodium.py)
- stats: loop 28.80 s / 360 steps @ 12.5 fps · motion median 29.1%, max 36.1% per 0.5 s · 72% of
  cells ever move · seam 4.0% (0.5x p90) CLEAN · void 79% · build 10.4 ms median / 21 ms max,
  563 ms first frame incl. setup. iPhone canvas (80×81): seam 0.4x p90 CLEAN · void 83% ·
  11.7 ms / 22 ms / 594 ms.
- change: bloom 0.8 → 0.55 (σ 2.6), window band 0.16 → 0.3, ribs 0.45 → 0.65, halo σ cap 8 /
  extent 3.5σ. sodium.py pixel-identical to iter-13 (cmp on t=7.2 s).
- works: "tunnel" at 400 px — ribbed arch over the lamps, lit trim line and tile courses running to
  the vanishing point, deep black between; the first frame opens on two unpaired fixtures; the
  car is a dark body with two red lights, a bloom and two long streaks. Motion fell 46% → 29% as
  the blacks stopped drifting.

## self-review

1. **First frame** — t=0 (v2): one upright fixture at 12 m on the right, a smaller slanted one at
   18 m on the left, the ribbed arch and the lit junction line running into the bend, a wet road
   laid with converging amber streaks, and a car with its brake lights on 14 m ahead. "Tunnel at
   night, following a car" in 200 ms; the 400 px sheet thumbnails carry it; no paired fixtures.
2. **One subject** — the tunnel. Read order by luminance: near fixtures → lamp strings → mid-road
   reflection pairs → the red pair at the convergence → walls. The upper third and the near road
   are negative space; void 79% (v2).
3. **Palette** — ground (10, 8, 12); one accent, sodium amber (rust → amber → pale yellow,
   fixtures the only near-white) — monochrome by physics, not by choice; one support, the brake
   lights' red (255, 36, 22) and their streaks. No third hue.
4. **Motion** — three timescales: the lamp rhythm (one pair every 2.4 s, sweeping up while its
   reflection sweeps down), the car's breathing (±1.5 m, 1 + 3 cycles per loop) and the dead-lamp
   run once per loop. Nothing jitters; every pixel drifts at 18 km/h. Honest caveat: the stat
   said 46% of cells changing per 0.5 s in v1 because a first-person drive moves the whole field —
   calibrated on the same renderer, aurora is 27%, chlorine 18%. v2's deep blacks brought it to
   29% — inside aurora's band. The feel is a crawl, not churn.
5. **Seam** — loop = 144 m = 12 lamps = 18 dashes, texture index advances 2 samples per frame, the
   car's sway uses integer cycles; seam.png last|first indistinguishable; 0.4x p90.
6. **TAC-ness** — 2am, stuck at a crawl under the river, brake lights ahead, the exit hidden
   round a bend that never straightens; the rest beat is the stretch where two lamps are out and
   the tunnel goes dark for a breath (t ≈ 14 s). Quiet, late, held, no climax.
7. **Novelty** — the catalog's first first-person interior in true perspective: a sphere-traced
   curved horseshoe tunnel, inverse-square lamp irradiance baked per surface sample and advanced
   by integer texture steps, and wet-asphalt reflections from reflected rays with the real stretch
   law (streak length = lobe × t₂ / sin elevation, radiance kept, not diluted) plus the
   grazing-angle sheen of a water film over asphalt texture. citadel is 3D from outside;
   night-drive is a flat plane; nothing in the catalog puts the viewer inside the geometry.

## renderer / pipeline notes
- v1 preview.gif was 12.9 MB at --width 400 (360 frames; ~90% of cells drifting every step, GIF's
  worst case); v1 reel.mp4 7.4 MB, iPhone 8.4 MB. v2 sizes are appended below after export.
  v2: preview.gif 13.1 MB at --width 400 (360 frames), reel.mp4 7.5 MB (720 frames @ 25 fps),
  sodium-iphone.mp4 8.5 MB. Catalog previews top out at 8.9 MB, so publishing may want an
  mp4/webm preview — the gif cannot get under it without a shorter loop or lower fps.
- The scratchpad is shared between the parallel artists: my helper scripts were overwritten
  mid-session (bench.py, probe.py). Suffixed copies (-7) fixed it; nothing in the repo affected.
- `studio/tac` behaved; no renderer bugs found. The Bash tool blocks a leading `sleep`, so
  renders that depend on a file wait with an `until [ -f … ]` loop instead.

## catalog description
Crawling through a long highway tunnel at 2am — two strings of sodium lamps bend out of sight,
the wet road throws them back as long amber streaks, a car's brake lights hold steady up ahead,
and once a loop two dead lamps drift overhead and the tunnel goes dark for a breath.
