---
description: Research golf-cart manufacturers, distributors, dealers and wholesalers; capture evidence and import them as scored targets
argument-hint: "[optional query group: denago_core, tao_motor, geographic, supply_side, zero_dollar_signals, trade_channels]"
---
Invoke the `dealer-arbitrage` skill for discovery only. Focus: $ARGUMENTS

1. `cd dealer-arbitrage && ./da research plan` (add `--groups <name>` if a group was named).
2. Run each query with web search. For every company collect: company, website,
   location, phone, email, contact person, role, products, dealer status, wholesale
   status, evidence, source URL, estimated relevance.
3. Vet before promoting: physical address, domain age, real phone, registered entity,
   original photos, plausible prices, payment terms. Wire-only / crypto-only / deposit
   before verification → `risk_flags`, do not pursue.
4. Every status claim needs a `{claim, url, quoted_text}` evidence entry or it will be
   downgraded to unknown — which is correct, not a problem to work around.
5. `./da research import findings.json`, then `./da score` and `./da strategy`.
6. Report what was found, what scored well, and which evidence gaps are worth closing.

Research only. Do not draft or send anything.
