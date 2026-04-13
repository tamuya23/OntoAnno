from __future__ import annotations

import base64
import collections
import csv
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import streamlit as st

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from annoagent.agent_memory import load_agent_memory
from annoagent.agent_router import route_agent_request
from annoagent.agent_session import reset_agent_session, session_path
from annoagent.config import load_config
from annoagent.orchestrator import Orchestrator
from annoagent.utils import dump_json, load_json
from annoagent.worker_runtime import AVAILABLE_WORKERS, run_named_worker


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _config_path() -> Path:
    value = os.getenv("ANNOAGENT_STREAMLIT_CONFIG")
    if value:
        return Path(value).expanduser().resolve()
    return (_repo_root() / "configs" / "agingv2.yaml").resolve()


def _default_reset_session() -> bool:
    return os.getenv("ANNOAGENT_STREAMLIT_RESET_SESSION", "0") == "1"


def _load_runtime(config_path: str) -> tuple[dict[str, Any], Orchestrator]:
    repo_root = _repo_root()
    config = load_config(config_path, repo_root)
    orchestrator = Orchestrator(repo_root, config)
    return config, orchestrator


def _refresh_runtime() -> tuple[dict[str, Any], Orchestrator]:
    config, orchestrator = _load_runtime(str(_config_path()))
    orchestrator = Orchestrator(_repo_root(), config)
    return config, orchestrator


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
    selected_resolution = "unknown"
    annotate_parent = outputs.get("annotate_parent", {}) if isinstance(outputs.get("annotate_parent"), dict) else {}
    best_resolution = str(annotate_parent.get("best_resolution") or "").strip()
    if best_resolution:
        selected_resolution = best_resolution
    else:
        forced_resolution = str(annotation.get("forced_parent_resolution") or "").strip()
        if forced_resolution:
            selected_resolution = forced_resolution
    return {
        "project": config["project"]["name"],
        "run_id": orchestrator.run_id,
        "parent_resolution_options": parent_resolution_options,
        "parent_resolution": selected_resolution,
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
    return Path(str(config["project"]["work_dir"])) / "agent_ui_history.json"


def _load_ui_history(config: dict[str, Any]) -> list[dict[str, Any]]:
    path = _ui_history_path(config)
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
    if phase == "Cluster":
        gptanno = outputs.get("gptanno_tools", {}) if isinstance(outputs.get("gptanno_tools"), dict) else {}
        return bool(gptanno.get("cluster_parent_markers"))
    if phase == "Annotate":
        annotate_parent = outputs.get("annotate_parent", {}) if isinstance(outputs.get("annotate_parent"), dict) else {}
        gptanno = outputs.get("gptanno_tools", {}) if isinstance(outputs.get("gptanno_tools"), dict) else {}
        return bool(annotate_parent) or bool(gptanno.get("assign_parent_labels"))
    if phase == "Subcluster":
        annotate_subclusters = outputs.get("annotate_subclusters", {}) if isinstance(outputs.get("annotate_subclusters"), dict) else {}
        gptanno = outputs.get("gptanno_tools", {}) if isinstance(outputs.get("gptanno_tools"), dict) else {}
        subcluster_dir = orchestrator.work_dir / "annotate_subclusters"
        file_complete = any(
            (subcluster_dir / name).exists()
            for name in ["metadata_final.csv", "seurat_final_annotated.rds", "seurat_ontology_annotated.rds"]
        )
        return bool(annotate_subclusters) or bool(gptanno.get("finalize_subcluster_annotations")) or file_complete
    if phase == "RAG_Check":
        return any(
            bool(outputs.get(key))
            for key in ["review_packets", "ontology_relations", "llm_compare", "controller", "reviewed_parent"]
        )
    if phase == "Report":
        report = outputs.get("report", {}) if isinstance(outputs.get("report"), dict) else {}
        return bool(report.get("report_html") or report.get("report_pdf") or report.get("report_path"))
    return False


def _render_stage_status(orchestrator: Orchestrator, *, is_running: bool) -> None:
    active_log_name, _ = _active_log_snapshot(orchestrator, limit=50)
    active_worker = _active_worker_label(active_log_name)
    rows: list[dict[str, str]] = []
    for phase, workers in PHASE_WORKER_MAP.items():
        status = "pending"
        if is_running and active_worker in workers:
            status = "running"
        elif _phase_completed(phase, orchestrator):
            status = "completed"
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
    if not path.exists():
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
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def _render_preview_table(title: str, path_str: str, *, limit: int = 12) -> None:
    rows = _read_csv_preview(path_str, limit=limit)
    if not rows:
        st.info(f"No preview available for {title.lower()}.")
        return
    st.markdown(f"**{title}**")
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_pdf_embed(path: Path, *, height: int = 720) -> None:
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    iframe = (
        f'<iframe src="data:application/pdf;base64,{encoded}" '
        f'width="100%" height="{height}" type="application/pdf"></iframe>'
    )
    st.markdown(iframe, unsafe_allow_html=True)


def _read_csv_rows(path_str: str) -> list[dict[str, Any]]:
    path = Path(path_str)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        return list(csv.DictReader(handle))


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


def _render_parent_annotation_tab(orchestrator: Orchestrator) -> None:
    outputs = orchestrator.manifest.get("outputs", {}) if isinstance(orchestrator.manifest.get("outputs"), dict) else {}
    gptanno = outputs.get("gptanno_tools", {}) if isinstance(outputs.get("gptanno_tools"), dict) else {}
    parent_dir = orchestrator.work_dir / "annotate_parent"
    scores_csv = str((parent_dir / "annotation_summary_scores.csv"))
    mapping_csv = str((parent_dir / "parent_ontology_mapping.csv"))
    best_resolution_json = parent_dir / "best_parent_resolution.json"
    prediction_dir = parent_dir / "prediction"
    if not Path(scores_csv).exists():
        st.info("No parent annotation outputs recorded yet.")
        return

    score_rows = _read_csv_rows(scores_csv)
    available_resolutions = [str(row.get("resolution") or "").strip() for row in score_rows if str(row.get("resolution") or "").strip()]
    selected_default = available_resolutions[0] if available_resolutions else "unknown"
    if best_resolution_json.exists():
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
    st.dataframe(score_rows, use_container_width=True, hide_index=True)

    mapping_rows = _read_csv_rows(mapping_csv)
    filtered_mapping = [
        row for row in mapping_rows
        if str(row.get("resolution") or "").strip() == selected_resolution
    ]
    if filtered_mapping:
        selected_rows = [row for row in filtered_mapping if str(row.get("role") or "").strip() == "selected"]
        cluster_rows = [
            {
                "cluster": str(row.get("cluster") or ""),
                "cleaned_label": str(row.get("cleaned_label") or row.get("label") or ""),
            }
            for row in selected_rows
        ]
        st.markdown("**Cluster annotation results**")
        st.dataframe(cluster_rows[:100], use_container_width=True, hide_index=True)
    else:
        st.info("No parent annotation table rows found for the selected resolution.")

    st.markdown("**Prediction figure**")
    preview_png, preview_error = _ensure_parent_annotation_preview(orchestrator, selected_resolution)
    prediction_path = next((path for path in _prediction_figure_candidate_paths(prediction_dir, selected_resolution) if path.exists()), None)
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
        st.dataframe(cluster_table[:100], use_container_width=True, hide_index=True)
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
    if not any([reviewed, review_packets, ontology_relations, llm_compare, controller]):
        st.info("No RAG review outputs recorded yet.")
        return

    reviewed_csv = str(reviewed.get("metadata_csv") or "")
    reviewed_decisions = str(reviewed.get("cluster_decisions_csv") or "")
    review_packets_summary = str(review_packets.get("summary_csv") or "")
    ontology_summary = str(ontology_relations.get("summary_csv") or "")
    llm_compare_summary = str(llm_compare.get("summary_csv") or "")
    controller_summary = str(controller.get("summary_csv") or "")

    rag_col1, rag_col2, rag_col3, rag_col4 = st.columns(4)
    with rag_col1:
        st.metric("Review packets", _csv_row_count(review_packets_summary) or 0)
    with rag_col2:
        st.metric("Candidate map rows", _csv_row_count(ontology_summary) or 0)
    with rag_col3:
        st.metric("LLM compare rows", _csv_row_count(llm_compare_summary) or 0)
    with rag_col4:
        st.metric("Reviewed decisions", _csv_row_count(reviewed_decisions) or 0)

    if reviewed_decisions:
        _render_preview_table("Reviewed cluster decisions", reviewed_decisions, limit=12)
    elif controller_summary:
        _render_preview_table("Controller summary", controller_summary, limit=12)

    if review_packets_summary:
        _render_preview_table("Review packet summary", review_packets_summary, limit=10)
    if ontology_summary:
        _render_preview_table("Candidate map summary", ontology_summary, limit=10)
    if llm_compare_summary:
        _render_preview_table("LLM compare summary", llm_compare_summary, limit=10)
    if reviewed_csv:
        _render_preview_table("Reviewed parent metadata", reviewed_csv, limit=10)

    with st.expander("Raw RAG review files"):
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

    if figures:
        selected = st.selectbox("Report figure", [path.name for path in figures], index=0, key="artifacts_report_figure")
        selected_path = next((path for path in figures if path.name == selected), None)
        if selected_path is not None:
            st.image(str(selected_path), caption=selected_path.name)
            st.code(str(selected_path), language="bash")

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


def _render_external_evidence_tab(config: dict[str, Any]) -> None:
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
            "markers": ", ".join(str(x) for x in item.get("markers", []) if str(x).strip()),
            "note": str(item.get("note") or ""),
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
            "markers": ", ".join(str(x) for x in item.get("markers", []) if str(x).strip()),
            "note": str(item.get("note") or ""),
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
        st.dataframe(user_rows, use_container_width=True, hide_index=True)
    else:
        st.info("No user-provided evidence stored yet.")

    st.markdown("**Literature-provided evidence**")
    if literature_rows:
        st.dataframe(literature_rows, use_container_width=True, hide_index=True)
    else:
        st.info("No literature-provided evidence yet.")


def _render_chat_result(result: dict[str, Any]) -> str:
    lines: list[str] = []
    for item in result.get("tool_calls", []):
        lines.append(f"Tool: {item['tool_name']}")
        lines.append(f"Arguments: {item['arguments']}")
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
    try:
        container = st.container(height=700, border=True)
    except TypeError:
        container = st.container()
    with container:
        if not messages:
            st.info("No chat history yet.")
            return
        for item in messages:
            role = str(item.get("role") or "assistant").strip().lower()
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            with st.chat_message("user" if role == "user" else "assistant"):
                st.markdown(content)


def _agent_job_state() -> dict[str, Any] | None:
    return st.session_state.get("ui_agent_job")


def _start_agent_job(message: str) -> None:
    config_path = str(_config_path())
    job: dict[str, Any] = {
        "message": message,
        "running": True,
        "result": None,
        "error": None,
        "started_at": time.time(),
    }

    def _target() -> None:
        try:
            config, orchestrator = _load_runtime(config_path)
            result = route_agent_request(
                config=config,
                orchestrator=orchestrator,
                user_message=message,
                apply=True,
                reset_session=False,
            )
            job["result"] = result
        except Exception as exc:  # noqa: BLE001
            job["error"] = f"{exc}\n\n{traceback.format_exc()}"
        finally:
            job["running"] = False

    thread = threading.Thread(target=_target, daemon=True)
    job["thread"] = thread
    st.session_state["ui_agent_job"] = job
    history = _chat_history()
    history.append({"role": "user", "content": message})
    _save_ui_history(load_config(config_path, _repo_root()), history)
    thread.start()


def _finalize_agent_job(config: dict[str, Any]) -> None:
    job = _agent_job_state()
    if not job or job.get("running"):
        return
    history = _chat_history()
    if job.get("error"):
        text = f"Agent error: {job['error']}"
        result = {"assistant_message": text}
    else:
        result = job.get("result") or {"assistant_message": "No result returned."}
        text = _render_chat_result(result)
    history.append({"role": "assistant", "content": text})
    _save_ui_history(config, history)
    _record_worker_event("Agent turn", result)
    st.session_state["ui_agent_job"] = None


def _run_worker(orchestrator: Orchestrator, worker: str, *, force: bool, phase: str) -> None:
    result = run_named_worker(orchestrator, worker, force=force, phase=phase)
    _record_worker_event(f"worker-run: {worker}", result)


def main() -> None:
    st.set_page_config(page_title="AnnoAgent Workbench", layout="wide")
    config, orchestrator = _refresh_runtime()
    if _default_reset_session() and not st.session_state.get("_annoagent_ui_session_reset_done"):
        reset_agent_session(config)
        st.session_state["_annoagent_ui_session_reset_done"] = True

    if "ui_pending_prompt" not in st.session_state:
        st.session_state["ui_pending_prompt"] = None
    if "ui_chat_history" not in st.session_state:
        st.session_state["ui_chat_history"] = _load_ui_history(config)
    if "ui_agent_job" not in st.session_state:
        st.session_state["ui_agent_job"] = None

    state = _project_state(config, orchestrator)

    st.title("AnnoAgent Workbench")
    st.caption("Local Streamlit front-end for AnnoAgent. Python orchestrates; R workers still run locally through Rscript.")

    with st.sidebar:
        st.subheader("Project")
        st.write(f"Project: `{state['project']}`")
        st.write(f"Run: `{state['run_id']}`")
        parent_resolution_text = ", ".join(state.get("parent_resolution_options", [])) or "unknown"
        st.write(f"Parent resolutions: `{parent_resolution_text}`")
        st.write(f"Selected resolution: `{state['parent_resolution']}`")
        st.write(f"Granularity: `{state['granularity']}`")
        st.write(f"Ontology restricted: `{state['ontology']}`")
        st.write(f"Custom marker entries: `{state['memory_markers']}`")
        st.write(f"Custom celltype entries: `{state['memory_celltypes']}`")
        st.write(f"Session: `{state['session_path']}`")
        if st.button("Reset agent session", use_container_width=True):
            reset_agent_session(config)
            st.session_state["ui_chat_history"] = []
            _save_ui_history(config, [])
            st.success("Agent session reset.")
        if st.button("Refresh runtime state", use_container_width=True):
            st.session_state["ui_chat_history"] = _load_ui_history(config)
            st.rerun()

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
            force = st.checkbox("Force", value=False)
            if st.button("Run worker", use_container_width=True):
                with st.spinner(f"Running worker: {worker}"):
                    _run_worker(orchestrator, worker, force=force, phase=phase)
                st.rerun()
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

    prompt = st.chat_input("Ask AnnoAgent to run, review, explain, or show current state")
    if prompt:
        st.session_state["ui_pending_prompt"] = prompt
        st.rerun()

    pending_prompt = st.session_state.get("ui_pending_prompt")
    if pending_prompt:
        _start_agent_job(
            str(pending_prompt),
        )
        st.session_state["ui_pending_prompt"] = None
        st.rerun()

    active_job = _agent_job_state()
    if active_job and active_job.get("running"):
        time.sleep(1)
        st.rerun()
    if active_job and not active_job.get("running"):
        _finalize_agent_job(config)
        st.rerun()


if __name__ == "__main__":
    main()
