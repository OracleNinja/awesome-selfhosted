"""G8 — prompt plan completeness.

Every art page needs its own prompt, and the prompts must actually differ.

The failure this exists to catch is subtle and expensive: a plan that reuses
one prompt across twenty pages passes every structural check and then produces
twenty variations of the same drawing, which G4 correctly fails at QA — after
the generation spend. Catching it here costs nothing.
"""

from __future__ import annotations

from collections import defaultdict

from ..models.prompts import MIN_PROMPT_CHARS, PromptPlan
from ..models.spec import BookSpec
from .base import Finding, GateResult, Severity, decide

GATE_ID = "G8.prompt_plan"
STAGE = "planning"
VALIDATOR_VERSION = "1"


def run(spec: BookSpec, plan: PromptPlan | None) -> GateResult:
    if plan is None:
        return decide(
            GATE_ID,
            STAGE,
            [],
            evidence={"validator_version": VALIDATOR_VERSION},
            blocked_reason=(
                "No prompt plan attached to the manifest. Generation without a "
                "per-page plan cannot be reproduced or audited."
            ),
        )

    findings: list[Finding] = []
    art_pages = {p.page_id: p for p in spec.art_pages}
    planned = {p.page_id for p in plan.prompts}

    for missing in sorted(set(art_pages) - planned):
        findings.append(
            Finding(
                code="prompts.missing",
                severity=Severity.BLOCKER,
                message="Art page has no prompt.",
                subject=missing,
            )
        )

    for orphan in sorted(planned - set(art_pages)):
        findings.append(
            Finding(
                code="prompts.orphan",
                severity=Severity.MINOR,
                message="Prompt names a page the spec does not describe.",
                subject=orphan,
            )
        )

    by_hash: dict[str, list[str]] = defaultdict(list)
    for prompt in plan.prompts:
        by_hash[prompt.prompt_hash].append(prompt.page_id)

        rendered = prompt.render()
        if len(rendered) < MIN_PROMPT_CHARS:
            findings.append(
                Finding(
                    code="prompts.too_thin",
                    severity=Severity.BLOCKER,
                    message=(
                        f"Prompt renders to {len(rendered)} characters. A prompt "
                        "this thin will not produce consistent line art."
                    ),
                    subject=prompt.page_id,
                    detail={"length": len(rendered)},
                )
            )

        if not prompt.prohibited:
            findings.append(
                Finding(
                    code="prompts.no_prohibitions",
                    severity=Severity.MAJOR,
                    message=(
                        "Prompt names nothing prohibited. Colour, shading, text, "
                        "watermarks and signatures all need excluding explicitly "
                        "for coloring-book line art."
                    ),
                    subject=prompt.page_id,
                )
            )

        if not prompt.background_requirements:
            findings.append(
                Finding(
                    code="prompts.no_background_requirement",
                    severity=Severity.MAJOR,
                    message=(
                        "Prompt does not specify the background. Line art needs "
                        "it stated, or the provider will invent one."
                    ),
                    subject=prompt.page_id,
                )
            )

        page = art_pages.get(prompt.page_id)
        if page is not None and page.complexity != prompt.complexity:
            findings.append(
                Finding(
                    code="prompts.complexity_mismatch",
                    severity=Severity.MAJOR,
                    message=(
                        f"Prompt asks for complexity {prompt.complexity} but the "
                        f"spec says {page.complexity}; the difficulty curve would "
                        "not come out as designed."
                    ),
                    subject=prompt.page_id,
                )
            )

    for digest, pages in sorted(by_hash.items()):
        if len(pages) > 1:
            findings.append(
                Finding(
                    code="prompts.duplicate",
                    severity=Severity.BLOCKER,
                    message=(
                        f"{len(pages)} pages share an identical prompt and would "
                        "produce the same drawing."
                    ),
                    subject=", ".join(sorted(pages)),
                    detail={"prompt_hash": digest, "pages": sorted(pages)},
                )
            )

    if not plan.house_style.strip():
        findings.append(
            Finding(
                code="prompts.no_house_style",
                severity=Severity.MAJOR,
                message=(
                    "The plan states no shared house style, so pages have "
                    "nothing making them look like one book."
                ),
                subject="house_style",
            )
        )

    return decide(
        GATE_ID,
        STAGE,
        findings,
        evidence={
            "validator_version": VALIDATOR_VERSION,
            "plan_id": plan.plan_id,
            "art_pages": len(art_pages),
            "prompts": len(plan.prompts),
            "distinct_prompts": len(by_hash),
        },
    )
