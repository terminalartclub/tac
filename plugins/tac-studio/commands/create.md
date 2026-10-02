---
description: Make a new Terminal Art Club piece end to end (concept → iterate on the virtual screen → final)
argument-hint: "[--sketch] [--force] [seed, idea or \"theme\"]"
---

Make one new Terminal Art Club piece, end to end, by following the `tac-studio` skill in this plugin:
`${CLAUDE_PLUGIN_ROOT}/skills/tac-studio/SKILL.md`. Read it now, then read
`${CLAUDE_PLUGIN_ROOT}/skills/tac-studio/DNA.md`.

Arguments from the user (may be empty): $ARGUMENTS

1. **Size.** `--sketch` → a sketch: at most 3 iterations, with the same quality rules. Otherwise a full piece.
2. **Fit gate, before any work.** Run `"${CLAUDE_PLUGIN_ROOT}/bin/tacctl" fit` (add `--sketch` for a sketch).
   - Exit 0 → go ahead.
   - Exit 3 → it won't fit in the spare weekly window. Show the user the printed line and **don't start**.
     Offer the sketch if the line says one fits, or an override. Start anyway only if the arguments contain
     `--force` or the user explicitly says so.
3. **Seed.** Whatever remains after the flags is the human's seed. Empty → no seed: pick the concept yourself.
   Otherwise, once you've named the piece, log it verbatim with `tacctl direct <name> seed "…"` (see "Human
   steering" in the skill) and interpret it within the DNA. If it's a number, use it as `random.seed`
   when choosing among your 3 concepts.
4. Once named, run `tacctl start <name>` (add `--sketch` for a sketch) to create `./tac-work/<name>/` and record the size.
5. Interactive session: show your 3 concepts and ask once, "pick one, or say 'you choose'". Headless:
   don't ask.
6. At every iteration, show one line (sheet path + stats). Take any notes the user gives into the next
   iteration and log them. Never interrogate.
7. Work only in `./tac-work/<name>/`. A full piece takes at least 3 real iterations (usually 4–8). A sketch
   takes 1–3, looking at the sheets each time.
8. Finish with `<name>.py`, plus `notes.md` (iteration log, self-review, `## catalog description`).
9. End by telling the user the name, the one-line description and the next steps:
   `/tac:play <name>` to watch it, `/tac:submit <name>` to share it.
