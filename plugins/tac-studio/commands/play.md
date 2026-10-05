---
description: Watch a piece live in a pane beside Claude Code, or a new window (no name lists your pieces)
argument-hint: "[name]"
---

Run (Bash timeout 600000 ms: the first time it also renders previews for the review page, ~30 s):

```
"${CLAUDE_PLUGIN_ROOT}/bin/tacctl" play $ARGUMENTS
```

- In iTerm2 (and Ghostty 1.3+) it plays in a **pane on the right** of this session; elsewhere in a **new
  terminal window** (iTerm2 or Terminal on macOS; the usual emulators on Linux). Tell the user what it printed
  ("playing <name> in a pane on the right … Ctrl-C there stops it and closes the pane", or the window line),
  plus any line about the pane or window size.
- If the user asks for a tab or a separate window, add `--tab` or `--window` (`--window` gives the reel's full
  80×66 framing when the pane is smaller).
- If it couldn't open a window (SSH, no display, automation not allowed), it prints a `tac play …` command
  instead. Show that command verbatim for the user to paste into their own terminal. You can't run `tac
  play` yourself: it needs a real terminal.
- No name given: it lists the pieces (newest first). Ask which one, then run it again with that name.
- Don't send the user to the browser. The review page path it prints is optional; it opens the page only
  with `--page`, when the user asks for it.
