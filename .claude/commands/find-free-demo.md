---
description: Hunt specifically for $0 opportunities — demo programs, evaluation units, excess, cancelled orders, blemished stock
---
Invoke the `dealer-arbitrage` skill, scoped to the zero-dollar objective.

1. `cd dealer-arbitrage && ./da research plan --groups zero_dollar_signals`
2. Run those searches. You are looking for published evidence of: demo/evaluation unit
   programs, dealer-development support, floor-model programs, overstock and clearance,
   cancelled orders, freight-damaged or cosmetically blemished units, and
   credited-sample arrangements.
3. Capture `demo_program`, `excess_inventory`, `previous_model_year` evidence — these
   are exactly the signals that put a target on the zero-dollar shortlist.
4. `./da research import findings.json`, `./da score`, then `./da zero`.
5. Report the shortlist ranked by realistic chance of a $0 landed unit, and say plainly
   which are long shots.

Be honest about probability. A genuinely free unit is rare; a demo credited 100%
against a first order is the achievable version of the same outcome, and usually the
better thing to chase.
