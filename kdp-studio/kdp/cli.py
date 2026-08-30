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

from .errors import KDPError
from .gates import (
    metadata_compliance,
    originality,
    print_preflight,
    provenance_complete,
    research_hygiene,
    spec_legality,
)
from .gates.runner import StageReport, run_stage
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
    result = originality.run(
        manifest,
        catalogue_hashes=catalogue,
        require_perceptual=not args.exact_match_only,
    )
    return _emit(run_stage("qa", [result]), args.json)


def cmd_production(args: argparse.Namespace) -> int:
    manifest = BookManifest.read(args.manifest)
    result = print_preflight.run(
        manifest, interior_pdf=args.interior, cover_pdf=args.cover
    )
    return _emit(run_stage("production", [result]), args.json)


def cmd_publishing_prep(args: argparse.Namespace) -> int:
    manifest = BookManifest.read(args.manifest)
    return _emit(
        run_stage("publishing_prep", [metadata_compliance.run(manifest)]), args.json
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
    p.set_defaults(func=cmd_qa)

    p = sub.add_parser("production", help="run G5 print preflight")
    p.add_argument("manifest")
    p.add_argument("--interior", help="path to the interior PDF")
    p.add_argument("--cover", help="path to the cover PDF")
    p.set_defaults(func=cmd_production)

    p = sub.add_parser("publishing-prep", help="run G6 metadata compliance")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_publishing_prep)

    p = sub.add_parser("disclosure", help="report the KDP AI-content answer")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_disclosure)

    p = sub.add_parser("advance", help="advance a manifest one stage")
    p.add_argument("manifest")
    p.add_argument("report", help="path to a stored stage report JSON file")
    p.set_defaults(func=cmd_advance)

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
