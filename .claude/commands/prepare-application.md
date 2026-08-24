---
description: Open a dealer application, map business.json onto it, fill what is provable, screenshot it, and stop for approval
argument-hint: "<target id> <application url>"
---
Invoke the `dealer-arbitrage` skill: $ARGUMENTS

1. `cd dealer-arbitrage && ./da profile check` — the `dealer_application` level must
   pass, or list exactly what is missing and stop.
2. Open the application and inspect the form. If browser automation is available and
   the domain is allowlisted, the CLI can do it; otherwise list the fields yourself and
   pass them with `--fields fields.json`.
3. `./da application prepare --target-id <id> --url <url> --fields fields.json --program "<name>"`
4. Review the READY FOR APPROVAL report with the user: what was filled and from which
   profile field, what needs their confirmation, what only they can supply, and what
   the agent refused to touch.
5. **STOP.** The agent does not submit.

Never fill a signature, initials, terms acceptance, CAPTCHA, password, SSN, bank or
card field. Never guess at volume, years in business, or licensure — a wrong answer on
a dealer application is a misrepresentation, not a typo.

After the user submits it themselves:
`./da application submitted --application-id <n> --by "<name>" --ref "<confirmation>" --screenshot <path>`
