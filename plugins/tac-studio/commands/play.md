---
description: Watch a piece live in a new terminal window (no name lists your pieces)
argument-hint: "[name]"
---

Run (Bash timeout 600000 ms: the first time it also renders previews for the review page, ~30 s):

```
"${CLAUDE_PLUGIN_ROOT}/bin/tacctl" play $ARGUMENTS
```

- It opens a **new terminal window** playing the piece (iTerm2 or Terminal on macOS; the usual emulators on
  Linux). Tell the user "playing <name> in a new terminal window. Ctrl-C there stops it", plus any line it
  printed about the window size.
- If it couldn't open a window (SSH, no display, automation not allowed), it prints a `tac play …` command
  instead. Show that command verbatim for the user to paste into their own terminal. You can't run `tac
  play` yourself: it needs a real terminal.
- No name given: it lists the pieces (newest first). Ask which one, then run it again with that name.
- Don't send the user to the browser. The review page path it prints is optional; it opens the page only
  with `--page`, when the user asks for it.
