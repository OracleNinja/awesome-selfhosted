"""Command line entry point: ``python -m kdp``.

Each stage is reachable on its own so it can be tested, re-run and diffed
without invoking the ones around it. Exit codes are the contract for CI and
for the orchestrating agent:

    0  the stage passed
    1  the stage did not pass (a gate failed, or could not be evaluated)
    2  the command was used wrongly, or an input could not be read
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from datetime import datetime, timezone

from .errors import KDPError
from .gates import (
    GATES,
    asset_qa,
    cover_qa,
    final_audit,
    human_approval,
    interior_qa,
    kdp_compliance,
    metadata_compliance,
    originality,
    print_preflight,
    prompt_plan,
    provenance_complete,
    research_hygiene,
    spec_legality,
    strategy_differentiation,
)
from .gates.runner import StageReport, run_stage
from .models.approval import ApprovalRecord, ApprovalStatus
from .models.artifacts import Artifact, ArtifactKind
from .models.manifest import BookManifest
from .models.research import ResearchBrief
from .pipeline import STAGES, advance
from .specs import SPEC_REVISION, VERIFICATION, VERIFICATION_NOTE, is_verified

EXIT_OK = 0
EXIT_STOP = 1
EXIT_USAGE = 2


def _load_json(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise KDPError(f"cannot read {path}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise KDPError(f"{path} is not valid JSON: {exc}") from None


def _emit(report: StageReport, as_json: bool) -> int:
    if as_json:
        sys.stdout.write(report.to_json())
    else:
        sys.stdout.write(report.summary() + "\n")
    return EXIT_OK if report.passed else EXIT_STOP


def cmd_specs(args: argparse.Namespace) -> int:
    """Print the resolved print geometry for a trim/paper/page-count."""
    from .specs import cover_geometry, get_paper, get_trim, interior_geometry

    trim = get_trim(args.trim)
    paper = get_paper(args.paper)
    payload = {
        "spec_revision": SPEC_REVISION,
        "verification": VERIFICATION,
        "interior": interior_geometry(trim, args.pages, not args.no_bleed).to_dict(),
        "cover": cover_geometry(trim, paper, args.pages).to_dict(),
    }
    sys.stdout.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    if not is_verified():
        sys.stderr.write(f"warning: {VERIFICATION_NOTE}\n")
    return EXIT_OK


def cmd_research(args: argparse.Namespace) -> int:
    brief = ResearchBrief.from_dict(_load_json(args.brief))
    return _emit(run_stage("research", [research_hygiene.run(brief)]), args.json)


def cmd_concept(args: argparse.Namespace) -> int:
    manifest = BookManifest.read(args.manifest)
    result = spec_legality.run(
        manifest.spec, require_verified_specs=not args.allow_unverified_specs
    )
    return _emit(run_stage("concept", [result]), args.json)


def cmd_generation(args: argparse.Namespace) -> int:
    manifest = BookManifest.read(args.manifest)
    return _emit(
        run_stage("generation", [provenance_complete.run(manifest)]), args.json
    )


def cmd_qa(args: argparse.Namespace) -> int:
    manifest = BookManifest.read(args.manifest)
    catalogue = _load_json(args.catalogue) if args.catalogue else {}
    phashes = _load_json(args.catalogue_phashes) if args.catalogue_phashes else {}
    assets_dir = Path(args.manifest).parent / "assets"
    return _emit(
        run_stage(
            "qa",
            [
                originality.run(
                    manifest,
                    catalogue_hashes=catalogue,
                    catalogue_phashes=phashes,
                    require_perceptual=not args.exact_match_only,
                    assets_dir=assets_dir,
                ),
                asset_qa.run(
                    manifest,
                    require_pixel_inspection=not args.skip_pixel_inspection,
                    assets_dir=assets_dir,
                ),
            ],
        ),
        args.json,
    )


def cmd_production(args: argparse.Namespace) -> int:
    manifest = BookManifest.read(args.manifest)
    result = print_preflight.run(
        manifest, interior_pdf=args.interior, cover_pdf=args.cover
    )
    return _emit(run_stage("production", [result]), args.json)


def cmd_publishing_prep(args: argparse.Namespace) -> int:
    manifest = BookManifest.read(args.manifest)
    return _emit(
        run_stage(
            "publishing_prep",
            [
                metadata_compliance.run(manifest),
                kdp_compliance.run(
                    manifest, require_verified_policy=not args.allow_unverified_policy
                ),
            ],
        ),
        args.json,
    )


def cmd_disclosure(args: argparse.Namespace) -> int:
    """Report the KDP AI-content answer derived from asset provenance."""
    manifest = BookManifest.read(args.manifest)
    answer = manifest.disclosure()
    if args.json:
        sys.stdout.write(json.dumps(answer.to_dict(), sort_keys=True, indent=2) + "\n")
    else:
        sys.stdout.write(answer.statement() + "\n")
    return EXIT_OK if answer.complete else EXIT_STOP


def cmd_advance(args: argparse.Namespace) -> int:
    """Advance a manifest one stage, given a stored stage report."""
    manifest = BookManifest.read(args.manifest)
    report_data = _load_json(args.report)
    if report_data.get("stage") != manifest.stage:
        sys.stderr.write(
            f"report is for stage {report_data.get('stage')!r} but the book is "
            f"at {manifest.stage!r}\n"
        )
        return EXIT_STOP
    if not report_data.get("passed"):
        sys.stderr.write(f"stage {manifest.stage!r} did not pass; not advancing\n")
        return EXIT_STOP

    # Rebuild a minimal report rather than trusting the stored 'passed' flag
    # alone: advance() re-checks the invariants it cares about.
    from .gates.base import GateResult, Status

    results = [
        GateResult(
            gate_id=r["gate_id"],
            stage=r["stage"],
            status=Status(r["status"]),
            spec_revision=r.get("spec_revision", ""),
        )
        for r in report_data.get("results", [])
    ]
    advanced = advance(manifest, run_stage(manifest.stage, results))
    advanced.write(args.manifest)
    sys.stdout.write(f"{manifest.stage} -> {advanced.stage}\n")
    return EXIT_OK


def cmd_strategy(args: argparse.Namespace) -> int:
    manifest = BookManifest.read(args.manifest)
    result = strategy_differentiation.run(manifest.strategy)
    return _emit(run_stage("strategy", [result]), args.json)


def cmd_planning(args: argparse.Namespace) -> int:
    manifest = BookManifest.read(args.manifest)
    result = prompt_plan.run(manifest.spec, manifest.prompt_plan)
    return _emit(run_stage("planning", [result]), args.json)


def cmd_production_qa(args: argparse.Namespace) -> int:
    manifest = BookManifest.read(args.manifest)
    return _emit(
        run_stage("production_qa", [interior_qa.run(manifest), cover_qa.run(manifest)]),
        args.json,
    )


def cmd_final_audit(args: argparse.Namespace) -> int:
    manifest = BookManifest.read(args.manifest)
    result = final_audit.run(
        manifest, require_verified_specs=not args.allow_unverified_specs
    )
    if args.json:
        sys.stdout.write(run_stage("final_audit", [result]).to_json())
    else:
        sys.stdout.write(f"{final_audit.verdict(result)}\n")
        sys.stdout.write(run_stage("final_audit", [result]).summary() + "\n")
    return EXIT_OK if result.status.allows_advance else EXIT_STOP


def cmd_approval(args: argparse.Namespace) -> int:
    manifest = BookManifest.read(args.manifest)
    return _emit(run_stage("human_approval", [human_approval.run(manifest)]), args.json)


def cmd_generate(args: argparse.Namespace) -> int:
    """Run the generator against a provider. Metered providers cost money."""
    from .generation import generate_book
    from .providers import METERED, get_provider

    manifest = BookManifest.read(args.manifest)

    if args.auto:
        # --auto never spends. The router only ever selects a FREE provider, so
        # there is no --confirm-spend interaction to get wrong: authorising a
        # spend means naming the provider, which is the other branch entirely.
        if args.confirm_spend:
            sys.stderr.write(
                "--auto selects only free providers, so --confirm-spend has "
                "nothing to authorise. Name the provider you want to pay for "
                "instead: --provider <name> --confirm-spend.\n"
            )
            return EXIT_STOP
        from .providers.router import ProviderRouter

        provider = ProviderRouter()
        selected = "auto"
    else:
        provider = get_provider(args.provider)
        selected = args.provider

    if selected in METERED and not args.confirm_spend:
        sys.stderr.write(
            f"{selected!r} is a metered provider and this would spend "
            f"money on {len(manifest.spec.art_pages)} page(s). Re-run with "
            "--confirm-spend if that is intended.\n"
        )
        return EXIT_STOP

    if not provider.available():
        reason = getattr(provider, "unavailable_reason", lambda: "not available")()
        sys.stderr.write(f"provider {selected!r} is unavailable: {reason}\n")
        return EXIT_STOP

    book_dir = Path(args.manifest).parent
    if args.regenerate and selected in METERED and not args.confirm_spend:
        sys.stderr.write(
            "regenerating named pages on a metered provider still costs money; "
            "re-run with --confirm-spend\n"
        )
        return EXIT_STOP

    outcome = generate_book(
        manifest,
        provider,
        book_dir / "assets",
        max_attempts=args.max_attempts,
        regenerate=tuple(args.regenerate or ()),
    )
    outcome.manifest.write(args.manifest)

    if args.json:
        sys.stdout.write(json.dumps(outcome.to_dict(), sort_keys=True, indent=2) + "\n")
    else:
        sys.stdout.write(
            f"generated {len(outcome.generated)}, skipped {len(outcome.skipped)}, "
            f"failed {len(outcome.failed)}\n"
        )
        for page in outcome.failed:
            sys.stdout.write(f"  FAILED {page}\n")
    return EXIT_OK if outcome.ok else EXIT_STOP


def cmd_smoke_test(args: argparse.Namespace) -> int:
    """Buy exactly one page and measure it, before authorising the whole book."""
    from .providers import METERED, get_provider
    from .smoke import SmokeTestError, run_smoke_test

    manifest = BookManifest.read(args.manifest)
    provider = get_provider(args.provider)

    # Both guards fire before anything is sent, and in the order that costs
    # least to get wrong: intent first, then capability.
    if args.provider in METERED and not args.confirm_spend:
        sys.stderr.write(
            f"{args.provider!r} is metered. This buys exactly one image for "
            f"{args.page_id}, and no others. Re-run with --confirm-spend if "
            "that is intended.\n"
        )
        return EXIT_STOP

    if not provider.available():
        reason = getattr(provider, "unavailable_reason", lambda: "not available")()
        sys.stderr.write(f"provider {args.provider!r} is unavailable: {reason}\n")
        return EXIT_STOP

    out = Path(args.out or (Path(args.manifest).parent / "smoke" / args.page_id))
    before = Path(args.manifest).read_bytes()

    try:
        report = run_smoke_test(manifest, args.page_id, provider, out)
    except SmokeTestError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_USAGE

    # The manifest is read-only here by construction; this asserts it rather
    # than trusting it, because "did not touch the book" is the promise that
    # makes running this safe.
    if Path(args.manifest).read_bytes() != before:
        sys.stderr.write("error: the smoke test modified the manifest; aborting\n")
        return EXIT_USAGE

    if args.json:
        sys.stdout.write(json.dumps(report.to_dict(), sort_keys=True, indent=2) + "\n")
    else:
        sys.stdout.write(report.render() + "\n")
    return EXIT_OK if report.would_pass_asset_qa else EXIT_STOP


def cmd_register_asset(args: argparse.Namespace) -> int:
    """Bring an externally produced image into the pipeline with provenance.

    The path for a generator this package does not drive itself — an MCP tool
    an agent calls, or a human-drawn page. The image is inspected on the way in
    rather than on trust: an unreadable or defective file is rejected here
    instead of surfacing three stages later as a mystery.
    """
    from dataclasses import replace

    from .generation import asset_id_for
    from .inspection.image import ImageLoadError, inspect_image
    from .models.provenance import (
        AIRole,
        AssetKind,
        AssetProvenance,
        AssetStatus,
        Transformation,
        content_hash,
    )

    manifest = BookManifest.read(args.manifest)
    page = next(
        (p for p in manifest.spec.art_pages if p.page_id == args.page_id), None
    )
    if page is None:
        sys.stderr.write(
            f"{args.page_id!r} is not an art page in this book. Art pages: "
            + ", ".join(p.page_id for p in manifest.spec.art_pages[:10])
            + "\n"
        )
        return EXIT_USAGE

    source = Path(args.image)
    if not source.is_file():
        sys.stderr.write(f"no readable file at {args.image}\n")
        return EXIT_USAGE

    try:
        inspection = inspect_image(source)
    except ImageLoadError as exc:
        sys.stderr.write(f"refusing to register an image that cannot be read: {exc}\n")
        return EXIT_USAGE

    ai_role = AIRole(args.ai_role)
    if ai_role in (AIRole.ASSISTED, AIRole.GENERATED) and not args.model:
        sys.stderr.write(
            f"--model is required for ai-role {args.ai_role}: an AI claim with "
            "no record of what produced it is not an audit trail\n"
        )
        return EXIT_USAGE

    book_dir = Path(args.manifest).parent
    assets_dir = book_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    attempt = len(manifest.attempts(args.page_id)) + 1
    asset_id = asset_id_for(args.page_id, attempt)
    suffix = source.suffix.lstrip(".").lower() or "png"
    target = assets_dir / f"{asset_id}.{suffix}"
    data = source.read_bytes()
    target.write_bytes(data)

    prompt = (
        manifest.prompt_plan.for_page(args.page_id) if manifest.prompt_plan else None
    )
    record = AssetProvenance(
        asset_id=asset_id,
        page_id=args.page_id,
        kind=AssetKind.IMAGE,
        ai_role=ai_role,
        content_hash=content_hash(data),
        tool=args.model or args.provider,
        provider=args.provider,
        prompt_id=args.prompt_id or (prompt.prompt_id if prompt else None),
        prompt_version=args.prompt_version or (prompt.version if prompt else None),
        attempt=attempt,
        # PENDING, never approved: an asset arriving from outside does not get
        # to skip the QA verdict that a generated one has to pass.
        status=AssetStatus.PENDING,
        transformation=Transformation(args.transformation),
        width=inspection.width,
        height=inspection.height,
        file_format=suffix,
        path=f"assets/{target.name}",
        created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        note=args.note or "",
    )
    manifest.with_assets(manifest.assets + (record,)).write(args.manifest)

    problems = inspection.problems()
    sys.stdout.write(
        f"registered {asset_id} ({inspection.width}x{inspection.height}) as attempt "
        f"{attempt} for {args.page_id}, status pending\n"
    )
    for issue in problems:
        sys.stdout.write(f"  {'blocker' if issue.blocking else 'warning'}: {issue.message}\n")
    if problems:
        sys.stdout.write("  QA will see these. Registered anyway; the verdict is QA's.\n")
    return EXIT_OK


def _wrap(reason: str) -> list[str]:
    """One sentence per line, so a long reason stays readable in a table."""
    parts = [piece.strip() for piece in reason.replace(". ", ".\n").split("\n")]
    return [piece for piece in parts if piece] or [reason]


def cmd_providers(args: argparse.Namespace) -> int:
    """Report which generation providers could run right now, and why not."""
    from .providers import REGISTRY, get_provider

    rows = []
    for entry in sorted(REGISTRY, key=lambda r: (r.priority, r.id)):
        provider = get_provider(entry.id)
        available = provider.available()
        if available:
            reason = "ready" if entry.cost.routable else entry.note
        else:
            reason = getattr(provider, "unavailable_reason", lambda: "not available")()
        rows.append((entry.id, entry.cost.value.upper(), available, entry.priority, reason))

    if args.json:
        sys.stdout.write(
            json.dumps(
                [
                    {"provider": p, "cost": c, "available": a, "priority": n,
                     "reason": r}
                    for p, c, a, n, r in rows
                ],
                indent=2,
            )
            + "\n"
        )
        return 0

    width = max(len(r[0]) for r in rows)
    sys.stdout.write(
        f"{'Provider':<{width}}  {'Cost':<8} {'Available':<10} {'Priority':<9} Reason\n"
    )
    for name, cost, available, priority, reason in rows:
        head = f"{name:<{width}}  {cost:<8} {'YES' if available else 'NO':<10} {priority:<9} "
        first, *rest = _wrap(reason)
        sys.stdout.write(head + first + "\n")
        for line in rest:
            sys.stdout.write(" " * len(head) + line + "\n")

    routable = [r for r in rows if r[1] == "FREE" and r[2]]
    sys.stdout.write(
        "\n"
        + (
            f"`--auto` would use: {routable[0][0]}\n"
            if routable
            else "`--auto` has no free provider to use and would stop without "
            "spending anything.\n"
        )
    )
    return 0


def cmd_review_assets(args: argparse.Namespace) -> int:
    """Approve or reject one generated asset version."""
    from .generation import approve_asset, reject_asset

    manifest = BookManifest.read(args.manifest)
    if args.decision == "approve":
        updated = approve_asset(manifest, args.asset_id)
    else:
        if not args.reason:
            sys.stderr.write("rejecting an asset requires --reason\n")
            return EXIT_USAGE
        updated = reject_asset(manifest, args.asset_id, args.reason)
    updated.write(args.manifest)
    sys.stdout.write(f"{args.asset_id}: {args.decision}d\n")
    return EXIT_OK


def cmd_build(args: argparse.Namespace) -> int:
    """Assemble the interior and cover PDFs and record them on the manifest."""
    from .assembly import build_cover, build_interior

    manifest = BookManifest.read(args.manifest)
    book_dir = Path(args.manifest).parent
    build_dir = book_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    interior_path = build_dir / "interior.pdf"
    interior_path.write_bytes(build_interior(manifest, book_dir / "assets"))
    manifest = manifest.with_artifact(
        Artifact.from_file(ArtifactKind.INTERIOR_PDF, interior_path, created_at=stamp)
    )

    cover_path = build_dir / "cover.pdf"
    cover_path.write_bytes(build_cover(manifest, cover_art=args.cover_art))
    manifest = manifest.with_artifact(
        Artifact.from_file(ArtifactKind.COVER_PDF, cover_path, created_at=stamp)
    )

    manifest.write(args.manifest)
    sys.stdout.write(f"built {interior_path} and {cover_path}\n")
    return EXIT_OK


def cmd_review(args: argparse.Namespace) -> int:
    """Render the human review package."""
    from .review import build_review

    manifest = BookManifest.read(args.manifest)
    results = []
    if args.results:
        payload = _load_json(args.results)
        from .gates.base import Finding, GateResult, Severity, Status

        for item in payload if isinstance(payload, list) else payload.get("results", []):
            results.append(
                GateResult(
                    gate_id=item["gate_id"],
                    stage=item["stage"],
                    status=Status(item["status"]),
                    findings=tuple(
                        Finding(
                            code=f["code"],
                            severity=Severity(f["severity"]),
                            message=f["message"],
                            subject=f.get("subject"),
                            detail=f.get("detail", {}),
                        )
                        for f in item.get("findings", [])
                    ),
                    evidence=item.get("evidence", {}),
                    spec_revision=item.get("spec_revision", ""),
                )
            )

    text = build_review(manifest, results)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        sys.stdout.write(f"wrote {args.out}\n")
    else:
        sys.stdout.write(text)
    return EXIT_OK


def cmd_approve(args: argparse.Namespace) -> int:
    """Record a human decision. The only way past G14."""
    manifest = BookManifest.read(args.manifest)
    status = ApprovalStatus.APPROVED if args.decision == "approve" else ApprovalStatus.REJECTED
    record = ApprovalRecord(
        status=status,
        reviewer=args.reviewer,
        decided_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        book_fingerprint=manifest.production_fingerprint(),
        approved_artifacts=tuple(
            a.content_hash for a in sorted(manifest.artifacts, key=lambda x: x.kind.value)
        ),
        notes=args.notes or "",
    )
    from dataclasses import replace

    replace(manifest, approval=record).write(args.manifest)
    sys.stdout.write(
        f"{args.decision}d by {args.reviewer} "
        f"(fingerprint {record.book_fingerprint[:23]}…)\n"
    )
    return EXIT_OK


def cmd_package(args: argparse.Namespace) -> int:
    """Build the publication package. Refuses without a current approval."""
    from .package import build_package

    manifest = BookManifest.read(args.manifest)
    destination = args.out or str(Path(args.manifest).parent / "build" / "publication.zip")
    result = build_package(manifest, destination, require_approval=not args.no_approval)
    sys.stdout.write(f"wrote {result.path} ({len(result.entries)} entries)\n")
    sys.stdout.write(f"  {result.content_hash}\n")
    return EXIT_OK


def cmd_verify_specs(args: argparse.Namespace) -> int:
    """Print what must be confirmed before any book can be certified."""
    from .specs.revision import SOURCES, checklist, is_verified

    if args.json:
        sys.stdout.write(
            json.dumps(
                {name: record.to_dict() for name, record in SOURCES.items()},
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
    else:
        sys.stdout.write(checklist() + "\n")
    return EXIT_OK if is_verified() else EXIT_STOP


def cmd_gates(args: argparse.Namespace) -> int:
    """List every gate, what it checks, and what it needs to run."""
    if args.json:
        sys.stdout.write(
            json.dumps(
                [
                    {
                        "gate_id": g.gate_id,
                        "stage": g.stage,
                        "purpose": g.purpose,
                        "requires": list(g.requires),
                        "validator_version": g.validator_version,
                    }
                    for g in GATES
                ],
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
    else:
        for gate in GATES:
            sys.stdout.write(f"{gate.gate_id:32} [{gate.stage}]\n")
            sys.stdout.write(f"  {gate.purpose}\n")
            sys.stdout.write(f"  needs: {', '.join(gate.requires)}\n")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kdp",
        description="Gated pipeline for original illustrated KDP books.",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("specs", help="print resolved print geometry")
    p.add_argument("--trim", default="8.5x11")
    p.add_argument("--paper", default="bw_white")
    p.add_argument("--pages", type=int, default=60)
    p.add_argument("--no-bleed", action="store_true")
    p.set_defaults(func=cmd_specs)

    p = sub.add_parser("research", help="run G1 research hygiene")
    p.add_argument("brief", help="path to a research brief JSON file")
    p.set_defaults(func=cmd_research)

    p = sub.add_parser("concept", help="run G2 spec legality")
    p.add_argument("manifest")
    p.add_argument(
        "--allow-unverified-specs",
        action="store_true",
        help="proceed even though the print tables are unverified (development only)",
    )
    p.set_defaults(func=cmd_concept)

    p = sub.add_parser("generation", help="run G3 provenance completeness")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_generation)

    p = sub.add_parser("qa", help="run G4 originality")
    p.add_argument("manifest")
    p.add_argument("--catalogue", help="JSON map of content_hash -> prior book_id")
    p.add_argument(
        "--catalogue-phashes",
        help="JSON map of perceptual hash -> prior book_id, for near-duplicates",
    )
    p.add_argument(
        "--exact-match-only",
        action="store_true",
        help="skip near-duplicate detection; the gate then BLOCKS rather than "
        "passing, because only exact copies were checked",
    )
    p.add_argument(
        "--skip-pixel-inspection",
        action="store_true",
        help="skip the pixel pass; the gate then BLOCKS rather than passing",
    )
    p.set_defaults(func=cmd_qa)

    p = sub.add_parser("production", help="run G5 print preflight")
    p.add_argument("manifest")
    p.add_argument("--interior", help="path to the interior PDF")
    p.add_argument("--cover", help="path to the cover PDF")
    p.set_defaults(func=cmd_production)

    p = sub.add_parser(
        "publishing-prep", help="run G6 metadata compliance and G12 KDP compliance"
    )
    p.add_argument("manifest")
    p.add_argument("--allow-unverified-policy", action="store_true")
    p.set_defaults(func=cmd_publishing_prep)

    p = sub.add_parser("disclosure", help="report the KDP AI-content answer")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_disclosure)

    p = sub.add_parser("advance", help="advance a manifest one stage")
    p.add_argument("manifest")
    p.add_argument("report", help="path to a stored stage report JSON file")
    p.set_defaults(func=cmd_advance)

    p = sub.add_parser("strategy", help="run G7 strategy differentiation")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_strategy)

    p = sub.add_parser("planning", help="run G8 prompt plan")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_planning)

    p = sub.add_parser("production-qa", help="run G10 interior QA and G11 cover QA")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_production_qa)

    p = sub.add_parser("final-audit", help="run G13 the final publishing audit")
    p.add_argument("manifest")
    p.add_argument("--allow-unverified-specs", action="store_true")
    p.set_defaults(func=cmd_final_audit)

    p = sub.add_parser("approval", help="run G14 human approval")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_approval)

    p = sub.add_parser("generate", help="generate art pages through a provider")
    p.add_argument(
        "--auto",
        action="store_true",
        help="let the router pick the best available FREE provider; never spends",
    )
    p.add_argument("manifest")
    p.add_argument("--provider", default="mock")
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument(
        "--confirm-spend",
        action="store_true",
        help="required before using a metered provider",
    )
    p.add_argument(
        "--regenerate",
        nargs="+",
        metavar="PAGE_ID",
        help="redo these pages even though they are approved or awaiting a QA "
        "verdict. Pages must be named individually: there is no redo-everything "
        "switch, because naming them is what makes the spend deliberate.",
    )
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser(
        "smoke-test",
        help="generate ONE page and inspect it, before authorising a full run",
    )
    p.add_argument("manifest")
    p.add_argument("page_id", help="the single art page to generate")
    p.add_argument("--provider", default="mock")
    p.add_argument(
        "--confirm-spend",
        action="store_true",
        help="required for a metered provider, even for one image",
    )
    p.add_argument("--out", help="where to write it (default: <book>/smoke/<page>)")
    p.set_defaults(func=cmd_smoke_test)

    p = sub.add_parser(
        "register-asset",
        help="record an externally produced image (MCP tool, or hand-drawn)",
    )
    p.add_argument("manifest")
    p.add_argument("page_id")
    p.add_argument("image", help="path to the image file")
    p.add_argument("--provider", required=True, help="service or process that made it")
    p.add_argument("--model", help="model id; required unless --ai-role none")
    p.add_argument("--prompt-id")
    p.add_argument("--prompt-version")
    p.add_argument(
        "--ai-role",
        choices=("generated", "assisted", "none"),
        default="generated",
        help="drives the KDP disclosure answer; 'generated' is the safe default",
    )
    p.add_argument(
        "--transformation",
        choices=("generated", "repaired", "human_edited", "imported"),
        default="generated",
    )
    p.add_argument("--note")
    p.set_defaults(func=cmd_register_asset)

    p = sub.add_parser("providers", help="report which providers can run, and why not")
    p.set_defaults(func=cmd_providers)

    p = sub.add_parser("asset", help="approve or reject one generated asset version")
    p.add_argument("manifest")
    p.add_argument("decision", choices=("approve", "reject"))
    p.add_argument("asset_id")
    p.add_argument("--reason")
    p.set_defaults(func=cmd_review_assets)

    p = sub.add_parser("build", help="assemble the interior and cover PDFs")
    p.add_argument("manifest")
    p.add_argument("--cover-art", help="optional PNG for the front panel")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("review", help="render the human review package")
    p.add_argument("manifest")
    p.add_argument("--results", help="stored gate results JSON to include")
    p.add_argument("--out", help="write to this path instead of stdout")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("approve", help="record a human approval decision")
    p.add_argument("manifest")
    p.add_argument("decision", choices=("approve", "reject"))
    p.add_argument("--reviewer", required=True)
    p.add_argument("--notes")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("package", help="build the publication package")
    p.add_argument("manifest")
    p.add_argument("--out")
    p.add_argument(
        "--no-approval",
        action="store_true",
        help="build without a current approval (development only; never for a "
        "package anyone might submit)",
    )
    p.set_defaults(func=cmd_package)

    p = sub.add_parser("gates", help="list every gate and what it needs")
    p.set_defaults(func=cmd_gates)

    p = sub.add_parser(
        "verify-specs",
        help="show which KDP specification tables are unverified, and what to check",
    )
    p.set_defaults(func=cmd_verify_specs)

    p = sub.add_parser("stages", help="list the pipeline stages in order")
    p.set_defaults(func=lambda a: (sys.stdout.write("\n".join(STAGES) + "\n"), EXIT_OK)[1])

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KDPError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
