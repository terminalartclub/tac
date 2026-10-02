# insomnia — notes

## concept

Lying awake in a dark bedroom at 3am, looking up past the foot of the bed at the window and the
ceiling above it. A street lamp below the window throws a sheared amber copy of the window's
cross onto the ceiling, leaf shadows of a street tree shifting inside it; once in a while a car
passes in the street below and a cold white copy of the window sweeps across the whole ceiling,
stretching, carrying branch shadows, then dies as the car passes — a faint red ghost of its tail
lights trailing after.

Format bet: real projective light transport in a ray-cast room. Every pixel is a 3D point on a
wall, the window is a physical aperture (frame + cross mullions at the glass plane, wall opening
at the inner face, a 25 cm reveal between), and every light is a real 3D position. Patch shape,
its shear as the car moves, the penumbra that widens with distance from the glass
(blur = light radius × s, s = distance ratio along the ray), the inverse-square / Lambert falloff
and the branch shadows riding inside the sweep are computed, not keyframed.

## physical scale (metres, room frame: x right, y away from viewer, z up; floor z=0)

- room 3.4 wide × 3.9 deep × 2.5 high — a small old-building bedroom.
- window on the far wall: 1.2 wide × 1.35 tall, sill 0.85, head 2.2; frame 7 cm, cross mullion
  5 cm, transom at 64 % height; wall reveal 25 cm.
- viewer: eye 0.78 above the floor (head on a pillow on a mattress), 0.5 from the back wall,
  1.9 from the left wall; looking straight ahead, pitched 36° up; 58° horizontal FOV
  (80 px across → ~24 px per metre at the far wall).
- building: the bedroom floor is 7.5 m above the street (third floor of an old building:
  raised ground floor + two 3 m storeys).
- street lamp: 4.5 m post, 6 m out from the glass, 2.5 m to the right of the window centre;
  head radius 10 cm. Sodium amber.
- street tree: crown on a plane 1.5 m outside the glass, trunk 1.8 m right of the window centre.
- car: headlights 0.65 m above the road, 1.4 m apart, 1.9 m ahead of the car centre; tail lights
  0.8 m, 2.1 m behind. Lane 15 m out (far side of a wide street), ~3.5–4.5 m/s.

## iteration log

### iter-1
- stats: loop 40 s / 400 steps · motion median 0.1% · seam CLEAN · void 81% · 5 ms/frame.
- the engine works on the first try: a ray-cast box room, a window with a tree outside, a lamp
  patch and a car sweep computed through the aperture.
- biggest problem: **the geometry, not the render.** Window on the far wall right of centre, lamp
  9 m to the side and 1.6 m below the floor → its rays graze the ceiling and the "window" becomes
  a 3 m amber streak along the wall–ceiling corner, unreadable as a window. The car patch is a
  white sliver on the right wall. Bounce term 20× too strong (whole room goes beige when the car
  passes).
- next: parametrize floor height, lamp position, camera; probe side by side.

### iter-2 (probes: probes/p1–p5)
- p1 (floor height / lamp distance / pitch, 4 variants): steeper rays make compact patches; the
  window cross only reads when the lamp rays hit the ceiling at ≥30°.
- p3 (camera, 5 variants): HFOV 72° makes everything small and central; 52–58° fills the frame.
- p4 (lamp placement, 5 variants): lamp **to the right** (+2.5 m, 6 m out) gives a crisp,
  strongly sheared amber window leaning left — the best static image so far — and keeps the
  ceiling right of it dark for the sweep to cross.
- p5 (sweep timeline): the sweep enters as a white zigzag on the right wall, slides left, widens,
  and dies at the centre; the red tail-light ghost shows faintly on the left after.
- biggest problem: **the sweep is brightest where it is least readable** (a thin zigzag at the
  right wall corner, forced by the narrow forward beam lobe) and dies just as it becomes a
  recognisable window shape in mid-ceiling.
- next (iter-3): third-floor geometry + lamp right, window reveal, a real tree, two cars in
  opposite directions, colour headlights cold white against the amber lamp.

### iter-3
- stats: loop 40 s / 400 steps · motion median 1.0%, max 38.7% per 0.5 s · 57% of cells ever move ·
  seam 15.9% (1.7x p90 step) CHECK · void 84% · build 7.9 ms median / 22 ms max, 97 ms first
  frame incl. setup.
- change: third-floor geometry (floor 7.5 m above the street), lamp 2.5 m right / 6 m out / 4.5 m
  post, 25 cm window reveal (jambs, sill, head soffit ray-cast and lit through the glass plane),
  inner wall opening as a second aperture, a real tree texture (trunk + 4 branches + leaf
  clusters on a plane 1.5 m outside), two cars: A in the far lane (15 m) going right at t≈2 s,
  B in the near lane (12.5 m) going left at t≈24 s; headlight brightness driven by an envelope
  over the car's along-road position (rise from −18 m, peak −8…−1 m, gone by +3 m) instead of
  the narrow forward lobe; tail lights take over after the pass. Cold-white LED headlights against
  sodium amber.
- probes: p6 (car lane 11/15/20 m × pitch) — 15 m stretches the sweep from the window all the way
  across the ceiling over the viewer's head; 20 m overfills the frame. tree3/3b/3c (texture with
  the regions each light samples drawn in) — first tree was a solid leaf mass in front of both the
  lamp and the window; redrawn as a late-autumn crown: 4 branches, sparse clusters, one twig placed
  inside the lamp's ray bundle.
- works: t=0 is the image — a long cold-white window, cross mullions and branch shadows inside it,
  leaning out of the dark next to the static amber cross; where they overlap near the window the
  two lights add to pale pink. The window below anchors both. Car B's red tail-light ghost crossing
  the ceiling after its white sweep dies (t=25–28 s) is a quiet, real detail.
- biggest problem: **the rest state is dead.** For ~25 of 40 s the only motion is leaf sway of
  ~2 px inside the amber patch (motion median 1.0%); the leaf shadows are soft blots, not leaves.
- also: the sweep reads as a flat grey slab at full res (stair-stepped edges, no glow); the red ghost
  is as strong as the amber — a third hue; panes are near-black with brown flecks; far wall is lit
  muddy brown by the lamp bounce; jamb pixels stair-step.
- seam CHECK is the sweep straddling the cut: the last→first step is the same car moving one frame
  (verified below), not a jump.
- next: gusty wind (sway envelope, ~2× amplitude), sharp leaf shadows in the lamp patch, red ghost
  to ~35 %, sky glow + lamp-lit leaves in the panes, less lamp bounce, a soft bloom on the sweep.

### iter-4
- stats: loop 40 s · motion median 3.1%, max 43.3% per 0.5 s · 62% of cells ever move ·
  seam 17.5% (1.7x p90) CHECK — seamcheck.py on iter-3 showed the cut sits on a monotonic ramp
  (…15.3% → **15.9%** → 16.3%…, the car moving one frame), so it is motion, not a jump ·
  void 82% · build 12.4 ms median / 30.8 ms max.
- change: rewrite on a sample list — pixels whose 2×2 sub-rays hit different faces (room corners,
  window reveal edges) get 4 weighted samples, everything else 1, so edges are anti-aliased by real
  coverage; leaf shadows in the lamp patch now sample the sharp texture (lamp radius 10 cm → ~1 texel
  penumbra on the tree plane, so sharp is the physical answer); wind = two gusts per loop
  (t=12 s strong, t=31.5 s weak) driving sway amplitude 0.55→1.45 plus a high-frequency leaf
  flutter only during gusts; tail-light ghost 30→11; sky glow + lamp-lit leaves in the panes;
  lamp bounce halved; bloom: box-blurred (7×7) copy of each light's contribution added back
  (35 % for headlights per frame, 30 % for the static lamp patch, precomputed).
- probe p9 (camera x 1.9/1.4/1.0, lamp mirrored left): camera at 1.4 m puts the window at the
  centre and the whole amber parallelogram (corner included) in frame; with the sweep they form a
  V of light springing from the window. Mirrored lamp makes both lights lean the same way — worse.
- works: the leaf shadows now read as leaves and visibly drift; t=12 s gust moves them ~4 px; the
  red ghost is a whisper; the sweep glows instead of sitting on the ceiling like a slab.
- biggest problem: **the window is dead.** It is the source of every light in the room, yet at full
  res its panes are navy with brown doodles — no lit leaves, no response when a car passes.
- next: camera x 1.4; leaves outside lit properly by the lamp from below (≈3×) and flaring cold
  white from the headlights in sync with the sweep (same car, same envelope).

### iter-5
- stats: loop 40 s · motion median 4.4%, max 52.6% per 0.5 s · 64% of cells ever move ·
  seam 11.7% (1.1x p90) CLEAN · void 78% · build 13.0 ms median / 31.9 ms max, 113 ms first frame.
  iPhone canvas (80×81): composition holds — the extra rows add dark ceiling above and dark wall
  below, the V of light stays centred (iter-5-iphone/sheet.png).
- change: camera x 1.9 → 1.4 (window centred, whole amber parallelogram in frame); leaves outside
  lit by the lamp from below (probe p10: 12/22/40 → 22) and by every live headlight with the same
  1/r² — they flare cold white in the panes exactly while the sweep crosses the ceiling.
- probe p11 (car B lane 12.5 vs 15 m): 15 m makes B a mirror of A; 12.5 m gives a shorter patch
  that never reaches the top of the frame — kept for the variation in scale (near lane / far lane).
- works: the window is alive now — amber leaves against a navy-to-mauve city sky echo the amber
  cross above it, and flash white with the car. Rest frames are a complete small picture:
  window → its amber ghost on the ceiling → dark.
- biggest problem: **preview.gif is 12.4 MB at 400 px.** Bisected with probe variants:
  no bounce 12.5 MB, no bounce + no bloom 12.3 MB, no cars 12.2 MB, no cars + no sway 0.0 MB.
  The cost is the leaf sway: it changes the lamp patch and the panes every frame for all 40 s, so
  every GIF frame carries a ~50 %-of-frame delta box. (Also a DNA point: there is never a moment
  of true stillness.)
- next: wind only in gusts — sway amplitude has compact support (cos² bumps), the tree sits
  exactly at rest between gusts, so calm frames are identical and GIF merges them; that gives the
  piece real rest beats.

### iter-6
- stats: loop 40 s · motion median 3.9%, max 49.5% per 0.5 s · 63% of cells ever move ·
  seam 12.8% (1.3x p90) CLEAN · void 78% · preview gif at 400 px **8.2 MB** (was 12.4).
- change: wind only in one gust (t=8–17 s, cos² envelope with compact support): the crown leans,
  sways and flutters, then sits exactly still. Car envelopes cut to a hard zero (headlights from
  −13 m, tails gone by +8 m) so between events the frame is bit-identical and GIF merges it.
  Probe p12/p13 (consecutive frames mid-sweep): at 4.2 m/s the patch edge jumped 3–4 px per frame
  at the top of the frame — trackable, not relaxing; cars slowed to 3.0 / 2.8 m/s (a car crawling
  down a residential street at 3am), passes re-timed so t=0 is the V.
- timeline: 0–2 s car A's white window peaks as a V with the amber one, dies, red ghost ·
  3–8 s still · 8–17 s gust (leaf shadows lean and flutter in the amber patch, leaves in the panes) ·
  17–21 s still · 21–29 s car B (near lane, shorter patch, enters over the amber, crosses right,
  red ghost) · 29–36 s still · 36–40 s car A approaching.
- biggest problem: **the room is abstract** — two dark planes, a window and two light shapes. Nothing
  says bedroom or gives the light something in the room to touch, so the 3D (the reason the piece
  exists) is only implied by the shear of the patches.
- next: a paper globe pendant hanging from the ceiling between the window and the viewer — lit by
  the lamp and by the sweep (translucent paper), casting its own soft shadow into the patches.
  Big enough (36 cm) to read at true scale; cut it if it clutters.

### iter-7
- stats: loop 40 s · motion median 3.6%, max 49.8% per 0.5 s · 63% of cells ever move ·
  seam 12.8% (1.3x p90) CLEAN (seam.png: the sweep advanced one frame) · void 78% ·
  build 5.6 ms median / 18 ms p95 / 27 ms max, 107 ms first frame incl. setup.
- the pendant globe (probes p14, p15): ray-cast sphere with translucent paper shading, soft
  sphere shadows for the lamp (static) and both headlights (per frame, closest-approach distance
  vs radius ± penumbra), cord drawn by projected coverage. Physically it works — the globe glows
  from below as the sweep passes and its shadow stretches into a long ellipse that swings across
  the ceiling. **Cut it.** At 36 cm the shadow is a dark hole in the middle of the sweep; at 26 cm
  with a cord it reads as "a ball on a string" and, at rest, puts a dark ellipse and a blob inside
  the amber cross. Both versions turned the clean read (window → its light) into window + light +
  object. Same lesson as chlorine's loungers. The room stays empty.
- change instead: amber bounce cut to 40 % and ambient raised slightly (probe p17) so the walls are
  blue-black instead of muddy brown; gust sway 0.055 → 0.045; dead code (mirror switch) removed.
  Perf: tone curve → 4096-entry LUT, rows built as one Text with Spans instead of append(),
  bloom blur on prefix sums + row-vector running sums — median 6.8 → 5.6 ms, max 33 → 27 ms
  (profile: tone() was 5.5 ms/frame, Text.append 5.4 ms/frame).
- biggest problem: **the room box barely reads at phone scale** — the left wall and ceiling-wall
  corner are within 3–4 levels of black, so at rest the frame is two light shapes floating in a void.
- next: probe ambient/sky level (p19).

### iter-8
- probe p19 (ambient 0.016 / 0.028 / 0.028 + sky 1.5 / 0.040): ambient 0.028 with sky ×1.5 lifts
  the left wall and the blue sky-glow band along the ceiling–wall corner enough to read as a room
  while the ceiling stays near-black; 0.040 greys the whole ceiling and kills the depth.
- stats: loop 40 s · motion median 3.5%, max 49.2% per 0.5 s · 62% of cells ever move ·
  seam 12.7% (1.3x p90) CLEAN · void 75%.
- works: the box reads at rest — left wall, the ceiling–wall corner glowing blue above the window,
  the window's reveal; the ceiling stays the darkest plane, so both light shapes still own it.
  (Banding check: ×5-boosted crop of the dark ceiling shows only 1-level 8-bit steps, no sampling
  artefacts from the 16-point window area light.)
- biggest problem: **the hero frame's sweep is smudged** — the long low branch carries ~20 leaf
  clusters exactly where car A's rays cross the tree plane; projected 4 m across the ceiling at a
  grazing angle they smear into grey clouds, so the white window reads dirty instead of crossed
  by branches.
- next: thin that branch's leaves only where the car rays cross (u < 2.3 m on the tree plane),
  without disturbing the random stream (the amber patch's leaf shadows are composed and must not move).

### iter-9 (final → insomnia.py)
- change: deterministic thinning — the low branch's leaves left of u = 2.3 m are stamped 1 in 3
  (random draws still consumed, so every other branch and the amber patch's shadows are
  bit-identical). Probe p20 (1/2/3/5): 3 keeps a crisp branch line and two or three dapples inside
  the sweep; 5 starts to look bare. Also a vertical-FOV floor (≥ 80°) so landscape canvases widen
  the view instead of cropping the window.
- stats (final/): loop 40.00 s / 400 steps @ 10 fps · motion median 3.5%, max 49.3% per 0.5 s ·
  62% of cells ever move · seam 12.3% (1.3x p90) CLEAN — seamcheck.py: 11.7% → **12.3%** → 12.7%,
  the cut sits on the sweep's own ramp · void 75% · build 5.7–6.2 ms median, 19–21 ms p95,
  29–35 ms max, 113–139 ms first frame incl. setup (3 runs, machine shared with other renders).
- iPhone canvas (80×81, iphone-check/): motion median 2.8%, seam 10.1% CLEAN, void 80%; the extra
  15 rows go to dark ceiling above and dark wall below — the window + V of light stay centred.
- robustness: runs without error at 120×40, 60×30, 80×24, 200×60, 100×80, 80×66, 80×81;
  120×40 landscape shows the whole room wide with the V intact (probes/land-120x40.png).
  200×60 costs ~30 ms median (cost is linear in pixels).
- exports: preview.gif 7.9 MB at 400 px (catalog previews top out at 8.9 MB) · reel.mp4 2.9 MB ·
  insomnia-iphone.mp4 3.4 MB (1206×2622, cell 15×32, 30 fps).

## self-review

1. **First frame** — t=0 is the V: the static amber window leaning left and car A's long cold-white
   window leaning right, both springing from the real window below, overlapping into a pale-pink
   wedge at the glass. "Light from a window on a bedroom ceiling" reads in 200 ms; the double
   mullion line inside the white (one per headlight) rewards the second look.
2. **One subject** — the window and its light. Read order is fixed by luminance: white sweep →
   amber cross → window (lit leaves) → the room box. No props (globe tried and cut, iter-7).
   Ceiling above and wall below are negative space; void 75%.
3. **Palette** — ground blue-black (measured: dark ceiling (7,8,15), lower wall (10,8,10) at
   t=10 s — the DNA's #08080f); dominant accent sodium amber
   (lamp patch, lamp-lit leaves, head soffit); one support, cold LED white (sweeps, leaf flare).
   The tail-light red is a whisper — a dark wine window visible for ~3 s twice a loop — in the
   warm family, not a third accent.
4. **Motion** — three timescales: a gust once per loop (t=8–17 s: leaf shadows lean and flutter in
   the amber cross, leaves toss in the panes); two car passes in opposite directions and lanes
   (A far lane at t≈0, B near lane at t≈24 s), each a 3–4 s glide (leading edge ~2–3 px/frame
   at the top of the frame at 3 m/s) plus fades; rest beats where consecutive frames are
   cell-identical (measured: 5.6–8.3 s, 16.7–19.6 s, 28.6–37.2 s — 142 of 399 steps).
5. **Seam** — every motion is a function of frame mod 400; cars live on circular time and are
   at a hard zero envelope ±20 s from their pass; the cut lands mid-sweep by choice (t=0 hero)
   and seamcheck shows it as one more step of the same ramp.
6. **TAC-ness** — 3am, third floor, a sodium lamp and an autumn street tree, a car crawling past
   while you lie awake. Quiet, late, held. No climax: the sweep arrives and leaves the room exactly
   as it was.
7. **Novelty** — the catalog's first light-transport piece: a ray-cast room where the window is a
   physical aperture (frame + cross at the glass, wall opening at the inner face, 25 cm reveal),
   every light is a 3D position, and patch shape, shear, keystoning, penumbra growing with distance
   from the glass, double mullion shadows from two headlights, branch shadows riding inside the
   sweep and the leaves outside flaring with the same car are all computed. citadel proved real 3D
   reaches; this is real 3D where the subject is the light, and it is instantly readable.

## renderer / pipeline notes
- `tac frame --t` with a negative t never satisfies the capture condition and runs to MAX_STEPS
  (200k frames) — my mistake in a probe, but a cheap guard in vscreen would save the next artist
  a hung process.
- GIF size is dominated by how many frames differ from the previous one, not by gradients: a
  continuous 0.3 px leaf sway over 15 % of the frame cost 12 MB on its own; letting the tree come to
  a true rest between gusts (compact-support envelope) brought the preview to 7.9 MB.
- tools in this dir: probe.py (constant-override variants side by side), treeview.py (tree
  texture + the regions each light samples), seamcheck.py (per-step change across the cut),
  bench.py (per-frame build time outside the renderer).

## catalog description
Lying awake in a third-floor bedroom at 3am — a street lamp throws the window's amber cross onto
the ceiling through a swaying tree, and now and then a passing car sweeps a cold white copy of the
window across the dark, branch shadows riding inside it, a faint red ghost of its tail lights
trailing after.
