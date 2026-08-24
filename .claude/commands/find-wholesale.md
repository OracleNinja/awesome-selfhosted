---
description: Find wholesale supply — manufacturers, importers, distributors and B2B channels
---
Invoke the `dealer-arbitrage` skill, scoped to upstream supply.

1. `cd dealer-arbitrage && ./da research plan --groups supply_side trade_channels`
2. Run those searches. Target manufacturers and importers who publish dealer or
   wholesale programs, and B2B marketplaces where dealer pricing is visible.
3. For each: MOQ, dealer price band, freight terms, sample policy, and whether a
   sample's cost is credited against a first container order — ask for that credit
   explicitly, it is standard and often granted.
4. Capture `wholesale`, `company_type` and `dealer_program` evidence.
5. `./da research import findings.json`, `./da score`, `./da strategy`.

For overseas suppliers, vet hard: verified supplier status, audit reports, trade
assurance. Never send money, and never route a payment discussion around the human.
