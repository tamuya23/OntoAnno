from __future__ import annotations

import base64
import collections
import csv
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ontoanno.agent_memory import load_agent_memory
from ontoanno.agent_router import route_agent_request
from ontoanno.agent_session import reset_agent_session, session_path
from ontoanno.config import load_config
from ontoanno.external_evidence import (
    ExternalEvidenceError,
    _parse_page_ranges,
    extract_literature_evidence_from_pdf,
    save_uploaded_literature_pdf,
)
from ontoanno.interactive_cli import export_reviewed_parent_annotations
from ontoanno.orchestrator import Orchestrator
from ontoanno.review_packets import resolve_imported_parent_annotations
from ontoanno.utils import dump_json, load_json
from ontoanno.worker_runtime import AVAILABLE_WORKERS, run_named_worker, worker_prerequisite_status


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _config_path() -> Path:
    value = os.getenv("ONTOANNO_STREAMLIT_CONFIG")
    if value:
        return Path(value).expanduser().resolve()
    return (_repo_root() / "configs" / "agingv2.yaml").resolve()


def _default_reset_session() -> bool:
    return (os.getenv("ONTOANNO_STREAMLIT_RESET_SESSION") or "0") == "1"


WORKER_GUIDE: dict[str, dict[str, Any]] = {
    "inspect_dataset": {
        "category": "Dataset",
        "purpose": "Read basic configured dataset metadata without running analysis.",
        "inputs": "Project config fields for species, tissue, Seurat input, preprocessing, and clustering resolutions.",
        "outputs": "A read-only structured dataset summary returned to the Agent or manual worker console.",
        "manual_use": "Use when checking that the loaded config describes the intended dataset.",
    },
    "preprocess_parent": {
        "category": "Parent annotation",
        "purpose": "Prepare the input Seurat object for parent-level clustering and marker detection.",
        "inputs": "Configured Seurat RDS input and annotation preprocessing settings.",
        "outputs": "`annotate_parent/seurat_preprocessed.rds`.",
        "manual_use": "Run only when the input object or preprocessing-related config changed.",
    },
    "cluster_parent_markers": {
        "category": "Parent annotation",
        "purpose": "Cluster cells at configured parent resolutions and compute marker genes for each cluster.",
        "inputs": "`seurat_preprocessed.rds`, `annotation.parent_res`, and marker-detection settings. If `inputs.marker_genes_dir` is configured, this worker validates/copies the supplied marker folder and skips marker recomputation.",
        "outputs": "`seurat_clustered.rds` and `annotate_parent/marker_genes/`.",
        "manual_use": "Useful after changing parent resolutions. This can be slow unless an external marker folder is supplied.",
    },
    "annotate_parent_raw": {
        "category": "Parent annotation",
        "purpose": "Run GPTAnno parent annotation for each candidate parent resolution.",
        "inputs": "Parent cluster marker genes and tissue/species/ontology settings.",
        "outputs": "`annotation_parent.rds` and parent prediction figures under `prediction/`.",
        "manual_use": "Run after marker files change or when regenerating parent label candidates.",
    },
    "map_parent_ontology": {
        "category": "Parent annotation",
        "purpose": "Map raw parent annotation candidates onto ontology-compatible labels.",
        "inputs": "`annotation_parent.rds` and ontology policy settings.",
        "outputs": "`parent_ontology_mapping.csv` and `parent_ontology_mapping.rds`.",
        "manual_use": "Run after raw parent annotation or ontology restriction settings change.",
    },
    "select_parent_resolution": {
        "category": "Parent annotation",
        "purpose": "Score available parent resolutions and choose the current parent resolution.",
        "inputs": "`annotation_summary_scores.csv` inputs derived from parent annotation results.",
        "outputs": "`annotation_summary_scores.csv` and `best_parent_resolution.json`.",
        "manual_use": "Good standalone test worker. Run this after removing or changing forced resolution settings.",
    },
    "assign_parent_labels": {
        "category": "Parent annotation",
        "purpose": "Apply the selected parent resolution and final parent labels to the Seurat object.",
        "inputs": "`best_parent_resolution.json`, ontology mapping, clustered Seurat object.",
        "outputs": "`seurat_parent_annotated.rds` and manifest parent-assignment entries.",
        "manual_use": "Run after `select_parent_resolution` when switching to an already-computed resolution.",
    },
    "subcluster_find_markers": {
        "category": "Subcluster",
        "purpose": "Subset configured parent cell types, recluster them, and compute subcluster markers.",
        "inputs": "Parent-annotated Seurat object and `alignment.celltypes_to_subcluster`.",
        "outputs": "Subcluster marker files and intermediate subcluster Seurat artifacts.",
        "manual_use": "Run when adding/changing target parent cell types or subcluster resolutions.",
    },
    "subcluster_annotate_ontology": {
        "category": "Subcluster",
        "purpose": "Annotate subclusters using ontology-constrained labels.",
        "inputs": "Subcluster marker genes and ontology restriction settings.",
        "outputs": "Ontology-based subcluster annotation artifacts.",
        "manual_use": "Run after subcluster markers are available.",
    },
    "subcluster_annotate_inheritance": {
        "category": "Subcluster",
        "purpose": "Annotate subclusters with inherited parent-label context.",
        "inputs": "Subcluster marker genes plus parent annotation context.",
        "outputs": "Inheritance-based subcluster annotation artifacts.",
        "manual_use": "Run after subcluster markers are available; useful for comparing ontology vs inheritance workflows.",
    },
    "finalize_subcluster_annotations": {
        "category": "Subcluster",
        "purpose": "Merge ontology and inheritance subcluster outputs into the final subcluster annotation result.",
        "inputs": "Ontology and inheritance subcluster annotations.",
        "outputs": "`annotate_subclusters/seurat_ontology_annotated.rds` and final subcluster tables/manifest entries.",
        "manual_use": "Run after both subcluster annotation branches finish.",
    },
    "build_review_packets": {
        "category": "RAG check",
        "purpose": "Build per-cluster review packets from current parent annotations and marker evidence.",
        "inputs": "Parent annotation outputs, marker genes, selected labels.",
        "outputs": "`review_packets/` JSON/CSV packet files.",
        "manual_use": "First RAG worker. Blocked if parent annotation outputs do not exist.",
    },
    "decide_rag_check": {
        "category": "RAG check",
        "purpose": "Controller step that decides which clusters can pass and which need candidate maps, LLM compare, or human review.",
        "inputs": "Review packets plus artifacts from earlier RAG phases.",
        "outputs": "`controller/` state files with next actions.",
        "manual_use": "Use `Phase` to choose `initial`, `post_ontology`, or `post_compare`; `auto` is usually fine for quick testing.",
    },
    "build_candidate_map": {
        "category": "RAG check",
        "purpose": "Build candidate label maps and ontology/reference relationships for clusters selected by the controller.",
        "inputs": "Controller states whose next action is candidate-map construction.",
        "outputs": "Ontology relation and candidate-map artifacts.",
        "manual_use": "Usually run after `build_review_packets` and `decide_rag_check`.",
    },
    "retrieve_rag_evidence": {
        "category": "RAG check",
        "purpose": "Expose the evidence-retrieval layer used by RAG checks.",
        "inputs": "Candidate maps and external evidence memory.",
        "outputs": "Currently shares outputs with `build_candidate_map`; reference retrieval is embedded there.",
        "manual_use": "Mostly diagnostic for now; it may report skipped if no candidate-map outputs are needed.",
    },
    "run_llm_compare": {
        "category": "RAG check",
        "purpose": "Ask the LLM to compare annotation labels against marker and ontology evidence.",
        "inputs": "Controller-selected clusters, candidate maps, retrieved evidence.",
        "outputs": "LLM comparison results and summary counts.",
        "manual_use": "Run after candidate maps exist. This can call the LLM and may take time.",
    },
    "human_review": {
        "category": "RAG check",
        "purpose": "Load or report clusters that still require human review after automated checks.",
        "inputs": "Post-compare controller state and saved review decisions if present.",
        "outputs": "Human-review status and decision-file references.",
        "manual_use": "This Streamlit worker does not collect new decisions yet; it reports or loads saved decisions.",
    },
    "export_reviewed_parent_annotations": {
        "category": "Output",
        "purpose": "Apply saved human-review decisions and export reviewed parent annotations.",
        "inputs": "`reviewed_parent/interactive_decisions.json` and parent annotation outputs.",
        "outputs": "Reviewed parent Seurat object and reviewed annotation artifacts.",
        "manual_use": "Run only after human-review decisions exist.",
    },
    "generate_report": {
        "category": "Output",
        "purpose": "Generate the final OntoAnno HTML report from current parent, subcluster, RAG, and reviewed artifacts.",
        "inputs": "Current project artifacts and manifest entries.",
        "outputs": "Final report HTML and report figures/tables.",
        "manual_use": "Safe to run after major workflow steps complete; use Force to rebuild.",
    },
}


def _load_runtime(config_path: str) -> tuple[dict[str, Any], Orchestrator]:
    repo_root = _repo_root()
    config = load_config(config_path, repo_root)
    orchestrator = Orchestrator(repo_root, config)
    return config, orchestrator


def _refresh_runtime() -> tuple[dict[str, Any], Orchestrator]:
    return _load_runtime(str(_config_path()))


def _resolution_value(value: Any) -> str:
    text = str(value or "").strip()
    return text.removeprefix("res_")


def _algorithm_best_parent_resolution(orchestrator: Orchestrator) -> str:
    imported = resolve_imported_parent_annotations(orchestrator.config)
    scores_csv = Path(str(imported.get("annotation_scores_csv") or "")) if imported.get("annotation_scores_csv") else orchestrator.work_dir / "annotate_parent" / "annotation_summary_scores.csv"
    if not scores_csv.exists():
        return ""
    score_rows = _read_csv_rows(str(scores_csv))
    best_row: dict[str, Any] | None = None
    best_score: float | None = None
    for row in score_rows:
        try:
            score = float(row.get("composite_score"))
        except (TypeError, ValueError):
            continue
        if best_score is None or score > best_score:
            best_score = score
            best_row = row
    if best_row is None and score_rows:
        best_row = score_rows[0]
    return _resolution_value(best_row.get("resolution") if best_row else "")


def _selected_parent_resolution(config: dict[str, Any], orchestrator: Orchestrator) -> tuple[str, str]:
    annotation = config.get("annotation", {}) if isinstance(config.get("annotation"), dict) else {}
    forced_resolution = _resolution_value(annotation.get("forced_parent_resolution"))
    if forced_resolution:
        return forced_resolution, "forced"

    outputs = orchestrator.manifest.get("outputs", {}) if isinstance(orchestrator.manifest.get("outputs"), dict) else {}
    gptanno = outputs.get("gptanno_tools", {}) if isinstance(outputs.get("gptanno_tools"), dict) else {}
    for worker in ("assign_parent_labels", "select_parent_resolution"):
        payload = gptanno.get(worker, {}) if isinstance(gptanno.get(worker), dict) else {}
        selected = _resolution_value(payload.get("best_resolution_value") or payload.get("best_resolution"))
        if selected:
            return selected, "auto"

    best_json = orchestrator.work_dir / "annotate_parent" / "best_parent_resolution.json"
    if best_json.exists():
        payload = load_json(best_json)
        selected = _resolution_value(payload.get("best_resolution_value") or payload.get("best_resolution"))
        if selected:
            return selected, "auto"
    imported = resolve_imported_parent_annotations(config)
    selected = _resolution_value(imported.get("best_resolution"))
    if selected:
        return selected, "imported"
    return "", ""


def _project_state(config: dict[str, Any], orchestrator: Orchestrator) -> dict[str, Any]:
    state = orchestrator.state or {}
    stages = state.get("stages", {}) if isinstance(state.get("stages"), dict) else {}
    memory = load_agent_memory(config)
    policy = config.get("policy", {}) if isinstance(config.get("policy"), dict) else {}
    annotation = config.get("annotation", {}) if isinstance(config.get("annotation"), dict) else {}
    outputs = orchestrator.manifest.get("outputs", {}) if isinstance(orchestrator.manifest.get("outputs"), dict) else {}
    report = outputs.get("report", {}) if isinstance(outputs.get("report"), dict) else {}
    reviewed = outputs.get("reviewed_parent", {}) if isinstance(outputs.get("reviewed_parent"), dict) else {}
    configured_parent_res = annotation.get("parent_res")
    if isinstance(configured_parent_res, list):
        parent_resolution_options = [str(item).strip() for item in configured_parent_res if str(item).strip()]
    elif configured_parent_res in (None, "", []):
        parent_resolution_options = []
    else:
        parent_resolution_options = [str(configured_parent_res).strip()]
    algorithm_resolution = _algorithm_best_parent_resolution(orchestrator) or "unknown"
    selected_resolution, selected_resolution_source = _selected_parent_resolution(config, orchestrator)
    selected_resolution = selected_resolution or "unknown"
    return {
        "project": config["project"]["name"],
        "run_id": orchestrator.run_id,
        "parent_resolution_options": parent_resolution_options,
        "algorithm_parent_resolution": algorithm_resolution,
        "parent_resolution": selected_resolution,
        "parent_resolution_source": selected_resolution_source,
        "granularity": policy.get("granularity", "balanced"),
        "ontology": policy.get("ontology", True),
        "session_path": str(session_path(config)),
        "memory_markers": len(memory.get("custom_markers", [])),
        "memory_celltypes": len(memory.get("custom_celltypes", [])),
        "report_path": report.get("report_path") or report.get("report_html") or "",
        "reviewed_csv": reviewed.get("metadata_csv") or "",
        "reviewed_rds": reviewed.get("seurat_rds") or "",
        "stages": stages,
    }


def _ui_history_path(config: dict[str, Any]) -> Path:
    return Path(str(config["project"]["work_dir"])) / "ontoanno_ui_history.json"


def _legacy_ui_history_path(config: dict[str, Any]) -> Path:
    return Path(str(config["project"]["work_dir"])) / "agent_ui_history.json"


def _load_ui_history(config: dict[str, Any]) -> list[dict[str, Any]]:
    path = _ui_history_path(config)
    legacy_path = _legacy_ui_history_path(config)
    if not path.exists() and legacy_path.exists():
        path = legacy_path
    if path.exists():
        payload = load_json(path)
        if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
            messages: list[dict[str, Any]] = []
            for item in payload["messages"]:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "").strip()
                content = item.get("content")
                if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                    messages.append({"role": role, "content": content})
            return messages
    raw_session_path = session_path(config)
    if raw_session_path.exists():
        payload = load_json(raw_session_path)
        if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
            messages: list[dict[str, Any]] = []
            for item in payload["messages"]:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "").strip()
                content = item.get("content")
                if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                    messages.append({"role": role, "content": content})
            return messages
    return []


def _save_ui_history(config: dict[str, Any], messages: list[dict[str, Any]]) -> None:
    payload = {"messages": messages[-40:]}
    dump_json(_ui_history_path(config), payload)


def _chat_history() -> list[dict[str, Any]]:
    return st.session_state.setdefault("ui_chat_history", [])


def _append_chat_message(
    config: dict[str, Any],
    role: str,
    content: str,
    *,
    dedupe_last: bool = True,
) -> None:
    content = str(content or "").strip()
    role = "user" if role == "user" else "assistant"
    if not content:
        return
    history = _chat_history()
    if dedupe_last and history:
        last = history[-1]
        if last.get("role") == role and str(last.get("content") or "").strip() == content:
            return
    history.append({"role": role, "content": content})
    _save_ui_history(config, history)


def _append_chat_message_to_disk(
    config: dict[str, Any],
    role: str,
    content: str,
    *,
    dedupe_last: bool = True,
) -> None:
    content = str(content or "").strip()
    role = "user" if role == "user" else "assistant"
    if not content:
        return
    path = _ui_history_path(config)
    payload = load_json(path) if path.exists() else {}
    messages = payload.get("messages", []) if isinstance(payload, dict) else []
    messages = [item for item in messages if isinstance(item, dict)]
    if dedupe_last and messages:
        last = messages[-1]
        if last.get("role") == role and str(last.get("content") or "").strip() == content:
            return
    messages.append({"role": role, "content": content})
    dump_json(path, {"messages": messages[-40:]})


def _worker_history() -> list[dict[str, Any]]:
    return st.session_state.setdefault("ui_worker_history", [])


def _record_worker_event(title: str, payload: dict[str, Any]) -> None:
    history = _worker_history()
    history.insert(0, {"title": title, "payload": payload})
    del history[10:]


def _tail_log_lines(log_path: str, limit: int = 80) -> str:
    path = Path(log_path)
    if not path.exists():
        return "No log file found."
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    return "".join(lines[-limit:]).strip() or "(log is empty)"


def _collect_log_paths(orchestrator: Orchestrator) -> list[Path]:
    log_paths = sorted(path for path in orchestrator.run_dir.rglob("*.log") if path.is_file())
    unique_logs: list[Path] = []
    seen: set[str] = set()
    for path in log_paths:
        path_str = str(path)
        if path_str in seen:
            continue
        seen.add(path_str)
        unique_logs.append(path)
    return unique_logs


def _active_log_snapshot(orchestrator: Orchestrator, *, limit: int = 200) -> tuple[str, str]:
    log_paths = _collect_log_paths(orchestrator)
    if not log_paths:
        return "none", "No log output available yet for this run."
    active = max(log_paths, key=lambda path: (path.stat().st_mtime, str(path)))
    return str(active.relative_to(orchestrator.run_dir)), _tail_log_lines(str(active), limit=limit)


def _active_worker_label(log_name: str) -> str:
    if log_name in {"none", ""}:
        return "idle"
    label = Path(log_name).stem
    label = label.replace("gptanno-tool-", "")
    label = label.replace("parent_review_packets", "build_review_packets")
    label = label.replace("ontology_relations", "build_candidate_map")
    label = label.replace("llm_compare", "run_llm_compare")
    label = label.replace("controller", "decide_rag_check")
    label = label.replace("reviewed_parent", "human_review")
    return label


def _active_worker_for_job(orchestrator: Orchestrator, job: dict[str, Any]) -> str:
    started_at = float(job.get("started_at") or time.time())
    fresh_logs: list[Path] = []
    for path in _collect_log_paths(orchestrator):
        try:
            if path.stat().st_mtime >= started_at - 3:
                fresh_logs.append(path)
        except OSError:
            continue
    if not fresh_logs:
        return ""
    active = max(fresh_logs, key=lambda path: (path.stat().st_mtime, str(path)))
    return _active_worker_label(str(active.relative_to(orchestrator.run_dir)))


def _format_agent_progress_event(event: dict[str, Any]) -> str:
    stage = str(event.get("stage") or "").strip()
    if stage == "selected_tool":
        tool_name = str(event.get("tool_name") or "unknown").strip()
        lines = [f"Selected workflow: `{tool_name}`."]
        arguments = event.get("arguments") or {}
        if isinstance(arguments, dict):
            reason = str(arguments.get("reason") or "").strip()
            if reason:
                lines.append(f"Decision: {reason}")
        return "\n\n".join(lines)
    return ""


def _maybe_append_agent_decision_events(config: dict[str, Any], job: dict[str, Any] | None) -> None:
    if not job or not job.get("running"):
        return
    events = job.get("progress_events")
    if not isinstance(events, list) or not events:
        return
    consumed = int(job.get("consumed_progress_events") or 0)
    for event in events[consumed:]:
        if not isinstance(event, dict):
            continue
        message = _format_agent_progress_event(event)
        if message:
            _append_chat_message(config, "assistant", message)
    job["consumed_progress_events"] = len(events)


PHASE_WORKER_MAP: dict[str, list[str]] = {
    "Cluster": ["preprocess_parent", "cluster_parent_markers"],
    "Annotate": [
        "annotate_parent_raw",
        "map_parent_ontology",
        "select_parent_resolution",
        "assign_parent_labels",
        "annotate_parent",
    ],
    "Subcluster": [
        "subcluster_find_markers",
        "subcluster_annotate_ontology",
        "subcluster_annotate_inheritance",
        "finalize_subcluster_annotations",
        "annotate_subclusters",
    ],
    "RAG_Check": [
        "build_review_packets",
        "decide_rag_check",
        "build_candidate_map",
        "retrieve_rag_evidence",
        "run_llm_compare",
        "human_review",
        "review_packets",
        "ontology_relations",
        "llm_compare",
        "controller",
        "reviewed_parent",
    ],
    "Report": ["generate_report", "report"],
}


def _phase_completed(phase: str, orchestrator: Orchestrator) -> bool:
    outputs = orchestrator.manifest.get("outputs", {}) if isinstance(orchestrator.manifest.get("outputs"), dict) else {}
    stages = orchestrator.state.get("stages", {}) if isinstance(orchestrator.state.get("stages"), dict) else {}
    imported = resolve_imported_parent_annotations(orchestrator.config)
    if phase == "Cluster":
        gptanno = outputs.get("gptanno_tools", {}) if isinstance(outputs.get("gptanno_tools"), dict) else {}
        imported_markers = imported.get("markers_dir")
        parent_dir = orchestrator.work_dir / "annotate_parent"
        file_complete = (parent_dir / "seurat_clustered.rds").exists() and (parent_dir / "marker_genes").exists()
        return bool(gptanno.get("cluster_parent_markers")) or bool(imported_markers) or file_complete
    if phase == "Annotate":
        annotate_parent = outputs.get("annotate_parent", {}) if isinstance(outputs.get("annotate_parent"), dict) else {}
        gptanno = outputs.get("gptanno_tools", {}) if isinstance(outputs.get("gptanno_tools"), dict) else {}
        stage_done = str((stages.get("annotate_parent") or {}).get("status") or "") == "completed"
        imported_parent = imported.get("annotation_parent_rds")
        return bool(annotate_parent) or bool(gptanno.get("assign_parent_labels")) or bool(imported_parent) or stage_done
    if phase == "Subcluster":
        annotate_subclusters = outputs.get("annotate_subclusters", {}) if isinstance(outputs.get("annotate_subclusters"), dict) else {}
        gptanno = outputs.get("gptanno_tools", {}) if isinstance(outputs.get("gptanno_tools"), dict) else {}
        stage_done = str((stages.get("annotate_subclusters") or {}).get("status") or "") == "completed"
        subcluster_dir = orchestrator.work_dir / "annotate_subclusters"
        file_complete = any(
            (subcluster_dir / name).exists()
            for name in ["metadata_final.csv", "seurat_final_annotated.rds", "seurat_ontology_annotated.rds"]
        )
        return bool(annotate_subclusters) or bool(gptanno.get("finalize_subcluster_annotations")) or file_complete or stage_done
    if phase == "RAG_Check":
        output_complete = any(
            bool(outputs.get(key))
            for key in ["review_packets", "ontology_relations", "llm_compare", "controller", "reviewed_parent"]
        )
        run_dir_complete = any(
            (orchestrator.run_dir / rel_path).exists()
            for rel_path in [
                "review_packets/index.json",
                "ontology_relations/index.json",
                "llm_compare/index.json",
                "controller/index.json",
                "reviewed_parent/reviewed_parent.outputs.json",
                "reviewed_parent/cluster_decisions.csv",
            ]
        )
        return output_complete or run_dir_complete
    if phase == "Report":
        report = outputs.get("report", {}) if isinstance(outputs.get("report"), dict) else {}
        stage_done = str((stages.get("report") or {}).get("status") or "") == "completed"
        return bool(report.get("report_html") or report.get("report_pdf") or report.get("report_path")) or stage_done
    return False


def _render_stage_status(orchestrator: Orchestrator, *, is_running: bool) -> None:
    active_log_name, _ = _active_log_snapshot(orchestrator, limit=50)
    active_worker = _active_worker_label(active_log_name)
    rows: list[dict[str, str]] = []
    for phase, workers in PHASE_WORKER_MAP.items():
        status = "pending"
        # Completion must be sticky; log recency should not downgrade completed phases.
        if _phase_completed(phase, orchestrator):
            status = "completed"
        elif is_running and active_worker in workers:
            status = "running"
        elif phase == "Subcluster":
            status = "optional"
        rows.append(
            {
                "stage": phase,
                "status": status,
            }
        )
    completed_count = sum(1 for row in rows if row["status"] == "completed")
    total_count = len(rows)
    progress = completed_count / total_count if total_count else 0.0
    st.progress(progress, text=f"Pipeline progress: {completed_count}/{total_count} phases completed")

    status_styles = {
        "completed": {"bg": "#e8f5e9", "border": "#2e7d32", "text": "#1b5e20", "dot": "#2e7d32", "label": "Completed"},
        "running": {"bg": "#fff8e1", "border": "#f9a825", "text": "#8d6e00", "dot": "#f9a825", "label": "Running"},
        "failed": {"bg": "#ffebee", "border": "#c62828", "text": "#b71c1c", "dot": "#c62828", "label": "Failed"},
        "optional": {"bg": "#f3f4f6", "border": "#9ca3af", "text": "#4b5563", "dot": "#9ca3af", "label": "Optional"},
        "pending": {"bg": "#f8fafc", "border": "#cbd5e1", "text": "#475569", "dot": "#94a3b8", "label": "Pending"},
    }
    columns = st.columns(len(rows))
    for column, row in zip(columns, rows, strict=False):
        style = status_styles.get(row["status"], status_styles["pending"])
        with column:
            st.markdown(
                (
                    "<div style='display:flex;flex-direction:column;align-items:center;justify-content:center;"
                    f"padding:0.6rem 0.35rem;min-height:84px;border-radius:10px;text-align:center;"
                    f"background:{style['bg']};border:1px solid {style['border']};'>"
                    f"<span style='display:inline-block;width:12px;height:12px;border-radius:999px;background:{style['dot']};margin-bottom:0.45rem;'></span>"
                    f"<div style='font-weight:700;color:{style['text']};font-size:0.95rem;line-height:1.15;'>{row['stage']}</div>"
                    f"<div style='margin-top:0.35rem;font-size:0.82rem;font-weight:600;color:{style['text']};'>{style['label']}</div>"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )


def _collect_figure_paths(orchestrator: Orchestrator) -> list[Path]:
    figures_dir = orchestrator.run_dir / "report_assets" / "figures"
    if not figures_dir.exists():
        return []
    paths = [path for path in sorted(figures_dir.iterdir()) if path.suffix.lower() in {".png", ".jpg", ".jpeg"}]
    return paths


def _artifact_line(label: str, value: str) -> None:
    if not value:
        return
    st.markdown(f"**{label}**")
    st.code(value, language="bash")


def _read_csv_preview(path_str: str, limit: int = 12) -> list[dict[str, Any]]:
    path = Path(path_str)
    if not path.exists() or not path.is_file():
        return []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[dict[str, Any]] = []
        for idx, row in enumerate(reader):
            rows.append(dict(row))
            if idx + 1 >= limit:
                break
    return rows


def _csv_row_count(path_str: str) -> int | None:
    path = Path(path_str)
    if not path.exists() or not path.is_file():
        return None
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def _render_preview_table(title: str, path_str: str, *, limit: int = 12) -> None:
    rows = _read_csv_preview(path_str, limit=limit)
    if not rows:
        st.info(f"No preview available for {title.lower()}.")
        return
    st.markdown(f"**{title}**")
    st.dataframe(rows, width="stretch", hide_index=True)


def _render_pdf_embed(path: Path, *, height: int = 720) -> None:
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    iframe = (
        f'<iframe src="data:application/pdf;base64,{encoded}" '
        f'width="100%" height="{height}" type="application/pdf"></iframe>'
    )
    st.markdown(iframe, unsafe_allow_html=True)


def _read_csv_rows(path_str: str) -> list[dict[str, Any]]:
    path = Path(path_str)
    if not path.exists() or not path.is_file():
        return []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        return list(csv.DictReader(handle))


def _cluster_sort_key(cluster_id: str) -> tuple[int, str]:
    try:
        return (0, f"{int(cluster_id):06d}")
    except (TypeError, ValueError):
        return (1, str(cluster_id))


def _load_json_if_exists(path: Path) -> Any:
    if not path.exists() or not path.is_file():
        return None
    try:
        return load_json(path)
    except Exception:
        return None


def _controller_state_rows(run_dir: Path) -> list[dict[str, Any]]:
    index = _load_json_if_exists(run_dir / "controller" / "index.json")
    if not isinstance(index, dict):
        return []
    states: list[dict[str, Any]] = []
    for item in index.get("states", []):
        if not isinstance(item, dict):
            continue
        state_path = Path(str(item.get("state_json") or ""))
        state = _load_json_if_exists(state_path)
        if isinstance(state, dict):
            states.append(state)
        else:
            states.append(dict(item))
    states.sort(key=lambda state: _cluster_sort_key(str(state.get("cluster_id") or "")))
    return states


def _split_pipe_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value or "").split("|") if part.strip()]


def _gene_key(value: Any) -> str:
    return str(value or "").strip().upper()


def _dedupe_genes(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    genes: list[str] = []
    for value in values:
        gene = str(value or "").strip()
        key = _gene_key(gene)
        if not key or key in seen:
            continue
        seen.add(key)
        genes.append(gene)
    return genes


def _state_markers(state: dict[str, Any]) -> list[str]:
    markers = state.get("markers")
    if isinstance(markers, list) and markers:
        return _dedupe_genes(markers)
    packet_path = Path(str(state.get("packet_json") or ""))
    packet = _load_json_if_exists(packet_path)
    if not isinstance(packet, dict):
        return []
    marker_rows = packet.get("markers", [])
    if not isinstance(marker_rows, list):
        return []
    return _dedupe_genes([row.get("gene") for row in marker_rows if isinstance(row, dict)])


def _relation_payload_for_state(run_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    relation_path = Path(str(state.get("relation_json") or ""))
    if not relation_path.exists():
        cluster_id = str(state.get("cluster_id") or "")
        relation_path = run_dir / "ontology_relations" / "relations" / f"cluster-{cluster_id}.json"
    payload = _load_json_if_exists(relation_path)
    return payload if isinstance(payload, dict) else {}


def _llm_payload_for_state(run_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    result_path = Path(str(state.get("result_json") or ""))
    if not result_path.exists():
        cluster_id = str(state.get("cluster_id") or "")
        result_path = run_dir / "llm_compare" / "results" / f"cluster-{cluster_id}.json"
    payload = _load_json_if_exists(result_path)
    return payload if isinstance(payload, dict) else {}


def _rag_overview_rows(states: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for state in states:
        next_action = str(state.get("next_action") or "").strip()
        phase = str(state.get("phase") or "").strip()
        history = state.get("action_history", [])
        checked = phase in {"post_ontology", "post_compare"} or bool(state.get("relation_json")) or bool(state.get("result_json"))
        if isinstance(history, list):
            checked = checked or any(
                isinstance(item, dict) and str(item.get("phase") or "") in {"post_ontology", "post_compare"}
                for item in history
            )
        candidates = _split_pipe_values(state.get("focus_candidates"))
        if not candidates:
            current = str(state.get("current_label") or state.get("label") or "").strip()
            candidates = [current] if current else []
        if next_action in {"build_ontology_relations", "run_llm_compare"}:
            next_step = "Continue RAG check"
        elif next_action == "ask_user":
            next_step = "Human review"
        elif next_action == "finalize_llm_choice":
            next_step = "Use RAG choice"
        elif next_action == "finalize_keep_current":
            next_step = "Keep current label"
        else:
            next_step = next_action or "No action"
        rows.append(
            {
                "cluster": str(state.get("cluster_id") or ""),
                "current_label": str(state.get("current_label") or state.get("label") or ""),
                "potential_celltypes": " | ".join(candidates),
                "RAG_checked": "Yes" if checked else "No",
                "next_step": next_step,
            }
        )
    return rows


def _reference_marker_rows(relation: dict[str, Any]) -> list[dict[str, Any]]:
    reference_compare = relation.get("reference_compare", {}) if isinstance(relation.get("reference_compare"), dict) else {}
    rows: list[dict[str, Any]] = []
    for candidate in reference_compare.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        candidate_name = str(candidate.get("candidate") or "")
        for source_key, source_name in [("cellmarker", "CellMarker"), ("panglaodb", "PanglaoDB")]:
            source_payload = candidate.get(source_key, {})
            if not isinstance(source_payload, dict):
                continue
            markers = _dedupe_genes(
                list(source_payload.get("top_markers", []) or [])
                + list(source_payload.get("canonical_markers", []) or [])
            )
            rows.append(
                {
                    "candidate": candidate_name,
                    "source": source_name,
                    "reference_label": source_payload.get("reference_label") or "no hit",
                    "overlap_count": source_payload.get("overlap_count", 0),
                    "overlap_genes": ", ".join(_dedupe_genes(source_payload.get("overlap_genes", []) or [])) or "-",
                    "marker_genes": ", ".join(markers[:30]) if markers else "-",
                }
            )
    return rows


def _candidate_reference_sets(relation: dict[str, Any], candidate_name: str) -> tuple[list[str], list[str]]:
    reference_compare = relation.get("reference_compare", {}) if isinstance(relation.get("reference_compare"), dict) else {}
    for candidate in reference_compare.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("candidate") or "") != candidate_name:
            continue
        cellmarker = candidate.get("cellmarker", {}) if isinstance(candidate.get("cellmarker"), dict) else {}
        panglaodb = candidate.get("panglaodb", {}) if isinstance(candidate.get("panglaodb"), dict) else {}
        cellmarker_genes = _dedupe_genes(list(cellmarker.get("top_markers", []) or []) + list(cellmarker.get("canonical_markers", []) or []))
        panglao_genes = _dedupe_genes(list(panglaodb.get("top_markers", []) or []) + list(panglaodb.get("canonical_markers", []) or []))
        return cellmarker_genes, panglao_genes
    return [], []


def _render_marker_overlap_venn(cluster_genes: list[str], cellmarker_genes: list[str], panglao_genes: list[str]) -> None:
    a = {_gene_key(gene) for gene in cluster_genes if _gene_key(gene)}
    b = {_gene_key(gene) for gene in cellmarker_genes if _gene_key(gene)}
    c = {_gene_key(gene) for gene in panglao_genes if _gene_key(gene)}
    if not any([a, b, c]):
        st.info("No marker sets are available for overlap visualization.")
        return
    counts = {
        "a": len(a - b - c),
        "b": len(b - a - c),
        "c": len(c - a - b),
        "ab": len((a & b) - c),
        "ac": len((a & c) - b),
        "bc": len((b & c) - a),
        "abc": len(a & b & c),
    }
    svg = f"""
    <svg viewBox="0 0 640 360" width="100%" height="360" role="img" aria-label="Marker overlap">
      <rect width="640" height="360" fill="#ffffff"/>
      <circle cx="255" cy="155" r="112" fill="#4f8cff" fill-opacity="0.30" stroke="#2f5fba" stroke-width="2"/>
      <circle cx="385" cy="155" r="112" fill="#ff9f43" fill-opacity="0.32" stroke="#b86505" stroke-width="2"/>
      <circle cx="320" cy="245" r="112" fill="#4caf50" fill-opacity="0.28" stroke="#2e7d32" stroke-width="2"/>
      <text x="145" y="45" font-size="18" font-weight="700" fill="#2f5fba">Calculated markers</text>
      <text x="398" y="45" font-size="18" font-weight="700" fill="#b86505">CellMarker</text>
      <text x="282" y="345" font-size="18" font-weight="700" fill="#2e7d32">PanglaoDB</text>
      <text x="215" y="138" font-size="22" font-weight="700">{counts['a']}</text>
      <text x="412" y="138" font-size="22" font-weight="700">{counts['b']}</text>
      <text x="314" y="290" font-size="22" font-weight="700">{counts['c']}</text>
      <text x="313" y="126" font-size="22" font-weight="700">{counts['ab']}</text>
      <text x="263" y="218" font-size="22" font-weight="700">{counts['ac']}</text>
      <text x="365" y="218" font-size="22" font-weight="700">{counts['bc']}</text>
      <text x="314" y="190" font-size="24" font-weight="800">{counts['abc']}</text>
    </svg>
    """
    st.markdown(svg, unsafe_allow_html=True)
    overlaps = {
        "Calculated and CellMarker": sorted(a & b),
        "Calculated and PanglaoDB": sorted(a & c),
        "All three": sorted(a & b & c),
    }
    st.dataframe(
        [{"overlap": key, "genes": ", ".join(value) or "-"} for key, value in overlaps.items()],
        width="stretch",
        hide_index=True,
    )


def _review_draft_path(run_dir: Path) -> Path:
    return run_dir / "reviewed_parent" / "interactive_decisions_draft.json"


def _rerun_app() -> None:
    rerun = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
    if callable(rerun):
        rerun()


def _load_review_draft(run_dir: Path) -> dict[str, dict[str, Any]]:
    payload = _load_json_if_exists(_review_draft_path(run_dir))
    if not isinstance(payload, list):
        return {}
    return {
        str(item.get("cluster_id") or ""): item
        for item in payload
        if isinstance(item, dict) and str(item.get("cluster_id") or "").strip()
    }


def _save_review_draft(run_dir: Path, decisions: dict[str, dict[str, Any]]) -> None:
    rows = [decisions[key] for key in sorted(decisions, key=_cluster_sort_key)]
    dump_json(_review_draft_path(run_dir), rows)


def _human_review_states(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [state for state in states if str(state.get("next_action") or "") == "ask_user"]


def _decision_from_state(state: dict[str, Any], *, final_label: str, selection_source: str, user_note: str = "") -> dict[str, Any]:
    return {
        "cluster_id": str(state.get("cluster_id") or ""),
        "current_label": str(state.get("current_label") or state.get("label") or ""),
        "final_label": final_label,
        "selection_source": selection_source,
        "status": str(state.get("llm_status") or ""),
        "user_note": user_note,
        "focus_candidates": _split_pipe_values(state.get("focus_candidates")),
        "result_json": str(state.get("result_json") or ""),
    }


def _assemble_review_decisions(states: list[dict[str, Any]], draft: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    decisions: list[dict[str, Any]] = []
    missing: list[str] = []
    for state in states:
        cluster_id = str(state.get("cluster_id") or "")
        current_label = str(state.get("current_label") or state.get("label") or "")
        next_action = str(state.get("next_action") or "")
        if next_action == "ask_user":
            draft_decision = draft.get(cluster_id)
            if not draft_decision:
                missing.append(cluster_id)
                continue
            decisions.append(draft_decision)
        elif next_action == "finalize_llm_choice":
            final_label = str(state.get("recommended_label") or state.get("llm_best_candidate") or current_label)
            decisions.append(_decision_from_state(state, final_label=final_label, selection_source="controller_llm_choose"))
        elif next_action == "finalize_keep_current":
            decisions.append(_decision_from_state(state, final_label=current_label, selection_source="controller_keep_current"))
        else:
            decisions.append(_decision_from_state(state, final_label=current_label, selection_source=f"controller_{next_action or 'unknown'}"))
    decisions.sort(key=lambda item: _cluster_sort_key(str(item.get("cluster_id") or "")))
    return decisions, missing


def _cluster_annotation_rows(
    rows: list[dict[str, Any]],
    *,
    cluster_col: str,
    label_col: str,
    filter_col: str | None = None,
    filter_value: str | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        if filter_col and filter_value is not None:
            if str(row.get(filter_col) or "").strip() != filter_value:
                continue
        cluster = str(row.get(cluster_col) or "").strip()
        if not cluster:
            continue
        grouped[cluster].append(row)

    result: list[dict[str, Any]] = []
    for cluster, bucket in sorted(grouped.items(), key=lambda item: item[0]):
        labels = [str(item.get(label_col) or "").strip() for item in bucket if str(item.get(label_col) or "").strip()]
        top_label = ""
        if labels:
            top_label = collections.Counter(labels).most_common(1)[0][0]
        result.append(
            {
                "cluster": cluster,
                "cleaned_label": top_label or "unannotated",
                "cells": len(bucket),
            }
        )
    return result


def _prediction_figure_candidate_paths(prediction_dir: Path, resolution: str) -> list[Path]:
    names = [resolution]
    if resolution.startswith("res_"):
        names.append(resolution.replace("res_", ""))
    else:
        names.append(f"res_{resolution}")
    candidates: list[Path] = []
    for name in names:
        for suffix in [".png", ".jpg", ".jpeg", ".pdf"]:
            candidates.append(prediction_dir / f"{name}{suffix}")
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _parent_annotation_preview_spec_path(orchestrator: Orchestrator, resolution: str) -> Path:
    token = resolution.replace("/", "_")
    return orchestrator.work_dir / "annotate_parent" / "ui_preview" / f"{token}.json"


def _parent_annotation_preview_png_path(orchestrator: Orchestrator, resolution: str) -> Path:
    token = resolution.replace("/", "_")
    return orchestrator.work_dir / "annotate_parent" / "ui_preview" / f"{token}.png"


def _subcluster_annotation_preview_root(orchestrator: Orchestrator, selected_root_name: str, selected_celltype: str, workflow_dir_name: str) -> Path:
    token = f"{selected_root_name}_{selected_celltype}_{workflow_dir_name}".replace("/", "_")
    return orchestrator.work_dir / "annotate_subclusters" / "ui_preview" / token


def _reviewed_parent_preview_paths(orchestrator: Orchestrator) -> tuple[Path, Path]:
    root = orchestrator.run_dir / "reviewed_parent" / "ui_preview"
    return root / "reviewed_parent_preview.json", root / "reviewed_parent_preview.png"


def _ensure_parent_annotation_preview(orchestrator: Orchestrator, resolution: str) -> tuple[Path | None, str | None]:
    parent_dir = orchestrator.work_dir / "annotate_parent"
    seurat_rds = parent_dir / "seurat_parent_annotated.rds"
    mapping_csv = parent_dir / "parent_ontology_mapping.csv"
    output_png = _parent_annotation_preview_png_path(orchestrator, resolution)
    spec_path = _parent_annotation_preview_spec_path(orchestrator, resolution)

    if not seurat_rds.exists() or not mapping_csv.exists():
        return None, "Parent annotation preview inputs are missing."

    newest_source = max(seurat_rds.stat().st_mtime, mapping_csv.stat().st_mtime)
    if output_png.exists() and output_png.stat().st_mtime >= newest_source:
        return output_png, None

    dump_json(
        spec_path,
        {
            "gptanno_path": str(orchestrator.config["_runtime"]["gptanno_path"]),
            "seurat_rds": str(seurat_rds),
            "mapping_csv": str(mapping_csv),
            "resolution": resolution,
            "output_png": str(output_png),
        },
    )
    command = [
        str(orchestrator.config["_runtime"]["rscript"]),
        str(orchestrator.repo_root / "scripts" / "render_parent_annotation_preview.R"),
        str(spec_path),
    ]
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        return None, process.stdout.strip() or f"Preview render failed with exit code {process.returncode}."
    if not output_png.exists():
        return None, "Preview render finished but no PNG was created."
    return output_png, None


def _ensure_subcluster_annotation_preview(
    orchestrator: Orchestrator,
    *,
    selected_root_name: str,
    selected_celltype: str,
    workflow_dir_name: str,
    workflow_dir: Path,
    resolution: str,
) -> tuple[Path | None, Path | None, str | None]:
    seurat_rds = workflow_dir.parent / "seurat_subset.rds"
    summary_rds = workflow_dir / f"annotation_summary_res_{resolution}.rds"
    preview_root = _subcluster_annotation_preview_root(orchestrator, selected_root_name, selected_celltype, workflow_dir_name)
    output_png = preview_root / f"res_{resolution}.png"
    output_csv = preview_root / f"res_{resolution}.csv"
    spec_path = preview_root / f"res_{resolution}.json"

    if not seurat_rds.exists() or not summary_rds.exists():
        return None, None, "Subcluster annotation preview inputs are missing."

    newest_source = max(seurat_rds.stat().st_mtime, summary_rds.stat().st_mtime)
    if output_png.exists() and output_csv.exists():
        if min(output_png.stat().st_mtime, output_csv.stat().st_mtime) >= newest_source:
            return output_png, output_csv, None

    dump_json(
        spec_path,
        {
            "gptanno_path": str(orchestrator.config["_runtime"]["gptanno_path"]),
            "seurat_rds": str(seurat_rds),
            "summary_rds": str(summary_rds),
            "resolution": resolution,
            "output_png": str(output_png),
            "output_csv": str(output_csv),
        },
    )
    command = [
        str(orchestrator.config["_runtime"]["rscript"]),
        str(orchestrator.repo_root / "scripts" / "render_subcluster_annotation_preview.R"),
        str(spec_path),
    ]
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        return None, None, process.stdout.strip() or f"Subcluster preview render failed with exit code {process.returncode}."
    if not output_png.exists() or not output_csv.exists():
        return None, None, "Subcluster preview render finished but expected files were not created."
    return output_png, output_csv, None


def _ensure_reviewed_parent_preview(
    orchestrator: Orchestrator,
    *,
    reviewed_outputs: dict[str, Any],
) -> tuple[Path | None, str | None]:
    seurat_rds = Path(str(reviewed_outputs.get("seurat_rds") or ""))
    cluster_col = str(reviewed_outputs.get("cluster_col") or "").strip()
    label_col = str(reviewed_outputs.get("label_col") or "").strip()
    spec_path, output_png = _reviewed_parent_preview_paths(orchestrator)

    if not seurat_rds.exists() or not cluster_col or not label_col:
        return None, "Reviewed parent preview inputs are missing."

    newest_source = seurat_rds.stat().st_mtime
    if output_png.exists() and output_png.stat().st_mtime >= newest_source:
        return output_png, None

    dump_json(
        spec_path,
        {
            "gptanno_path": str(orchestrator.config["_runtime"]["gptanno_path"]),
            "seurat_rds": str(seurat_rds),
            "cluster_col": cluster_col,
            "label_col": label_col,
            "output_png": str(output_png),
        },
    )
    command = [
        str(orchestrator.config["_runtime"]["rscript"]),
        str(orchestrator.repo_root / "scripts" / "render_reviewed_parent_preview.R"),
        str(spec_path),
    ]
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        return None, process.stdout.strip() or f"Reviewed parent preview render failed with exit code {process.returncode}."
    if not output_png.exists():
        return None, "Reviewed parent preview render finished but no PNG was created."
    return output_png, None


def _render_parent_annotation_tab(orchestrator: Orchestrator) -> None:
    outputs = orchestrator.manifest.get("outputs", {}) if isinstance(orchestrator.manifest.get("outputs"), dict) else {}
    gptanno = outputs.get("gptanno_tools", {}) if isinstance(outputs.get("gptanno_tools"), dict) else {}
    parent_dir = orchestrator.work_dir / "annotate_parent"
    imported = resolve_imported_parent_annotations(orchestrator.config)
    scores_csv = str(imported.get("annotation_scores_csv") or (parent_dir / "annotation_summary_scores.csv"))
    mapping_csv = str((parent_dir / "parent_ontology_mapping.csv"))
    best_resolution_json = parent_dir / "best_parent_resolution.json"
    prediction_dir = Path(str(imported.get("prediction_dir") or (parent_dir / "prediction")))
    if not Path(scores_csv).exists():
        st.info("No parent annotation outputs recorded yet.")
        return

    score_rows = _read_csv_rows(scores_csv)
    available_resolutions = [str(row.get("resolution") or "").strip() for row in score_rows if str(row.get("resolution") or "").strip()]
    selected_default = available_resolutions[0] if available_resolutions else "unknown"
    imported_best_resolution = str(imported.get("best_resolution") or "").strip()
    if imported_best_resolution:
        selected_default = imported_best_resolution
    elif best_resolution_json.exists():
        best_payload = load_json(best_resolution_json)
        selected_default = str(best_payload.get("best_resolution") or selected_default)
    selected_resolution = st.selectbox(
        "Resolution",
        available_resolutions or [selected_default],
        index=(available_resolutions.index(selected_default) if selected_default in available_resolutions else 0),
        key="artifacts_parent_resolution",
    )

    top1, top2 = st.columns(2)
    with top1:
        st.metric("Available resolutions", len(available_resolutions))
    with top2:
        st.metric("Selected", selected_resolution)

    st.markdown("**Resolution scores**")
    st.dataframe(score_rows, width="stretch", hide_index=True)

    mapping_rows = _read_csv_rows(mapping_csv) if Path(mapping_csv).exists() else []
    filtered_mapping = [row for row in mapping_rows if str(row.get("resolution") or "").strip() == selected_resolution]
    cluster_rows: list[dict[str, str]] = []
    if filtered_mapping:
        selected_rows = [row for row in filtered_mapping if str(row.get("role") or "").strip() == "selected"]
        cluster_rows = [{"cluster": str(row.get("cluster") or ""), "cleaned_label": str(row.get("cleaned_label") or row.get("label") or "")} for row in selected_rows]
    elif imported.get("annotation_parent_rds"):
        review_summary = orchestrator.run_dir / "review_packets" / "summary.csv"
        if review_summary.exists():
            cluster_rows = [
                {"cluster": str(row.get("cluster_id") or ""), "cleaned_label": str(row.get("assigned_label") or "")}
                for row in _read_csv_rows(str(review_summary))
            ]
    if cluster_rows:
        st.markdown("**Cluster annotation results**")
        st.dataframe(cluster_rows[:100], width="stretch", hide_index=True)
    else:
        st.info("No standardized cluster annotation table is available yet. Run build_review_packets to generate a lightweight table from imported annotations.")

    st.markdown("**Prediction figure**")
    prediction_path = next((path for path in _prediction_figure_candidate_paths(prediction_dir, selected_resolution) if path.exists()), None)
    if imported.get("annotation_parent_rds"):
        preview_png = None
        preview_error = None
    else:
        preview_png, preview_error = _ensure_parent_annotation_preview(orchestrator, selected_resolution)
    if imported.get("annotation_parent_rds") and prediction_path is not None:
        preview_error = None
    if preview_png is not None:
        st.image(str(preview_png), caption=f"Parent annotation preview for {selected_resolution}")
    elif prediction_path is not None and prediction_path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
        st.image(str(prediction_path), caption=prediction_path.name)
    elif prediction_path is not None and prediction_path.suffix.lower() == ".pdf":
        if preview_error:
            st.warning(preview_error)
        _render_pdf_embed(prediction_path)
    elif preview_error:
        st.warning(preview_error)
    else:
        st.info("No prediction figure is available for the selected resolution.")

    with st.expander("Raw parent annotation files"):
        _artifact_line("Annotation scores CSV", scores_csv)
        _artifact_line("Ontology mapping CSV", mapping_csv)
        if best_resolution_json.exists():
            _artifact_line("Best resolution JSON", str(best_resolution_json))
        if preview_png is not None:
            _artifact_line("Rendered PNG preview", str(preview_png))
        if prediction_path is not None:
            _artifact_line("Selected prediction figure", str(prediction_path))
        clustered_rds = str((gptanno.get("cluster_parent_markers") or {}).get("clustered_rds") or "")
        parent_rds = str((gptanno.get("assign_parent_labels") or {}).get("parent_seurat_rds") or "")
        _artifact_line("Clustered Seurat RDS", clustered_rds)
        _artifact_line("Parent annotated Seurat RDS", parent_rds)


def _render_subcluster_tab(orchestrator: Orchestrator) -> None:
    outputs = orchestrator.manifest.get("outputs", {}) if isinstance(orchestrator.manifest.get("outputs"), dict) else {}
    annotate_subclusters = outputs.get("annotate_subclusters", {}) if isinstance(outputs.get("annotate_subclusters"), dict) else {}
    gptanno = outputs.get("gptanno_tools", {}) if isinstance(outputs.get("gptanno_tools"), dict) else {}
    subcluster_dir = orchestrator.work_dir / "annotate_subclusters"
    if not annotate_subclusters and not any(gptanno.get(name) for name in [
        "subcluster_find_markers",
        "subcluster_annotate_ontology",
        "subcluster_annotate_inheritance",
        "finalize_subcluster_annotations",
    ]):
        st.info("No subcluster outputs recorded yet.")
        return

    metadata_csv = subcluster_dir / "metadata_final.csv"
    final_rds = subcluster_dir / "seurat_final_annotated.rds"
    ontology_rds = subcluster_dir / "seurat_ontology_annotated.rds"

    subcluster_roots = [path for path in sorted(subcluster_dir.iterdir()) if path.is_dir() and path.name.startswith("subclusters_res")]
    selected_root = subcluster_roots[0] if subcluster_roots else None
    selected_root_name = selected_root.name if selected_root else "unknown"
    if subcluster_roots:
        selected_root_name = st.selectbox(
            "Parent resolution",
            [path.name for path in subcluster_roots],
            index=0,
            key="artifacts_subcluster_parent_resolution",
        )
        selected_root = next((path for path in subcluster_roots if path.name == selected_root_name), None)

    parent_celltypes = [path.name for path in sorted(selected_root.iterdir()) if path.is_dir()] if selected_root else []
    selected_celltype = parent_celltypes[0] if parent_celltypes else "unknown"
    if parent_celltypes:
        selected_celltype = st.selectbox(
            "Parent celltype",
            parent_celltypes,
            index=0,
            key="artifacts_subcluster_celltype",
        )

    workflow_options = {
        "Ontology": {
            "dir_name": "annotation_ontology",
            "label_col": "celltype_final",
        },
        "Inheritance": {
            "dir_name": "annotation_marker_inheritance",
            "label_col": "celltype_final_inherited",
        },
    }
    selected_workflow = st.selectbox(
        "Workflow",
        list(workflow_options.keys()),
        index=0,
        key="artifacts_subcluster_workflow",
    )
    workflow_info = workflow_options[selected_workflow]

    workflow_dir = (
        selected_root / selected_celltype / workflow_info["dir_name"]
        if selected_root and parent_celltypes
        else None
    )
    resolution_pdfs: list[Path] = []
    available_subcluster_resolutions: list[str] = []
    if workflow_dir and workflow_dir.exists():
        resolution_pdfs = sorted(workflow_dir.glob("annotation_plot_res_*.pdf"))
        for path in resolution_pdfs:
            token = path.stem.replace("annotation_plot_res_", "")
            if token:
                available_subcluster_resolutions.append(token)

    selected_subcluster_resolution = available_subcluster_resolutions[0] if available_subcluster_resolutions else "0.1"
    if available_subcluster_resolutions:
        selected_subcluster_resolution = st.selectbox(
            "Subcluster resolution",
            available_subcluster_resolutions,
            index=0,
            key="artifacts_subcluster_resolution",
        )

    top1, top2, top3 = st.columns(3)
    with top1:
        st.metric("Available parent celltypes", len(parent_celltypes))
    with top2:
        st.metric("Selected workflow", selected_workflow)
    with top3:
        st.metric("Selected subcluster resolution", selected_subcluster_resolution)

    preview_png = None
    preview_csv = None
    preview_error = None
    selected_pdf = None
    if workflow_dir and workflow_dir.exists():
        selected_pdf = workflow_dir / f"annotation_plot_res_{selected_subcluster_resolution}.pdf"
        if not selected_pdf.exists():
            selected_pdf = None
        preview_png, preview_csv, preview_error = _ensure_subcluster_annotation_preview(
            orchestrator,
            selected_root_name=selected_root_name,
            selected_celltype=selected_celltype,
            workflow_dir_name=workflow_info["dir_name"],
            workflow_dir=workflow_dir,
            resolution=selected_subcluster_resolution,
        )

    if preview_csv is not None and preview_csv.exists():
        cluster_rows = _read_csv_rows(str(preview_csv))
        cluster_table = [
            {
                "cluster": str(row.get("cluster") or ""),
                "cleaned_label": str(row.get("most_frequent_annotation") or ""),
            }
            for row in cluster_rows
        ]
        st.markdown("**Cluster annotation results**")
        st.dataframe(cluster_table[:100], width="stretch", hide_index=True)
    elif preview_error:
        st.info(preview_error)
    else:
        st.info("No subcluster annotation rows found for the selected view.")

    st.markdown("**Prediction figure**")
    if preview_png is not None and preview_png.exists():
        st.image(str(preview_png), caption=f"Subcluster annotation preview for {selected_subcluster_resolution}")
    elif selected_pdf is not None:
        _render_pdf_embed(selected_pdf)
    else:
        st.info("No subcluster figure is available for the selected view.")

    with st.expander("Raw subcluster files"):
        if metadata_csv.exists():
            _artifact_line("Final metadata CSV", str(metadata_csv))
        if preview_csv is not None and preview_csv.exists():
            _artifact_line("Rendered summary CSV", str(preview_csv))
        if preview_png is not None and preview_png.exists():
            _artifact_line("Rendered PNG preview", str(preview_png))
        if selected_pdf is not None:
            _artifact_line("Selected subcluster figure", str(selected_pdf))
        if final_rds.exists():
            _artifact_line("Final annotated Seurat RDS", str(final_rds))
        if ontology_rds.exists():
            _artifact_line("Ontology annotated Seurat RDS", str(ontology_rds))
        if subcluster_dir.exists():
            for path in sorted(subcluster_dir.rglob("*")):
                if path.is_file():
                    if path in {metadata_csv, final_rds, ontology_rds}:
                        continue
                    if preview_csv is not None and path == preview_csv:
                        continue
                    if preview_png is not None and path == preview_png:
                        continue
                    if selected_pdf is not None and path == selected_pdf:
                        continue
                    _artifact_line(path.relative_to(subcluster_dir).as_posix(), str(path))
        else:
            st.info("No subcluster directory found.")


def _render_rag_review_tab(orchestrator: Orchestrator) -> None:
    outputs = orchestrator.manifest.get("outputs", {}) if isinstance(orchestrator.manifest.get("outputs"), dict) else {}
    reviewed = outputs.get("reviewed_parent", {}) if isinstance(outputs.get("reviewed_parent"), dict) else {}
    review_packets = outputs.get("review_packets", {}) if isinstance(outputs.get("review_packets"), dict) else {}
    ontology_relations = outputs.get("ontology_relations", {}) if isinstance(outputs.get("ontology_relations"), dict) else {}
    llm_compare = outputs.get("llm_compare", {}) if isinstance(outputs.get("llm_compare"), dict) else {}
    controller = outputs.get("controller", {}) if isinstance(outputs.get("controller"), dict) else {}
    rag_files_exist = any(
        (orchestrator.run_dir / rel_path).exists()
        for rel_path in [
            "controller/index.json",
            "review_packets/index.json",
            "ontology_relations/index.json",
            "llm_compare/index.json",
            "reviewed_parent/reviewed_parent.outputs.json",
        ]
    )
    if not any([reviewed, review_packets, ontology_relations, llm_compare, controller, rag_files_exist]):
        st.info("No RAG review outputs recorded yet.")
        return

    reviewed_csv = str(reviewed.get("metadata_csv") or "")
    reviewed_decisions = str(reviewed.get("cluster_decisions_csv") or reviewed.get("decisions_csv") or "")
    review_packets_summary = str(review_packets.get("summary_csv") or "")
    ontology_summary = str(ontology_relations.get("summary_csv") or "")
    llm_compare_summary = str(llm_compare.get("summary_csv") or "")
    controller_summary = str(controller.get("summary_csv") or "")
    if not review_packets_summary:
        review_packets_summary = str(orchestrator.run_dir / "review_packets" / "summary.csv")
    if not ontology_summary:
        ontology_summary = str(orchestrator.run_dir / "ontology_relations" / "summary.csv")
    if not llm_compare_summary:
        llm_compare_summary = str(orchestrator.run_dir / "llm_compare" / "summary.csv")
    if not controller_summary:
        controller_summary = str(orchestrator.run_dir / "controller" / "summary.csv")

    states = _controller_state_rows(orchestrator.run_dir)
    overview_rows = _rag_overview_rows(states)
    human_states = _human_review_states(states)
    draft = _load_review_draft(orchestrator.run_dir)

    st.markdown("**RAG review overview**")
    if overview_rows:
        st.dataframe(overview_rows, width="stretch", hide_index=True)
    elif controller_summary:
        fallback_rows = _read_csv_rows(controller_summary)
        slim_rows = [
            {
                "cluster": str(row.get("cluster_id") or ""),
                "current_label": str(row.get("current_label") or ""),
                "potential_celltypes": str(row.get("focus_candidates") or ""),
                "RAG_checked": "Yes" if str(row.get("phase") or "") in {"post_ontology", "post_compare"} else "No",
                "next_step": "Human review" if str(row.get("next_action") or "") == "ask_user" else ("Continue RAG check" if str(row.get("next_action") or "") in {"build_ontology_relations", "run_llm_compare"} else str(row.get("next_action") or "No action")),
            }
            for row in fallback_rows
        ]
        st.dataframe(slim_rows, width="stretch", hide_index=True)
    else:
        st.info("Controller summary is not available yet.")

    if human_states:
        st.markdown("**Interactive human review**")
        st.caption("Each unresolved cluster is reviewed once. Saved choices are kept as a draft until all unresolved clusters have a decision.")
        progress_done = len([state for state in human_states if str(state.get("cluster_id") or "") in draft])
        st.progress(progress_done / len(human_states), text=f"Human review decisions saved: {progress_done}/{len(human_states)}")
        cluster_options = [str(state.get("cluster_id") or "") for state in human_states]
        selected_cluster = st.selectbox(
            "Cluster requiring human review",
            cluster_options,
            index=0,
            key="rag_human_review_cluster",
        )
        selected_state = next((state for state in human_states if str(state.get("cluster_id") or "") == selected_cluster), human_states[0])
        selected_relation = _relation_payload_for_state(orchestrator.run_dir, selected_state)
        selected_llm = _llm_payload_for_state(orchestrator.run_dir, selected_state)
        selected_markers = _state_markers(selected_state)
        selected_candidates = _split_pipe_values(selected_state.get("focus_candidates"))
        current_label = str(selected_state.get("current_label") or selected_state.get("label") or "")
        if current_label and current_label not in selected_candidates:
            selected_candidates = [current_label, *selected_candidates]
        if not selected_candidates:
            selected_candidates = [current_label or "unannotated"]

        st.write(f"Current label: `{current_label or 'unannotated'}`")
        st.write("Calculated marker genes: " + (", ".join(selected_markers[:20]) if selected_markers else "not available"))
        selected_parsed = selected_llm.get("parsed_response", {}) if isinstance(selected_llm.get("parsed_response"), dict) else {}
        llm_reason = str(selected_state.get("llm_reason") or selected_parsed.get("reason") or "")
        if llm_reason:
            st.info(llm_reason)

        saved_decision = draft.get(selected_cluster, {})
        saved_label = str(saved_decision.get("final_label") or current_label or selected_candidates[0])
        default_index = selected_candidates.index(saved_label) if saved_label in selected_candidates else 0
        selected_label = st.selectbox(
            "Final annotation for this cluster",
            selected_candidates,
            index=default_index,
            key=f"rag_human_review_label_{selected_cluster}",
        )
        override_label = st.text_input(
            "Optional custom final label",
            value="" if saved_label in selected_candidates else saved_label,
            key=f"rag_human_review_override_{selected_cluster}",
            help="Use this only when none of the candidate labels is acceptable.",
        )
        note = st.text_area(
            "Review note",
            value=str(saved_decision.get("user_note") or ""),
            key=f"rag_human_review_note_{selected_cluster}",
            height=90,
        )
        final_label = override_label.strip() or selected_label
        save_col, export_col = st.columns(2)
        with save_col:
            if st.button("Save decision for this cluster", key=f"rag_human_review_save_{selected_cluster}"):
                draft[selected_cluster] = _decision_from_state(
                    selected_state,
                    final_label=final_label,
                    selection_source="streamlit_human_review",
                    user_note=note,
                )
                _save_review_draft(orchestrator.run_dir, draft)
                st.success(f"Saved decision for cluster {selected_cluster}: {final_label}")
                _rerun_app()
        decisions, missing = _assemble_review_decisions(states, draft)
        with export_col:
            if st.button(
                "Export reviewed annotations",
                key="rag_human_review_export",
                disabled=bool(missing),
                help="Enabled after every unresolved cluster has a saved decision.",
            ):
                try:
                    outputs = export_reviewed_parent_annotations(
                        config=orchestrator.config,
                        run_dir=orchestrator.run_dir,
                        decisions=decisions,
                    )
                    orchestrator.manifest.setdefault("outputs", {})["reviewed_parent"] = outputs
                    orchestrator._save_manifest()
                    orchestrator.clear_report_outputs()
                    st.success("Reviewed parent annotations exported.")
                    _rerun_app()
                except Exception as exc:
                    st.error(f"Export failed: {exc}")
        if missing:
            st.caption("Remaining unresolved clusters: " + ", ".join(sorted(missing, key=_cluster_sort_key)))

    reviewed_preview_png, reviewed_preview_error = _ensure_reviewed_parent_preview(
        orchestrator,
        reviewed_outputs=reviewed,
    ) if reviewed else (None, None)
    if reviewed_preview_png is not None:
        st.markdown("**Reviewed annotation figure**")
        st.image(str(reviewed_preview_png), caption="Reviewed parent annotation preview")
    elif reviewed_preview_error and reviewed:
        st.info(reviewed_preview_error)

    st.markdown("**RAG marker evidence**")
    evidence_states = [
        state for state in states
        if _relation_payload_for_state(orchestrator.run_dir, state)
    ]
    if evidence_states:
        evidence_cluster_options = [str(state.get("cluster_id") or "") for state in evidence_states]
        selected_evidence_cluster = st.selectbox(
            "Cluster evidence view",
            evidence_cluster_options,
            index=0,
            key="rag_evidence_cluster",
        )
        state = next((item for item in evidence_states if str(item.get("cluster_id") or "") == selected_evidence_cluster), evidence_states[0])
        relation = _relation_payload_for_state(orchestrator.run_dir, state)
        llm_payload = _llm_payload_for_state(orchestrator.run_dir, state)
        cluster_genes = _state_markers(state)
        st.write("Calculated marker genes: " + (", ".join(cluster_genes[:30]) if cluster_genes else "not available"))
        ref_rows = _reference_marker_rows(relation)
        if ref_rows:
            st.dataframe(ref_rows, width="stretch", hide_index=True)
            candidates = _split_pipe_values(state.get("focus_candidates"))
            if not candidates:
                candidates = sorted({str(row.get("candidate") or "") for row in ref_rows if str(row.get("candidate") or "").strip()})
            selected_candidate = st.selectbox(
                "Marker-overlap candidate",
                candidates,
                index=0,
                key=f"rag_overlap_candidate_{selected_evidence_cluster}",
            )
            cellmarker_genes, panglao_genes = _candidate_reference_sets(relation, selected_candidate)
            _render_marker_overlap_venn(cluster_genes, cellmarker_genes, panglao_genes)
        else:
            st.info("No reference marker rows are available for this cluster.")
        parsed = llm_payload.get("parsed_response", {}) if isinstance(llm_payload.get("parsed_response"), dict) else {}
        if parsed:
            with st.expander("LLM comparison note", expanded=False):
                st.write(str(parsed.get("reason") or ""))
                supporting = parsed.get("supporting_markers", [])
                weakening = parsed.get("weakening_markers", [])
                if supporting:
                    st.write("Supporting markers: " + ", ".join(str(item) for item in supporting))
                if weakening:
                    st.write("Weakening markers: " + ", ".join(str(item) for item in weakening))
    else:
        st.info("No per-cluster RAG evidence is available yet.")

    if reviewed_decisions and Path(reviewed_decisions).exists():
        with st.expander("Reviewed decisions", expanded=False):
            _render_preview_table("Reviewed cluster decisions", reviewed_decisions, limit=20)
    if reviewed_csv and Path(reviewed_csv).exists():
        with st.expander("Reviewed parent metadata", expanded=False):
            _render_preview_table("Reviewed parent metadata", reviewed_csv, limit=10)

    with st.expander("Raw RAG review files"):
        if reviewed_preview_png is not None:
            _artifact_line("Reviewed preview PNG", str(reviewed_preview_png))
        _artifact_line("Reviewed metadata CSV", reviewed_csv)
        _artifact_line("Cluster decisions CSV", reviewed_decisions)
        _artifact_line("Review packets summary CSV", review_packets_summary)
        _artifact_line("Candidate map summary CSV", ontology_summary)
        _artifact_line("LLM compare summary CSV", llm_compare_summary)
        _artifact_line("Controller summary CSV", controller_summary)


def _render_report_tab(orchestrator: Orchestrator) -> None:
    outputs = orchestrator.manifest.get("outputs", {}) if isinstance(orchestrator.manifest.get("outputs"), dict) else {}
    report = outputs.get("report", {}) if isinstance(outputs.get("report"), dict) else {}
    report_html = str(report.get("report_html") or report.get("report_path") or "")
    report_pdf = str(report.get("report_pdf") or "")
    figures = _collect_figure_paths(orchestrator)
    if not any([report_html, report_pdf]) and not figures:
        st.info("No report output recorded yet.")
        return

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Report ready", "Yes" if any([report_html, report_pdf]) else "No")
    with col2:
        st.metric("Figures", len(figures))

    report_html_path = Path(report_html) if report_html else None
    report_pdf_path = Path(report_pdf) if report_pdf else None

    if report_html_path and report_html_path.exists() and report_html_path.is_file():
        st.markdown("**Report preview**")
        report_html_content = report_html_path.read_text(encoding="utf-8", errors="replace")
        iframe = getattr(st, "iframe", None)
        if callable(iframe):
            report_data_uri = "data:text/html;base64," + base64.b64encode(
                report_html_content.encode("utf-8")
            ).decode("ascii")
            iframe(report_data_uri, height=900)
        else:
            # Compatibility fallback for Streamlit versions predating st.iframe.
            components.html(report_html_content, height=900, scrolling=True)
    elif report_pdf_path and report_pdf_path.exists() and report_pdf_path.is_file():
        st.markdown("**Report preview**")
        _render_pdf_embed(report_pdf_path, height=900)
    elif figures:
        selected = st.selectbox("Report figure", [path.name for path in figures], index=0, key="artifacts_report_figure")
        selected_path = next((path for path in figures if path.name == selected), None)
        if selected_path is not None:
            st.image(str(selected_path), caption=selected_path.name)

    with st.expander("Raw report files"):
        _artifact_line("HTML report", report_html)
        _artifact_line("PDF report", report_pdf)


def _render_worker_payload(payload: dict[str, Any]) -> None:
    st.write(f"Status: `{payload.get('status', '')}`")
    if payload.get("notes"):
        for note in payload["notes"]:
            st.write(f"- {note}")
    artifacts = payload.get("artifacts", {})
    if artifacts:
        st.json(artifacts)


def _render_worker_guide(worker: str) -> None:
    guide = WORKER_GUIDE.get(worker, {})
    if not guide:
        st.info("No guide is available for this worker yet.")
        return
    try:
        container = st.container(border=True)
    except TypeError:
        container = st.container()
    with container:
        st.markdown(f"**{guide.get('category', 'Worker')}**")
        st.write(str(guide.get("purpose") or ""))
        st.markdown(f"**Input:** {guide.get('inputs', 'Not documented yet.')}")
        st.markdown(f"**Output:** {guide.get('outputs', 'Not documented yet.')}")
        st.markdown(f"**Manual use:** {guide.get('manual_use', 'Use with care.')}")


def _render_worker_readiness(readiness: dict[str, Any]) -> None:
    notes = [str(item) for item in readiness.get("notes", []) if str(item).strip()]
    if readiness.get("ok"):
        st.success("Ready to run.")
        for note in notes:
            st.caption(note)
        return
    st.warning("Blocked: this worker is missing prerequisite artifacts.")
    missing = [str(item) for item in readiness.get("missing", []) if str(item).strip()]
    if notes:
        for note in notes:
            st.write(f"- {note}")
    if missing:
        with st.expander("Missing prerequisites", expanded=False):
            for item in missing:
                st.code(item)


def _render_external_evidence_tab(config: dict[str, Any]) -> None:
    st.markdown("**Extract literature evidence from uploaded PDF**")
    st.caption(
        "Text evidence is extracted with GPTAnno/PDF2markers for stricter sentence-level filtering. Selected pages are also rendered as images for figure-specific marker evidence from dot plots, heatmaps, UMAP labels, and legends."
    )
    uploaded_pdf = st.file_uploader(
        "Upload literature PDF",
        type=["pdf"],
        key="external_evidence_pdf_upload",
    )
    upload_col1, upload_col2, upload_col3 = st.columns([2, 1, 1])
    with upload_col1:
        page_range = st.text_input(
            "Pages to inspect",
            value="1-6",
            key="external_evidence_pdf_pages",
            help="Use a comma/range list, for example 1-4,7,10. Choose pages with figures or marker tables when possible.",
        )
    with upload_col2:
        dpi = st.number_input(
            "Render DPI",
            min_value=100,
            max_value=220,
            value=150,
            step=25,
            key="external_evidence_pdf_dpi",
        )
    with upload_col3:
        st.write("")
        st.write("")
        extract_clicked = st.button(
            "Extract PDF evidence",
            disabled=uploaded_pdf is None,
            key="external_evidence_extract_pdf",
        )
    if extract_clicked and uploaded_pdf is not None:
        pages = _parse_page_ranges(page_range, default_max_pages=6)
        if not pages:
            st.error("No valid pages selected.")
        elif len(pages) > 10:
            st.error("Please inspect at most 10 pages per extraction to keep the image LLM call stable.")
        else:
            try:
                pdf_path = save_uploaded_literature_pdf(
                    config=config,
                    filename=uploaded_pdf.name,
                    data=uploaded_pdf.getvalue(),
                )
                with st.spinner("Extracting text evidence with PDF2markers and figure evidence with the vision LLM..."):
                    result = extract_literature_evidence_from_pdf(
                        config=config,
                        pdf_path=pdf_path,
                        pages=pages,
                        dpi=int(dpi),
                    )
                orchestrator.clear_rag_dependent_outputs()
                st.success(
                    "Extracted "
                    f"{result.get('evidence_count', 0)} literature evidence entrie(s): "
                    f"{result.get('text_evidence_count', 0)} from PDF2markers text, "
                    f"{result.get('image_evidence_count', 0)} from page images."
                )
                if result.get("evidence"):
                    st.dataframe(
                        [
                            {
                                "source_type": item.get("source_type", ""),
                                "celltype": item.get("celltype", ""),
                                "original_celltype": item.get("original_celltype", ""),
                                "markers": ", ".join(item.get("markers", [])),
                                "figure_or_page": item.get("figure_or_page", ""),
                                "confidence": item.get("confidence", ""),
                                "note": item.get("note", ""),
                            }
                            for item in result["evidence"]
                        ],
                        width="stretch",
                        hide_index=True,
                    )
            except ExternalEvidenceError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"PDF evidence extraction failed: {exc}")

    memory = load_agent_memory(config)
    custom_markers = memory.get("custom_markers", []) if isinstance(memory.get("custom_markers"), list) else []
    custom_celltypes = memory.get("custom_celltypes", []) if isinstance(memory.get("custom_celltypes"), list) else []

    user_rows: list[dict[str, Any]] = []
    literature_rows: list[dict[str, Any]] = []
    for item in custom_markers:
        if not isinstance(item, dict):
            continue
        row = {
            "type": "marker",
            "celltype": str(item.get("celltype") or ""),
            "original_celltype": str(item.get("original_celltype") or ""),
            "markers": ", ".join(str(x) for x in item.get("markers", []) if str(x).strip()),
            "note": str(item.get("note") or ""),
            "source": str(item.get("source") or "user"),
            "source_type": str(item.get("source_type") or ""),
            "evidence_count": item.get("evidence_count") or "",
            "figure_or_page": str(item.get("figure_or_page") or ""),
            "confidence": str(item.get("confidence") or ""),
        }
        source = str(item.get("source") or "user").strip().lower()
        if source in {"pdfmarker", "literature"}:
            literature_rows.append(row)
        else:
            user_rows.append(row)

    for item in custom_celltypes:
        if not isinstance(item, dict):
            continue
        row = {
            "type": "celltype",
            "celltype": str(item.get("celltype") or ""),
            "original_celltype": str(item.get("original_celltype") or ""),
            "markers": ", ".join(str(x) for x in item.get("markers", []) if str(x).strip()),
            "note": str(item.get("note") or ""),
            "source": str(item.get("source") or "user"),
            "source_type": str(item.get("source_type") or ""),
            "evidence_count": item.get("evidence_count") or "",
            "figure_or_page": str(item.get("figure_or_page") or ""),
            "confidence": str(item.get("confidence") or ""),
        }
        source = str(item.get("source") or "user").strip().lower()
        if source in {"pdfmarker", "literature"}:
            literature_rows.append(row)
        else:
            user_rows.append(row)

    top1, top2 = st.columns(2)
    with top1:
        st.metric("User evidence entries", len(user_rows))
    with top2:
        st.metric("Literature evidence entries", len(literature_rows))

    st.markdown("**User-provided evidence**")
    if user_rows:
        st.dataframe(user_rows, width="stretch", hide_index=True)
    else:
        st.info("No user-provided evidence stored yet.")

    st.markdown("**Literature-provided evidence**")
    if literature_rows:
        st.dataframe(literature_rows, width="stretch", hide_index=True)
    else:
        st.info("No literature-provided evidence yet.")


def _render_chat_result(result: dict[str, Any]) -> str:
    lines: list[str] = []
    for item in result.get("tool_calls", []):
        lines.append(f"Tool: {item['tool_name']}")
        arguments = item.get("arguments") or {}
        if isinstance(arguments, dict) and arguments.get("reason"):
            lines.append(f"Plan: {arguments['reason']}")
        lines.append(f"Arguments: {arguments}")
        tool_result = item.get("result") or {}
        if tool_result.get("message"):
            lines.append(f"Result: {tool_result['message']}")
        if tool_result.get("executed_workers"):
            lines.append("Executed workers:")
            for worker in tool_result["executed_workers"]:
                label = worker.get("label") or worker.get("worker") or worker.get("tool")
                lines.append(f"- {label}")
        if tool_result.get("next_step"):
            lines.append(f"Suggested next step: {tool_result['next_step']}")
        lines.append("")
    if result.get("suggested_next_tools"):
        lines.append("Suggested next actions:")
        for item in result["suggested_next_tools"]:
            lines.append(f"- {item['tool_name']}: {item['arguments']}")
        lines.append("")
    if result.get("assistant_message"):
        lines.append(result["assistant_message"])
    if not lines:
        return "No tool call proposed."
    return "\n".join(lines).strip()


def _render_chat_history_panel(messages: list[dict[str, Any]]) -> None:
    clean_messages = [
        item for item in messages
        if str(item.get("content") or "").strip()
    ]
    try:
        container = st.container(height=700, border=True)
    except TypeError:
        container = st.container()
    with container:
        if not clean_messages:
            st.info("No chat history yet.")
            return
        st.markdown("**Full history**")
        for item in clean_messages:
            role = str(item.get("role") or "assistant").strip().lower()
            content = str(item.get("content") or "").strip()
            with st.chat_message("user" if role == "user" else "assistant"):
                st.markdown(content)


def _agent_job_state() -> dict[str, Any] | None:
    return st.session_state.get("ui_agent_job")


def _tail_text(path: Path, *, max_lines: int = 60) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max_lines:])


def _agent_error_message(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    parts = [message]
    match = re.search(r"(?P<spec>/[^ \t\r\n]+/specs/gptanno-tool-(?P<tool>[A-Za-z0-9_-]+)\.json)", message)
    if match:
        spec_path = Path(match.group("spec"))
        log_path = spec_path.parent.parent / "logs" / f"gptanno-tool-{match.group('tool')}.log"
        tail = _tail_text(log_path)
        if tail:
            parts.append(f"Worker log: `{log_path}`")
            parts.append(f"Last worker log lines:\n```text\n{tail}\n```")
    return "\n\n".join(parts)


def _start_agent_job(message: str) -> None:
    config_path = str(_config_path())
    config_for_history = load_config(config_path, _repo_root())
    job: dict[str, Any] = {
        "message": message,
        "running": True,
        "result": None,
        "error": None,
        "started_at": time.time(),
        "announced_workers": [],
        "progress_events": [],
        "consumed_progress_events": 0,
    }

    def _target() -> None:
        thread_config: dict[str, Any] | None = None
        try:
            config, orchestrator = _load_runtime(config_path)
            thread_config = config

            def _progress(event: dict[str, Any]) -> None:
                job.setdefault("progress_events", []).append(event)

            result = route_agent_request(
                config=config,
                orchestrator=orchestrator,
                user_message=message,
                apply=True,
                reset_session=False,
                progress_callback=_progress,
            )
            job["result"] = result
        except Exception as exc:  # noqa: BLE001
            job["error"] = _agent_error_message(exc)
            job["traceback"] = traceback.format_exc()
        finally:
            if thread_config is not None:
                if job.get("error"):
                    final_text = f"Agent error: {job['error']}"
                    job["persisted_result"] = {"assistant_message": final_text}
                else:
                    persisted_result = job.get("result") or {"assistant_message": "No result returned."}
                    final_text = _render_chat_result(persisted_result)
                    final_text = f"Completed.\n\n{final_text}" if final_text else "Completed."
                    job["persisted_result"] = persisted_result
                _append_chat_message_to_disk(thread_config, "assistant", final_text, dedupe_last=True)
                job["persisted_final"] = True
            job["running"] = False

    thread = threading.Thread(target=_target, daemon=True)
    job["thread"] = thread
    st.session_state["ui_agent_job"] = job
    _append_chat_message(config_for_history, "user", message, dedupe_last=False)
    thread.start()


def _maybe_append_agent_progress(
    config: dict[str, Any],
    orchestrator: Orchestrator,
    job: dict[str, Any] | None,
) -> None:
    if not job or not job.get("running"):
        return
    worker = _active_worker_for_job(orchestrator, job)
    if not worker or worker == "idle":
        return
    announced = job.setdefault("announced_workers", [])
    if worker in announced:
        return
    announced.append(worker)
    _append_chat_message(
        config,
        "assistant",
        f"Running worker: `{worker}`.",
    )


def _finalize_agent_job(config: dict[str, Any]) -> None:
    job = _agent_job_state()
    if not job or job.get("running"):
        return
    if job.get("persisted_final"):
        result = job.get("persisted_result") or job.get("result") or {"assistant_message": "No result returned."}
        st.session_state["ui_chat_history"] = _load_ui_history(config)
    elif job.get("error"):
        text = f"Agent error: {job['error']}"
        result = {"assistant_message": text}
        _append_chat_message(config, "assistant", text, dedupe_last=False)
    else:
        result = job.get("result") or {"assistant_message": "No result returned."}
        text = _render_chat_result(result)
        text = f"Completed.\n\n{text}" if text else "Completed."
        _append_chat_message(config, "assistant", text, dedupe_last=False)
    _record_worker_event("Agent turn", result)
    st.session_state["ui_agent_job"] = None


def _format_manual_worker_result(worker: str, result: dict[str, Any], *, force: bool, phase: str) -> str:
    status = str(result.get("status") or "unknown").strip()
    if status == "completed":
        title = f"Worker completed: `{worker}`."
    elif status == "blocked":
        title = f"Worker blocked: `{worker}`."
    elif status in {"failed", "partial"}:
        title = f"Worker {status}: `{worker}`."
    else:
        title = f"Worker finished with status `{status}`: `{worker}`."

    lines = [title, f"Options: `force={force}`, `phase={phase}`."]
    implementation = str(result.get("implementation") or "").strip()
    if implementation:
        lines.append(f"Implementation: `{implementation}`.")

    notes = [str(item).strip() for item in result.get("notes", []) if str(item).strip()]
    if notes:
        lines.append("Notes:")
        for note in notes[:6]:
            lines.append(f"- {note}")

    artifacts = result.get("artifacts", {})
    if isinstance(artifacts, dict) and artifacts:
        preview_items: list[str] = []
        for key, value in artifacts.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                preview_items.append(f"`{key}`: `{value}`")
            elif isinstance(value, dict):
                preview_items.append(f"`{key}`: object")
            elif isinstance(value, list):
                preview_items.append(f"`{key}`: {len(value)} item(s)")
            if len(preview_items) >= 8:
                break
        if preview_items:
            lines.append("Outputs:")
            lines.extend(f"- {item}" for item in preview_items)
    return "\n".join(lines)


def _run_worker(config: dict[str, Any], orchestrator: Orchestrator, worker: str, *, force: bool, phase: str) -> None:
    _append_chat_message(
        config,
        "user",
        f"Run worker: `{worker}` (`force={force}`, `phase={phase}`).",
        dedupe_last=False,
    )
    try:
        result = run_named_worker(orchestrator, worker, force=force, phase=phase)
    except Exception as exc:  # noqa: BLE001
        result = {
            "worker": worker,
            "tool": worker,
            "label": worker,
            "implementation": "manual_worker_console",
            "status": "failed",
            "notes": [str(exc)],
            "artifacts": {"traceback": traceback.format_exc()},
        }
    _record_worker_event(f"worker-run: {worker}", result)
    _append_chat_message(
        config,
        "assistant",
        _format_manual_worker_result(worker, result, force=force, phase=phase),
        dedupe_last=False,
    )


def main() -> None:
    st.set_page_config(page_title="OntoAnno", layout="wide")
    config, orchestrator = _refresh_runtime()
    if _default_reset_session() and not st.session_state.get("_ontoanno_ui_session_reset_done"):
        reset_agent_session(config)
        st.session_state["_ontoanno_ui_session_reset_done"] = True

    if "ui_pending_prompt" not in st.session_state:
        st.session_state["ui_pending_prompt"] = None
    if "ui_chat_history" not in st.session_state:
        st.session_state["ui_chat_history"] = _load_ui_history(config)
    if "ui_agent_job" not in st.session_state:
        st.session_state["ui_agent_job"] = None

    state = _project_state(config, orchestrator)

    st.title("OntoAnno")
    st.caption("Local Streamlit front-end for OntoAnno. Python orchestrates; R workers still run locally through Rscript.")

    with st.sidebar:
        st.subheader("Project")
        st.write(f"Project: `{state['project']}`")
        st.write(f"Run: `{state['run_id']}`")
        parent_resolution_text = ", ".join(state.get("parent_resolution_options", [])) or "unknown"
        st.write(f"Parent resolutions: `{parent_resolution_text}`")
        st.write(f"Algorithm best resolution: `{state['algorithm_parent_resolution']}`")
        st.write(f"Selected resolution: `{state['parent_resolution']}`")
        st.write(f"Granularity: `{state['granularity']}`")
        st.write(f"Ontology restricted: `{state['ontology']}`")
        st.write(f"Custom marker entries: `{state['memory_markers']}`")
        st.write(f"Custom celltype entries: `{state['memory_celltypes']}`")
        st.write(f"Session: `{state['session_path']}`")
        if st.button("Reset agent session", width="stretch"):
            reset_agent_session(config)
            st.session_state["ui_chat_history"] = []
            _save_ui_history(config, [])
            st.success("Agent session reset.")
        if st.button(
            "Reload saved session",
            help="Reload the saved chat/session files from disk if the page looks stale.",
            width="stretch",
        ):
            st.session_state["ui_chat_history"] = _load_ui_history(config)
            _rerun_app()

    col_chat, col_side = st.columns([1.3, 1.0], gap="large")

    with col_chat:
        st.subheader("Chat")
        _render_chat_history_panel(_chat_history())

    with col_side:
        status_tab, evidence_tab, workers_tab, artifacts_tab, logs_tab = st.tabs(
            ["Status", "External Evidence", "Workers", "Artifacts", "Logs"]
        )

        with status_tab:
            st.subheader("Run Status")
            st.markdown("**Stage Status**")
            active_job = _agent_job_state()
            _render_stage_status(orchestrator, is_running=bool(active_job and active_job.get("running")))
            st.markdown("**Terminal Output**")
            active_log_name, active_log_text = _active_log_snapshot(orchestrator, limit=200)
            if active_job and active_job.get("running"):
                elapsed = max(int(time.time() - float(active_job.get("started_at") or time.time())), 0)
                st.caption(
                    f"Agent is running worker: `{_active_worker_label(active_log_name)}` | elapsed: {elapsed}s"
                )
            st.caption(f"Showing active worker log: `{active_log_name}`")
            st.text_area(
                "Current run output",
                active_log_text,
                height=360,
            )

        with evidence_tab:
            st.subheader("External Evidence")
            _render_external_evidence_tab(config)

        with workers_tab:
            st.subheader("Worker Console")
            worker = st.selectbox("Worker", AVAILABLE_WORKERS, index=0)
            phase = st.selectbox("Phase", ["auto", "initial", "post_ontology", "post_compare"], index=0)
            if worker == "decide_rag_check":
                st.caption("Phase controls which RAG controller checkpoint to run.")
            else:
                st.caption("Phase is ignored by this worker.")
            _render_worker_guide(worker)
            readiness = worker_prerequisite_status(orchestrator, worker)
            _render_worker_readiness(readiness)
            force = st.checkbox("Force", value=False)
            if st.button("Run worker", width="stretch", disabled=not bool(readiness.get("ok"))):
                with st.spinner(f"Running worker: {worker}"):
                    _run_worker(config, orchestrator, worker, force=force, phase=phase)
                _rerun_app()
            history = _worker_history()
            if history:
                for item in history[:6]:
                    with st.expander(item["title"], expanded=False):
                        _render_worker_payload(item["payload"])
            else:
                st.info("No worker executions recorded in this UI session yet.")

        with artifacts_tab:
            st.subheader("Artifacts")
            parent_tab, subcluster_tab, rag_tab, report_tab = st.tabs(
                ["Parent Annotation", "Subcluster", "RAG Review", "Report"]
            )
            with parent_tab:
                _render_parent_annotation_tab(orchestrator)
            with subcluster_tab:
                _render_subcluster_tab(orchestrator)
            with rag_tab:
                _render_rag_review_tab(orchestrator)
            with report_tab:
                _render_report_tab(orchestrator)

        with logs_tab:
            st.subheader("Logs")
            unique_logs = _collect_log_paths(orchestrator)
            if unique_logs:
                selected_log = st.selectbox("Log file", [str(path) for path in unique_logs], index=0)
                st.text_area("Log tail", _tail_log_lines(selected_log), height=360)
            else:
                st.info("No log files found yet.")

    prompt = st.chat_input("Ask OntoAnno to run, review, explain, or show current state")
    if prompt:
        st.session_state["ui_pending_prompt"] = prompt
        _rerun_app()

    pending_prompt = st.session_state.get("ui_pending_prompt")
    if pending_prompt:
        _start_agent_job(
            str(pending_prompt),
        )
        st.session_state["ui_pending_prompt"] = None
        _rerun_app()

    active_job = _agent_job_state()
    if active_job and active_job.get("running"):
        _maybe_append_agent_decision_events(config, active_job)
        _maybe_append_agent_progress(config, orchestrator, active_job)
        time.sleep(1)
        _rerun_app()
    if active_job and not active_job.get("running"):
        _finalize_agent_job(config)
        _rerun_app()


if __name__ == "__main__":
    main()
