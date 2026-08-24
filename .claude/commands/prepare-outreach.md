---
description: Draft personalized, evidence-backed outreach for a target and present it for approval
argument-hint: "<target id> [channel: email|web_form|phone|linkedin]"
---
Invoke the `dealer-arbitrage` skill: $ARGUMENTS

1. `cd dealer-arbitrage && ./da profile check` — stop if outreach is blocked.
2. `./da targets <id>` — confirm there are at least two evidenced facts to reference.
   If not, research first. A generic message is worse than no message.
3. `./da outreach <id> --present`
4. If the draft is blocked, fix the cause — missing profile fields, thin research, a
   claim-guard violation. Do not edit around the guard.
5. Show the user the full approval block: TARGET, COMPANY, CONTACT, CHANNEL, MESSAGE,
   ATTACHMENTS, REQUEST, EXPECTED OUTCOME, RISKS, TOTAL COST COMMITMENT.
6. **STOP.** Wait for `APPROVE SEND`, `EDIT`, or `CANCEL`.

On approval:
`./da approvals approve --approval-id <n> --by "<user's name>"`, the user sends it,
then `./da approvals sent --outreach-id <n> --by "<user's name>"` to log the send and
start the 3/7/14/30-day follow-up ladder.
