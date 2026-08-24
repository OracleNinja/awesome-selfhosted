# What to ask the user for `business.json`

Ask in these groups, in this order. Stop at the first group the user cannot complete —
later groups are useless without the earlier ones. **Never fill any of these in on the
user's behalf, and never soften a "no" into a maybe.** A false answer here becomes a
false statement in a message signed with their name.

Anything the user does not have yet is fine. Record it as `null` and note what it
blocks. "Not yet" is a legitimate answer that keeps the system honest.

## Group 1 — Identity (blocks all outreach)

| Field | Question |
|---|---|
| `legal_name` | What is the exact legal name of the business as registered? |
| `owner_name` | Whose name goes on the correspondence? |
| `business_name` | What is the trade name / DBA customers see? |
| `phone` | What business phone should suppliers call? |
| `email` | What business email should suppliers reply to? |
| `city`, `state` | What city and state is the business in? |

## Group 2 — Outreach substance (blocks drafting)

| Field | Question |
|---|---|
| `business_description` | In one or two sentences, what is the business today? Not the plan — what exists right now. |
| `territory.primary_market` | What market are you selling into? (e.g. "the DFW metro") |
| `storefront_description` | Describe your location: shop, lot, showroom, home-based? Square footage if you know it. |
| `display_area.type` | Where would a demo unit physically sit? |
| `shipping_capability.receiving_method` | How would you take delivery of a crated cart — dock, forklift, liftgate, pickup? |
| `projected_initial_inventory.units` / `.timeframe` | How many units do you plan to order first, and by when? **A plan, not a promise.** |
| `website`, `social_profiles` | Any website, Facebook page, or Google Business Profile? |
| `years_in_business` | How long has the business been operating? Leave blank if it hasn't started. |
| `current_products` | What do you sell or service today, if anything? |
| `marketing_channels` | How do you reach customers? |
| `service_capabilities.*` | Can you do service work in-house? Lithium/battery work? How many technicians? |
| `projected_annual_volume.units` / `.basis` | What annual volume do you project, and on what basis? |

## Group 3 — Dealer application (blocks applications)

| Field | Question |
|---|---|
| `business_address`, `zip` | Full street address for the dealer file. |
| `tax_information.entity_type` | LLC, sole proprietor, S-corp? |
| `tax_information.ein` | Do you have an EIN? **Goes in `profile/sensitive.local.json`, not business.json.** |
| `licenses.sales_tax_permit` | Do you have a state sales tax permit? Number goes in the local file. |
| `licenses.business_license` | City/county business license? |
| `licenses.state_dealer_license` | Does your state require a dealer license to sell LSVs/golf carts? Do you have one? |
| `licenses.liability_insurance` | Do you carry liability insurance? Carrier and coverage? |
| `display_area.square_feet` | Display/showroom square footage. |
| `photos`, `documents` | Can you provide location photos and copies of your permits? |

## Group 4 — Terms and constraints

| Field | Question |
|---|---|
| `constraints.max_out_of_pocket_cents` | Defaults to `0` — the $0 objective. Is there any amount you would spend? |
| `constraints.willing_to_pay_freight` / `willing_to_split_freight` | Would you cover freight if the unit itself were free? |
| `constraints.willing_to_commit_first_order` / `first_order_units_willing` | Are you willing to commit to a first order to unlock a credited demo, and for how many units? |
| `banking_and_credit.*` | Do you have trade or bank references? (Yes/no only — **never store account numbers**.) |
| `territory.exclusivity_requested` | Do you want to ask for territory exclusivity? |

## Also set in `config/settings.json`

- `campaign.home_market` — city, state, zip, metro, and `metro_cities` (nearby cities
  that count as pickup distance). Discovery queries containing `{state}`/`{city}`/
  `{metro}` are skipped until these are set.
- `approval.approver` — the name recorded on every approval decision.

## Never ask for, never store

Passwords, credit card numbers, bank account or routing numbers, SSN, or auth
cookies. If a form needs one, the human types it directly. The profile loader refuses
to hold them and the form-filler refuses to transmit them.
