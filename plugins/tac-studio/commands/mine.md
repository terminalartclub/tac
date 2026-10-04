---
description: Your submitted TAC pieces — status and who's watching (view counts)
---

Run `"${CLAUDE_PLUGIN_ROOT}/bin/tacctl" mine` and show its output verbatim in a code block, keeping the table and
sparklines intact. Statuses: `rendering` (on our servers, ~1–2 min), `waiting for review` (a person looks at it
next), `published` (with its link), `hidden (reported)`, `rejected` (with the reasons; "not counted against
your daily limit" when our side failed, so they can simply resubmit). Each line leads with `views: <total> · <7d> last 7 days`. Totals are public on the site
(anonymous, one per IP per piece per day); the 7-day count and the sparkline are the user's own view.
If it says they're not logged in, tell them to run `/tac:login`. Unpublishing and account deletion are
web-only (terminalart.club/me, with a typed confirmation). Never try to do them from here.
