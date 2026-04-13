from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .agent_router import route_agent_request
from .chat_cli import run_chat_session
from .config import load_config
from .interactive_cli import run_interactive_review
from .orchestrator import Orchestrator, format_validation_result
from .utils import GPTANNO_TOOLS, STAGES
from .worker_contracts import format_worker_contracts
from .worker_runtime import AVAILABLE_WORKERS, run_named_worker


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

    gptanno_tool = subparsers.add_parser(
        "gptanno-tool",
        help="Run one decomposed GPTAnno worker from the parent/subcluster backbone",
    )
    gptanno_tool.add_argument("--config", required=True)
    gptanno_tool.add_argument("--tool", required=True, choices=GPTANNO_TOOLS)
    gptanno_tool.add_argument("--force", action="store_true")

    agent = subparsers.add_parser(
        "agent",
        aliases=["interactive"],
        help="Run interactive parent-level review and export reviewed cell annotations",
    )
    agent.add_argument("--config", required=True)
    agent.add_argument("--force", action="store_true")

    ask = subparsers.add_parser(
        "ask",
        help="Parse and execute one natural-language agent request",
    )
    ask.add_argument("--config", required=True)
    ask.add_argument("--message")
    ask.add_argument("--reset-session", action="store_true")

    chat = subparsers.add_parser(
        "chat",
        help="Start a persistent natural-language chat session with the agent",
    )
    chat.add_argument("--config", required=True)
    chat.add_argument("--reset-session", action="store_true")

    ui = subparsers.add_parser(
        "ui",
        help="Start the local Streamlit workbench for AnnoAgent",
    )
    ui.add_argument("--config", required=True)
    ui.add_argument("--reset-session", action="store_true")
    ui.add_argument("--server-port", type=int, default=8501)
    ui.add_argument("--server-address", default="127.0.0.1")

    workers = subparsers.add_parser(
        "workers",
        help="Show the current worker contract inventory and deployment status",
    )
    workers.add_argument("--config", required=True)

    worker_run = subparsers.add_parser(
        "worker-run",
        help="Run one deployed worker through the normalized AnnoAgent worker runtime",
    )
    worker_run.add_argument("--config", required=True)
    worker_run.add_argument("--worker", required=True, choices=AVAILABLE_WORKERS)
    worker_run.add_argument("--force", action="store_true")
    worker_run.add_argument("--phase", choices=["auto", "initial", "post_ontology", "post_compare"], default="auto")

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

    if args.command == "gptanno-tool":
        outputs = orchestrator.generate_gptanno_tool(args.tool, force=args.force)
        print(f"GPTAnno tool '{args.tool}' completed.")
        if outputs.get("log"):
            print(f"Log: {outputs['log']}")
        print(outputs)
        return 0

    if args.command == "ask":
        message = args.message or input("AnnoAgent request> ").strip()
        result = route_agent_request(
            config=config,
            orchestrator=orchestrator,
            user_message=message,
            apply=True,
            reset_session=args.reset_session,
        )
        if result.get("tool_calls"):
            for item in result["tool_calls"]:
                print(f"Executed tool: {item['tool_name']}")
                print(f"Arguments: {item['arguments']}")
                tool_result = item.get("result") or {}
                if tool_result.get("message"):
                    print(f"Result: {tool_result['message']}")
                if tool_result.get("updated_config"):
                    print(f"Updated config: {tool_result['updated_config']}")
                if tool_result.get("updated_memory"):
                    print(f"Updated memory: {tool_result['updated_memory']}")
                if tool_result.get("executed_workers"):
                    print("Executed workers:")
                    for worker in tool_result["executed_workers"]:
                        label = worker.get("label") or worker.get("worker") or worker.get("tool")
                        print(f"  - {label}")
                if tool_result.get("next_step"):
                    print(f"Suggested next step: {tool_result['next_step']}")
                print("")
        if result.get("suggested_next_tools"):
            print("Suggested next actions:")
            for item in result["suggested_next_tools"]:
                print(f"  - {item['tool_name']}: {item['arguments']}")
            print("")
        if result.get("session_path"):
            print(f"Session: {result['session_path']}")
        if result.get("assistant_message"):
            print(result["assistant_message"])
        elif not result.get("tool_calls"):
            print("No tool call proposed.")
        return 0

    if args.command == "chat":
        return run_chat_session(
            orchestrator=orchestrator,
            reset_session=args.reset_session,
        )

    if args.command == "ui":
        env = os.environ.copy()
        env["ANNOAGENT_STREAMLIT_CONFIG"] = config["_meta"]["config_path"]
        env["ANNOAGENT_STREAMLIT_RESET_SESSION"] = "1" if args.reset_session else "0"
        matplotlib_cache = repo_root / ".cache" / "matplotlib"
        matplotlib_cache.mkdir(parents=True, exist_ok=True)
        env.setdefault("MPLCONFIGDIR", str(matplotlib_cache))
        env.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
        app_path = repo_root / "src" / "annoagent" / "streamlit_app.py"
        command = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(app_path),
            "--server.address",
            str(args.server_address),
            "--server.port",
            str(args.server_port),
        ]
        try:
            return subprocess.run(command, env=env, check=False).returncode
        except FileNotFoundError:
            print(
                "Streamlit is not installed in the current Python environment. "
                "Install it first, for example: `pip install -e .[ui]`",
                file=sys.stderr,
            )
            return 1

    if args.command == "workers":
        print(format_worker_contracts(repo_root))
        return 0

    if args.command == "worker-run":
        result = run_named_worker(
            orchestrator,
            args.worker,
            force=args.force,
            phase=args.phase,
        )
        print(f"Worker: {result.get('worker')}")
        print(f"Status: {result.get('status')}")
        print(f"Implementation: {result.get('implementation')}")
        if result.get("notes"):
            print("Notes:")
            for note in result["notes"]:
                print(f"  - {note}")
        if result.get("artifacts"):
            print("Artifacts:")
            for key, value in result["artifacts"].items():
                print(f"  - {key}: {value}")
        return 0

    if args.command in {"agent", "interactive"}:
        outputs = run_interactive_review(orchestrator=orchestrator, force=args.force)
        print(f"Reviewed parent annotations generated in {outputs['output_dir']}")
        return 0

    parser.print_help(sys.stderr)
    return 1
