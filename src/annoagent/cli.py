from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .interactive_cli import run_interactive_review
from .orchestrator import Orchestrator, format_validation_result
from .utils import STAGES


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_cli_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _report_output_path(config: dict[str, object], run_dir: Path) -> Path:
    report_format = str((config.get("report") or {}).get("format") or "html").lower()  # type: ignore[union-attr]
    suffix = ".pdf" if report_format == "pdf" else ".html"
    return run_dir / f"report{suffix}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AnnoAgent CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate config and environment")
    validate.add_argument("--config", required=True)

    run = subparsers.add_parser("run", help="Run the configured pipeline")
    run.add_argument("--config", required=True)
    run.add_argument("--from", dest="from_stage", choices=STAGES)
    run.add_argument("--to", dest="to_stage", choices=STAGES)
    run.add_argument("--force", action="store_true")

    report = subparsers.add_parser("report", help="Rebuild report from latest run")
    report.add_argument("--config", required=True)
    report.add_argument("--force", action="store_true")

    pdfmarkers = subparsers.add_parser("pdfmarkers", help="Run only the pdfmarkers stage")
    pdfmarkers.add_argument("--config", required=True)
    pdf_input = pdfmarkers.add_mutually_exclusive_group()
    pdf_input.add_argument("--pdf")
    pdf_input.add_argument("--pdf-dir")
    pdfmarkers.add_argument("--force", action="store_true")

    review_packets = subparsers.add_parser(
        "review-packets",
        help="Build review packets from existing subcluster outputs",
    )
    review_packets.add_argument("--config", required=True)
    review_packets.add_argument("--force", action="store_true")

    ontology_relations = subparsers.add_parser(
        "ontology-relations",
        help="Map review packet candidates onto Cell Ontology and export relations",
    )
    ontology_relations.add_argument("--config", required=True)
    ontology_relations.add_argument("--force", action="store_true")

    llm_compare = subparsers.add_parser(
        "llm-compare",
        help="Run reference-assisted LLM comparison for ontology candidate conflicts",
    )
    llm_compare.add_argument("--config", required=True)
    llm_compare.add_argument("--force", action="store_true")

    controller = subparsers.add_parser(
        "controller",
        help="Build a cluster-level next-action plan from current ontology and LLM outputs",
    )
    controller.add_argument("--config", required=True)
    controller.add_argument("--force", action="store_true")
    controller.add_argument("--phase", choices=["auto", "initial", "post_ontology", "post_compare"], default="auto")

    agent = subparsers.add_parser(
        "agent",
        aliases=["interactive"],
        help="Run interactive parent-level review and export reviewed cell annotations",
    )
    agent.add_argument("--config", required=True)
    agent.add_argument("--force", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = _repo_root()
    config = load_config(args.config, repo_root)

    if args.command == "pdfmarkers":
        if args.pdf:
            config["_runtime"]["pdf_override_files"] = [_resolve_cli_path(args.pdf)]
        if args.pdf_dir:
            config["_runtime"]["pdf_override_dir"] = _resolve_cli_path(args.pdf_dir)

    orchestrator = Orchestrator(repo_root, config)

    if args.command == "validate":
        result = orchestrator.validate()
        print(format_validation_result(result))
        return 0 if not result["errors"] else 1

    if args.command == "run":
        run_dir = orchestrator.run(from_stage=args.from_stage, to_stage=args.to_stage, force=args.force)
        print(f"Run completed in {run_dir}")
        return 0

    if args.command == "report":
        run_dir = orchestrator.run(from_stage="report", to_stage="report", force=args.force)
        print(f"Report generated at {_report_output_path(config, run_dir)}")
        return 0

    if args.command == "pdfmarkers":
        run_dir = orchestrator.run(
            from_stage="preflight",
            to_stage="preflight",
            force=args.force,
            validation_stages=["preflight", "pdfmarkers"],
        )
        orchestrator.run(from_stage="pdfmarkers", to_stage="pdfmarkers", force=args.force)
        orchestrator.run(from_stage="report", to_stage="report", force=True)
        print(f"PDF markers stage completed; report at {_report_output_path(config, run_dir)}")
        return 0

    if args.command == "review-packets":
        outputs = orchestrator.generate_review_packets(force=args.force)
        orchestrator.run(from_stage="report", to_stage="report", force=True)
        print(f"Review packets generated in {outputs['output_dir']}")
        return 0

    if args.command == "ontology-relations":
        outputs = orchestrator.generate_ontology_relations(force=args.force)
        orchestrator.run(from_stage="report", to_stage="report", force=True)
        print(f"Ontology relations generated in {outputs['output_dir']}")
        return 0

    if args.command == "llm-compare":
        outputs = orchestrator.generate_llm_compare(force=args.force)
        orchestrator.run(from_stage="report", to_stage="report", force=True)
        print(f"LLM compare results generated in {outputs['output_dir']}")
        return 0

    if args.command == "controller":
        outputs = orchestrator.generate_controller(force=args.force, phase=args.phase)
        orchestrator.run(from_stage="report", to_stage="report", force=True)
        print(f"Controller state generated in {outputs['output_dir']}")
        return 0

    if args.command in {"agent", "interactive"}:
        outputs = run_interactive_review(orchestrator=orchestrator, force=args.force)
        print(f"Reviewed parent annotations generated in {outputs['output_dir']}")
        return 0

    parser.print_help(sys.stderr)
    return 1
