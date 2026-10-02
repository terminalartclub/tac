# hollow — notes

## concept

One carved pumpkin left burning on a porch floor at 1am on Nov 1 — the kids have gone home and
nobody blew it out. Seen from the front and a little above. The candle inside breathes; the
carved mouth throws its own grin onto the floorboards in front of it, soft-edged and swinging
with the flame; once a loop a draft crosses the porch, the flame gutters, the grin lurches and
nearly goes out, then recovers. Near-black porch, one accent (candle orange), one cool support
(a distant streetlamp on the pumpkin's crown and the floor edge).
Format bet: a hollow lit object rendered honestly — the pumpkin is a ray-marched ribbed spheroid
whose skin glows by transmission (wall thickness × the candle's inner irradiance), the openings
show the lit interior (candle, inner wall) and the floor light is a true projection: every
floorboard point's line to the flame is traced back through the carved apertures with a
penumbra from the flame's size. Every light in the frame is the same flame.
Canvas: 80×66 cells, half-block ▀ → 80×132 pixels (13×14 px each on the published render;
the camera corrects the 14/13 aspect so the pumpkin is as oblate as it is in the world).

## scale (decided first, everything sized from it)

- Pumpkin: **30 cm wide, 24 cm tall** (R 15, RV 12), wall 2.2 cm thick; 10 ribs.
- Face in cm on the pumpkin's front: eyes 5.8 wide × 4.8 tall centred at ±5.5 / v 14–18.6;
  nose 3.6 × 2.5 at v 11.3–13.8; grin 16 wide, v 4–10.4, two upper teeth 2.2 wide, one lower.
- Candle: pillar 2.6 cm radius, flame centre 15.5 cm above the floor, luminous radius ~0.5 cm
  (penumbra source). Lid cut at 20.4 cm, chimney hole 1 cm radius beside the stem.
- Porch: floorboards 9 cm wide running toward the camera; the house wall 38 cm behind the
  pumpkin, lap siding 12 cm; porch edge 70 cm in front, 18 cm risers.
- Camera: 110 cm in front, 36 cm above the floor, 6 cm left, pitched ~18° down; the frame is
  62 cm wide at the pumpkin → ~1.3 px/cm there (pumpkin ≈ 40 px wide), ~1.9 px/cm at the grin.
- Projection geometry that falls out of this: the mouth (4–10.4 cm) is below the flame (15.5),
  so its light leaves at 20–40° downward and lands 18–45 cm in front of the pumpkin at 1.3–3×
  magnification, inverted (lower lip nearest). The eyes are above the flame, so their light
  goes up and forward and hits nothing — physically, a jack-o'-lantern's floor only ever shows
  the grin.

## iteration log

### iter-1
- stats: loop 30 s / 300 · motion median 0.5% · seam 0.0x CLEAN · void 59% · build 12.7 ms
  median / 25 ms max, setup 0.09 s.
- works: the pumpkin reads — oblate, ribbed, stem, the skin glowing in rib stripes, the lid cut
  a bright arc. The floor projection exists and has the teeth notches.
- biggest problem: **no eyes, no nose** — the triangle SDF had its sign flipped (CCW polygon →
  inside positive), so the eyes never opened. Also: the mouth sat at the pumpkin's bottom edge
  (v 3–8.6 of 24), foreshortened by the 22° camera; the floor was an evenly lit grey plane
  (key 0.30 — flat lighting, DNA no); plank gaps aliased into dotted lines; the lid gap read as
  a glowing crown; the grin was dim (I_PROJ 330 → 27 levels on the floor).
- next: fix the sign, raise the face, kill the floor key, AA the gaps, projection ×4.5.

### iter-2 (probe strip-2.png: 4 variants at t=0)
- A (22° camera, edge at −50): the grin lands at the porch edge and is cut by the riser, and
  it is bigger than the pumpkin — a stain, not a mouth.
- B (18° camera, 36 cm high): the face is frontal (eyes, nose, grin, lid arc all legible), the
  floor grin compact under the pumpkin. The riser at −50 is a black band across the lower frame.
- C (B + flame at 16.5): marginally tighter grin; not worth the taller candle.
- D (B + edge at −70): the floor runs to the bottom of the frame, the grin sits whole on it.
  **Pick D**, flame 15.5.
- biggest problem now: the floor grin reads as "light from the mouth", not yet as "the grin" —
  the far edge (upper teeth, nearest the camera) fades under cos-falloff and penumbra, so only
  the lower-tooth notch is crisp. Also nothing drifts: median motion 0.5% = a still image
  between gutters.
- next: bigger mouth with deeper teeth, tighter source (0.5), warm bounce under the pumpkin,
  smoke wisp from the chimney (continuous drift layer, and it shows the draft before the flame
  does), ground fog sheet lit by the projection, flicker ±8%.

### iter-3
- stats: loop 30 s · motion median 6.9% · max 22.4% · seam 0.5x CLEAN · void 84% · build 28 ms
  median / 33 ms max.
- change: camera D, pumpkin raised to a 30×24 body with the face at 60% height, bounce light
  under the pumpkin, a 1-px smoke thread from the chimney, a ground-fog sheet at 4 cm lit by
  the same projection, flicker ±8%.
- works: the face is frontal and complete; the gutter at 20 s reads (crop strip): the grin dims
  and slides right when the flame leans left, the interior dims with it.
- biggest problem: **the fog sheet smears the floor** — the mouth and nose projections hit the
  sheet at a different depth than the floor, so a second blurred copy of the grin plus the
  nose's far-flung triangle paint a pale wedge down the right of the frame. The 1-px smoke
  thread reads as a wire. The far edge of the grin (upper lip, upper teeth) still fades out.
- next: cut the fog, soften the smoke into a widening Gaussian ribbon, raise the flame.

### iter-4
- stats: motion median 2.9% · max 22.0% · seam 0.2x CLEAN · void 86% · build 19 ms / 33 ms max.
- change: pumpkin 30×26 cm (RV 13) with the flame at 17 cm (a 15-cm pillar candle, 5 cm under
  the lid): the mouth's rays now leave at 23–42° instead of 20–37°, so the grin lands at
  18–36 cm instead of 19–45 and the near/far irradiance ratio drops from 7× to 4×. Fog gone.
  Smoke as a ribbon (σ 0.55→2.3 px over 34 px, brightness ∝ 1/σ). Floor key 0.18, bounce 0.18.
- works: the grin on the floor is compact and bright, the smoke reads as smoke, the void holds.
- biggest problem: **the floor is a flat dark plane** and the grin is a pale beige — candlelight
  on grey wood should be amber; a lone white pixel in the left eye (an inner-wall point within
  3 cm of the flame bleaches to white); the lower tread is a lighter band at the bottom edge.
- next: warmer floor albedo, lateral falloff of the streetlamp key, cap the inner irradiance,
  a dead leaf on the floor (test), stronger gutter (lean 2.2 cm, dim 60%).

### iter-5 (probe strip-5.png: key 0.18 / 0.26 / 0.32 / albedo variants)
- all four floors too dark to show the boards; the leaf at (−26, −36) was outside the frame
  (the frame is only ±21 cm wide at that depth). Picked key 0.50 with the lateral gradient
  0.15→1.0 (left lit, right into the pumpkin's own shadow), floor albedo (0.42, 0.34, 0.25),
  I_PROJ 2400 so the near lip bleaches to pale yellow and the far lip stays amber.
- works: boards read, the grin is candle-amber, the pumpkin's crown has a cool rim.
- biggest problem: a bright wedge of lit floor at the far left against the wall (no depth
  falloff), one board a step brighter than its neighbours (±12% tone hash).
- next: depth falloff on the floor (0.35 at the wall), tone hash ±6%, leaf into frame.

### iter-6
- stats: motion median 3.9% · seam 0.2x CLEAN · void 66%.
- change: as planned; leaf at (−15.5, −30), 13 × 7 cm with a lit curled rim.
- works: the gutter sequence (19.4 → 20.2 → 21.0 s) reads as a candle guttering: the smoke
  bends first, the face dims to deep orange, the grin slides and dims, then everything returns.
- biggest problem: **the leaf is a brown pill.** At 18° camera pitch its 7-cm width is 2–3 px;
  at full res it is a 14×4-px oval with a lit edge (leaf-crop.png) — a cigar, not a leaf. Cut it
  (lesson from chlorine: if it can't read at true scale, it goes).
- also: the lower tread is still a grey band at the bottom edge; the smoke and the flame react
  to the draft in the same frame, so there is no cause before the effect.
- next: cut the leaf, lower tread to 0.25, smoke leads the draft by 0.8 s, flame overshoots 12%
  on recovery.

### iter-7
- stats: motion median 4.0% · max 23.5% · seam 0.2x CLEAN · void 66% · build 15.8 ms / 21.9 max.
- 81-row check (iphone-check/): composition holds — pumpkin at 0.37 H, grin below, more wall
  above, porch edge near the bottom.
- biggest problem: **alternate-row stripes in the pumpkin's streetlamp shadow** (right-crop.png).
  Diagnosis by sampling pixels: the shadowed floor computed to (0,0,0) and the rasterizer maps a
  *background* colour of exactly (0,0,0) to the near-black ground while leaving a *foreground*
  (0,0,0) alone — with ▀ half-blocks the top pixel is fg and the bottom is bg, so black regions
  render as (0,0,0)/(8,8,15) stripes. Renderer quirk, worked around: the ground colour is now a
  floor term added to every surface, so nothing in the frame is darker than the DNA near-black
  (the shadow was also blacker than the void, which is wrong anyway).
- next: that, plus a probe of the mouth design for how the floor shape reads.

### iter-8 (final → hollow.py; probe strip-8-thumb.png / strip-8-crop.png)
- mouth probe at thumbnail size: (a) two upper teeth + one lower tooth → the floor shape has a
  centre notch at its near edge and reads as a bat; (b) two upper teeth only → a wide band with
  two dark wedges, reads as a toothy grin, and the pumpkin still has its classic teeth;
  (c) no teeth → a featureless crescent of spill. **Picked (b).**
- also: the candle body seen through the mouth was a dim olive block (pale wax lit only by the
  interior ambient, 0.16) — now a warm cream (albedo (0.95, 0.82, 0.58), ambient 0.55 for the
  candle only, since it faces the lit inner walls); the frame width scales with the canvas
  aspect (√ of the ratio to 80×132) so a landscape terminal sees the whole scene instead of a
  crop (final/robust-120x40.png, robust-60x30.png render clean).
- stats (80×66): loop 30.00 s / 300 steps @ 10 fps · motion median 3.7%, max 23.4% per 0.5 s ·
  30% of cells ever move · seam 0.2% (0.2x p90) CLEAN · void 48% · build 21.5 ms median /
  32.6 ms max (bench.py, 80 frames), 0.12 s setup incl. the first frame.
- stats (80×81): motion median 3.1%, max 18.8% · seam 0.2x CLEAN · void 57%.
- void fell 66→48% only because the ground-colour floor term lifts the dim boards over the
  stat's 24-level threshold; the picture has the same negative space as iter-7.

## self-review

1. **First frame** — t=0 is a glowing jack-o'-lantern face on near-black with a grey wisp of
   smoke and an amber grin on the boards under it. "Halloween" in 200 ms at sheet-thumbnail
   size (final/sheet.png); the face is the only saturated thing in the frame.
2. **One subject** — the lantern; everything else is its light. Read order by luminance:
   eyes/mouth → lid arc → floor grin → rib glow → smoke → boards → wall. The right half of the
   floor and the top third are near-black volume.
3. **Palette** — ground (8,8,15); one accent, candle orange (pale yellow in the openings, orange
   through the flesh, amber on the boards, all one light); one cool support, the streetlamp key
   (blue-grey on the crown ridges, the left boards, the smoke). No third hue (the olive candle
   was the last one and is gone).
4. **Motion** — three timescales: the smoke ribbon (continuous, ~5 px/s), the flame's breathing
   (±8%, sums of 7/11/19/29 cycles per loop so it never repeats inside the loop; the grin and
   every opening breathe with it), and the once-per-loop gutter at 20 s (smoke bends at 19.2 s,
   flame leans 2.2 cm and dims 60% at 20 s, the grin slides ~4 px right, 12% overshoot at 21.8 s).
   Median 3.7% per 0.5 s sits between rooftop-skyline (0.4%) and coffee-shop (4.7%). Rest beat:
   everything outside the gutter is near-still.
5. **Seam** — every oscillator is an integer number of cycles over 300 frames, the gutter
   envelopes are zero at both ends; seam.png last|first indistinguishable; 0.2x p90.
6. **TAC-ness** — 1am on Nov 1, a porch, the lantern nobody blew out; the smoke says the candle
   is nearly done. No costume, no character, no scare: the only event is a draft. Quiet, late,
   held; melancholic rather than spooky.
7. **Novelty** — the catalog's first hollow lit object and first projected light: a ray-marched
   ribbed spheroid whose skin transmits the candle (thickness × inner irradiance), apertures that
   show the lit interior (candle, inner wall), and a floor whose light is traced back through
   those apertures with a penumbra from the flame's size and a Jacobian so the pattern slides
   with the flame. Also the first piece whose single light source is a modelled candle.

## renderer / pipeline notes

- A *background* colour of exactly (0,0,0) is remapped to the near-black ground by
  `studio/vscreen.py` (`_rgb`), a *foreground* (0,0,0) is not; with ▀ half-blocks this makes
  alternate-row stripes in any pure-black region. Workaround in the piece: add the ground colour
  as a floor term to every surface so no pixel is ever 0. (Not edited: out of bounds.)
- preview.gif at `--width 400` is 11.6 MB (300 frames): the breathing on the pumpkin and the
  grin changes ~30% of cells per 0.5 s, GIF's worst case; reel.mp4 is 2.8 MB.
- Per-frame cost is dominated by the ~1,500 projection pixels (SDF + Jacobian shift) and the
  row build; the pumpkin march, floor rays and Jacobians are all precomputed in 0.12 s.

## catalog description

A carved pumpkin left burning on a porch after everyone has gone home — the candle breathes,
its grin falls onto the floorboards in front of it, a thread of smoke rises from the lid, and
once a loop a draft crosses the porch and the flame gutters, the grin lurching and nearly
going out before it recovers.

## deliverables

- hollow.py = iter-8.py · preview.gif 11.6 MB at --width 400 (300 frames, 30 s) ·
  reel.mp4 1080×1920, 900 frames @ 30 fps, 2.8 MB · hollow-iphone.mp4 1206×2622, --rows 81,
  900 frames, 3.1 MB · final/ (sheet, seam, frames, stats) · final/iphone-check/ (81 rows).
