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
3. **Style.** Run `"${CLAUDE_PLUGIN_ROOT}/bin/tacctl" style --print`. If it prints anything, it's the person's
   standing taste: apply it as the skill says ("Standing style"). Absent → ignore.
4. **Seed.** Whatever remains after the flags is the human's seed. Empty → no seed: pick the concept yourself.
   Otherwise, once you've named the piece, log it verbatim with `tacctl direct <name> seed "…"` (see "Human
   steering" in the skill) and interpret it within the house style (DNA.md, "Precedence"). If it's a number, use it as `random.seed`
   when choosing among your 3 concepts.
5. Once named, run `tacctl start <name>` (add `--sketch` for a sketch) to create `WORK/<name>/` (it prints the
   absolute path; `tacctl root` prints `WORK`, usually `~/tac-work`) and record the size,
   then `tacctl style --log <name>`, which does nothing if there's no style file.
6. Interactive session: show your 3 concepts and ask once, "pick one, or say 'you choose'". Headless:
   don't ask.
7. At every iteration, show one line (sheet path + stats). Take any notes the user gives into the next
   iteration and log them. Never interrogate.
8. Work only in `WORK/<name>/` by its absolute path, and never `cd`. Write and append files with the Write and Edit tools (not `cp`,
   heredocs or `>>`), and run one command per Bash call. A full piece takes at least 3 real iterations (usually 4–8). A sketch
   takes 1–3, looking at the sheets each time.
9. Finish with `<name>.py`, plus `notes.md` (iteration log, self-review, `## catalog description`).
10. End by telling the user the name, the one-line description and the next steps:
   `/tac:play <name>` to watch it, `/tac:submit <name>` to share it.
