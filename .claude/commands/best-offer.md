---
description: Show the best current offer by true landed cost, with everything still undisclosed
---
Invoke the `dealer-arbitrage` skill.

1. `cd dealer-arbitrage && ./da best-offer`
2. Present the full landed-cost breakdown: vehicle, freight, setup/PDI, destination,
   doc fee, taxes, dealer fees, required purchases.
3. Name every undisclosed component explicitly. An undisclosed cost is not $0, and an
   incomplete quote must never be presented as the best offer without that caveat.
4. If a credit is involved, state what is owed *today* alongside the conditional net,
   plus the qualifying order size and expiry.
5. Recommend: accept, counter, or keep looking — and say what a counter would ask for.

The agent never accepts. Acceptance is the user's decision, made with the complete
number in front of them.
