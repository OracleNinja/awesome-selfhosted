---
description: Show the campaign dashboard — targets, outreach, offers, landed cost, next action
argument-hint: "[--html]"
---
Invoke the `dealer-arbitrage` skill.

1. `cd dealer-arbitrage && ./da status` (add `--html` to write a shareable dashboard
   to `var/exports/dashboard.html`).
2. Also run `./da zero` for the zero-dollar shortlist and `./da tasks` for open work.
3. Summarize for the user: where the campaign stands, the best current offer with its
   real landed cost, anything waiting on their approval, and the single highest-value
   next action.

If any offer is incomplete, say so explicitly rather than quoting its disclosed portion
as if it were the total.
