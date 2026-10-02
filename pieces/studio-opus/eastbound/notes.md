# eastbound — notes

## concept

Blue hour over the prairie, facing south-west. The sun has been down a few minutes; the ground is
already in the Earth's shadow, but an airliner at cruise altitude is still in sunlight. It comes out
of the glow on the right edge as a gold glint, draws a hairline contrail diagonally up the sky, and
climbs into the shadow: the trail runs gold → orange → crimson, and the plane goes on east as a
blinking red beacon. Each loop the previous trail is still there, spread into a soft rose ribbon and
drifting downwind.

Format bet: **the sky is computed, not painted.** Spectral single scattering through a spherical
atmosphere (8 wavelength bins 420–700 nm; Rayleigh λ⁻⁴, aerosol with Cornette–Shanks phase, ozone
Chappuis absorption in a 10–40 km tent profile; Earth-shadow test on every sample; Chapman-function
optical depths for the sun ray; CIE 1931 → sRGB, white-balanced to the sun). The trail's colour at
each point is the real transmittance of the grazing sun ray reaching that point (sun disc integrated
over 7 elevation offsets, so the terminator is a physical penumbra, not a drawn line). Sky precompute
runs once (~0.13 s); per frame only the trail and plane are splatted.

## physical scale (decided first, everything sized from it)

- observer on a low rise 30 m above a flat plain → sea-level atmosphere, horizon ~20 km away.
- camera azimuth 225° (SW), horizontal FOV 40° → 0.52° per pixel column (80 px), pitched so the
  horizon sits at 89% of the frame height.
- sun azimuth 250°, depression 2.9° (civil twilight, ~12 min after sunset at 45° N in summer).
- airliner: cruise altitude 10.7 km (FL350), ground speed 245 m/s, heading 074°. Its closest pass is
  9 km horizontal, so it crosses the frame at slant distances 60 → 13 km. Wingspan 36 m = 0.03–0.16°
  = sub-pixel → drawn as a point (glint/beacon), never as a shape.
- contrail: initial width 40 m (σ), widening by wind shear at 1.2 m/s → 0.5 km after 7 min; crosswind
  10 m/s from the NNW, vortex sink 0.4 m/s; visible lifetime ~7 min. 1 px = 0.12 km at 13 km,
  0.5 km at 60 km, so the young trail is a sub-pixel line (drawn anti-aliased, min σ 0.42 px).
- **time is compressed 4.6×**: one 50 s loop = 3 min 50 s of flight (56 km). Real-time, the
  crossing would take ~3 min; distances and sizes are real, only the clock runs fast (the beacon
  keeps its real 1 Hz).

## iteration log

### iter-1
- stats: loop 40 s / 400 steps · motion median 0.4% · seam CLEAN · void 46%. Setup 0.14 s.
- first render at EXPO 9 was black: physical radiances are ~1e-4–2e-3 of the TOA sun. Probed 5 tone
  curves side by side (exposure 300–2500, toe 1–3, saturation 1–1.5): exposure 500 + per-channel toe 2.5
  + saturation 1.5 gave the blue-hour gradient (deep blue → teal → amber → red band).
- works: the physics produces the right picture unprompted — orange band, blue zenith, and the trail
  is red where sunlit and blue-grey in the shadow.
- biggest problem: the plane never leaves the frame. Its loop path ends at row 37 and it is teleported
  back to the horizon on the wrap; the trail is a short stub low in the frame for most of the loop.
- also: trail 2–3 px wide reads as a stick; real time is impossible (a 10.7 km plane needs ~3 min to
  cross a 55° frame) → time compression is required.

### iter-2
- stats: loop 50 s · motion median 0.9% · seam CLEAN · void 36%.
- change: track re-aimed (heading 063°, closest pass 8 km at az 205°) so the trail runs from the glow
  to the top-left corner; 6.9× time compression; δ 3.45° → 3.2°.
- works: t=42.8 s is the first real image: a long orange → crimson → mauve diagonal across teal-blue.
- biggest problem: the trail starts at a point in mid-air just above the glow (the start of the loop
  path) — reads as a rocket launch. And for 30 s of the loop only a short orange stub exists.

### iter-3
- change: two generations of trail (age and age + loop), older one wind-drifted and patchier.
- biggest problem: the launch point is still there, now doubled; three near-parallel lines converge
  on it — a railway, not a sky.

### iter-4
- change: vanishing point moved off-frame right (heading 074°, closest pass 9 km at az 203°), plane
  enters from the right edge and exits top-left; time compression down to 4.5×; old generation decays
  faster (exponent 2.5).
- works: the line now spans edge to corner; the ghost drifts left as a soft second stroke.
- biggest problem: the lower sky goes olive-khaki — per-channel toe pushes the yellow band green.
- probes: hue-preserving tone (5 variants) came out grey; per-channel toes R/G/B 2.2/2.9/2.4 + 10%
  red white-balance kills the olive and gives the purple-light rose transition. Sun depression × occluder
  height probe (5 variants): occluder > 1.5 km cuts a visible lobe into the sky (a shadow edge that
  reads as a cloud) → kept 1.5 km; δ 2.9° gives a gold → red trail instead of red → red.

### iter-5
- stats: loop 50 s · motion median 2.1% · p max 3.9% · seam 0.7x p90 CLEAN · void 32% ·
  build 22 ms median / 30 ms p95 / 73 ms max, 130 ms first frame incl. setup.
- change: new tone (above), δ 2.9°, horizon 89%, ground floor at #09090f, phase offset T0 = 38 s so
  t=0 shows the plane mid-climb with the full ghost; shear widening (σ linear in age, not √age).
- works: t=0 is the hero frame — gold line from the right edge to a red head, rose ghost beside it,
  deep indigo above.
- biggest problem: the fresh trail is neon (255,200,0) — saturation 1.4 applied to an already
  saturated reddened spectrum; reads as a laser.

### iter-6
- change: trail emission desaturated to 1.0 *before* compositing (first attempt toned trail pixels with
  a different saturation than the sky → any pixel touched by a faint splat changed tone → a visible
  hard-edged box around the ghost; fixed by desaturating the emission in linear space so every pixel
  goes through the same curve). Highlight rolloff probed 0/0.15/0.3 — anything above 0.03 bleaches the
  trail white and kills the warm accent. Glint gain 0.35.
- works: trail is peach-gold at the edge, crimson at the head; ghost is a rose ribbon; no artefacts
  across 6 sampled times.

### iter-7
- stats: loop 50 s · motion median 1.3% · max 3.0% · seam 0.7x p90 CLEAN · void 37% ·
  build 20 ms median / 25 ms p95 / 33 ms max.
- biggest problem going in: the horizon glow was uniform left-to-right, so nothing says where the sun
  is and the trail doesn't come *from* anywhere. Cause: the only aerosol had a 1.2 km scale height, and
  at δ 2.9° everything below ~4 km toward the sun is in the Earth's shadow — the lit aerosol has no
  density, so there is no forward-scattering aureole.
- change: second aerosol layer (free troposphere, β 0.003 /km, scale height 5.5 km, g 0.8). Probed 5
  variants (0 / 0.002 / 0.003–0.006, g 0.78–0.85, sun az 245–250): 0.006 or g 0.85 paints a fake sun
  disc; 0.003 / 5.5 km / 0.8 gives a gold aureole low right and a darker, redder left horizon.
- works: the trail now visibly comes out of the sunset; the frame has a warm corner and a cold one.

### iter-8
- change: bloom — a 3× wider, 12%-strength splat of the young trail's emission (probed 0 / 0.08 /
  0.16 / 0.3; 0.3 fattens the line). The line glows instead of looking drawn.
- perf: bloom doubled build time (44 ms median, 55 ms p95 — over budget). Fixed by precomputing the
  track grid once (positions, sample spacing, phase-function colour), inlining the projection and
  splatting bloom on every 4th sample ×4: 26 ms median, 31 ms p95, 43 ms max.

### iter-9
- biggest problem: landscape terminals. HFOV was fixed at 40°, so at 120×40 the vertical view shrank
  to 27° and the trail only clipped the top-right corner.
- change: focal length = min(40° horizontal, 50° vertical), and the camera yaws so the **right edge**
  stays at azimuth 245° (the sun and the vanishing point stay just off-frame right on every canvas).
  The vertical canvas is pixel-identical to iter-8 (cmp on t=3 s); 120×40, 80×24 and 200×60 now show a
  wide twilight panorama with the trail exiting the top.

### iter-10 (final → eastbound.py)
- stats: loop 50.00 s / 500 steps @ 10 fps · motion median 1.2%, max 3.0% per 0.5 s · 9% of cells
  ever move · seam 0.2% (0.9x p90 step) CLEAN · void 44% · build 27.6 ms median / 33 ms p95 /
  43 ms max, 169 ms first frame incl. setup (sky precompute ~0.13 s) · 12 gen-2 GCs per loop, no spikes.
- change: probed 5 final grades on the hero frame; picked sky saturation 1.25, trail 0.9, exposure 420
  (from 500): the upper sky sinks to near-black indigo, void 37% → 44%, and the trail reads warmer
  against it. Seam bug found: at 80×81 the plane was still 3 px inside the left edge at the wrap and
  vanished there (stats called it CLEAN — it's one pixel). Fixed by lengthening the per-loop path
  (time compression 4.5× → 4.6×, path shifted 1 km): the plane now starts ≥2.5 px off the right edge
  and ends off-frame on 80×66, 80×81, 120×40, 80×24 and 200×60.
- dead code stripped (old fade(), unused names); eastbound.py renders pixel-identical to iter-10.

## self-review

1. **First frame** — t=0 is the hero: a gold line comes out of the aureole at the right edge, cools
   through salmon to crimson, and ends at the plane at 40% height, 60% across, beacon lit; a rose ghost
   lies beside it, indigo above. At 180 px wide it reads in 200 ms as "jet trail at sunset" (thumb7).
2. **One subject** — the trail. Read order is fixed by luminance: aureole + trail foot → the line →
   the plane head. Upper-left quadrant and the ground (11% of frame) are empty; void 44% (53% on the
   iPhone canvas).
3. **Palette** — ground #09090f; one accent, amber→crimson (aureole, trail, beacon), all one warm
   family; support deep indigo/blue (sky). No third hue — the rose transition band is the twilight's
   own purple light.
4. **Motion** — slow: the sky is held; the aged trail widens by shear and drifts ~10 px per loop
   downwind. Medium: the plane crosses in ~25 s, accelerating as it comes closer (real perspective).
   Fast-small: the anti-collision beacon, 1 Hz. Rest beat: ~13 s per loop with no plane in frame, only
   ageing trails. Median 1.2% of cells per 0.5 s — between rooftop-skyline (0.4%) and coffee-shop (4.7%).
5. **Seam** — 0.9x p90, seam.png last|first indistinguishable; plane off-frame at both ends of the
   loop on all five canvases tested; trail ages are (s, loop phase) functions, periodic by construction.
6. **TAC-ness** — a specific time (civil twilight, ~12 min after sunset, the sun 2.9° down), a lonely
   place, and a quiet narrative without a climax: somebody up there still has the sun, and is flying
   out of it into the night.
7. **Novelty** — first computed sky in the catalog: spectral single scattering (Rayleigh, two aerosol
   layers, ozone) through a spherical atmosphere with the Earth's shadow; the contrail's colour is the
   transmittance of the grazing sun ray at each point of it, so the gold→crimson→dark gradient *is* the
   Earth's penumbra moving across the line. First real perspective flight path (3D track at 10.7 km
   projected through a pitched pinhole camera).

## renderer / pipeline notes
- preview.gif is **11.8 MB at 400 px** (500 frames). vscreen's gif uses one 255-colour global palette,
  no dither, and `disposal=2` (every frame re-encoded whole, even though only the trail moves). The
  navy→gold→red sky bands visibly in the gif and the trail shifts toward yellow; the mp4s are clean
  (reel 1.8 MB, iPhone 2.1 MB). Publishing should prefer an mp4/webm preview for this piece.
- the shared scratchpad is shared between parallel artists: a helper (`timing.py`) was overwritten by
  another agent mid-session; moved my tools to `scratchpad/a3/tools/`.

## catalog description
Blue hour over the prairie — the sky computed from sunlight scattering through the air, an airliner
still in the sun drawing a contrail out of the glow that cools from gold to crimson where it crosses
into the Earth's shadow, then flying on east into the night under a blinking red light.
