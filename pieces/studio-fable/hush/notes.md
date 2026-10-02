# hush — notes

## concept

One sodium streetlamp in falling snow at 3am, seen from a dark first-floor window across an empty
lot. The flakes exist only where the light is. Lane: Weather. Format bet: every flake is a 3D point
lit by the luminaire's real distribution (inverse square × a cut-off emission curve) with a
Henyey–Greenstein forward-scattering phase function, streaked along its true velocity by the
shutter, blown and lofted by one gust per loop, and once a loop the snowfall thins to nothing from
the lamp down (a Lagrangian gap in the fall, keyed to each flake's own speed) and refills from
above. The ground is a snow plane with a value-noise surface lit by the same lamp, with the post's
penumbral shadow computed from the luminaire's size. Canvas 80×66 cells, half-block ▀ → 80×132
square pixels. Near-black ground, one accent (sodium amber), no support hue.

## scale assumption (decided before iter-4; everything is sized from it)

- Pedestrian / courtyard lamp: luminaire **3.5 m** up, cobra head **0.6 × 0.2 m** on a **0.9 m**
  arm angled 45° toward the camera, post **Ø 0.10 m**. Pool half-brightness radius ≈ 2 m, 10 %
  at the frame edge (±3.3 m) — a 5–6 m lamp lights a ±3.3 m strip almost evenly, which is why
  iter-1..3 read as a flat slab (see log).
- Camera at a window **3.5 m** up, **11 m** from the post, pitched down 8°, focal 132 px for an
  80-column frame → **12 px/m at the post**, frame 6.6 m wide × 10.9 m tall there. Post 1.2 px
  wide, head 7 × 2.4 px, arm 1 px: all drawn with coverage AA at true scale, none inflated.
- Snow: 1,600 flakes in a 10 × 6.5 × 9 m box around the lamp (≈2.7 flakes/m³ — light snow; real
  moderate snowfall is 10–100/m³, the unresolved rest is the airlight term), fall speeds
  0.45–1.25 m/s (dendrites → small aggregates), per-flake brightness ∝ size, lateral sway
  3–12 cm. Gust peak 1.3 m/s, σ 2.2 s, loft 0.35 m. Shutter 60 % of the 100 ms frame.
- Pixels are 13 × 14 screen px (7.7 % taller than wide); treated as square, under the threshold
  where it shows.

## iteration log

### iter-1
- stats: motion median 18 % · seam 0.7× p90 CLEAN · void 40 % · build 6.9 ms median.
- 6-m cobra head, eye-level camera at 10 m. The ground was a flat cream slab (exposure ×95 and a
  6-m lamp's pool is wider than the frame), the post shadow a hard black bar (umbra model, no
  penumbra), flakes rendered as hard 2-px vertical dashes (read as rain), and two white dashes at
  the left edge: `int()` truncates toward zero, so flakes at x ∈ (−1, 0) got negative bilinear
  weights. Fix all four.

### iter-2
- stats: motion 19.9 % · seam CLEAN · void 53 %.
- Ground exposure ×42, correct penumbra (max 25 % far from the post), 3×3 tent footprint, shutter
  40 %, slower flakes. It reads as a lamp in snow. Biggest problem: still a wide flat band of
  ground — physics: at 6 m the irradiance at the frame edge is 0.7× the centre. Also the glare
  square: the glare was clipped at its 29-px bounding box.

### iter-3
- stats: motion 9.8 % · void 56 %.
- Lamp 5 m, camera 2.6 m / 13 m, Lambertian-ish emission, ground gamma 1.5, forward-scatter
  mix 0.5 (the HG peak was ×19 at the head: hot blobs). Pool now an ellipse but the pool's near
  half is a bright bar across the whole width; flakes too dim and sparse — the box was 14 × 10 ×
  19 m for 3,000 flakes = 0.5/m³ with most of them far from the lamp.

### iter-4
- probe (6 variants at t=0): EXPO 1.2 / 2.0 / 3.2 × HALO 0.06 / 0.15 → EXPO 3.2, HALO 0.06.
- Lamp 3.5 m / camera 1.9 m / 11 m: solved analytically — with cosφ^1.7 · 1/r² and gamma 1.5,
  10 % brightness at d = 3.8 m needs h ≈ 3.35 m. Flake box concentrated around the lamp
  (9 × 6.5 × 9.5 m, 3,600 flakes), bloom on bright flakes. Snow finally reads as snow, but
  3× too dense (motion 30 %) and the eye-level pool is a bar (seen at 10° grazing).

### iter-5
- stats: motion 23.9 % · void 65 %.
- Camera up to a window at 3.5 m, pitched down 8° → the pool is a full ellipse with 23 rows of
  depth. Horizon line killed (far snow = sky tone) because at eye level it passed through the
  head (tangent). 2,400 flakes. Biggest problem: the post shadow was black for 1.2 m and then a
  dark line across the whole left — physics for a 0.14-m post 0.9 m from a 0.52-m source, but it
  read as a bracket.

### iter-6 (rejected direction)
- Post-top globe luminaire (no arm → no shadow line). Omnidirectional light lit the whole near
  ground and every flake above the head: a ball of specks around a dot, motion 36 %, void 46 %.
  Reads as a sparkler. Back to the cobra head.

### iter-7
- stats: motion 20.8 % · void 51 %.
- Arm rotated 45° toward the camera (the shadow runs back-left, foreshortened), post Ø 0.10 m,
  head 0.6 m, shadow darkness capped at 0.6 (snow is translucent; shadows on it are never black),
  lull keyed to the time each flake passes the head height (so the cone empties for all speeds at
  once), wider flake-size spread. The lull frame (t = 17.5 s) is the best image in the piece: the
  lamp alone, the last flakes landing. Problem: the cut-off emission lit the near ground (row 90 %
  R = 54 vs 21 in iter-5).

### iter-8
- stats: motion 17.3 % · void 59 %.
- Narrower beam (full inside 40°, half at 55°, off by 72°), halo 0.04 + glare core r 7 px, and a
  near-camera bokeh layer lit by a window light behind the viewer (thin-lens CoC discs). Flakes at
  the head's level went dark (SIDE 0.06): a horizontal ceiling to the swarm.

### iter-9
- stats: motion 20.1 % · void 57 %.
- SIDE 0.2 (the refractor leaks sideways; the ceiling is gone), ground noise ×2.4 with a 22-cm
  octave so the pool reads as snow, not fog. The bokeh discs read as grey smudges on the lens:
  cool hue, 6 px/frame jumps at 10 fps, and physically the volume in front of the post is outside
  the beam. **Cut the near layer and the depth-of-field claim** — at 11 m with an 85 mm f/1.4
  equivalent, flakes 4 m in front of the post blur 0.4 px: no DoF exists at this geometry.

### iter-10 (final → hush.py)
- Clean rewrite without the near layer; focal tied to width (1.65·W) so taller canvases add sky and
  near ground instead of cropping the pool; uplight 0.06; loft made periodic (was a 1.3 cm jump at
  the seam — 0.16 px — now zero).
- stats (80×66): loop 30.00 s / 300 steps @ 10 fps · motion median 20.1 %, max 22.9 % per 0.5 s
  · 77 % of cells ever move · seam 17.6 % (0.9× p90 step) CLEAN · void 57 % · build 6.4 ms
  median / 14.1 ms max over the full loop · first frame 113 ms incl. ~0.4 s setup.
- stats (80×81, iPhone): motion median 16.3 %, max 18.5 % · seam 14.2 % (0.9× p90) CLEAN ·
  void 65 % · build 6.5 ms median / 12.1 ms max. Composition holds: head at 39 %, pool centre
  at 65 %, more near ground fading to black at the bottom.
- renders without error at 120×40, 60×30, 200×60, 80×24, 100×80 (landscape crops top and bottom,
  keeps the lamp and the pool).
- timeline: t 0–7 calm steady snow · 7–12 gust (flakes slant and loft, streaks tilt) · 15.5–19.5
  the gap passes the head, cone empty by ~17.5 (rest beat) · 19.5–24 refill from above while the
  last flakes land · 24–30 steady, into t = 0.

## self-review

1. **First frame** — t=0: a white-hot lens on a dark L, a swarm of amber flakes below it, an
   amber ellipse of snow with a diagonal shadow, everything else near-black. "Streetlamp in
   snow" at thumbnail scale (sheet thumbs); the head is 7 × 2.4 px but the glare carries it.
2. **One subject** — the lamp and what it lights. Read order: lens → swarm → pool → shadow. The
   upper 35 % and the bottom 15 % are void; void 57 % (65 % on the iPhone canvas).
3. **Palette** — sky (8,8,14) → horizon (11,11,17); sodium amber (255,176,78) is the only hue:
   flakes bleach toward pale amber at the knee, never white; the pool's hottest point is
   (235,190,110). The luminaire's housing cap is a dim warm grey. No support hue (the cool bokeh
   layer was cut for exactly this reason).
4. **Motion** — slow: 0.09 m/s base drift + per-flake sway (8–20 cycles/loop); medium: one gust
   (t ≈ 9 s, visible as tilted streaks); rare: the lull. Rest beat = the empty cone at 17–19.5 s.
   Motion 20.1 % median per 0.5 s (chlorine 18 %, aurora 27 %); the sky, post, pool and shadow
   are static.
5. **Seam** — x-drift wraps exactly one box width per loop (base wind solved from the gust
   integral), y-wrap spans are v·T/k, sway is integer cycles, lull/loft/halo are mod T.
   seam.png last|first: same distribution, 0.9× a normal step.
6. **TAC-ness** — 3am, nobody has crossed the pool, the last sodium lamp on the lot (they are
   being replaced by LED — the amber itself is nostalgic). Quiet, late, held; the lull is a breath,
   not a beat.
7. **Novelty** — the catalog's first physically lit particle field: per-flake inverse-square ×
   luminaire distribution × HG phase, shutter streaks, a wind gust with loft, a Lagrangian
   snowfall gap that descends at each flake's own speed, a penumbral post shadow sized from the
   source, and a volumetric airlight term integrated along each pixel's ray. winter-windows paints
   snow with fixed halos on a facade; here the snow is the light.

## honest rating

7/10 against the catalog. What I'd still improve: the flakes are all 1-px squares of one
character; a real size spectrum (slow 2-px dendrites with soft bloom vs fast 1-px grains) and a
hint of the eddy behind the post would make the swarm read as snow rather than embers at full
reel resolution.

## renderer / pipeline notes
- preview.gif is 11.9 MB at `--width 400` (catalog previews top out at 8.9 MB): ~900 flake pixels
  change every frame over 40 % of the frame — GIF's worst case. reel.mp4 7.0 MB,
  hush-iphone.mp4 7.7 MB.
- No renderer bugs hit. One shell footgun: zsh does not word-split `$var` in `for sz in "120 40"`;
  size probes must use `${sz%x*}` / `${sz#*x}`.
- Timing harness (not part of the piece): scratchpad `bench.py` execs the script with stub
  `canvas`/`sleep` and times the gap between `sleep` calls.

## catalog description
A single sodium streetlamp in falling snow at 3am, seen from a dark window — every flake lit by the
lamp's real light, the air glowing where the beam is, a gust leaning the fall sideways, and once a
loop the snow thins to nothing from the lamp down and quietly returns.

## deliverables
- `hush.py` = iter-10.py (loft periodic) · `preview.gif` 11.9 MB (400 px, 300 frames) ·
  `reel.mp4` 1080×1920, 900 frames @ 30 fps, 7.0 MB · `hush-iphone.mp4` 1206×2622, --rows 81,
  900 frames, 7.7 MB · `final/` sheet + seam + frames at 80×66 · `iphone-check/` sheet at 80×81.

---

## revision 2 — "snow, not embers" (coordinator cross-check; v1 kept as hush-v1.py / preview-v1.gif / reel-v1.mp4 / hush-iphone-v1.mp4)

### what changed
1. **Size spectrum tied to distance.** Three populations, each with its own depth band so
   parallax, size and speed agree: **near** 140 flakes at z 3–5.5 m, large dendrites falling
   0.40–0.65 m/s, drawn as thin-lens-defocused Gaussian discs (σ 0.8–1.6 px, CoC from the
   85 mm f/1.4 model) and **alpha-composited** as opaque occluders (α ≤ 0.35, colour = sky-lit
   snow (90,94,110)) — so over the sky they are soft blue-grey blobs and over the pool they are
   faint dark smudges, which is what a defocused near flake does; **mid** 1,600 flakes at
   z 7–14.5 m (the lamp's band, unchanged); **far** 350 small grains at z 14.5–17.5 m falling
   0.95–1.55 m/s, 1-px, dim. On screen: near ≈ 2.5 px blobs at 1.5–2.5 px/frame, mid 1 px at
   ~1 px/frame, far 1 px at ~0.8 px/frame.
2. **Cool ambient.** Every flake also receives a uniform overcast-skyglow irradiance (AMB 0.055 ×
   size) rendered through a second, cool ramp (0.76, 0.84, 1.0) in its own front/back buffers
   (post-occluded like the amber ones). Outside the beam a flake is a dim blue-white speck
   (≈ 10–20 levels over the (8,8,14) sky); inside it the amber term is 10–50× larger, so the
   amber reads as the lamp's light on white snow.
3. **Snow caps.** The luminaire's top row is now lit snow (214,196,168) instead of dark housing,
   and the arm carries a 1-px pale line (168,156,138) at 70 % coverage. At 7 × 2.4 px and 5 × 1 px
   both read as "snow on it", not as a glitch (checked at full res and on the sheet thumbs).
- Also: compact 4-entry sub-pixel deposit lists (the 3×3 tent with half-width 1 has ≤ 4 non-zero
  weights) to pay for the second buffer set.
- Unchanged: scale, camera, gust, lull timing, halo, pool, seam construction.

### iterations in this pass
- iter-11: AMB 0.16, 1,000 far grains, plus-shaped near kernel → a blizzard of grey static
  (motion 43 %, void 48 %); near flakes read as grey crosses.
- iter-12: AMB 0.07, 450 far, Gaussian alpha near flakes → snow reads; motion 33 %, void 53 %.
- iter-13 (final → hush.py): AMB 0.055, 350 far, near α 0.35 and lighter; compact deposits.

### stats (final)
- 80×66: loop 30.00 s / 300 steps · motion median 30.0 %, max 32.3 % per 0.5 s · seam 26.1 %
  (0.9× p90 step) CLEAN · void 54 %. The motion rise vs v1 (20 %) is the ambient specks: faint,
  slow, over the whole sky — it is the subject now (snow everywhere, the lamp lights a part).
- 80×81: motion median 25.0 %, max 27.1 % · seam 0.9× p90 CLEAN · void 63 %. Composition holds.
- build: measured under a machine load of 11–15 (parallel renders): 26.6 ms median / 65 ms max;
  iter-12 under the same load 30.8 ms. v1 unloaded was 6.4 ms, so v2 unloaded is ≈ 12–15 ms.
- preview.gif 14.7 MB at --width 400 (v1 11.9 MB) · reel.mp4 9.7 MB · hush-iphone.mp4 10.9 MB.
  The ambient specks add changing pixels across the whole frame — GIF's worst case again.

### catalog description (unchanged)
