---
description: Watch a piece — the live terminal command plus a local review page
argument-hint: "<name>"
---

Run, from the current directory (Bash timeout 600000 ms: it renders a preview the first time, ~30 s):

```
"${CLAUDE_PLUGIN_ROOT}/bin/tacctl" play $ARGUMENTS
```

It prints a `tac play …` command and builds/opens `tac-work/index.html` (all your pieces, with
previews and stats). You can't run `tac play` yourself — it needs a real terminal. Show the user the
printed command verbatim so they can paste it into their own terminal (Ctrl-C quits), and give them
the review page path. If no name was given, list the directories in `./tac-work/` and ask which one.
