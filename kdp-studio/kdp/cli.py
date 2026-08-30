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
    return _emit(
        run_stage(
            "qa",
            [
                originality.run(
                    manifest,
                    catalogue_hashes=catalogue,
                    require_perceptual=not args.exact_match_only,
                ),
                asset_qa.run(
                    manifest, require_pixel_inspection=not args.skip_pixel_inspection
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
    provider = get_provider(args.provider)

    if args.provider in METERED and not args.confirm_spend:
        sys.stderr.write(
            f"{args.provider!r} is a metered provider and this would spend "
            f"money on {len(manifest.spec.art_pages)} page(s). Re-run with "
            "--confirm-spend if that is intended.\n"
        )
        return EXIT_STOP

    if not provider.available():
        reason = getattr(provider, "unavailable_reason", lambda: "not available")()
        sys.stderr.write(f"provider {args.provider!r} is unavailable: {reason}\n")
        return EXIT_STOP

    book_dir = Path(args.manifest).parent
    outcome = generate_book(
        manifest, provider, book_dir / "assets", max_attempts=args.max_attempts
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
        description="Gated pipeline for original low-content KDP books.",
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
        "--exact-match-only",
        action="store_true",
        help="record an exact-match-only result instead of blocking on Pillow",
    )
    p.add_argument(
        "--skip-pixel-inspection",
        action="store_true",
        help="record a record-level-only asset QA result instead of blocking",
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
    p.add_argument("manifest")
    p.add_argument("--provider", default="mock")
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument(
        "--confirm-spend",
        action="store_true",
        help="required before using a metered provider",
    )
    p.set_defaults(func=cmd_generate)

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
