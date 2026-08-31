# Compliance notes

Where each KDP policy constraint is enforced in code, and what is still a
human's job.

> **Source caveat.** Every `amazon.com` host is refused by network policy in
> the environment this package is developed in — the proxy answers 403 to
> CONNECT — so the policy details below were gathered from secondary summaries
> and **have not been confirmed against Amazon's own pages**. Treat this
> document as a starting checklist, not as authority.
>
> `python3 -m kdp verify-specs` prints, table by table, the page to read and
> what to confirm on it.
>
> `kdp/specs/revision.py` carries the same caveat as machine state: one
> `SourceRecord` per table (`trim`, `paper`, `interior`, `cover`, `royalty`,
> `policy`), each holding a source URL, a verification date and a marketplace.
> All six are `UNVERIFIED`, and **G2, G12 and G13 refuse to certify a book**
> while that stands. Verifications also expire after 180 days, so a check made
> once does not stay true forever.
>
> To verify: read the primary page, confirm every value in the corresponding
> module, then fill in `source_url` and `verified_on`. That alone flips the
> table.

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
gates G3, G6, G12 and G13.

Four properties matter:

1. `AIRole.UNKNOWN` is the default, and it **never** rolls up to "no disclosure
   required". An unclassified asset makes the answer `complete: false`, which
   fails G3, G6, G12 and G13. Because over-disclosure is free and
   under-disclosure is expensive, the system fails toward disclosing.
2. The `disclosure` block written into a manifest is **derived on read**, not
   trusted. Hand-editing it in the JSON file changes nothing a gate sees.
3. The roll-up counts **only versions that ship**. A rejected AI attempt is not
   in the book, and disclosing it would misdescribe what was published.
4. An AI claim must name the tool that produced it. `AssetProvenance` refuses
   to construct otherwise: "AI-generated" with no record of what generated it
   is a note to self, not an audit trail.

The answer is also printed in the copyright page of the built interior, not
only on the KDP form — the form answer is internal to Amazon, and a reader is
owed the information too.

**Human's job:** answering the question on the KDP form, then setting
`ai_disclosure_confirmed`. No agent may tick that box.

## Content classification

**A coloring book is not automatically a low-content book.** KDP's Low-Content
Books documentation lists coloring books under *Not Generally Low-Content*, so
the low-content rules do not apply to a title merely because it is a coloring
book. Classify a specific title on what it actually contains, never on its
category.

This matters in both directions: applying low-content restrictions to a book
that is not one imposes constraints Amazon does not, and assuming a coloring
book escapes scrutiny ignores the duplicative-content rules below, which apply
to every title.

## Duplicative content and quality

Content is allowed when it offers real customer value and is not duplicative. Common removal triggers: duplicate interiors, minimal
differentiation between titles, mass-generated uploads, and misleading or
keyword-stuffed metadata. Large-scale uploads of near-identical books raise
scrutiny even where no single title is over a line.

**Enforced by:** gate G4 (`originality`) for intra-book and cross-catalogue
duplication; gate G6 for keyword stuffing, duplicate keyword slots and thin
descriptions; gate G1 for a research brief that names no opportunity — a book
with no stated gap to fill is the undifferentiated kind.

Near-duplicate detection is implemented: a difference hash over the pixels
flags pages within 10 bits of 64 of each other, catching a rescaled or mirrored
repeat that an exact byte-match would miss. A page that could not be read is
reported as unchecked and blocks the gate, because "no exact byte-matches" is a
weaker claim than "no duplicates".

**A similarity score is a QA signal, never a legal determination.** G4 finds
repeated and near-repeated images; it does not and cannot decide a copyright
question. G12 flags trademark *proximity* in metadata and page subjects and
routes it to a human with that stated explicitly. Neither gate claims legal
certainty, and the compliance agent is instructed never to adjudicate fair use.

Differentiation is checked earlier and separately: **G7** rejects a strategy
with no articulated unique angle or fewer than two *concrete* differentiators.
Vague ones ("higher quality", "more beautiful") are rejected by name, because a
book that cannot say how it differs is the undifferentiated kind KDP removes.

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

**Enforced by:** nothing — this pipeline does not publish. The cap appears in
the generated `SUBMISSION_CHECKLIST.md` so a batch is paced deliberately rather
than throttled mid-upload.

## Print manufacturing limits

Bleed of 0.125" on outer edges (asymmetric: +0.125" width, +0.25" height).
Minimum outer margins of 0.25" without bleed, 0.375" with. Gutter widening with
page count across five bands from 0.375" to 0.875". Page counts from 24 to 828
depending on paper. Spine text only above a page-count threshold, with 0.0625"
of clearance each side. Interior artwork at 300 DPI or better.

**Enforced by:** `kdp/specs/` and gates G2 and G5.

**Enforced by inspection:** the built PDFs are opened and measured — page
count, page dimensions against trim-plus-bleed, uniform page size, artwork on
the pages the spec describes, embedded image resolution, single-sided layout,
cover canvas and spine geometry. The reader is `pypdf`, deliberately a
different implementation from the writer in `kdp/pdfwrite.py`: a writer that
verified its own output would certify its own bugs. Without it, G5/G10/G11
block.

**Not enforced:** colour space and embedded font subsetting. KDP's own
previewer is the authority on both and should be run before publishing; the
review package says so rather than implying they were checked.

## Human approval

**Enforced by:** gate G14, `kdp/models/approval.py`, and `kdp/package.py`.

No book reaches a publication package without a person recording a decision
about that exact book. The approval carries a fingerprint over the shipping
assets, the built PDFs, the listing and the price; changing any of them voids
it, and both G14 and the packager refuse. There is no flag that satisfies G14.

**Human's job:** reading the review package and deciding. Everything else is
prepared for them.

## Economics and honest projections

**Enforced by:** `kdp/models/economics.py` and G13.

Royalty and printing-cost figures are projections labelled with a confidence,
and the roll-up takes the *weakest* input — a projection is only as sound as
its shakiest assumption. The royalty rate and cost constants are `UNKNOWN`
until the `royalty` table is verified, so today every projection says so. G13
refuses a price that would lose money on every copy, and the review package
states plainly that these are projections rather than income.

## What this system will not do

- Publish, upload, or submit anything to Amazon. No module outside
  `kdp/providers/` imports an HTTP client at all, and a test asserts it.
- Answer the AI disclosure question on a human's behalf.
- Approve a book, or keep an approval alive across an edit.
- Advance a book past a gate it did not pass.
- Treat a missing dependency as a passing check.
- Claim legal certainty about copyright or trademark.
- Present an estimate as an observation, or a projection as income.
- Report a check as clean when nothing looked. Text detection needs OCR, which
  is not installed, so the review package lists it under "not attempted"
  instead.
