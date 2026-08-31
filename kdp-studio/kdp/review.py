"""The human review package.

Everything a reviewer needs to decide, in one document, written so it can be
read without opening the source. It is the input to the only gate no automation
can satisfy, so it has two obligations beyond being informative:

**It must not editorialise the gates.** It reports what each gate said, in the
gate's own words. A summary that softens a BLOCKED into "minor issue" would
defeat the separation the whole pipeline is built on.

**It must state what was not checked.** A blocked gate means a check did not
run, and a reviewer who does not know that will read silence as approval.
"""

from __future__ import annotations

from .gates import GATES, final_audit
from .gates.base import GateResult, Severity, Status
from .specs.revision import SOURCES, verification_problem
from .models.artifacts import ArtifactKind
from .models.manifest import BookManifest
from .models.strategy import Confidence


def _heading(text: str) -> str:
    return f"\n## {text}\n"


def _kv(label: str, value: object) -> str:
    return f"- **{label}:** {value}"


def build_review(
    manifest: BookManifest, results: list[GateResult] | None = None
) -> str:
    """Render the review package as Markdown."""
    results = results or []
    spec = manifest.spec
    lines: list[str] = [f"# Review package — {spec.title}", ""]

    fingerprint = manifest.production_fingerprint()
    lines.append(_kv("Book id", spec.book_id))
    lines.append(_kv("Stage", manifest.stage))
    lines.append(_kv("Production fingerprint", f"`{fingerprint[:23]}…`"))
    lines.append(
        _kv(
            "Approval",
            f"{manifest.approval.status.value}"
            + ("" if manifest.approval_is_current() else " (not current for this book)"),
        )
    )

    # The reviewer must not read a PASS as "the print maths was verified".
    # A run can only reach them at all by overriding this, so it is stated
    # before anything else rather than buried in a later section.
    spec_problem = verification_problem()
    if spec_problem:
        unverified = sorted(t for t, r in SOURCES.items() if r.state == "UNVERIFIED")
        lines.append("")
        lines.append(
            "> **The KDP specification tables are not verified.** "
            f"Unverified: {', '.join(unverified)}. Every gate result below that "
            "depends on print geometry, printing cost or policy was produced by "
            "overriding that check — trim, bleed, margins, spine width and "
            "royalty were computed from numbers nobody has confirmed against "
            "Amazon's own documentation. Run `python3 -m kdp verify-specs` for "
            "what to check. **Do not treat a PASS below as confirmation that "
            "this book will print or price as described.**"
        )

    # 1-4. Concept, research, positioning, differentiation.
    lines.append(_heading("1. Concept"))
    if manifest.strategy:
        s = manifest.strategy
        lines += [
            _kv("Niche", s.niche),
            _kv("Audience", s.audience + (f" ({s.age_range})" if s.age_range else "")),
            _kv("Purpose", s.purpose),
            "",
            s.concept or "_No concept statement._",
        ]
    else:
        lines.append("_No strategy recorded._")

    lines.append(_heading("2. Market research"))
    if manifest.research:
        brief = manifest.research
        lines.append(_kv("Segment", brief.segment))
        lines.append(_kv("Opportunity", brief.opportunity or "_none stated_"))
        lines.append("")
        lines.append("| Signal | Price | Pages | Trim | Demand |")
        lines.append("|---|---|---|---|---|")
        for signal in brief.signals:
            lines.append(
                f"| {signal.signal_id} | {signal.price_usd or '—'} | "
                f"{signal.page_count or '—'} | {signal.trim or '—'} | "
                f"{signal.demand_band or '—'} |"
            )
        lines.append("")
        lines.append(
            "_Competitor books were studied as market signals only; no "
            "competitor artwork, text or layout is stored or reproduced._"
        )
    else:
        lines.append("_No research recorded._")

    lines.append(_heading("3. Positioning and claims"))
    if manifest.strategy and manifest.strategy.claims:
        lines.append("| Claim | Value | Confidence | Basis |")
        lines.append("|---|---|---|---|")
        for claim in manifest.strategy.claims:
            marker = "" if claim.confidence.is_evidence else " ⚠"
            lines.append(
                f"| {claim.label} | {claim.value} | {claim.confidence.value}{marker} "
                f"| {claim.basis or '—'} |"
            )
        lines.append("")
        lines.append("_⚠ marks a figure that is estimated or unknown, not observed._")
    else:
        lines.append("_No market claims recorded._")

    lines.append(_heading("4. Differentiation"))
    if manifest.strategy:
        lines.append(manifest.strategy.unique_angle or "_No unique angle stated._")
        lines.append("")
        for item in manifest.strategy.differentiators:
            lines.append(f"- {item}")
        if manifest.strategy.risks:
            lines.append("")
            lines.append("**Risks named:**")
            for risk in manifest.strategy.risks:
                lines.append(f"- {risk}")

    # 5-6. Pages and how they were made.
    lines.append(_heading("5. Pages"))
    lines.append(
        _kv("Format", f"{spec.trim}, {spec.paper}, {spec.page_count} pages, "
        f"{'bleed' if spec.bleed else 'no bleed'}")
    )
    lines.append(_kv("Art pages", len(spec.art_pages)))
    lines.append("")
    lines.append("| # | Page | Role | Subject | Cx | Attempts | Approved |")
    lines.append("|---|---|---|---|---|---|---|")
    for page in sorted(spec.pages, key=lambda p: p.index):
        attempts = manifest.attempts(page.page_id)
        approved = manifest.approved_for(page.page_id)
        lines.append(
            f"| {page.index} | {page.page_id} | {page.role} | "
            f"{page.subject or '—'} | {page.complexity} | {len(attempts) or '—'} "
            f"| {approved.asset_id if approved else '—'} |"
        )

    lines.append(_heading("6. Artefacts"))
    if manifest.artifacts:
        for artifact in sorted(manifest.artifacts, key=lambda a: a.kind.value):
            lines.append(
                _kv(
                    artifact.kind.value,
                    f"`{artifact.path}` ({artifact.bytes_size or '?'} bytes, "
                    f"`{artifact.content_hash[:19]}…`)",
                )
            )
    else:
        lines.append("_No artefacts built._")
    if manifest.artifact(ArtifactKind.CONTACT_SHEET) is None:
        lines.append("")
        lines.append(
            "_No contact sheet was produced. Open the interior PDF to review "
            "the pages visually._"
        )

    # 7-11. The listing.
    lines.append(_heading("7. Listing metadata"))
    if manifest.metadata:
        m = manifest.metadata
        lines += [
            _kv("Title", m.title),
            _kv("Subtitle", m.subtitle or "—"),
            _kv("Author", m.author or "—"),
            _kv("Language", m.language),
            _kv("Keywords", ", ".join(m.keywords) or "—"),
            _kv("Categories", ", ".join(m.categories) or "—"),
            "",
            "**Description**",
            "",
            m.description or "_none_",
        ]
    else:
        lines.append("_No metadata recorded._")

    lines.append(_heading("8. Price and economics"))
    if manifest.economics and manifest.economics.scenarios:
        econ = manifest.economics
        lines.append("| Scenario | List | Print cost | Net royalty | Viable |")
        lines.append("|---|---|---|---|---|")
        for scenario in econ.scenarios:
            chosen = " ✓" if scenario.label == econ.selected else ""
            lines.append(
                f"| {scenario.label}{chosen} | ${scenario.list_price_usd:.2f} | "
                f"${scenario.print_cost_usd:.2f} | "
                f"${scenario.net_royalty_usd:.2f} | "
                f"{'yes' if scenario.viable else 'NO'} |"
            )
        lines.append("")
        lines.append(
            f"**These are projections at confidence "
            f"`{econ.confidence.value}`, not income.** "
            + (
                "The royalty rate and printing-cost constants are assumed and "
                "have not been checked against KDP's own figures."
                if econ.confidence is Confidence.UNKNOWN
                else ""
            )
        )
    else:
        lines.append("_No pricing scenarios._")

    # 12. Disclosure — the field a human has to answer on the KDP form.
    lines.append(_heading("9. AI content disclosure"))
    disclosure = manifest.disclosure()
    lines.append(f"**{disclosure.statement()}**")
    lines.append("")
    if disclosure.generated_assets:
        lines.append(
            _kv("AI-generated assets", f"{len(disclosure.generated_assets)} shipping")
        )
    if disclosure.unclassified:
        lines.append(
            _kv("Unclassified", ", ".join(disclosure.unclassified))
        )
    lines.append("")
    lines.append(
        "_KDP requires disclosure of AI-**generated** text, images and "
        "translations, even where edited afterwards. AI-**assisted** work "
        "(brainstorming, grammar checking, refining human-made material) does "
        "not require disclosure._"
    )

    # 13. Provenance.
    lines.append(_heading("10. Provenance"))
    providers = sorted({a.provider for a in manifest.approved_assets if a.provider})
    tools = sorted({a.tool for a in manifest.approved_assets if a.tool})
    lines += [
        _kv("Shipping assets", len(manifest.approved_assets)),
        _kv("Total attempts recorded", len(manifest.assets)),
        _kv("Providers", ", ".join(providers) or "—"),
        _kv("Tools/models", ", ".join(tools) or "—"),
        _kv("Spec revision", manifest.spec_revision),
    ]
    retried = [
        p.page_id for p in spec.art_pages if len(manifest.attempts(p.page_id)) > 1
    ]
    if retried:
        lines.append(_kv("Pages that needed more than one attempt", ", ".join(retried)))

    # 14-17. Gate results, verbatim.
    lines.append(_heading("11. Gate results"))
    if results:
        lines.append("| Gate | Status | Blocking | Warnings |")
        lines.append("|---|---|---|---|")
        for result in results:
            warnings = sum(
                1 for f in result.findings if f.severity is Severity.MAJOR
            )
            lines.append(
                f"| {result.gate_id} | **{result.status.value.upper()}** | "
                f"{len(result.blockers) or '—'} | {warnings or '—'} |"
            )
        lines.append("")
        for result in results:
            if result.status is Status.PASS and not result.findings:
                continue
            lines.append(f"**{result.gate_id}** — {result.status.value.upper()}")
            lines.append("")
            for finding in result.findings:
                lines.append(
                    f"- `{finding.severity.value}` {finding.message}"
                    + (f" _({finding.subject})_" if finding.subject else "")
                )
            lines.append("")
    else:
        lines.append("_No gate results supplied._")

    # Inspection coverage: what was actually measured, so "passed QA" has a
    # meaning a reviewer can check rather than take on faith.
    lines.append(_heading("12. Inspection coverage"))
    coverage = _coverage(results)
    if coverage:
        for line in coverage:
            lines.append(line)
    else:
        lines.append("_No inspection evidence recorded._")

    # Warnings: findings that did not stop the book but that a person should
    # see before signing. These are the easiest thing for a generated report to
    # bury, and the most expensive to miss.
    lines.append(_heading("13. Outstanding warnings"))
    warnings = [
        (result.gate_id, finding)
        for result in results
        for finding in result.findings
        if finding.severity is Severity.MAJOR
    ]
    if warnings:
        lines.append(
            "These did **not** stop the book. Read them before approving — "
            "nothing downstream will raise them again."
        )
        lines.append("")
        for gate_id, finding in warnings:
            lines.append(
                f"- **{gate_id}** — {finding.message}"
                + (f" _({finding.subject})_" if finding.subject else "")
            )
    else:
        lines.append("_None._")

    # What was not checked. The section a reviewer most needs and is most
    # likely to be missing from a generated report.
    lines.append(_heading("14. What was NOT checked"))
    blocked = [r for r in results if r.status is Status.BLOCKED]
    lines.append("**Gates that could not run**")
    lines.append("")
    if blocked:
        for result in blocked:
            spec_entry = next((g for g in GATES if g.gate_id == result.gate_id), None)
            lines.append(f"- **{result.gate_id}** did not run.")
            if spec_entry:
                lines.append(f"  - Purpose: {spec_entry.purpose}")
            for finding in result.findings:
                lines.append(f"  - {finding.message}")
    else:
        lines.append("_None — every gate was evaluated._")

    lines.append("")
    lines.append("**Checks not attempted by the gates that did run**")
    lines.append("")

    unattempted = sorted(
        {
            note
            for result in results
            for note in result.evidence.get("not_checked", ())
        }
        | {
            finding.message.removeprefix("Not checked: ")
            for result in results
            for finding in result.findings
            if finding.code.endswith("not_checked")
        }
    )
    if any(
        f.code == "asset_qa.text_detection_unavailable"
        for r in results
        for f in r.findings
    ):
        unattempted.append(
            "accidental text on artwork — needs OCR, which is not installed"
        )
    unattempted.append(
        "whether any drawing resembles someone else's work closely enough to "
        "matter — a similarity score is not a copyright judgement, and no part "
        "of this pipeline makes one"
    )
    if spec_problem:
        unattempted.append(
            "the KDP print and royalty specifications themselves — "
            f"{spec_problem.rstrip('.')}"
        )
    for note in sorted(unattempted):
        lines.append(f"- {note}")

    # Recommendation, derived rather than composed.
    lines.append(_heading("15. Recommendation"))
    audit = next((r for r in results if r.gate_id == final_audit.GATE_ID), None)
    if audit is None:
        lines.append(
            "**NO RECOMMENDATION** — the final audit has not been run against "
            "this version of the book."
        )
    else:
        verdict = final_audit.verdict(audit)
        lines.append(f"**{verdict}**")
        lines.append("")
        if verdict == "FINAL_PASS":
            lines.append(
                "Every automated check that could run has passed. A human still "
                "has to decide."
            )
        elif verdict == "FINAL_FAIL":
            lines.append("Blocking findings remain. This book is not submittable.")
        else:
            lines.append(
                "The audit could not be completed. Resolve what it names above "
                "before asking anyone to approve this book."
            )

    # What approval actually requires, spelled out. A reviewer should never
    # have to infer what they are being asked to certify.
    lines.append(_heading("16. What approving this book means"))
    lines.append(
        "Approval is a statement about **this exact book**. Before recording "
        "one, confirm by looking at the artefacts yourself:"
    )
    lines.append("")
    lines.append(
        "- [ ] The interior PDF reads correctly end to end — open it, do not "
        "trust the page count alone."
    )
    lines.append("- [ ] The cover looks right, and the spine text sits on the spine.")
    lines.append(
        "- [ ] Every drawing is original, and none reproduces a recognisable "
        "character, trade dress or another artist's distinctive style. **No "
        "automated check can decide this.**"
    )
    lines.append(
        "- [ ] The listing describes the book that was actually built, and makes "
        "no claim it cannot support."
    )
    lines.append(
        f"- [ ] The AI-content answer above ({disclosure.statement()}) is what "
        "you will enter on the KDP form."
    )
    lines.append("- [ ] Any warnings in section 13 are acceptable to you.")
    if spec_problem:
        lines.append(
            "- [ ] **You have checked the trim, bleed, margin, spine and royalty "
            "figures against KDP's own documentation yourself**, because this "
            "pipeline could not."
        )
    lines.append("")
    lines.append(
        "Record the decision with:\n\n"
        f"```\npython3 -m kdp approve <manifest> approve --reviewer <your name>\n```"
    )
    lines.append("")
    lines.append(
        f"That binds the approval to fingerprint `{fingerprint[:23]}…`. "
        "**Changing the assets, either PDF, the listing or the price after "
        "approving voids it**, and packaging will refuse until a fresh decision "
        "is recorded. There is no flag that skips this step."
    )

    lines.append("")
    lines.append("---")
    lines.append(
        "_This package prepares a submission. It does not publish anything, and "
        "nothing in this pipeline can. Submission to KDP is a manual step._"
    )
    return "\n".join(lines) + "\n"


def _coverage(results: list[GateResult]) -> list[str]:
    """What the inspection layers actually measured, per gate."""
    lines: list[str] = []
    for result in results:
        evidence = result.evidence
        if "pages_inspected" in evidence:
            lines.append(
                f"- **{result.gate_id}** inspected the pixels of "
                f"{evidence['pages_inspected']} page(s); "
                f"{evidence.get('pages_uninspectable', 0)} could not be read. "
                "Checked: " + ", ".join(evidence.get("pixel_checks", ())) + "."
            )
        if "pages_hashed" in evidence:
            lines.append(
                f"- **{result.gate_id}** perceptually hashed "
                f"{evidence['pages_hashed']} page(s) against each other"
                + (
                    f" and against {evidence['catalogue_size']} catalogue entries"
                    if evidence.get("catalogue_size")
                    else ""
                )
                + f", flagging anything within {evidence.get('near_duplicate_distance')} "
                "bits of 64. A similarity score is a QA signal, not a copyright "
                "determination."
            )
        inspection = evidence.get("inspection")
        if isinstance(inspection, dict):
            lines.append(
                f"- **{result.gate_id}** opened the PDF: "
                f"{inspection.get('page_count')} page(s), "
                f"{len(inspection.get('pages_with_images', []))} carrying artwork, "
                f"{len(inspection.get('distinct_page_sizes', []))} distinct page "
                "size(s)."
            )
    return lines
