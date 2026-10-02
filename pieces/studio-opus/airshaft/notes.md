# airshaft — notes

## concept

The bottom of a fourteen-storey tenement light well in the small hours of the first snow, looking
straight up. Four walls of windows converge in true three-point perspective to a small rectangle of
amber city cloud; most windows are dark, a few are still lit, and once a loop someone walks down
the stairs, tripping the fluorescent landing lights one by one. Lane: Cities (density, depth,
infrastructure).
Format bet: a per-pixel ray-cast worm's-eye canyon — the first upward framing in the catalog — with
analytic box-filtered window texture (each pixel's footprint on the wall plane is integrated against
the window/ledge/AC pattern), so ~420 windows minify into texture toward the sky without aliasing.

## physical scale (every object sized from this)

- courtyard interior 12.0 m (E–W) × 18.0 m (N–S); walls = ground floor 3.6 m + 14 storeys × 2.9 m
  + 1.0 m parapet → roofline 45.2 m.
- bays 2.0 m (6 per N/S wall, 9 per E/W wall). Windows: kitchen 0.9 × 1.2 m (sill 0.95), living
  1.4 × 1.3 m (sill 0.90), bathroom 0.5 × 0.65 m (sill 1.50), stairwell 0.8 × 1.0 m offset
  half a storey (sill 2.25). Floor ledge 0.30 m band at each slab. AC units 0.75 × 0.45 m,
  0.55 m deep, under ~55 % of kitchen/living windows. Drain pipes Ø 0.16 m.
- rooftop: water tanks 1.6–3.6 m long, 1.6–2.4 m tall; mast 0.12 m × 7 m — from the bottom of a
  45 m shaft these foreshorten to < 1.5 px, so they only notch the rim (kept because they are true).
- camera (final): standing, eye 1.6 m, 2.6 m from the west wall, 8.0 m from the south wall,
  tilted 12° off zenith toward the north wall; focal 0.72·W px (70° × 98° field on the 80×132-px portrait canvas).
  (iter-1–3 used 4.6 / 7.4 m.)
- lamp: one cool-white LED bulkhead at 3.2 m on the west wall, 1.2 m north of the camera, out of
  frame; it lights the snowed courtyard floor and the walls see that bounce (patch centred 1.2 m
  off the wall, softened over 1.6 m).
- snow: 2.2 cm aggregate flakes (×0.7–1.3), 4.5 /m³ at peak, 0.45–0.68 m/s, simulated from
  0.6 m to 4.75 m above the eye (beyond that a flake is < 0.25 px). Visibility-scale scattering
  σ ≈ 0.012 /m (moderate snow) behind the veil/halo gains.
- stairwell: 0.8 × 1.0 m landing windows half a storey up, walker 5.5 s per 2.9 m storey.
- low cloud at 600 m, wind 5.5 m/s (220 m per 40 s loop). (Airliner at 10 km cut in iter-6: it
  would be above the snow deck.)

## iteration log

### iter-1
- stats: loop 40 s / 400 steps · motion median 0.3% · seam CLEAN · void 86% · 41 ms setup,
  5.9 ms/frame.
- render: 10 × 14 m well, 16 storeys, tilt 24° toward the north wall, focal 0.8·W.
- works: the analytic footprint texture is clean — no aliasing even where storeys compress to
  sub-pixel; the build is cheap (41 ms incl. setup).
- biggest problem: **it reads as a corridor, not a shaft.** With 24° tilt the north wall becomes a
  receding "floor" with horizontal storey lines, the south wall a "ceiling" with big foreshortened
  window parallelograms, and the sky a flat brown rectangle at the end — a lit doorway. Walls are
  nearly black (lamp at 1/d² is all the light they get), so there is no window grid to read.
  Windows come out pink/green/blue (per-channel tone curve shifts hue) — four hues, no family.
- next: look (nearly) straight up so all four walls rise; wider/shorter well so the sky is bigger;
  cross-illumination from lit windows onto the opposite walls (coarse form-factor grid);
  hue-preserving tone curve.

### iter-2
- stats: 80 ms setup (incl. 2k-point cross-illumination grid × ~60 window sources), 9.8 ms/frame.
- probes: (a) exposure 3.2 vs 6.0, tilt 0/6/14°, focal 0.7/0.8/0.9 → straight up at 0.72·W reads
  as "rising walls" best; (b) four lighting balances (sky-ambient 0.4–0.8, cross-gain 0.2–0.5,
  lamp 12–40) — the first pass (sky 2.2, cross 1.0) was a daylight render with tan walls.
- works: straight up, 12 × 18 m, 14 storeys — the window grid converging on four sides is strong and
  clean; the stairwell column (cool fluorescent, half a storey offset) reads as the support hue.
- biggest problem: **the sky does not read as sky.** It is a flat pink rectangle with diagonal
  stripes (the sine "clouds" are too regular), cleanly cut, centered, with walls dark right up to it
  — so the image is still a tunnel with a lit door at the end. Nothing protrudes, so nothing says
  "seen from underneath".
- also: lit windows split into salmon and yellow-green (clamping in the hue-preserving curve); the
  nearest windows are 10-px flat blocks that read as UI tiles.
- next: value-noise clouds; aerial haze along the ray (far = lifted toward the skyglow, so the shaft
  brightens into the sky); taller rooftop clutter breaking the rim; AC units as real 3D boxes
  (analytic coverage of the bottom and front faces) so you see their undersides; mullion + transom
  on windows; tone curve that desaturates to white instead of clamping.

### iter-3
- stats: 95 ms setup, 7.7 ms/frame median.
- change: value-noise clouds periodic in the wind direction (4/8/16 cells per 220 m, so one loop
  advects exactly one period); AC units as true 3D boxes — the pixel footprint is projected onto
  the box's bottom plane and front plane and integrated analytically, so the undersides show from
  below; mullion + transom on the glass; curtains on 60% of lit windows; tone curve desaturates
  to warm white instead of clamping.
- probes: aerial haze (λ 90/150 m) lifted every wall to flat grey-brown and killed the grid → cut.
  Wall albedo/cross-light gains (4 variants) → "pale-painted concrete" albedo ×1.3, cross gain 1.0.
  Camera/tilt (4 variants): straight-up centred vs 10° vs camera 2.6 m from the west wall with
  12° tilt → the last is the composition: the near west wall rises steep up the left edge, the
  sky sits right of centre in the upper half.
- works: **it reads now** — a night light well seen from the bottom, AC boxes jutting out of the
  near walls, the window grid compressing into texture toward an amber sky.
- biggest problem: **a hard vertical light stripe up the near wall.** Traced numerically
  (pixel 66·80+10: ledge underside radiance 1.09 vs wall 0.047): the bulkhead was an isotropic
  point source flush on the wall, so it lit every ledge and AC underside straight above it.
  A real bulkhead under a door canopy throws no light upward.
- next: the lamp lights the courtyard floor — fresh snow, albedo ~0.8 — and the walls are lit by
  that bounce (a Lambertian upward source at the floor, softened over the lit patch); add the snow
  itself, the aircraft, and art-direct the dynamic windows.

### iter-4
- stats: loop 40 s / 400 steps @ 10 fps · motion median 18.6%, max 20.1% per 0.5 s · seam 0.9x p90
  CLEAN · void 60% · 106 ms setup, 20 ms/frame median (25.7 max).
- change: the lamp is now a cool-white LED bulkhead that throws no light upward; walls, ledges and
  AC undersides are lit by its bounce off the snowed courtyard floor (Lambertian upward source,
  softened over a 1.6 m patch) — cool at the bottom, warm sky + windows above. Snow: ~1500 flakes
  (2.2 cm aggregates, 4 /m³, 0.45–0.68 m/s, integer wraps per loop) simulated only within 4.75 m
  above the eye — beyond that a flake is < 0.25 px and contributes < 2 levels; each is splatted
  with energy-conserving coverage and a 1/16 s exposure streak, lit by the snow bounce + the lit
  windows below 10 m. Stairwell moved to the north wall (below the sky on screen) with
  motion-sensor lights: once a loop someone walks down from the 11th floor, each landing lights
  3 s after the one above and stays on 7 s. One kitchen light 9–25 s, one TV, an airliner crossing
  the sky 20.5–34.5 s (red beacon every 1.2 s).
- bug found: lit-window colours were random per channel (`c * rnd.uniform()` inside the tuple
  generator) — that was the salmon / red / yellow-green family from iter-2 onward. Fixed; every lit
  window is now the same amber.
- probes: bounce 12/20/35 × cross gain 1.0–3.0 × wall albedo; 5 window seeds (7 kept: lights
  cluster around the sky, few big near ones); snow density 4 vs 6 /m³ (6 = blizzard of specks).
- works: palette is finally TAC — navy-black walls, one amber family (sky, windows), one cool
  support (snow, stair lights, ground bounce). Void 60%. Thumbnails read "looking up, snowing".
  The stair descent reads as a light walking toward you down the bottom-centre column.
- biggest problem: **the sky is a flat orange panel with a razor edge.** It looks like a lit
  screen stuck on the end of the shaft — nothing connects it to the air the snow is falling
  through, and the lit windows are flat tiles with no atmosphere around them.
- next: single scattering in the snowfall — (1) a sky-lit veil along each ray (closed-form solid
  angle of the 12 × 18 m opening, integrated through a 1-D antiderivative table so it costs one
  lookup per pixel), brightening the top of the shaft into the sky; (2) halos around each lit
  window and around the opening (in-scatter of their light by the flakes in front of them).

### iter-5
- stats: motion median 14.8% · seam 1.0x p90 CLEAN · void 14% · ~115 ms setup, ~19 ms/frame.
- change: single scattering in the snowfall. (1) Sky-lit veil: in-scatter σ·L_sky·Ω(z)/4π along
  each ray, Ω(z) the closed-form solid angle of the 12 × 18 m opening seen from height z, integrated
  through a 1-D antiderivative table (one lookup per pixel). (2) Halos: each lit window's coverage
  and centroid are accumulated during the footprint pass, then a forward-scatter kernel
  exp(−r/1.7)/(1+r) is laid around it, measured from the window's equivalent-disc edge so big near
  windows don't bloom into blobs, scaled up with distance (more snow in between). Dynamic windows
  get the same halo as per-frame lists. (3) The opening gets the same halo from its screen
  rectangle. Clouds re-done at 2/4 cells, stretched across the wind, with a floor so there are
  no hard gaps.
- probes: halo 0.05 × coverage × distance/10 (first try: near windows exploded into 8-px white
  blobs) → edge-distance kernel 0.18/0.30/0.45; veil 0.01/0.02/0.035; sky halo 0.22/0.35; cloud
  floor 0.45/0.25; snow 4 vs 2.5 /m³; crop probes at full res of wall albedo ×1.0/1.4/1.8.
- works: at phone scale it is the piece — an amber opening glowing into the snow at the top of a
  black shaft, windows like embers in the air, cold flakes, the stair lights walking down.
- biggest problem: **the sky is an overexposed flat orange lightbox** (radiance ~1.6 after
  exposure → no cloud variation survives the shoulder). And the airliner is invisible against it
  — and physically it would be: a 10 km aircraft is above a 600 m snow-cloud deck. Cut it.
- also: the stair descent fills 32 s of 40, so there is no rest beat; t=0 shows only one stray
  landing light in the corner instead of the column.
- next: drop the sky radiance ~20% so the cloud field stays below the shoulder; cut the plane;
  retime the descent (2.4 s per storey, 7.6 s of dark stairwell = the rest beat) so t=0 lands
  mid-descent with three landings lit; move the kitchen light out of the rest beat.

### iter-6
- stats: loop 40 s · motion median 15.4%, max 17.0% per 0.5 s · seam 1.0x p90 CLEAN · void 27% ·
  setup 114 ms · 15.2 ms/frame median, 29.4 ms max (80×66); 23.6 / 35.1 ms at 80×81.
- change: sky radiance −20% so the cloud field stays under the tone shoulder (clouds now 4/8
  cells, floor 0.1 — soft darker drifts across the opening); sky halo masked off the sky itself;
  airliner cut (it would be above a 600 m snow deck — physically invisible); stair descent
  re-timed to 2.4 s per storey with a 6 s hold, phased so t=0 lands mid-descent (four landings lit
  in a line from the sky toward you) and the stairwell is dark 15.6–23.2 s = the rest beat;
  kitchen light moved to a visible north-wall window, on 2.5–15 s; AC undersides get ambient
  occlusion toward the wall; walls ×1.15 / cross ×1.3 (void 14% → 27%); flakes within 0.6 m of
  eye height are no longer simulated (they crossed the frame in 3 frames as 8-px streaks).
- perf: setup was 168 ms (cProfile: 313k overlap calls, the AC-box loop over far pixels whose
  footprint spans whole storeys). Boxes are skipped when the pixel footprint exceeds 0.6 m of
  wall height → 110 ms; diff vs full: 1% of rendered pixels change > 6 levels, all sub-pixel AC
  units at the rim.
- probes: sky (cloud floor 0/0.1/0.2, cells 3/6 vs 4/8, contrast window), exposure 2.2 vs 2.6,
  wall/cross gains; motion strips at 0.1 s for the snow and at 4 s for the events.
- works: thumbnail at 180 px reads as a glowing opening at the end of a shaft of windows; the
  stair column gives t=0 a leading line from the sky to the viewer.
- biggest problem: **up vs down is ambiguous in a still** — a lit rectangle at the end of a
  shaft could be a courtyard floor seen from above. In motion the snow settles it (flakes come out
  of the opening and swell toward you), and the AC undersides say "below", but the still needs
  it too.

### iter-7
- stats: motion median 13.8%, max 21.3% · seam 1.0x p90 CLEAN · void 27% · 137 ms setup,
  16.5 ms/frame median, 36.4 ms max.
- change (answering iter-6's up/down problem): the sky is now **broken stratocumulus** —
  clouds underlit amber by the city with dark gaps (gap radiance 0.03/0.032/0.055, transition
  0.40–0.64 on the value noise), three stars that only show through gaps. Drifting gaps across a
  bright deck read as sky in a still; a lit floor would not have holes moving across it.
  Snowfall now comes in bursts: each flake's wrap cycle is born or not from a hash against a
  density envelope (0.3–1.0) evaluated at its birth time at the top of the slab, so flakes never
  pop in mid-air — the thinning travels down with the fall. Lull lands at t ≈ 17–24 s, with the
  dark stairwell = the rest beat. Focal length is now FOCK·min(W, H/1.25), so landscape
  terminals get a wider lens instead of a crop (120×40: opening ~30% of height).
- probes: gap thresholds 0.40/0.56, 0.44/0.62, 0.36/0.56 + warmer gaps; sky strip every 5 s
  (clouds cross ~0.7 px/s); robustness at 120×40, 60×30, 80×24, 200×60, 100×80, 40×66 — all
  render; 200×60 costs 50 ms/frame (2.3× the pixels).
- biggest problem: **the stair descent is 3× too fast.** 2.4 s per storey is a sprint; a person
  walking down a 2.9 m storey takes ~5–6 s. It reads as an effect, not a person.

### iter-8 (final → airshaft.py)
- change: stairwell at walking pace — 5.5 s per storey, landings 5 → 2 (the four nearest, so
  each lit landing is 3–5 px instead of 1–2), each light holds 10 s after the walker passes;
  phased so t=0 shows three landings lit, the last goes dark at 21 s, the next walker trips the
  5th-floor light at 29 s. Dead code stripped (haze, airliner); airshaft.py renders
  pixel-identical to its iteration (cmp at t = 0 / 17.3 / 33.1 s).
- final stats (80×66): loop 40.00 s / 400 steps @ 10 fps · motion median 13.8%, max 21.0% per
  0.5 s · 100% of cells ever move (snow) · seam 11.5% of cells, 1.0x p90 step → CLEAN ·
  void 26% · first frame incl. setup 109 ms · 15.4 ms/frame median, 24.8 p95, 36.4 max.
  iPhone (80×81): motion 12.6% · seam 1.1x CLEAN · void 27% · 131 ms setup · 22.2 / 34.4 ms.


## v2 pass (coordinator cross-check: "doesn't read at phone size yet")
v1 backed up as airshaft-v1.py / preview-v1.gif / airshaft-iphone-v1.mp4 / reel-v1.mp4.

### iter-9
- change: (1) **restraint** — ~44 random lit windows → 6 placed ones + the kitchen event + a dim
  TV: a stack of four on the north wall's left bay (storeys 2/5/8/12, on screen a diagonal of
  shrinking ambers running up the NW corner into the sky), one on the east wall and one on the
  south wall by the rim. The rest of the facade is dark, carried by the snow bounce, the sky
  and the veil. (2) **structure** — white-painted slab edges (front face sees wall irradiance,
  underside sees the bounce), and 0.3 m light corner trim on all four vertical corners; most
  drain pipes cut (they turned the lines into a grid). Wall albedo ×0.75. (3) the stairwell is
  now warm-white (1.0/0.84/0.64) at 0.45 instead of mint at 0.85; the night-light landing is cut.
  (4) sky deck dimmed (cloud 0.30/0.185/0.10, gaps 0.012/0.014/0.03), sky halo 0.2.
- probes: line/wall contrast (wall ×0.5–0.75, ledge 1.0–1.7, trim 1.0–1.4, exposure 2.6–3.4,
  bounce 20/30); pipes all / one per wall / none.
- stats: void 26% → 60%; motion 13.8%; seam 1.0x CLEAN.
- works: the four corner edges make an X into the opening and the storey lines step down to it.
  At 180 px wide it reads as looking up a shaft (thumb-v1-v2.png, v1 left, v2 right).
- biggest problem: the cloud deck is still a hard-edged orange blob with an X-shaped rift. Also,
  the near-wall first-floor ledge (lit by the bounce patch right below it, 10× the wall) runs
  the full height of the left edge as a bright rail.

### iter-10 (→ airshaft.py)
- change: cloud transition widened to 0.28–0.80 on the noise with a 0.7 gamma (soft plateaus,
  no hard edges); deck 0.26/0.16/0.088, gaps 0.008/0.009/0.02. Ledge-underside weight
  0.45 → 0.25, so the near rail drops ~40%.
- stats (80×66): loop 40.00 s / 400 steps · motion median 13.5%, max 20.9% per 0.5 s · seam
  11.7% of cells, 1.0x p90 → CLEAN · void 62%. iPhone 80×81: motion 12.4% · seam 1.0x CLEAN ·
  void 64%. Robustness renders at 120×40, 60×30, 80×24 and 200×60 all succeed.
- build cost: the machine was at load average ~17 (parallel artists), so wall-clock timing was
  meaningless (one frame stalled 17 s). CPU time on the same loaded machine: v2 29.9 ms median vs
  v1 38.1 ms — v2 is 0.78× v1, which measured 15.4 ms unloaded → v2 ≈ 12 ms unloaded.
- self-review deltas vs v1: palette is amber + one dim cool family (snow, bounce, TV); the
  walker is a pale warm light, not a second accent. Negative space is real now (void 62%).
  First frame: the X of corners + rungs + a lit diagonal reads "up" without motion.

## self-review

1. **First frame** — t=0: an amber opening with drifting cloud and dark gaps in the upper-middle,
   a black shaft of windows converging on it, three cyan stair landings leading from the sky down
   toward you, snow at peak density. At 180 px wide it reads "looking up between buildings at
   night" (thumb-final.png). Weakness: a still can still be misread as looking *down* at a lit floor;
   the cloud gaps and the AC undersides argue against it, the snow motion settles it in < 1 s.
2. **One subject** — the opening. Read order by luminance: sky → stair column → lit windows
   (the halos make them embers in the air) → snow. The near west wall is a dark repoussoir up
   the left 20%; void 26% is under the ~30% heuristic because the walls sit at luminance 25–40
   (dark navy) just above the 24 cut — the frame is dark, but it is textured dark, not empty.
3. **Palette** — ground (8,8,14); dominant amber (sky, ~40 lit windows, veil); one cool support
   (snow-bounce on the walls, flakes, stair fluorescents, the TV). No third hue since the
   per-channel tint bug was fixed in iter-4.
4. **Motion** — slow: cloud deck crossing the opening at ~0.7 px/s (220 m per loop); medium:
   snow, 0.45–0.68 m/s with 1/16 s exposure streaks, in bursts; rare: a walker's stair lights
   (5.5 s per storey), a kitchen light 2.5–15 s, a TV breathing. Rest beat 21–29 s: stairwell
   dark, kitchen off, snow at its thinnest.
5. **Seam** — every term is periodic in 400 frames: cloud advection = exactly one noise period,
   flake speeds = integer wraps of the slab, burst hashes indexed by wrap cycle mod its count,
   stair/kitchen schedules modular. seam.png: last/first differ by one snow step and one landing.
6. **TAC-ness** — the small hours of the first snow, one person going downstairs, someone up
   for a glass of water, a TV nobody is watching. Quiet, late, held; no climax.
7. **Novelty** — the catalog's first upward view, and its first ray-cast architecture with
   analytic anti-aliasing: every pixel's footprint on the wall plane is integrated in closed form
   against the window / mullion / ledge / pipe pattern and against the bottom and front faces of
   3D AC boxes, so ~420 windows minify into texture toward the sky with no shimmer. Light is
   physical: a cool bounce off the snowed floor, cross-illumination between walls from lit
   windows (form-factor grid), and single scattering in the snowfall (sky veil from the opening's
   closed-form solid angle; forward-scatter halos around windows and the opening). Snow falls in
   true perspective with energy-conserving splats.

## renderer / pipeline notes
- preview.gif is 30.4 MB at 400 px (400 frames). `tac gif` writes every frame with disposal=2,
  so each frame is a full keyframe (~76 KB here); size ≈ frames × per-frame entropy. A snow-free
  test was 7.1 MB per 10 s — the snow is not the cause, the window texture is. Catalog previews
  top out at 8.9 MB; publishing should use the mp4/webm or a frame-diffing gif encoder.
- the session scratchpad is shared between parallel artists: another artist's `probe.py` /
  `timing.py` overwrote mine mid-session; my tools moved to `scratchpad/a5/`.
- the live viewer re-runs the script each loop, so setup (109 ms) is a once-per-40 s hitch at the
  seam; it was 168 ms before the far-pixel AC-box cutoff.

## exports
- v2 (current): preview.gif 400 px · 400 frames · 26.2 MB (v1: 30.4 MB) · reel.mp4 8.4 MB ·
  airshaft-iphone.mp4 10.7 MB (1206×2622, 1200 frames @ 30 fps, 40.0 s). Darker walls only cut
  the gif 14%: `tac gif` stores every frame as a full keyframe (disposal=2), and the snow plus the
  soft gradients still change each frame. A real cut needs a frame-diffing encoder or an mp4/webm
  preview, not fewer windows.
- v1 (below, kept as *-v1.*):
- preview.gif — 400 px, 400 frames, 30.4 MB (see pipeline note).
- reel.mp4 — 1080×1920, 1200 frames @ 30 fps, 40.0 s, 8.8 MB.
- airshaft-iphone.mp4 — 1206×2622 (80×81 cells), 1200 frames @ 30 fps, 40.0 s, 11.4 MB.
- iphone-check/ — composition holds at 81 rows: the opening sits at ~42% height, with more wall
  above and below; a lit near window is cropped at the bottom-left corner.
- the first `tac mp4` run died with exit 143: another artist ran `pkill -f studio.vscreen` at
  ~16:46 (they confirmed it). Re-ran both mp4s; ffprobe shows 1200 frames / 40.000 s each.

## catalog description
Looking straight up a tenement light well in the small hours of the first snow — the window grid
of fourteen floors converges on a patch of amber city cloud, a few lights still on, flakes
falling out of the sky toward you, and once a loop someone walks down the stairs, landing by
landing.
