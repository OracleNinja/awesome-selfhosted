---
description: Show due follow-ups and draft them with a fresh angle
---
Invoke the `dealer-arbitrage` skill.

1. `cd dealer-arbitrage && ./da followups list` — what is due (3/7/14/30 days).
2. `./da followups draft` — drafts each with a rotated angle: specific inventory
   question → program mechanics → right-person referral → close the loop.
3. Present each for approval. Follow-ups need `APPROVE SEND` like anything else, unless
   autonomous follow-ups have been explicitly enabled *and* the company is on the
   allowlist.
4. `./da followups stop --target-id <id> --reason "<why>"` for anything that should end.

A follow-up must change the angle, never repeat the ask. If a target has replied, the
ladder is superseded — handle the reply instead. After day 30, stop; four touches is
the ceiling and the fourth one says goodbye.
