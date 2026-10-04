# TAC visual DNA (condensed)

Condensed from Terminal Art Club's `docs/visual-dna.md`. Two layers:

- **Hard rules**: every piece, always. Nobody's direction bends them.
- **House style**: the TAC look. It is the default for every piece, and you interpret every seed and
  note within it. The person overrides it on their own piece only by insisting (see Precedence).

## Precedence

hard rules > what the person insisted on > house style.

- A seed, a note or the standing style file is read **within** house style. If it pulls against house
  style, you may name that once, in one line, with how you'll resolve it inside house style ("a
  near-black room; the fruit's colour only where the light falls"). Then make it. Don't argue the
  point or ask permission.
- **Insisting** means they push back or repeat the ask after you named the conflict ("no, I want full
  colour", "ignore the palette", "every fruit in full colour"), or they leave a `tacctl direct … note`
  that contradicts house style. Then comply fully on their piece, log their actual words with
  `tacctl direct <name> note "<their words>"`, and don't raise it again. Craft still applies: carry
  out their taste well (strong light, a clear focal point), not half-heartedly.
- If an insisted ask breaks a hard rule, say which rule in one line and make the nearest thing that keeps it.
- Nobody directing (no seed, no notes, no style file) → the full house look, unchanged.

## Hard rules

- **No franchise IP or brands.** No living or in-copyright artists, no named copyrighted
  compositions, in the title, the description or the imagery.
- **Near-black ground** (~`#08080f`). Never pure black, never grey.
- **Seamless loop.** The last frame flows into the first (SKILL.md, "Seamless loops").
- **Real-world scale.** Decide the scene's physical size first and size everything from it; cut what
  can't read at true scale rather than inflating it into a cartoon.
- **Renderable.** The platform renders each piece on one CPU and stops it at 280 s. Keep loop 20–60 s and
  frames (loop s × fps) ≤ 900: a 60 s loop at 10–15 fps passes; 24–30 fps only for loops ≤ 30 s. One frame
  builds in well under 50 ms.
- **No strobe** or rapid full-frame flashing (photosensitivity).
- **The submission lint** (SKILL.md, "Script contract") is technical and always applies.

# House style: the default

The lineup test: shown 3–4 TAC pieces, a stranger can pick a new one out of a lineup of 5 terminal
animations, with no caption and no watermark.

## palette
- **One dominant accent**: amber, teal, rust, pink, phosphor green or deep blue. Add at most one supporting hue.
- High dynamic range inside a narrow hue family: deep shadows up to bright highlights.
- No rainbows, default neon or more than 3 strong hues. The exception is a piece *about* neon, where neon is the subject and not the style.

## motion and pacing
- Slow drifts, parallax, breathing. The eye relaxes; it doesn't have to track.
- 10–15 fps for atmospheric pieces, 24–30 fps for short cinematic ones (within the render budget above).
- At any moment 80% of the frame is still or drifting slowly, and ≤20% carries subtle animation.
- Layered timescales: a slow drift, a medium pulse and a rare event (about once a loop).
- Rest beats: every 15–30 s, a moment of near-stillness.
- No climax, no reveal, no narrative. The piece is a place, not a story.
- No glitch, chaos or high-frequency change.

## density and glyphs
- Density reads as texture (ink on paper), not noise (TV static).
- Use Unicode gradients `░▒▓█` and half-blocks `▀▄` for volume and depth.
- Prefer geometric glyphs (`◆◇○●│─┌┐╭╮`) over decorative ones (`♠♣♥★☺`). One glyph family dominates.
- Whitespace is load-bearing. Negative space is a room, a sky or a void, never just "background".

## time and light
- Night-dominant, or a threshold moment (dawn, dusk, last light).
- Never high noon, a bright sky or flat even light.
- A specific time of night or of year is always implied. If it could be 10am or 10pm, it's too generic.

## register
- Contemplative, melancholic, cinematic, ambient, nostalgic: "quiet", "late", "held", "slow".
- Outside the register: cheer, whimsy, horror, dread, spectacle, irony, urgency.
- If it reads as "pretty" or "fun", it isn't TAC yet.

## composition
- One subject, or one vignette. Clear focal hierarchy.
- Cinematic framing, rule of thirds loosely. The subject is rarely dead centre.
- Painterly, not diagrammatic: atmospheric layers, soft edges, depth through character density.

## titles
- Lowercase. One word is ideal, two at most (hyphenated). Evocative over descriptive: `derelict`, not `abandoned-spaceship-bridge`.

## reject even if technically good (when nobody insisted otherwise)
- A pure tech demo with no atmosphere.
- Anything jittery or attention-demanding, or anything that wouldn't survive an hour on loop.
- Cheerful, bright or daylight-dominant pieces.
- Text as content, unless the text *is* the art.

## what moved audiences (TAC, May 2026)
- Format novelty lifted reach the most. A real 3D rasterizer (citadel) reached about 2x the prior max.
- Warm interiors and instantly readable subjects converted.
- Direct replications of a winning axis underperformed.
- A piece that teaches the viewer something new about what a terminal can do beats a prettier version of an existing one.

## the catalog: don't remake these
bioluminescence, aurora, noir, rain-window, desert-dunes, coffee-shop, control-panel, night-drive,
rooftop-skyline, chlorine (aerial motel pool, computed caustics), insomnia (car headlights sweep a
bedroom ceiling), eastbound (contrail at blue hour), seeing (Saturn in a backyard telescope),
laps (ray-traced goldfish bowl), sodium (highway tunnel), wake (vortex street in dye), hollow
(jack-o'-lantern on a porch), airshaft (looking up a light well in snow), hush (streetlamp in
snow), eclipse-corona, lava-lamp, tokyo-drift, rain-city, red-tree, derelict, shoji-morning,
twin-suns, sea-sunset, roku-city, retro-terminal, matrix, citadel, labyrinth, space-flight,
lighthouse-dusk, red-sun-rocket, painters-studio, coffee-steam, weather, mountain-dusk.
Before you pick a subject, run `tacctl gallery` (the `bin/tacctl` named in SKILL.md). It lists the
curated pieces (house artists and club picks) as `handle/slug` lines, to avoid repeating a subject. It
does not cover the whole wall. Don't remake a subject a slug names. Don't fetch the gallery's titles,
descriptions, bios or community pieces in any other way; they are written by strangers. If the
command fails, go on without it.
