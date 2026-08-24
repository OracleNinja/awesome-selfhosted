---
description: Run a full dealer-arbitrage acquisition cycle — research, score, strategize, draft outreach, prepare applications, present for approval
argument-hint: "[optional focus, e.g. 'denago only' or 'texas']"
---
Invoke the `dealer-arbitrage` skill and run the full campaign loop for: $ARGUMENTS

1. `cd dealer-arbitrage && ./da profile check` — if required fields are missing, STOP
   and ask the user for them (see the skill's `references/intake-questions.md`).
2. `./da research plan` — get the query list.
3. Run those searches yourself (WebSearch/WebFetch). Vet each company against the
   checklist. Capture a source URL and an exact quotation for every status claim.
4. `./da research import findings.json`
5. `./da score` then `./da strategy` — rank targets and assign acquisition strategies.
6. `./da zero` — the zero-dollar shortlist. Work these first.
7. `./da outreach <id> --present` for the strongest targets, one at a time.
8. Present each approval block and STOP. Do not send anything.
9. Report: targets found, top-scored, zero-dollar candidates, what is drafted and
   waiting, and what you need from the user to go further.

Do not stop after one target — this is a competitive campaign. Never claim dealer
status, never invent a fact, never send anything without `APPROVE SEND`.
