---
description: Log in to the Terminal Art Club platform (device code in your browser; once per machine)
---

This logs in to the TAC community platform only. It never reads or touches Anthropic/Claude credentials.

1. Run `"${CLAUDE_PLUGIN_ROOT}/bin/tacctl" login --start`. It prints a short code and opens the
   approval page in the browser. Immediately show the user the **code** and the **URL** exactly as
   printed, and tell them to approve it in the browser. First sign-in, or after the terms change, the page
   asks them to agree to the Terms and the Content policy; only they tick that box.
2. Then run `"${CLAUDE_PLUGIN_ROOT}/bin/tacctl" login --wait` (Bash timeout 600000 ms). It returns once
   the code is approved and saves the token to `~/.config/tac/credentials.json` (mode 600).
3. Report the handle it prints. If it fails or expires, show the error and offer to start again.

If `TAC_API` is set in the environment, that platform is used (default `http://127.0.0.1:8790`).
It must be `https://`; plain `http://` is refused except on localhost. tacctl opens the browser only
for https pages on that same host; for anything else it prints the URL and does not open it.
