---
description: Watch this week's wall, piece after piece, in a pane beside Claude Code (or a window)
argument-hint: "[--picks] [--seconds N] [--tab|--window]"
---

Run (Bash timeout 120000 ms):

```
"${CLAUDE_PLUGIN_ROOT}/bin/tacctl" wall $ARGUMENTS
```

- It plays this week's published pieces (no pieces this week: the picks; `--picks` asks for them), each for
  `--seconds N` (default 30; 0 = one loop each), with a one-line credit at the bottom, then the next, looping.
  Nobody's code runs on this machine: the platform renders every piece in its sandbox and sends the frames as
  data, fetched one piece at a time as it comes up.
- In iTerm2 (and Ghostty 1.3+) it plays in a **pane on the right** of this session; `--tab` or `--window` if
  the user asks; other terminals get a window. Tell the user what it printed (what's playing, how many pieces,
  where), and that Ctrl-C there stops it.
- Offline it plays what's cached and says so; with nothing cached it says to try again online. Say that line.
- If it couldn't open a window (SSH, no display), it prints a command to paste: show it verbatim. You can't
  play the wall yourself: it needs a real terminal.
