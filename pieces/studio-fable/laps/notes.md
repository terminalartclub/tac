# laps — notes

## concept

A goldfish bowl on a dark table at 3am, lit by a window off to the upper left. One goldfish swims
slow laps and pauses once per loop. The bowl is a real sphere of water: every pixel is a ray
refracted (Snell), weighted (Schlick Fresnel) and attenuated through the water, with total internal
reflection at the surface, so the fish is magnified and bent as it passes the near wall, the table is
seen inverted through the bottom, and the surface mirrors the fish from below. The window light is
forward-traced through the bowl onto the table (shadow + caustic crescent). The loop is the subject:
the laps never end. Vessels lane, cold palette (window light, water) with one warm accent (the fish).

## physical scale (everything is sized from this)

- Bowl: sphere R = 11 cm (Ø 22 cm drum bowl), opening cut at z = +7 (Ø 17 cm), flat base at z = −9.5
  (Ø 11 cm) resting on the table; water to z = +4 (13.5 cm deep). Centre = world origin.
- Fish: body 5 cm + tail 2.6 cm ≈ 7.6 cm (a fancy goldfish that has outgrown its bowl); body
  2.9 cm deep, 1.9 cm wide. Orbit radius 5.5 cm at 6 cm above the base, two laps per 30 s loop
  (≈ 2.3 cm/s, a lazy cruise), one 5 s hover at the front.
- Camera: 62 cm from the bowl, 9 cm above the water line, pitched 12.7° down. Frame = 29 × 48 cm at
  the bowl (0.364 cm per half-block pixel → the bowl is 60 px wide of 80). Canvas 80×66 cells,
  ▀ half-blocks → 80×132 square pixels.
- Light: directional (window), 49° elevation, from the left-front. Water n = 1.333.

## iteration log

### iter-1
- stats: loop 30 s / 300 steps · motion median 2.2% · seam 0.9x p90 CLEAN · void 84% ·
  setup 93 ms, frame 13 ms median / 17 ms max.
- works: the optics are real and visible — at t=7.5 s the fish at the far wall is stretched by the
  lens; the water surface mirrors the fish from below (the orange streak at the waterline is a true
  TIR reflection); the caustic crescent sits lower-right of the base where a water lens throws it.
- biggest problem: **the fish is a bun.** Two ellipsoids read as a potato with a dark marble behind it
  (the tail ellipsoid is seen edge-on and gets no diffuse light). No fish silhouette = no subject.
- also: underexposed (void 84%, table and water near-black, fish dull); the opening rim is dotted
  noise (per-pixel ring test with no coverage); no glass limb, so the bowl's silhouette is missing
  above the waterline.
- next: profiled body (elliptical sections along a goldfish profile, ray-marched inside its bounding
  ellipsoid) + planar fan tail hinged at the peduncle, two-sided translucent shading; exposure ×2.8;
  screen-space coverage for rim, water line and limb.

### iter-2
- stats: loop 30 s · motion median 2.2% · seam 0.9x p90 CLEAN · void 60% · setup 80 ms, frame 8 ms
  median / 12 ms max.
- change: body = elliptical cross-sections along a goldfish profile (egg + narrow peduncle), ray-marched
  inside its bounding ellipsoid with 4 bisections; tail = planar fan hinged at the peduncle, yawing
  with the beat, two-sided translucent; eye = albedo dot; exposure ×2.8; rim / water line / limb
  drawn with screen-space coverage; table pool offset toward the window.
- works: **it reads as a goldfish bowl in the sheet thumbnails** — eye, fan tail, glass rim, water
  line, grey limb, caustic crescent. The fish is magnified at the front (t=15 s hover) and shrinks and
  bends at the far wall (t=7.5 s). The TIR mirror image of the fish at the waterline moves with it.
- biggest problem: **the pale dashed rings around the fish** — a bug, not optics: fish-shadow pixels
  store `acc` after the table term, so the per-frame re-shade adds the table twice and the shadow
  shows as a lighter halo instead of a darker one.
- also: rim and meniscus rings are dashed (closest of 96 circle samples is coarser than the ring
  width); fish is pixel-art flat — one ray per pixel, no sheen, tail darker than the body; the bowl's
  shadow is a hard black wedge (directional light, no bounce); water a touch too dark.
- next: subtract only the lit term for the shadow; parabolic refinement of the ring distance; 2×2
  sub-rays for fish-candidate pixels, culled per frame by orbit bin; Blinn-Phong sheen on the body,
  brighter translucent tail; 5-tap blur ×2 on the caustic map + table bounce light; probe exposure.

### iter-3
- stats: motion median 2.5% · seam 0.8x p90 CLEAN · void 46% · setup 265 ms, frame 12.5 ms.
- change: shadow re-shade subtracts only the lit term (stored `lit0` per table pixel); ring distance
  refined by a parabola through the 3 nearest of 96 circle samples (rings continuous); 2×2 sub-rays
  for every pixel whose water ray can ever reach the orbit (per-pixel orbit-bin mask, 24 bins);
  Blinn-Phong sheen (exp 28) on the body; tail glow; caustic map blurred 5-tap ×2, clamped at 4;
  table ambient.
- works: rings continuous; the fish has volume (sheen along the back, dark belly); shadow soft.
- biggest problem: **pale horizontal streaks around the fish** — another bug: `ring_cover` was
  called with `d` AFTER the trace had refracted it, so rim/meniscus ghosts were painted along the
  exit directions of water pixels; different per sub-ray, hence visible as streaks.
- also: window highlight ~10× too dim (I used the window's irradiance as its radiance — a disc of
  solid angle Ω with irradiance E has radiance E/Ω); the table pool too bright and wide (void 46%).
- next: keep the primary direction for edge coverage; window as a radiance disc; forked tail +
  dorsal fin; probe table albedo / pool radius / exposure.

### iter-4
- stats: motion median 2.7% · seam 0.8x CLEAN · void 46% · setup 310 ms, frame 13 ms.
- change: `d0` kept for edge coverage; WIN radiance 10; V-fork in the tail (0.55 cm deep at the
  tip), planar dorsal fin (x −1.1…0.7, 0.7 cm tall), both translucent.
- works: streaks gone; **the fork + dorsal make it a goldfish at thumbnail size.** Probes: table
  (0.20,0.18,0.17)/pool 16/EXPO 3.2 frames the bowl best; extra scatter makes the water read as water.
  Window at 0.14 rad is physically placed (on the glass just above the waterline) but reads as a
  smudge — a tighter, brighter disc should read as a glass highlight.
- next: fold the probe picks in; WIN_R 0.08; cut setup (seg_bins 48 samples → 16, single-pass
  7-tap blur, map cell 0.35).

### iter-5
- stats: motion median 2.6% · seam 0.8x CLEAN · void 55% · setup 182 ms, frame 14 ms.
- change: as planned. Probes at the hover (t=15): FISH_GAIN 1.3 + saturated top (1.0,0.30,0.02)
  reads most "goldfish"; WIN_R 0.08 / WIN 30–60 gives a crisp double highlight (sphere + waterline).
- biggest problem: **the tail beat.** By the numbers it was 2.2 Hz = 4.5 frames per cycle at 10 fps
  — a flutter, not a cruise (a cruising goldfish beats ≈ 1 Hz). Not visible in a 5-frame sheet.
- next: tail 1.1 Hz cruising / 0.37 Hz hovering, amplitude 0.42 rad; probe picks; water line subtler.

### iter-6
- stats: motion median 2.7% · seam 0.8x CLEAN · void 55% · setup 181 ms, frame 14 ms.
- change: as planned. Rendered an 8-frame consecutive strip (t=2.0–2.7 s): the fish turns away at
  the right wall, tail beat visible and continuous, no jitter; from behind it collapses to an orange
  oval for ~0.5 s (acceptable).
- biggest problem: setup 181 ms is a visible hiccup at every loop restart in the live viewer (the
  viewer re-runs the script); half of it is the 9k sub-ray traces done up front.
- next: trace sub-rays lazily on first use; tail more translucent (glow 0.30).

### iter-7
- stats: seam **1.1x** p90 (3.2%) · setup 226 ms (noisy: six artists rendering on this machine).
- change: lazy sub-rays, but the lazy path rewrote `base` to the 4-ray average on first use → the
  first frames of every pass differ from steady state on ~1% of cells. A seam artifact by
  construction — caught by the seam ratio going up, not by eye.
- next: never rewrite `base`; write a pixel only when a sub-ray hits the fish; drop the orbit-bin
  filter on sub-ray segments (the per-frame bounding test is cheaper).

### iter-8
- stats: seam 0.8x CLEAN · void 55% · setup 147–160 ms vs iter-6 199–207 ms benched back to back ·
  frame 15 ms median / 24 ms max.
- iPhone canvas (--rows 81): composition holds — the bowl keeps its 60 px, void grows above and below.
- biggest problem: **the glass limb was drawn as a full circle**, including the arc above the opening
  where there is no glass — the bowl read as a closed sphere with a ring inside it. I had been
  reading that as "the bowl" for five iterations.
- next: draw the limb only where the silhouette's tangent point has z ∈ [base, opening].

### iter-9 (final → laps.py)
- stats: loop 30.00 s / 300 steps @ 10 fps · motion median 2.7%, max 5.0% per 0.5 s · 16% of cells
  ever move · seam 2.2% (0.8x p90) CLEAN · void 57% · setup ≈150 ms, frame 15 ms median / 24 ms max.
- change: limb clipped to the glass. **It reads as an open bowl now** — rim ellipse on top, limb
  running from the rim to the base. The t=0 full-res frame: bowl, water line with the window's
  double highlight, the fish mid-stroke with its faint mirror image at the surface, the caustic
  crescent, the soft shadow.
- laps.py = iter-9 + a canvas-adaptive pixel scale (bowl ≤ 75% of width, ≤ 55% of height);
  pixel-identical to iter-9 at 80×66.

## self-review

1. **First frame** — t=0: a glass bowl of dark water on a dim table, one orange goldfish mid-stroke
   at the front-left, a white window highlight on the glass, a bright caustic crescent lower-right.
   "Goldfish bowl, night" in 200 ms; the sheet thumbnails (half-size) read it unambiguously.
2. **One subject** — the bowl, and the fish inside it. Read order by luminance: highlight → fish →
   caustic → rim → water line → table pool. Top 30% and bottom 25% are void; void 57–58%.
3. **Palette** — ground (8,8,15); dominant cool blue (window light, water volume, glass rings);
   one warm accent, the goldfish. Table is a neutral dark grey-brown under the cool light; no third
   hue. The caustic and the window highlight are the only near-white pixels.
4. **Motion** — tail beat 1.1 Hz cruising / 0.37 Hz hovering (medium); two laps per 30 s with a
   depth bob (slow); one 5 s hover at the front, mid-loop (rare, and the rest beat). Everything else
   is still, as still water at 3am is. Motion median 2.7% per 0.5 s — far below coffee-shop (4.7%).
5. **Seam** — the lap angle, the tail phase (integer beats) and the bob (3 cycles) all close on
   frame 300; the hover is centred at frame 150 so the cut lands mid-cruise. 0.8x p90; seam.png pair
   identical but for the fish's one-step advance. No per-pass state (lazy sub-ray cache never changes
   a pixel the fish does not touch).
6. **TAC-ness** — 3am, a window's cold light across a table, a fish that cannot stop. Contemplative,
   melancholic, held; no climax — the hover is a breath, not a beat. Vessels lane: one object and
   its light.
7. **Novelty** — the catalog's first ray-traced lens: every pixel is refracted into and out of a
   sphere of water (Snell + Schlick Fresnel, total internal reflection at the surface, attenuation
   and in-scatter along the water path), the fish is magnified ×1.3 at the near wall and shrunk at
   the far wall, the surface mirrors it from below, and the window light is forward-traced through
   the bowl onto the table as a shadow with a caustic crescent. Chlorine computed caustics on a
   plane; this computes a full lens.

## renderer / pipeline notes
- Setup ≈150 ms (10.5k pixel traces, 4k light rays, map blur); the live viewer re-runs the script
  each pass, so that is the restart hiccup. Sub-ray AA for the fish is traced lazily (first lap),
  and never rewrites a background pixel — a version that did rewrite showed up as seam 1.1x.
- Shared scratchpad: another artist overwrote my probe harness mid-session; tools now live in a
  private subdirectory. Not a renderer bug, but worth knowing for parallel runs.
- Timing on this machine is noisy with six artists rendering; bench comparisons were run back to
  back in the same call.

## catalog description
A goldfish bowl on a table at 3am, lit from a window — the water is a real lens, bending the fish
large as it passes the near glass and throwing a caustic crescent across the table, while the fish
swims its laps and stops, once, to hover.

## final deliverables
- final sheet (7 frames): loop 30.00 s / 300 steps @ 10 fps · motion median 2.7%, max 5.0% per 0.5 s
  · 16% of cells ever move · seam 2.2% (0.8x p90) CLEAN · void 57%.
- iPhone canvas (--rows 81): motion median 2.2% · seam 0.8x CLEAN · void 66%; composition holds.
- robustness: renders at 120×40, 60×30, 80×24, 200×60, 100×80 (bowl auto-scales to ≤ 76% of width
  and ≤ 55% of height; 80×66 is pixel-identical to iter-9).
- preview.gif: 400 px wide, 300 frames, **8.3 MB** (catalog previews top out at 8.9 MB; the fish is
  the only thing changing, so the palette-quantized frames compress well).
- reel.mp4: 1080×1920, 900 frames @ 30 fps, 1.1 MB. laps-iphone.mp4: 1206×2622, 1.3 MB.
- laps.py: 778 lines, setup ≈150 ms, 15 ms median / 24 ms max per frame at 80×66.

## v2 pass (coordinator cross-check: lower frame + bowl shadow)

v1 kept as `laps-v1.py`, `preview-v1.gif`, `reel-v1.mp4`, `laps-iphone-v1.mp4`.

### iter-10
- change: the radial table pool replaced by the **window projected onto the table**: a mask in the
  light's own table coordinates (across = perpendicular to the light's horizontal travel, along =
  window height / tan 49°), feathered by the source's angular size (3 cm across, 3/sin 49° along).
  Every forward-traced light ray is weighted by the mask at its direct landing, so the caustic and
  the bowl shadow are inside the patch by construction. The caustic map became a deviation map
  (0 outside the bowl's influence) so the patch extends past the map. **Shadow wedge, two causes:**
  the map was clipped at ±16 cm while the sphere's shadow tip reaches ~25 cm (a literal hard edge
  at x=16), and the blur was uniform. Map now ±26 cm, and the penumbra grows with distance from the
  base: three blur levels (7-tap at stride 1/2/4) blended at r = 6–14 cm and 14–26 cm.
- probe (5 window layouts): the patch reads as "window light on a table" immediately; too bright
  and too large as first set (lower frame a grey slab).

### iter-11 / iter-12
- change: horizontal mullion option, table albedo halved; probe of four layouts → cross mullion,
  32×38 cm window, feather 3 cm. Sheet: void 56%, seam 0.8x.
- biggest problem: **the caustic crescent vanished.** The window was only 38 cm tall and centred
  in front of the bowl, so rays entering the sphere's upper half (direct landing 14–25 cm behind
  the bowl) fell outside the patch → masked out → no caustic. Physically that is the window head
  shading the top of the bowl, which the bowl's own shading did not reflect.

### iter-13
- change: window 53 cm tall, centred 3 cm behind the bowl (far edge ≥ 26 cm behind the bowl centre,
  sill edge still in frame 20 cm in front); **table far edge at y = +14 cm** so the lit table ends in
  void behind the bowl (a windowsill) and the bowl keeps its dark backdrop; vertical bar moved to
  clear the base. Caustic back, inside the patch; shadow soft, fading with distance; void 64%.
- probe: vertical bar on/off × table brightness. Without the vertical bar the lower-left has three
  bands (pane, bar, pane) plus the patch's right edge; with it, four. Restraint wins.

### iter-14 (final v2 → laps.py)
- change: horizontal sash bar only; table albedo (0.115, 0.106, 0.098).
- stats: loop 30.00 s / 300 steps @ 10 fps · motion median 2.7%, max 5.0% per 0.5 s · 17% of cells
  ever move · seam 2.2% (0.8x p90) CLEAN · **void 64%** (iPhone 81 rows: 71%, seam 0.8x) · setup
  ≈170 ms, 13 ms median / 19 ms max per frame.
- fish, optics, timing, seam untouched (the window code only changes table lighting and the light
  rays' weights). Robustness: 120×40, 60×30, 200×60 render.
- scale additions: window 32 × 53 cm (sash bar 3 cm, 13 cm below the window centre), its light
  landing centred 3 cm behind the bowl; windowsill/table ends 14 cm behind the bowl centre.
- v2 exports: preview.gif 400 px, 300 frames (size in export-v2.log); reel.mp4 1080×1920 @ 30 fps;
  laps-iphone.mp4 1206×2622 @ 30 fps. v1 files kept alongside for comparison.
