# Compliance notes

Where each KDP policy constraint is enforced in code, and what is still a
human's job.

> **Source caveat.** `kdp.amazon.com` was unreachable from the environment this
> was written in (blocked by the network egress proxy), so the policy details
> below were gathered from secondary summaries and **have not been confirmed
> against Amazon's own pages**. Treat this document as a starting checklist,
> not as authority. `kdp/specs/revision.py` carries the same caveat as machine
> state: it is marked `UNVERIFIED`, and G2 fails every book while it stays that
> way. Verify, then flip it.

## AI-generated content disclosure

KDP distinguishes two things that sound alike:

- **AI-generated** — text, images or translations *produced* by an AI tool.
  Editing the output afterwards does not change this. **Must be disclosed.**
- **AI-assisted** — brainstorming, grammar checking, refining text a human
  wrote, improving an image a human made. **Need not be disclosed.**

Disclosure is made at publish time (and when updating a title), is used
internally by Amazon, is not shown on the product page, and per Amazon's own
statements does not affect royalties or search ranking. Undisclosed
AI-generated content can get a title blocked or removed, and repeat or
egregious cases can suspend an account and withhold pending royalties.

**Enforced by:** `kdp/models/provenance.py` (`AIRole`, `roll_up_disclosure`),
gate G3 (`provenance_complete`), gate G6 (`metadata_compliance`).

Two properties matter:

1. `AIRole.UNKNOWN` is the default, and it **never** rolls up to "no disclosure
   required". An unclassified asset makes the answer `complete: false`, which
   fails G3 and G6. Because over-disclosure is free and under-disclosure is
   expensive, the system fails toward disclosing.
2. The `disclosure` block written into a manifest is **derived on read**, not
   trusted. Hand-editing it in the JSON file changes nothing a gate sees.

**Human's job:** answering the question on the KDP form, then setting
`ai_disclosure_confirmed`. No agent may tick that box.

## Duplicative content and low-content quality

Low-content books are allowed when they offer real customer value and are not
duplicative. Common removal triggers: duplicate interiors, minimal
differentiation between titles, mass-generated uploads, and misleading or
keyword-stuffed metadata. Large-scale uploads of near-identical books raise
scrutiny even where no single title is over a line.

**Enforced by:** gate G4 (`originality`) for intra-book and cross-catalogue
duplication; gate G6 for keyword stuffing, duplicate keyword slots and thin
descriptions; gate G1 for a research brief that names no opportunity — a book
with no stated gap to fill is the undifferentiated kind.

**Not yet enforced:** near-duplicate detection needs Pillow. Until it is
installed, G4 reports `BLOCKED` rather than passing, because "no exact
byte-matches" is a weaker claim than "no duplicates".

## Copyright and competitor research

Competitor books are market research only. The pipeline is built so that
copying is structurally awkward rather than merely discouraged:

- `MarketSignal` is a **whitelist**. It holds a segment label, price, page
  count, trim, a coarse demand band, and short observations. `from_dict`
  rejects any field outside that set, so competitor artwork, cover URLs, titles
  and blurbs cannot be represented at all.
- Gate G1 rejects observations longer than 240 characters (long free text is
  where copied material hides), observations referencing media files, and
  observations that open with a quotation mark.
- The `kdp-market-researcher` and `kdp-asset-generator` agents are both
  instructed that recognisable copyrighted characters, trade dress, distinctive
  artistic style, and competitor layouts are out of bounds.

**Human's job:** judgement about whether a theme, taken as a whole, is too
close to something that exists. No automated check substitutes for that.

## Publishing velocity

KDP caps new title submissions at **three per 24 hours**. Reprints, edits and
price changes do not count. Exemptions are handled case by case. Repeated
velocity violations escalate from a 24-hour hold to a full account review.

**Enforced by:** nothing — this pipeline does not publish. The
`kdp-publishing-preparer` agent surfaces the cap in its handover checklist so a
batch is paced deliberately rather than throttled mid-upload.

## Print manufacturing limits

Bleed of 0.125" on outer edges (asymmetric: +0.125" width, +0.25" height).
Minimum outer margins of 0.25" without bleed, 0.375" with. Gutter widening with
page count across five bands from 0.375" to 0.875". Page counts from 24 to 828
depending on paper. Spine text only above a page-count threshold, with 0.0625"
of clearance each side. Interior artwork at 300 DPI or better.

**Enforced by:** `kdp/specs/` and gates G2 and G5.

**Not yet enforced:** everything that requires opening the PDF — actual page
dimensions, embedded fonts, image DPI, colour space. G5 reports `BLOCKED`
without `pypdf`, because those cannot be inferred from a manifest.

## What this system will not do

- Publish, upload, or submit anything to Amazon.
- Answer the AI disclosure question on a human's behalf.
- Advance a book past a gate it did not pass.
- Treat a missing dependency as a passing check.
