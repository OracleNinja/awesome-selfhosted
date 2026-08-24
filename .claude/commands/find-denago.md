---
description: Find Denago and Tao Motor dealers, distributors, demo units and clearance inventory
---
Invoke the `dealer-arbitrage` skill, scoped to the Denago/Tao channel.

1. `cd dealer-arbitrage && ./da research plan --groups denago_core tao_motor`
2. Run those searches. Prioritize: the official dealer locator, the corporate dealer
   application page, distributors, and any dealer listing Nomad XL inventory,
   clearance, or previous-model-year units.
3. Capture `denago_relationship`, `dealer_program`, `demo_program` and
   `target_model_inventory` evidence with exact quotations.
4. `./da research import findings.json` then `./da score`.
5. Report the Denago supply chain as you found it: who manufactures, who distributes,
   who retails, and where a demo or non-current unit might come from.

Remember: another company's "Authorized Denago Dealer" badge is evidence about *them*.
It is never a claim we may make about ourselves.
