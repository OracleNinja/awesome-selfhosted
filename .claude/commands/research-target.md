---
description: Deep-research one target — contacts, programs, inventory, evidence — then rescore it
argument-hint: "<target id or company name>"
---
Invoke the `dealer-arbitrage` skill for one target: $ARGUMENTS

1. `cd dealer-arbitrage && ./da targets <id>` — see what is known and what is missing.
2. `./da tasks` — check for open `missing_evidence` tasks on this target. A signal that
   matched but scored 0 is evidence you can go collect.
3. Research: who runs dealer development (name and role), do they publish a dealer or
   demo program, what inventory is sitting, what is their freight situation, are they
   who they say they are.
4. `./da evidence add --company-id <n> --claim <key> --url <url> --quote "<exact text>"`
   for each finding.
5. `./da score <id>` and `./da strategy <id>` to rescore with the new evidence.
6. Report what changed, and whether this target is now worth outreach.

If the company cannot be verified as real, say so and flag it rather than proceeding.
