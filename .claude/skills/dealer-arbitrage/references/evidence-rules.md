# What counts as evidence

## The rule

A claim about a counterparty may be stored and stated only if there is a row in
`sources` containing a **URL** and the **exact quoted text** that supports it. A
summary is not evidence. A recollection is not evidence. "Their site seems to suggest"
is not evidence.

Unevidenced claims are not errors to be argued with — they are downgraded to
`unknown` on intake and the scoring signal contributes zero. That is the design
working, not a bug to route around.

## Claim keys

| Key | Supports | A good quote looks like |
|---|---|---|
| `dealer_program` | has a dealer/dealer-development program | "Become a dealer — submit a dealer application" |
| `demo_program` | places demo/evaluation/loaner units | "Qualified dealers may receive a demo unit" |
| `wholesale` | sells wholesale / at dealer pricing | "Wholesale and dealer pricing available" |
| `company_type` | is a manufacturer/distributor | "We manufacture electric low-speed vehicles" |
| `denago_relationship` | sells or has sold Denago | "Authorized Denago dealer" on *their* site |
| `target_model_inventory` | has the target model in stock | "Nomad XL — in stock" |
| `excess_inventory` | clearance/overstock available | "Clearance: overstock units must go" |
| `previous_model_year` | non-current units available | "2024 models — leftover pricing" |
| `dealer_to_dealer` | does dealer transfers | "Dealer trades welcome" |
| `pricing` | a market price you intend to cite | the listing itself, with the price visible |

## Recording it

```bash
./da evidence add --company-id 3 --claim demo_program \
  --url "https://example.com/dealers" \
  --quote "Qualified dealers may receive a demo unit for their showroom." \
  --confidence high
```

Or inline in a finding's `evidence` array during `./da research import`.

## Price citations

If a message cites a market price, the `pricing` source must exist and the URL goes
in the message. The claim guard warns on any unsourced dollar figure. Never cite a
price you cannot link to, and never present a price as a competing *offer* — a public
listing is a listing, not an offer made to us.

## Vetting checklist before promoting a target

- Physical address present and plausible (not a mail drop, not residential-only)
- Domain age and WHOIS consistent with the claimed history
- Phone answers, and answers as that business
- Registered with a Secretary of State (or foreign equivalent)
- Inventory photos original, not stock or lifted from another dealer
- Prices within a plausible band — far below market is a scam marker, not a bargain
- Payment terms: wire-only, crypto-only, or deposit-before-verification is a hard stop
- Reviews / BBB / complaint history
- Overseas suppliers: verified supplier status, audit reports, trade assurance

Anything failing these goes into `risk_flags`, which the scorer penalizes heavily.
