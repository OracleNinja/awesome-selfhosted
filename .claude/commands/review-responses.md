---
description: Classify inbound replies and determine the next best action for each
---
Invoke the `dealer-arbitrage` skill.

1. `cd dealer-arbitrage && ./da responses review`
2. For a new reply: `./da responses add --target-id <id> --file reply.txt`
   (classification, confidence, the cues that fired, and the next step are printed).
3. Where a reply contains a commercial offer, record it:
   `./da offer <response_id> '{"vehicle_price_cents":…,"freight_cents":…,"credit_pct":…}'`
   Use `null` for anything not stated — an undisclosed cost is never $0.
4. Draft replies where the next step calls for one, and route each through
   `/prepare-outreach` so it hits the approval gate.
5. Report each response, its classification, and what you recommend.

Never auto-accept a paid offer. Anything involving money, a commitment, or a
suspicious signal goes to the user with the landed-cost breakdown attached.
