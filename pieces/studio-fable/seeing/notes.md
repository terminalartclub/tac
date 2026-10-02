# seeing — notes

## concept

Saturn in a backyard telescope at 2am: the central 56 arcseconds of the eyepiece. The planet is
one ray-cast 3D object (oblate globe + rings with real radii and optical depths, the rings'
shadow on the globe, the globe's shadow on the far ring, moons at their real distances), and
nothing in the scene moves in 30 s — the animation is the air. A phase screen bends the image
into a slow swim, the blur breathes with it, and once a loop the atmosphere steadies and the
Cassini division snaps in. Title = the astronomers' word for how steady the air is.
Canvas 80×66 cells, half-block ▀ → 80×132 square pixels, one glyph.

## scale (decided first, everything sized from it)

- Frame width = **56 arcsec** → **1.429 px/arcsec**. Equivalent to the central 4.4°×7.4° of a
  330x eyepiece view, or a planetary camera at 0.70"/px. No field stop is drawn: at any real
  magnification the eyepiece circle is 10–20x wider than the planet, so a rim would be a lie.
- Saturn at **9.0 AU** (1" = 6527 km): equatorial radius 60,268 km → **RE = 13.2 px**
  (globe 26.4 px wide), polar radius 54,364 km → RP = 11.9 px (flattening 0.098, real).
- Rings in RE units with real optical depths: C 1.235–1.525 (τ 0.10), B 1.525–1.95 (τ 0.9–1.8),
  Cassini 1.95–2.025 (τ 0.05), A 2.025–2.27 (τ 0.5–0.7), Encke gap 2.211–2.217 (sub-pixel,
  kept for honesty). Outer A edge 136,775 km → **ring span 59.9 px** = 75% of the frame.
- Geometry: sub-Earth latitude **B = 20°** (rings reopened to this ~2030; in Oct 2026 they are
  nearly edge-on, which would not read as Saturn), sub-solar latitude B' ≈ 23.5° (the extra 3.5°
  is what makes the ring shadow visible on the globe south of the front rings), phase angle 5°
  (a couple of months from opposition: the globe's shadow peeks out on the far ring).
  View is orthographic (1.4 billion km away). Ring plane rotated 28° on the canvas, as an alt-az
  eyepiece view would be.
- Moons (semi-major axis in RE → px, V mag): Rhea 8.74 (115 px, 9.7), Tethys 4.89 (65 px, 10.2),
  Dione 6.26 (83 px, 10.4), Enceladus 3.95 (52 px, 11.7). Orbital phases chosen so all four fall
  inside the frame; brightness ratios from the magnitudes (Rhea 1 : 0.63 : 0.52 : 0.16).
  Titan (268 px out) and Mimas (mag 12.9) are outside the frame / below the eye's reach.
- Resolution: the clean render is 3×3 supersampled, so 1 px ≈ the 0.46" Airy FWHM of a 25-cm
  aperture — the clean image is the diffraction-limited view. Seeing blur σ 0.3–1.2 px
  (0.2–0.85", i.e. 0.5–2" FWHM), swim amplitude ≈ 1.6 px (1.1").
- Half-block pixels are 13×14 screen px (7.7% taller than wide); treated as square, as chlorine did.

## light model

- Globe: Minnaert k = 0.85 limb darkening, band albedo by planetocentric latitude (bright
  equatorial zone, darker belts, dusky polar cap). Ring shadow on the globe = exp(−τ(r)/sin B')
  where the sun ray from the surface point crosses the ring plane.
- Rings: single-scattering slab, I ∝ albedo · (1 − exp(−τ(1/μ+1/μ0))) · μ0/(μ+μ0) with
  μ = sin B, μ0 = sin B'. Globe shadow on the rings by intersecting the sun ray with the ellipsoid.
  Rings in front of the globe transmit exp(−τ/sin B): the globe shows through the C ring and the
  Cassini division — and is dark there because it is in the B ring's shadow.
- Seeing: displacement field = gradient of a drifting phase screen (6 plane waves, λ 12–26 px,
  integer cycles per loop), amplitude modulated by a slow "weather" envelope and a once-per-loop
  steady window centred on t = 0; blur = blend between pre-blurred clean images (σ 0, 0.5, 1.0,
  1.6). Real seeing boils at ~10 Hz; it is slowed ~20x here so it swims instead of shakes — which
  is what the eye-brain integrates it into at the eyepiece anyway.
- Mount sway ±0.7 px, one cycle per loop (periodic error).
- Scattered-light halo: the planet's luminance blurred σ 5 px × 0.14, warm-tinted, static.

## iteration log

### iter-1
- stats: loop 30 s / 300 steps · motion median 6.4% · seam 0.0x CLEAN · void 87% ·
  build 6.3 ms median / 11.8 ms max, setup ~150 ms (ray cast 54 ms, blur levels 61 ms).
- works: unmistakably Saturn at every size; the C-ring gap, the Cassini division and the ring
  shadow on the globe all come out of the geometry; motion is calm.
- biggest problem: **the blur swallows the planet** — σ up to 2.8 px turns Saturn into a cream
  blob for a third of the loop, and the swim (λ 7–15 px) reads as ring edges tearing, not air.
  Probed 5 variants (blur swing 0.5/1.0/1.6 × swim 1.1/1.8/2.5): short-λ swim at 2.5 px tears
  the thin rings; blur ≥ 1.6 kills the Cassini division.
- also: rings clip to white (255,255,251) — a Hubble photo, not an eyepiece; moons are 2×2
  blocks (σ 0.6); Dione fell outside the dynamic bbox and never drew.
- next: λ 12–26 px, swim 1.6 px, blur base 0.3 / swing 0.9; warm + dim the palette; moons
  smaller; Dione moved to φ = −105°.

### iter-2
- stats: motion median 7.8% · seam CLEAN · void 88% · build 5.9 ms median.
- change: as planned above; EXPO 0.86, ring/globe albedos warmed (less blue), halo 0.14.
- works: the swim now reads as the planet breathing through warm air (0.3 s burst: ring edges
  bulge and relax, no tearing); all five sheet frames keep the Cassini division.
- biggest problem: **it is a pretty Saturn photograph**, dead-centre horizontally, still too
  bright for an eyepiece at 2am, and the polar cap reads as a grey smudge. The register (late,
  intimate) is not in the light yet. Geometry probe (phase 0 / B'=B / B 14 / B 26) confirmed the
  dark wedge between globe and far B ring is the C ring against the sky (physically right), and
  B'>B is what shows the ring shadow — kept.
- next: exposure probe, planet off-centre (0.47 W), cap desaturated toward ochre, mount sway.

### iter-3
- stats: motion median 7.0% · max 10.6% · seam 0.0x CLEAN · void 89% · build ~6 ms.
- change: EXPO 0.68 (probed 0.86/0.75/0.65/0.55 — 0.65 looked like the eyepiece, 0.55 dull),
  CX 0.47 W, polar cap (0.72,0.66,0.54), sway ±0.7 px, moons σ 0.38.
- works: warm ivory rings, butterscotch globe, the shadowed gap — this is what Saturn looks like
  in a 10-inch at 330x; t = 0 is the sharp frame and stops a thumb.
- biggest problem: **nothing says night.** Between the sharp moment and the swim the loop is one
  texture; there is no slow layer the eye can rest on and no sign of where the viewer is.
- next: probe a thin cloud passage (global dimming + extra blur + raised halo, ~6 s) at t ≈ 19 s
  as the slow layer / the night's cue; decide by looking.

### iter-4
- stats: motion median 6.9% · max 10.2% · seam 0.0x CLEAN · void 88%.
- change: a thin cloud passage centred on t = 18.6 s (probed depth 0 / 0.4 / 0.55 / 0.7+halo 0.6
  at 16 / 18.6 / 21 s): the planet dims to 50%, goes 0.5 px softer, and its scattered-light halo
  swells (×0.35 of the planet's blurred luminance), then clears. Width K 6 (~6 s). Mount sway
  kept.
- works: at 17 s Saturn goes milky with a bloom around it — exactly what high cloud does at the
  eyepiece — and the loop now has a slow layer: steady → swim → milky → swim → steady. The night
  is in the frame without a prop.
- biggest problem: the halo (σ 5, ×0.14) is too faint to give the planet a volume in the void;
  probing σ 7 / ×0.28–0.32 shows a soft warm glow that places it in air — but my halo is computed
  on the dynamic bbox only, so a wider one would clip to a rectangle.

### iter-5
- stats: motion median 7.0% · max 10.5% · seam 0.0x CLEAN · void 87%.
- change: halo σ 7, ×0.28, computed on a bbox padded by 3σ with three running-sum box blurs
  (w = 15) instead of the Gaussian — 6 ms at setup instead of ~100 ms.
- bug found + fixed: the running-sum blur drifts to −1e-13 in some columns, `int()` truncates
  7.99999 → 7, so columns of the void went one level *below* the ground (8 → 7) — visible as a
  faint blocky mottling across the lower half of every sheet thumbnail. Clamp the halo at ≥ 0 and
  round (int(v + 0.5)) at the Style conversion; verified every void cell is ≥ (8, 8, 15).
- works: a smooth warm glow fades into the void with no edge (row profile 41 → 19 → 15 → 11 → 9 → 8).
- biggest problem: the belts on the globe are barely there in the sharp window; the reward of
  the steady moment should be detail.

### iter-6 (final → seeing.py)
- change: belts darkened a step (NEB/SEB 0.80 → 0.74), equatorial zone brightened a step.
  Side-by-side at t = 0 the difference is small but the belts now survive the σ 0.5 blur.
- robustness: renders without error at 120×40, 60×30, 80×24, 200×60, 100×80 (landscape 120×40:
  the planet scales with width and fills the frame; moons fall outside — holds).

## final stats
- 80×66: loop 30.00 s / 300 steps @ 10 fps · motion median 7.2%, max 10.5% per 0.5 s · 14% of
  cells ever move · seam 0.0% (0.0x p90) CLEAN · void 87%.
- 80×81 (iPhone): motion median 5.7%, max 8.5% · 12% ever move · seam 0.1% CLEAN · void 90%.
  Composition holds: the planet sits in the upper-middle, moons above and one below.
- build (CPU time, contention-free): setup 130–150 ms once (ray cast 67 ms, blur levels 48 ms,
  halo 6 ms), then 6 ms median / 11 ms p90 / 18 ms max per frame at 66 rows; 7 ms median at 81
  rows. The setup pause lands inside the steady window at the seam, where the image barely
  changes. Style cache capped at 6000 entries (never exceeded: ~1500 keys/frame).
- preview.gif 7.4 MB at --width 400 (400×711, 300 frames); reel.mp4 1080×1920, 900 frames @
  30 fps, 2.3 MB; seeing-iphone.mp4 1206×2622, --rows 81, 2.7 MB.

## self-review
1. **First frame** — t = 0 is the steady moment: a sharp, dim, warm Saturn with the Cassini
   division, the ring shadow and three moon-points, in a soft glow on near-black. The silhouette
   reads in 200 ms at the 400-px gif size (gif frame 0 checked).
2. **One subject** — the planet; the moons are four faint points, nothing else in the frame.
   Read order: rings → globe → shadow gap → moons → halo. 87% void; the halo makes the void air.
3. **Palette** — ground (8,8,15); one hue family, ivory/butterscotch (rings peak (216,195,163),
   globe (201,181,152)); the polar cap is a dusky ochre-grey, not a second hue. No white, no blue.
4. **Motion** — slow: mount sway 1/loop, seeing "weather" 2/loop, cloud passage once; medium:
   the swim (6 waves, periods 1.8–4.3 s) and blur breathing (3/loop); rare: the steady window.
   Rest beat = the ~5 s of calm straddling the seam. 7.2% per 0.5 s (coffee-shop 4.7%, chlorine
   18%). Nothing jumps; max 10.5%.
5. **Seam** — every wave has an integer cycle count, the envelopes are functions of cos(2πf/N),
   and the seam sits in the calm; seam.png last|first indistinguishable, 0.0% cells change.
6. **TAC-ness** — 2am at the eyepiece: the air boils, the planet is dim and swims, cloud comes
   through, and for a few seconds it holds still. Quiet, late, held; the cloud is weather, not a
   beat. Melancholy of a thing you can only see clearly for moments.
7. **Novelty** — the catalog's first planet and first ray-cast 3D body with cast shadows (oblate
   ellipsoid + ring plane, ring transparency, ring shadow on the globe, globe shadow on the far
   ring, moons at real distances and magnitudes), and the first piece whose motion is the optics
   between viewer and subject rather than the subject: a phase-screen displacement field plus
   blur, i.e. atmospheric seeing, with real angular scale throughout (56" frame, 1.43 px/").

## renderer / pipeline notes
- GIF palette quantisation turns the halo's dark gradient into faint blocky patches at 400 px;
  the mp4s are smooth. Nothing to fix in the piece.
- The timing harness I used labelled total run time as "setup" when run for 300 frames; setup
  figures above are from 3-frame runs and CPU time (the machine had load ~8 from parallel renders).

## catalog description
Saturn in a backyard telescope at 2am — a ray-cast globe and rings with their shadows on each
other, four moons, and the air between you and it: the image swims, goes milky as high cloud
passes, and once a loop the seeing steadies and the Cassini division snaps in.
