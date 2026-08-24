---
description: Find golf-cart dealers, wholesalers and distributors in the home state and metro
---
Invoke the `dealer-arbitrage` skill, scoped to geography.

1. Check `config/settings.json → campaign.home_market`. If city/state/metro are unset,
   ask the user before running — location-dependent queries are skipped, not guessed.
2. `cd dealer-arbitrage && ./da research plan --groups geographic`
3. Run those searches. Local matters because it can eliminate freight entirely — note
   for each whether pickup is realistic.
4. Capture evidence, `./da research import findings.json`, `./da score`.
5. Report by distance, flagging any target where pickup would make freight $0.

For in-state *dealers*, the angle is a dealer-to-dealer transfer of aging demo or
non-current stock, and the territory distinction must be explicit in any outreach.
