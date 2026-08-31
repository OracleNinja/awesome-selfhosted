"""A complete book fixture, built stage by stage.

Used by the end-to-end test and by the demo. Deliberately built through the
same functions the pipeline uses rather than hand-written JSON, so the fixture
cannot drift away from what the code actually produces.
"""

from __future__ import annotations

from kdp.models import (
    AssetPrompt,
    BookManifest,
    BookMetadata,
    BookSpec,
    BookStrategy,
    Claim,
    Confidence,
    MarketSignal,
    PageSpec,
    PromptPlan,
    ResearchBrief,
    TrimClass,
    compare,
)

BOOK_ID = "quiet-gardens"
TITLE = "Quiet Gardens"
SUBTITLE = "Twenty-Four Botanical Studies to Colour"
AUTHOR = "A. Author"

#: 2 front-matter pages + 24 art pages each backed by a blank = 50 pages.
ART_PAGE_COUNT = 24

SUBJECTS = (
    "a single fern frond uncurling",
    "three foxglove spires in a row",
    "a cluster of hellebore flowers",
    "one thistle head seen from above",
    "a trailing ivy stem across the page",
    "seed heads of wild carrot",
    "a hosta leaf with deep veins",
    "two poppy pods on bent stems",
    "a sprig of rosemary",
    "an open peony bloom",
    "a hazel catkin and leaf",
    "teasel heads against tall grass",
    "a hellebore seed pod splitting",
    "a fan of ginkgo leaves",
    "a snowdrop bowing on its stem",
    "an allium head in full sphere",
    "bracken fronds overlapping",
    "a rose hip cluster",
    "an oak leaf and acorn cup",
    "sea holly with spiked bracts",
    "a curl of birch bark",
    "honesty seed pods, translucent",
    "a bee orchid in profile",
    "a sunflower head gone to seed",
)


def build_research() -> ResearchBrief:
    return ResearchBrief(
        brief_id="brief-001",
        segment="adult-botanical-colouring",
        signals=(
            MarketSignal(
                signal_id="s1",
                segment="adult-botanical-colouring",
                price_usd=8.0,
                page_count=60,
                trim="8.5x11",
                demand_band="high",
                observations=(
                    "reviewers repeatedly ask for single-sided pages",
                    "complaints cluster on marker bleed-through",
                ),
            ),
            MarketSignal(
                signal_id="s2",
                segment="adult-botanical-colouring",
                price_usd=6.5,
                page_count=40,
                trim="8.5x11",
                demand_band="medium",
                observations=("buyers mention wanting simpler pages to start with",),
            ),
        ),
        opportunity=(
            "Single-sided botanical line art with a gentle difficulty curve, "
            "aimed at people who find dense mandala books discouraging."
        ),
        recommended_trim="8.5x11",
        recommended_page_count=50,
    )


def build_strategy() -> BookStrategy:
    return BookStrategy(
        strategy_id="strategy-001",
        brief_id="brief-001",
        niche="adult botanical colouring",
        audience="adults new to colouring, and returning hobbyists",
        concept=(
            "Twenty-four botanical studies drawn from northern-hemisphere "
            "hedgerow and garden plants, rising in difficulty from single "
            "specimens to layered compositions."
        ),
        unique_angle=(
            "A difficulty curve that starts genuinely easy: the first six pages "
            "are single specimens with wide open areas, so a beginner finishes "
            "one in a sitting rather than abandoning it."
        ),
        purpose="calm, low-pressure practice for people intimidated by dense line art",
        differentiators=(
            "every drawing is printed single-sided, backed by a blank page",
            "difficulty rises across five explicit complexity bands",
            "subjects are drawn from northern-hemisphere hedgerow plants only",
            "line weight is held above 2pt so markers and thicker pens work",
        ),
        risks=(
            "botanical colouring is a crowded segment; differentiation rests on "
            "the difficulty curve rather than the subject matter",
            "seasonal demand peaks in autumn and winter",
        ),
        claims=(
            Claim(
                "segment price band",
                "$6.50-$8.00",
                Confidence.OBSERVED,
                "two listed competitor prices",
            ),
            Claim(
                "demand for single-sided pages",
                "high",
                Confidence.INFERRED,
                "recurring theme in competitor reviews",
            ),
            Claim(
                "monthly segment volume",
                "unknown",
                Confidence.ESTIMATED,
                "no sales data available; not used for any decision",
            ),
        ),
        title_concepts=("Quiet Gardens", "Hedgerow Hours"),
        keyword_concepts=("botanical colouring book", "single sided colouring"),
        category_concepts=("Crafts, Hobbies & Home > Crafts & Hobbies",),
        recommended_trim="8.5x11",
        recommended_page_count=50,
        visual_direction="clean black line art, open interiors, no shading",
    )


def build_spec() -> BookSpec:
    pages = [
        PageSpec("PAGE-001", 1, "title"),
        PageSpec("PAGE-002", 2, "copyright"),
    ]
    index = 3
    for n, subject in enumerate(SUBJECTS):
        # Blanks on odd indices so every drawing lands on an even one and backs
        # onto a blank page.
        pages.append(PageSpec(f"PAGE-{index:03d}", index, "blank"))
        pages.append(
            PageSpec(
                f"PAGE-{index + 1:03d}",
                index + 1,
                "art",
                subject=subject,
                complexity=1 + (n * 5) // len(SUBJECTS),
            )
        )
        index += 2

    return BookSpec(
        book_id=BOOK_ID,
        title=TITLE,
        subtitle=SUBTITLE,
        trim="8.5x11",
        paper="bw_white",
        bleed=True,
        page_count=len(pages),
        single_sided_art=True,
        pages=tuple(pages),
        derived_from_brief="brief-001",
    )


def required_pixels(spec: BookSpec) -> tuple[int, int]:
    """Pixel size that clears KDP's 300 DPI floor over this book's live area.

    Derived rather than hard-coded: the live area depends on trim, bleed and
    the gutter band the page count falls into, so a fixed number would be right
    for one book and quietly wrong for the next.
    """
    from kdp.specs import MIN_IMAGE_DPI, get_trim, interior_geometry

    geometry = interior_geometry(get_trim(spec.trim), spec.page_count, spec.bleed)
    # A little headroom so rounding cannot land a page a pixel under the floor.
    dpi = MIN_IMAGE_DPI + 20
    return (
        int(geometry.live_width_in * dpi),
        int(geometry.live_height_in * dpi),
    )


def build_prompt_plan(spec: BookSpec) -> PromptPlan:
    width, height = required_pixels(spec)
    return PromptPlan(
        plan_id="plan-001",
        house_style=(
            "Uniform black line art on pure white. Consistent 2pt stroke, all "
            "regions closed, no shading, no greyscale fills, no background."
        ),
        prompts=tuple(
            AssetPrompt(
                prompt_id=f"PROMPT-{page.page_id}",
                page_id=page.page_id,
                version="v1",
                subject=page.subject,
                composition="single subject centred, generous white space around it",
                audience="adults new to colouring",
                line_art_requirements=(
                    "uniform black stroke of at least 2pt, every region fully "
                    "closed so fills cannot leak"
                ),
                background_requirements="pure white, entirely empty",
                complexity=page.complexity,
                consistency_requirements=(
                    "matches the other pages in stroke weight and level of detail"
                ),
                prohibited=(
                    "colour",
                    "shading or greyscale",
                    "text or lettering",
                    "watermarks, logos or signatures",
                    "recognisable characters or trademarks",
                ),
                aspect_ratio="4:5",
                width=width,
                height=height,
            )
            for page in spec.art_pages
        ),
    )


def build_metadata() -> BookMetadata:
    return BookMetadata(
        book_id=BOOK_ID,
        title=TITLE,
        subtitle=SUBTITLE,
        author=AUTHOR,
        description=(
            "Twenty-four botanical studies to colour, drawn from hedgerow and "
            "garden plants. Every drawing is printed on its own page and backed "
            "by a blank, so markers and gel pens cannot bleed through onto a "
            "second picture. The pages begin with single specimens and open "
            "shapes, then build gradually towards layered compositions, so "
            "there is somewhere comfortable to start whether this is your first "
            "colouring book or your fortieth. Printed single-sided on white "
            "paper at 8.5 by 11 inches."
        ),
        keywords=(
            "botanical coloring book",
            "single sided coloring book",
            "adult coloring book flowers",
            "beginner coloring book",
            "line art botanical",
        ),
        categories=(
            "Crafts, Hobbies & Home > Crafts & Hobbies",
            "Health, Fitness & Dieting > Mental Health",
        ),
        ai_disclosure_confirmed=True,
    )


def build_manifest() -> BookManifest:
    """A manifest at the concept stage, ready for planning and generation."""
    spec = build_spec()
    return BookManifest(
        spec=spec,
        research=build_research(),
        strategy=build_strategy(),
        prompt_plan=build_prompt_plan(spec),
        metadata=build_metadata(),
        # The trim class is stated rather than inferred: KDP prices regular
        # and large trims differently and kdp/specs/trim.py does not yet
        # know which 8.5x11 is. The fixture says REGULAR so the pipeline
        # can be exercised end to end; the resulting Economics reports at
        # UNKNOWN confidence precisely because of this assumption.
        economics=compare(
            [5.99, 7.99, 9.99], spec.page_count, trim_class=TrimClass.REGULAR
        ),
        stage="research",
    )
