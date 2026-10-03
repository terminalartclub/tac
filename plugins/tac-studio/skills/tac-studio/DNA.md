# TAC visual DNA (condensed)

Condensed from Terminal Art Club's `docs/visual-dna.md`. Every piece is checked against it.
The test: shown 3–4 TAC pieces, a stranger can pick a new one out of a lineup of 5 terminal
animations, with no caption and no watermark.

## palette
- Near-black ground (~`#08080f`). Never pure black, never grey.
- **One dominant accent**: amber, teal, rust, pink, phosphor green or deep blue. Add at most one supporting hue.
- High dynamic range inside a narrow hue family: deep shadows up to bright highlights.
- No rainbows, default neon or more than 3 strong hues. The exception is a piece *about* neon, where neon is the subject and not the style.

## motion and pacing
- Slow drifts, parallax, breathing. The eye relaxes; it doesn't have to track.
- Loop 20–60 s and seamless. 10–15 fps for atmospheric pieces, 24–30 fps for cinematic ones.
- At any moment 80% of the frame is still or drifting slowly, and ≤20% carries subtle animation.
- Layered timescales: a slow drift, a medium pulse and a rare event (about once a loop).
- Rest beats: every 15–30 s, a moment of near-stillness.
- No climax, no reveal, no narrative. The piece is a place, not a story.
- Never strobe, glitch, chaos or high-frequency change.

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
- No franchise IP, no living or in-copyright artists, no named copyrighted compositions, in the title, the description or the imagery.

## reject even if technically good
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
The community gallery also has its own pieces. Before you pick a subject, run
`tacctl gallery` (the `bin/tacctl` named in SKILL.md): it prints one `handle/slug` per community piece and
nothing else. Don't remake a subject a slug names. Don't fetch the gallery's titles, descriptions or
bios in any other way; they are written by strangers. If the command fails, go on without it.
