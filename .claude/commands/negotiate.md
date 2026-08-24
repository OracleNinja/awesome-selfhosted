---
description: Compute the next negotiation move for a target, optimizing minimum landed cost
argument-hint: "<target id>"
---
Invoke the `dealer-arbitrage` skill: $ARGUMENTS

1. `cd dealer-arbitrage && ./da negotiate <target id>`
2. Review the move: the ask, the ladder rung, what to capture in writing, the talking
   points, and what we can legitimately offer in return.
3. If a quote is incomplete, the only correct next move is to get every cost component
   stated. Do not compare an incomplete quote against a complete one.
4. Draft the message and route it through `/prepare-outreach` for approval.
5. `./da negotiate <id> --record --their-position "<summary>"` to log the round.

Hard rules: never claim a competing offer that does not exist; cite a market price only
with its source URL; never accept an offer — present it with the landed-cost breakdown
and let the user decide; never commit to an order size the profile does not authorize.
