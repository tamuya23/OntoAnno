from __future__ import annotations

import base64
import csv
import json
import subprocess
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape
from PIL import Image, ImageDraw, ImageFont

from .review_packets import resolve_imported_parent_annotations
from .utils import dump_json, ensure_dir, path_uri, utc_now


REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ project_name }} - OntoAnno Report</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; line-height: 1.5; color: #1f2328; background: #fcfcfa; }
    h1, h2, h3 { margin-bottom: 0.35rem; }
    h2 { margin-top: 2rem; }
    .meta, .warning, .error, .box, .card { border: 1px solid #ddd; padding: 1rem; margin: 1rem 0; border-radius: 10px; background: white; }
    .warning { background: #fff8e1; border-color: #e0b100; }
    .error { background: #ffebee; border-color: #c62828; }
    .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.8rem; margin: 1rem 0; }
    .summary-item { border: 1px solid #ddd; border-radius: 10px; padding: 0.9rem; background: white; }
    .summary-item .label { color: #666; font-size: 0.9rem; margin-bottom: 0.2rem; }
    .summary-item .value { font-size: 1.1rem; font-weight: 700; }
    .figure-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; margin: 1rem 0; }
    .figure-card { border: 1px solid #ddd; border-radius: 10px; padding: 0.75rem; background: white; }
    .figure-card img { display: block; width: 100%; max-width: 460px; max-height: 320px; height: auto; margin: 0 auto; object-fit: contain; background: #fafafa; border-radius: 8px; border: 1px solid #eee; }
    .figure-card h3 { margin-top: 0; }
    table { border-collapse: collapse; width: 100%; margin: 0.75rem 0 1.25rem; background: white; }
    th, td { border: 1px solid #ddd; padding: 0.5rem; text-align: left; vertical-align: top; }
    th { background: #f5f5f5; }
    code { background: #f5f5f5; padding: 0.1rem 0.3rem; border-radius: 4px; }
    .muted { color: #666; }
    .kv { margin: 0.25rem 0; }
  </style>
</head>
<body>
  <h1>{{ project_name }} - OntoAnno Report</h1>
  <div class="meta">
    <p><strong>Run ID:</strong> {{ run_id }}</p>
    <p><strong>Generated:</strong> {{ generated_at }}</p>
    <p><strong>Dataset:</strong> {{ annotation.tissue_name or "N/A" }}</p>
    <p><strong>Model:</strong> {{ annotation_provider }}/{{ annotation_model }}</p>
    <p><strong>Parent Resolution:</strong> {{ annotation.best_parent_resolution or "N/A" }}</p>
    <p><strong>Policy:</strong> ontology={{ policy.ontology }}, granularity={{ policy.granularity }}</p>
  </div>

  <div class="summary-grid">
    <div class="summary-item"><div class="label">Parent Clusters</div><div class="value">{{ cluster_summary.total_clusters }}</div></div>
    <div class="summary-item"><div class="label">Compared By LLM</div><div class="value">{{ cluster_summary.llm_compared }}</div></div>
    <div class="summary-item"><div class="label">Changed By Agent</div><div class="value">{{ cluster_summary.changed_clusters }}</div></div>
    <div class="summary-item"><div class="label">Manual Review Picks</div><div class="value">{{ cluster_summary.manual_reviewed }}</div></div>
  </div>

  {% if figures.parent %}
  <h2>Parent UMAP</h2>
  <div class="figure-grid">
    {% for item in figures.parent %}
    <div class="figure-card">
      <h3>{{ item.title }}</h3>
      <a href="{{ item.href }}"><img src="{{ item.src }}" alt="{{ item.title }}"></a>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  <h2>Parent Cluster Overview</h2>
  <table>
    <thead>
      <tr>
        <th>Cluster</th>
        <th>Cells</th>
        <th>Top Markers</th>
        <th>Initial Parent</th>
        <th>Final Parent</th>
        <th>Source</th>
        <th>Candidates</th>
      </tr>
    </thead>
    <tbody>
    {% for cluster in clusters %}
      <tr>
        <td>{{ cluster.cluster_id }}</td>
        <td>{{ cluster.cell_count or "" }}</td>
        <td>{{ cluster.marker_text }}</td>
        <td>{{ cluster.initial_label or "" }}</td>
        <td>{{ cluster.final_label or cluster.initial_label or "" }}</td>
        <td>{{ cluster.selection_source or "" }}</td>
        <td>{{ cluster.focus_candidates_text }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>

  {% if rag_review.available %}
  <h2>RAG Check Review</h2>
  <div class="box">
    <p><strong>Discussion:</strong> {{ rag_review.discussion }}</p>
  </div>
  <div class="summary-grid">
    <div class="summary-item"><div class="label">Review Packets</div><div class="value">{{ rag_review.review_packet_count }}</div></div>
    <div class="summary-item"><div class="label">Flagged Initially</div><div class="value">{{ rag_review.initial_flagged_count }}</div></div>
    <div class="summary-item"><div class="label">LLM Compared</div><div class="value">{{ rag_review.llm_compared_count }}</div></div>
    <div class="summary-item"><div class="label">Human Review Needed</div><div class="value">{{ rag_review.ask_user_count }}</div></div>
  </div>
  {% if rag_review.rows %}
  <table>
    <thead>
      <tr>
        <th>Cluster</th>
        <th>Current Label</th>
        <th>Phase</th>
        <th>Next Action</th>
        <th>Recommended Label</th>
        <th>LLM Decision</th>
        <th>Reason Codes</th>
      </tr>
    </thead>
    <tbody>
    {% for row in rag_review.rows %}
      <tr>
        <td>{{ row.cluster_id }}</td>
        <td>{{ row.current_label }}</td>
        <td>{{ row.phase }}</td>
        <td>{{ row.next_action }}</td>
        <td>{{ row.recommended_label }}</td>
        <td>{{ row.llm_decision }}</td>
        <td>{{ row.reason_codes }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}
  {% endif %}

  <h2>Cluster Decisions</h2>
  {% for cluster in clusters %}
  <div class="card">
    <h3>Cluster {{ cluster.cluster_id }}{% if cluster.initial_label %} - {{ cluster.initial_label }}{% endif %}</h3>
    <p class="kv"><strong>Cells:</strong> {{ cluster.cell_count or "N/A" }}</p>
    <p class="kv"><strong>Initial Annotation:</strong> {{ cluster.initial_label or "N/A" }}</p>
    <p class="kv"><strong>Final Annotation:</strong> {{ cluster.final_label or cluster.initial_label or "N/A" }}</p>
    <p class="kv"><strong>Selection Source:</strong> {{ cluster.selection_source or "N/A" }}</p>
    <p class="kv"><strong>Top Markers:</strong> {{ cluster.marker_text }}</p>
    {% if cluster.other_annotations %}
    <p class="kv"><strong>Other surfaced annotations:</strong> {{ cluster.other_annotations }}</p>
    {% endif %}
    {% if cluster.focus_candidates_text %}
    <p class="kv"><strong>Compared candidates:</strong> {{ cluster.focus_candidates_text }}</p>
    {% endif %}
    {% if cluster.relation_mode or cluster.consensus_ancestor %}
    <p class="kv"><strong>Ontology compare:</strong> {{ cluster.relation_mode or "N/A" }}{% if cluster.consensus_ancestor %} | shared ancestor: {{ cluster.consensus_ancestor }}{% endif %}</p>
    {% endif %}
    {% if cluster.llm_question %}
    <p class="kv"><strong>LLM Question:</strong> {{ cluster.llm_question }}</p>
    {% endif %}
    {% if cluster.llm_reason %}
    <p class="kv"><strong>LLM Reason:</strong> {{ cluster.llm_reason }}</p>
    {% endif %}
    {% if cluster.supporting_markers %}
    <p class="kv"><strong>Supporting markers:</strong> {{ cluster.supporting_markers | join(", ") }}</p>
    {% endif %}
    {% if cluster.weakening_markers %}
    <p class="kv"><strong>Weakening markers:</strong> {{ cluster.weakening_markers | join(", ") }}</p>
    {% endif %}
    {% if cluster.reference_limitations %}
    <p class="kv"><strong>Reference limitations:</strong> {{ cluster.reference_limitations }}</p>
    {% endif %}
    {% if cluster.user_note %}
    <p class="kv"><strong>User note:</strong> {{ cluster.user_note }}</p>
    {% endif %}
  </div>
  {% endfor %}

  {% if figures.subcluster or subcluster %}
  <h2>Subcluster</h2>
  {% if figures.subcluster %}
  <div class="figure-grid">
    {% for item in figures.subcluster %}
    <div class="figure-card">
      <h3>{{ item.title }}</h3>
      <a href="{{ item.href }}"><img src="{{ item.src }}" alt="{{ item.title }}"></a>
    </div>
    {% endfor %}
  </div>
  {% endif %}
  {% if subcluster %}
  <div class="box">
    <p><strong>Subclustering performed:</strong> {{ subcluster.performed }}</p>
    <p><strong>Subclustered parent cell types:</strong> {{ subcluster.parent_celltypes_text }}</p>
    <p><strong>Unique final labels:</strong> {{ subcluster.final_label_count or "N/A" }}</p>
    <p><strong>Unique inherited labels:</strong> {{ subcluster.inherited_label_count or "N/A" }}</p>
  </div>
  {% endif %}
  {% endif %}

  {% if warnings %}
  <h2>Warnings</h2>
  {% for warning in warnings %}
  <div class="warning">{{ warning }}</div>
  {% endfor %}
  {% endif %}

  {% if errors %}
  <h2>Errors</h2>
  {% for error in errors %}
  <div class="error">{{ error }}</div>
  {% endfor %}
  {% endif %}

  <p class="muted">Generated by OntoAnno.</p>
</body>
</html>
"""


PDF_PAGE_WIDTH = 1240
PDF_PAGE_HEIGHT = 1754
PDF_MARGIN = 72
PDF_BG = "white"
PDF_FG = "#1f2328"
PDF_SUBTLE = "#666666"
PDF_ACCENT = "#d9d9d9"
FONT_REGULAR_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
FONT_BOLD_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]


def _load_json_if_exists(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    target = Path(path)
    if not target.exists():
        return None
    with target.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv_rows(path: str | Path | None) -> list[dict[str, str]]:
    if not path:
        return []
    target = Path(path)
    if not target.exists():
        return []
    with target.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _stage_outputs_path(run_dir: Path, stage: str) -> Path | None:
    stage_output_files = {
        "review_packets": run_dir / "review_packets" / "parent_review_packets.outputs.json",
        "ontology_relations": run_dir / "ontology_relations" / "ontology_relations.outputs.json",
        "llm_compare": run_dir / "llm_compare" / "llm_compare.outputs.json",
        "controller": run_dir / "controller" / "controller.outputs.json",
        "reviewed_parent": run_dir / "reviewed_parent" / "reviewed_parent.outputs.json",
    }
    return stage_output_files.get(stage)


def _load_stage_outputs_from_run_dir(run_dir: Path, stage: str) -> dict[str, Any] | None:
    outputs_path = _stage_outputs_path(run_dir, stage)
    if outputs_path is not None and outputs_path.exists():
        payload = _load_json_if_exists(outputs_path)
        if isinstance(payload, dict):
            return payload

    if stage == "review_packets":
        index_path = run_dir / "review_packets" / "index.json"
        summary_csv = run_dir / "review_packets" / "summary.csv"
        if index_path.exists() or summary_csv.exists():
            payload: dict[str, Any] = {}
            if index_path.exists():
                payload["index_json"] = str(index_path)
            if summary_csv.exists():
                payload["summary_csv"] = str(summary_csv)
            return payload or None

    if stage == "reviewed_parent":
        decisions_csv = run_dir / "reviewed_parent" / "cluster_decisions.csv"
        metadata_csv = run_dir / "reviewed_parent" / "metadata.csv"
        if decisions_csv.exists() or metadata_csv.exists():
            payload: dict[str, Any] = {}
            if decisions_csv.exists():
                payload["cluster_decisions_csv"] = str(decisions_csv)
                payload["decisions_csv"] = str(decisions_csv)
            if metadata_csv.exists():
                payload["metadata_csv"] = str(metadata_csv)
            return payload or None

    return None


def _resolve_report_outputs(config: dict[str, Any], manifest: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    outputs = manifest.get("outputs", {}) if isinstance(manifest.get("outputs"), dict) else {}
    resolved: dict[str, Any] = dict(outputs)

    for stage in ("review_packets", "ontology_relations", "llm_compare", "controller", "reviewed_parent"):
        current = resolved.get(stage)
        if isinstance(current, dict) and current:
            continue
        disk_outputs = _load_stage_outputs_from_run_dir(run_dir, stage)
        if isinstance(disk_outputs, dict) and disk_outputs:
            resolved[stage] = disk_outputs

    gptanno_tools = resolved.get("gptanno_tools", {}) if isinstance(resolved.get("gptanno_tools"), dict) else {}
    if not gptanno_tools:
        parent_dir = Path(str(config["project"]["work_dir"])) / "annotate_parent"
        subcluster_dir = Path(str(config["project"]["work_dir"])) / "annotate_subclusters"
        recovered_tools: dict[str, Any] = {}

        parent_preprocessed = parent_dir / "seurat_preprocessed.rds"
        if parent_preprocessed.exists():
            recovered_tools.setdefault("preprocess_parent", {"preprocessed_rds": str(parent_preprocessed)})
        parent_clustered = parent_dir / "seurat_clustered.rds"
        if parent_clustered.exists():
            recovered_tools.setdefault("cluster_parent_markers", {"clustered_rds": str(parent_clustered)})
        parent_annotation = parent_dir / "annotation_parent.rds"
        if parent_annotation.exists():
            recovered_tools.setdefault("annotate_parent_raw", {"annotation_parent_rds": str(parent_annotation)})
        parent_mapping = parent_dir / "parent_ontology_mapping.csv"
        parent_mapping_rds = parent_dir / "parent_ontology_mapping.rds"
        if parent_mapping.exists() or parent_mapping_rds.exists():
            recovered_tools.setdefault(
                "map_parent_ontology",
                {
                    "ontology_mapping_csv": str(parent_mapping) if parent_mapping.exists() else None,
                    "ontology_mapping_rds": str(parent_mapping_rds) if parent_mapping_rds.exists() else None,
                },
            )
        best_resolution_json = parent_dir / "best_parent_resolution.json"
        if best_resolution_json.exists():
            best_payload = _load_json_if_exists(best_resolution_json) or {}
            recovered_tools.setdefault(
                "select_parent_resolution",
                {
                    "best_parent_resolution_json": str(best_resolution_json),
                    "best_resolution": best_payload.get("best_resolution"),
                    "best_resolution_value": best_payload.get("best_resolution_value"),
                    "cluster_col": best_payload.get("cluster_col"),
                },
            )
            recovered_tools.setdefault(
                "assign_parent_labels",
                {
                    "best_parent_resolution_json": str(best_resolution_json),
                    "best_resolution": best_payload.get("best_resolution"),
                    "best_resolution_value": best_payload.get("best_resolution_value"),
                    "cluster_col": best_payload.get("cluster_col"),
                    "parent_seurat_rds": str(parent_dir / "seurat_parent_annotated.rds"),
                },
            )
        subcluster_final = subcluster_dir / "seurat_final_annotated.rds"
        if subcluster_final.exists():
            recovered_tools.setdefault(
                "finalize_subcluster_annotations",
                {
                    "final_seurat_rds": str(subcluster_final),
                    "final_metadata_csv": str(subcluster_dir / "metadata_final.csv") if (subcluster_dir / "metadata_final.csv").exists() else None,
                    "final_dimplot_pdf": str(subcluster_dir / "DimPlot_celltype_final.pdf") if (subcluster_dir / "DimPlot_celltype_final.pdf").exists() else None,
                    "subclustering_performed": True,
                },
            )
        if recovered_tools:
            resolved["gptanno_tools"] = recovered_tools

    return resolved


def _path_entry(label: str, path: str | Path | None) -> dict[str, str] | None:
    if not path:
        return None
    target = Path(path)
    if not target.exists():
        return None
    return {"label": label, "path": str(target), "uri": path_uri(target) or ""}


def _relative_href(base_dir: Path, target: str | Path | None) -> str | None:
    if not target:
        return None
    try:
        return Path(target).resolve().relative_to(base_dir.resolve()).as_posix()
    except Exception:
        try:
            return Path(target).resolve().relative_to(Path.cwd().resolve()).as_posix()
        except Exception:
            try:
                return Path(target).resolve().as_posix()
            except Exception:
                return None


def _image_data_uri(path: str | Path | None) -> str | None:
    if not path:
        return None
    target = Path(path)
    if not target.exists():
        return None
    suffix = target.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }.get(suffix)
    if not mime:
        return None
    encoded = base64.b64encode(target.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _prepare_report_figures(
    config: dict[str, Any],
    outputs: dict[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    figure_dir = ensure_dir(run_dir / "report_assets" / "figures")
    spec_path = figure_dir / "report_figures.spec.json"
    outputs_json = figure_dir / "report_figures.outputs.json"
    log_path = figure_dir / "report_figures.log"
    helper = Path(config["_meta"]["repo_root"]) / "scripts" / "export_report_figures.R"
    subcluster_outputs = _resolve_subcluster_outputs(config, outputs)
    parent_outputs = _resolve_parent_annotation_outputs(config, outputs)
    reviewed_outputs = _resolve_reviewed_parent_outputs(outputs)

    spec = {
        "output_dir": str(figure_dir),
        "outputs_json": str(outputs_json),
        "parent": {
            "seurat_rds": parent_outputs.get("parent_seurat_rds"),
            "cluster_col": parent_outputs.get("cluster_col"),
            "label_col": "celltype_parent",
        },
        "reviewed_parent": {
            "seurat_rds": reviewed_outputs.get("seurat_rds"),
            "label_col": reviewed_outputs.get("label_col", "celltype_parent_reviewed"),
        },
        "subcluster": {
            "seurat_rds": subcluster_outputs.get("final_seurat_rds"),
            "label_col": "celltype_final",
            "inherited_label_col": "celltype_final_inherited",
        },
    }
    dump_json(spec_path, spec)

    if not helper.exists():
        return {"figures": {}, "warnings": [f"Missing report figure helper: {helper}"]}

    command = [config["_runtime"]["rscript"], str(helper), str(spec_path)]
    try:
        with log_path.open("w", encoding="utf-8") as handle:
            handle.write(f"$ {' '.join(command)}\n")
            process = subprocess.run(
                command,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
    except FileNotFoundError as exc:
        return {"figures": {}, "warnings": [f"Could not run report figure helper: {exc}"], "log": str(log_path)}

    if process.returncode != 0 or not outputs_json.exists():
        return {
            "figures": {},
            "warnings": [f"Report figure generation failed; see {log_path}"],
            "log": str(log_path),
        }

    payload = _load_json_if_exists(outputs_json) or {"figures": {}}
    warnings = payload.get("warnings", []) or []
    if isinstance(warnings, str):
        warnings = [warnings]
    reviewed_seurat_rds = str(reviewed_outputs.get("seurat_rds") or "").strip()
    if not reviewed_seurat_rds:
        warnings = [
            item
            for item in warnings
            if "parent_reviewed: missing Seurat input" not in str(item)
        ]
    payload["warnings"] = warnings
    payload["log"] = str(log_path)
    return payload


def _cluster_sort_key(cluster_id: str) -> tuple[int, str]:
    try:
        return (0, f"{int(cluster_id):06d}")
    except (TypeError, ValueError):
        return (1, str(cluster_id))


def _build_cluster_payload(outputs: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    clusters: dict[str, dict[str, Any]] = {}

    review_index = _load_json_if_exists(outputs.get("review_packets", {}).get("index_json")) or {}
    for packet_item in review_index.get("packets", []):
        cluster_id = str(packet_item.get("cluster_id") or "")
        packet_payload = _load_json_if_exists(packet_item.get("packet_json")) or {}
        summary = packet_payload.get("summary", {})
        markers = [str(item.get("gene")) for item in packet_payload.get("markers", []) if item.get("gene")]
        clusters[cluster_id] = {
            "cluster_id": cluster_id,
            "initial_label": summary.get("assigned_label"),
            "cell_count": summary.get("cell_count"),
            "max_percentage": summary.get("max_percentage"),
            "other_annotations": summary.get("other_annotations"),
            "avg_distance": summary.get("avg_distance"),
            "markers": markers,
            "marker_text": ", ".join(markers[:10]),
            "focus_candidates": [],
            "focus_candidates_text": "",
            "selection_source": "",
            "final_label": "",
            "status": "",
            "llm_reason": "",
            "supporting_markers": [],
            "weakening_markers": [],
            "reference_limitations": "",
            "llm_question": "",
            "relation_mode": "",
            "consensus_ancestor": "",
            "user_note": "",
        }

    ontology_index = _load_json_if_exists(outputs.get("ontology_relations", {}).get("index_json")) or {}
    for relation_item in ontology_index.get("relations", []):
        cluster_id = str(relation_item.get("cluster_id") or "")
        cluster = clusters.setdefault(cluster_id, {"cluster_id": cluster_id})
        cluster["relation_mode"] = relation_item.get("relation_mode")
        cluster["consensus_ancestor"] = relation_item.get("consensus_ancestor")
        cluster["focus_candidates"] = relation_item.get("focus_candidates", [])
        cluster["focus_candidates_text"] = " | ".join(relation_item.get("focus_candidates", []))
        relation_payload = _load_json_if_exists(relation_item.get("relation_json")) or {}
        comparison = relation_payload.get("comparison_brief", {})
        cluster["llm_question"] = comparison.get("llm_question", "")

    llm_index = _load_json_if_exists(outputs.get("llm_compare", {}).get("index_json")) or {}
    for result_item in llm_index.get("results", []):
        cluster_id = str(result_item.get("cluster_id") or "")
        cluster = clusters.setdefault(cluster_id, {"cluster_id": cluster_id})
        cluster["status"] = result_item.get("status") or ""
        result_payload = _load_json_if_exists(result_item.get("result_json")) or {}
        normalized = result_payload.get("result", {})
        cluster["llm_reason"] = str(normalized.get("reason") or "")
        cluster["supporting_markers"] = normalized.get("supporting_markers", []) or []
        cluster["weakening_markers"] = normalized.get("weakening_markers", []) or []
        cluster["reference_limitations"] = str(normalized.get("reference_limitations") or "")

    for row in _read_csv_rows(outputs.get("reviewed_parent", {}).get("cluster_decisions_csv")):
        cluster_id = str(row.get("cluster_id") or "")
        cluster = clusters.setdefault(cluster_id, {"cluster_id": cluster_id})
        cluster["final_label"] = row.get("final_label", "")
        cluster["selection_source"] = row.get("selection_source", "")
        cluster["status"] = row.get("status", cluster.get("status", ""))
        cluster["user_note"] = row.get("user_note", "")
        if not cluster.get("focus_candidates_text"):
            cluster["focus_candidates_text"] = row.get("focus_candidates", "")
            cluster["focus_candidates"] = [item.strip() for item in row.get("focus_candidates", "").split("|") if item.strip()]

    cluster_list = sorted(clusters.values(), key=lambda item: _cluster_sort_key(str(item.get("cluster_id") or "")))
    summary = {
        "total_clusters": len(cluster_list),
        "llm_compared": sum(1 for item in cluster_list if item.get("status") == "completed"),
        "changed_clusters": sum(
            1
            for item in cluster_list
            if item.get("final_label") and item.get("initial_label") and item["final_label"] != item["initial_label"]
        ),
        "manual_reviewed": sum(
            1 for item in cluster_list if str(item.get("selection_source") or "").startswith("interactive_review")
        ),
    }
    return cluster_list, summary


def _truthy_csv_value(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def _build_rag_review_payload(outputs: dict[str, Any]) -> dict[str, Any]:
    review_outputs = outputs.get("review_packets", {}) if isinstance(outputs.get("review_packets"), dict) else {}
    controller_outputs = outputs.get("controller", {}) if isinstance(outputs.get("controller"), dict) else {}
    llm_outputs = outputs.get("llm_compare", {}) if isinstance(outputs.get("llm_compare"), dict) else {}

    review_rows = _read_csv_rows(review_outputs.get("summary_csv"))
    controller_rows = _read_csv_rows(controller_outputs.get("summary_csv"))
    llm_rows = _read_csv_rows(llm_outputs.get("summary_csv"))
    controller_summary = controller_outputs.get("summary", {}) if isinstance(controller_outputs.get("summary"), dict) else {}

    available = bool(review_rows or controller_rows or llm_rows or controller_summary)
    if not available:
        return {
            "available": False,
            "discussion": "",
            "review_packet_count": 0,
            "initial_flagged_count": 0,
            "llm_compared_count": 0,
            "ask_user_count": 0,
            "rows": [],
        }

    review_packet_count = len(review_rows)
    initial_flagged_count = sum(1 for row in review_rows if _truthy_csv_value(row.get("needs_review")))
    llm_compared_count = len(llm_rows)
    ask_user_count = int(controller_summary.get("ask_user_count", 0) or 0)
    finalize_keep_count = int(controller_summary.get("finalize_keep_count", 0) or 0)
    finalize_llm_count = int(controller_summary.get("finalize_llm_count", 0) or 0)

    rows = []
    for row in controller_rows:
        rows.append(
            {
                "cluster_id": row.get("cluster_id", ""),
                "current_label": row.get("current_label", ""),
                "phase": row.get("phase", ""),
                "next_action": row.get("next_action", ""),
                "recommended_label": row.get("recommended_label", ""),
                "llm_decision": row.get("llm_decision", ""),
                "reason_codes": row.get("reason_codes", ""),
            }
        )

    discussion = (
        f"RAG check built {review_packet_count} review packet(s), initially flagged "
        f"{initial_flagged_count} cluster(s), sent {llm_compared_count} cluster(s) to LLM comparison, "
        f"kept {finalize_keep_count} cluster(s) without label changes, accepted {finalize_llm_count} LLM-supported choice(s), "
        f"and left {ask_user_count} cluster(s) for human review."
    )

    return {
        "available": True,
        "discussion": discussion,
        "review_packet_count": review_packet_count,
        "initial_flagged_count": initial_flagged_count,
        "llm_compared_count": llm_compared_count,
        "ask_user_count": ask_user_count,
        "rows": rows,
    }


def _resolve_parent_annotation_outputs(config: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
    imported = resolve_imported_parent_annotations(config)
    parent_outputs = outputs.get("annotate_parent", {}) if isinstance(outputs.get("annotate_parent"), dict) else {}
    gptanno_tools = outputs.get("gptanno_tools", {}) if isinstance(outputs.get("gptanno_tools"), dict) else {}
    assign_parent_outputs = (
        gptanno_tools.get("assign_parent_labels", {})
        if isinstance(gptanno_tools.get("assign_parent_labels"), dict)
        else {}
    )

    work_dir_value = None
    project_config = config.get("project", {}) if isinstance(config, dict) else {}
    if isinstance(project_config, dict):
        work_dir_value = project_config.get("work_dir")
    parent_dir = Path(str(work_dir_value)) / "annotate_parent" if work_dir_value else None
    fallback_parent_seurat = str(parent_dir / "seurat_parent_annotated.rds") if parent_dir else ""

    def existing_path(*candidates: Any) -> str:
        for candidate in candidates:
            value = str(candidate or "").strip()
            if value and Path(value).exists():
                return value
        return ""

    resolved = {
        "parent_seurat_rds": existing_path(
            imported.get("parent_seurat_rds") or "",
            parent_outputs.get("parent_seurat_rds") or "",
            assign_parent_outputs.get("parent_seurat_rds") or "",
            fallback_parent_seurat,
        ),
        "cluster_col": (
            imported.get("cluster_col")
            or
            parent_outputs.get("cluster_col")
            or assign_parent_outputs.get("cluster_col")
            or ""
        ),
        "best_resolution": (
            imported.get("best_resolution")
            or
            parent_outputs.get("best_resolution")
            or assign_parent_outputs.get("best_resolution")
            or ""
        ),
    }

    if not resolved["cluster_col"] and parent_dir:
        best_resolution_json = parent_dir / "best_parent_resolution.json"
        if best_resolution_json.exists():
            payload = load_json(best_resolution_json)
            best_resolution = str(payload.get("best_resolution") or "").strip()
            if best_resolution:
                resolved["best_resolution"] = resolved["best_resolution"] or best_resolution
                normalized = best_resolution.replace("res_", "", 1)
                resolved["cluster_col"] = f"cluster_res.{normalized}"

    return resolved


def _resolve_reviewed_parent_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
    reviewed_outputs = outputs.get("reviewed_parent", {}) if isinstance(outputs.get("reviewed_parent"), dict) else {}
    return {
        "seurat_rds": reviewed_outputs.get("seurat_rds") or "",
        "label_col": reviewed_outputs.get("label_col") or "celltype_parent_reviewed",
    }


def _build_figure_payload(figure_outputs: dict[str, Any], report_dir: Path) -> dict[str, list[dict[str, str]]]:
    figures = figure_outputs.get("figures", {}) if isinstance(figure_outputs, dict) else {}
    parent_keys = [
        ("parent_clusters", "Parent Clusters"),
        ("parent_initial", "Initial Parent Annotation"),
        ("parent_reviewed", "Reviewed Parent Annotation"),
    ]
    subcluster_keys = [
        ("subcluster_final", "Subcluster Final Annotation"),
        ("subcluster_inherited", "Subcluster Inherited Annotation"),
    ]
    payload = {"parent": [], "subcluster": []}
    for key, title in parent_keys:
        path = figures.get(key)
        if path and Path(path).exists():
            rel = _relative_href(report_dir, path) or str(path)
            src = _image_data_uri(path) or rel
            payload["parent"].append(
                {
                    "title": title,
                    "path": str(path),
                    "uri": path_uri(path) or "",
                    "href": rel,
                    "src": src,
                }
            )
    for key, title in subcluster_keys:
        path = figures.get(key)
        if path and Path(path).exists():
            rel = _relative_href(report_dir, path) or str(path)
            src = _image_data_uri(path) or rel
            payload["subcluster"].append(
                {
                    "title": title,
                    "path": str(path),
                    "uri": path_uri(path) or "",
                    "href": rel,
                    "src": src,
                }
            )
    return payload


def _build_subcluster_payload(config: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
    subcluster_outputs = _resolve_subcluster_outputs(config, outputs)
    if not subcluster_outputs or not subcluster_outputs.get("subclustering_performed"):
        return {}

    parent_names = []
    root_candidates: list[Path] = []
    subcluster_folder_value = str(subcluster_outputs.get("subcluster_folder") or "").strip()
    if subcluster_folder_value:
        subcluster_folder = Path(subcluster_folder_value)
        if subcluster_folder.exists() and subcluster_folder.is_dir():
            root_candidates.append(subcluster_folder)

    work_dir_value = None
    project_config = config.get("project", {}) if isinstance(config, dict) else {}
    if isinstance(project_config, dict):
        work_dir_value = project_config.get("work_dir")
    if work_dir_value:
        annotate_subclusters_dir = Path(str(work_dir_value)) / "annotate_subclusters"
        if annotate_subclusters_dir.exists():
            root_candidates.extend(
                path
                for path in sorted(annotate_subclusters_dir.glob("subclusters_res*"))
                if path.is_dir()
            )

    seen_parent_names: set[str] = set()
    for root in root_candidates:
        for item in root.iterdir():
            if not item.is_dir():
                continue
            parent_name = item.name.replace("_", " ").strip()
            if parent_name and parent_name not in seen_parent_names:
                seen_parent_names.add(parent_name)
                parent_names.append(parent_name)
    parent_names = sorted(parent_names)

    final_labels = set()
    inherited_labels = set()
    for row in _read_csv_rows(subcluster_outputs.get("final_metadata_csv")):
        final_value = (row.get("celltype_final") or "").strip()
        inherited_value = (row.get("celltype_final_inherited") or "").strip()
        if final_value:
            final_labels.add(final_value)
        if inherited_value:
            inherited_labels.add(inherited_value)

    return {
        "performed": True,
        "parent_celltypes": parent_names,
        "parent_celltypes_text": ", ".join(parent_names) if parent_names else "N/A",
        "final_label_count": len(final_labels),
        "inherited_label_count": len(inherited_labels),
    }


def _resolve_subcluster_outputs(config: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
    explicit = outputs.get("annotate_subclusters", {})
    if explicit and explicit.get("final_seurat_rds"):
        return explicit

    work_dir_value = None
    project_config = config.get("project", {}) if isinstance(config, dict) else {}
    if isinstance(project_config, dict):
        work_dir_value = project_config.get("work_dir")
    if not work_dir_value:
        return explicit or {}

    subcluster_dir = Path(str(work_dir_value)) / "annotate_subclusters"
    final_seurat = subcluster_dir / "seurat_final_annotated.rds"
    final_metadata = subcluster_dir / "metadata_final.csv"
    ontology_workflow = subcluster_dir / "ontology_workflow.rds"
    inheritance_workflow = subcluster_dir / "marker_inheritance_workflow.rds"
    dimplot_pdf = subcluster_dir / "DimPlot_celltype_final.pdf"
    if not final_seurat.exists():
        return explicit or {}

    resolved = dict(explicit or {})
    resolved.setdefault("final_seurat_rds", str(final_seurat))
    if final_metadata.exists():
        resolved.setdefault("final_metadata_csv", str(final_metadata))
    if ontology_workflow.exists():
        resolved.setdefault("ontology_workflow_rds", str(ontology_workflow))
    if inheritance_workflow.exists():
        resolved.setdefault("inheritance_workflow_rds", str(inheritance_workflow))
    if dimplot_pdf.exists():
        resolved.setdefault("final_dimplot_pdf", str(dimplot_pdf))
    subcluster_roots = sorted(
        path for path in subcluster_dir.glob("subclusters_res*") if path.is_dir()
    )
    if subcluster_roots:
        resolved.setdefault("subcluster_folder", str(subcluster_roots[0]))
        resolved.setdefault("subclustering_performed", True)
    else:
        resolved.setdefault("subclustering_performed", final_seurat.exists())
    return resolved


def _build_report_context(config: dict[str, Any], state: dict[str, Any], manifest: dict[str, Any], report_path: Path) -> dict[str, Any]:
    outputs = _resolve_report_outputs(config, manifest, report_path.parent)
    warnings: list[str] = []
    errors: list[str] = []

    for stage_name, stage_state in state.get("stages", {}).items():
        if stage_state.get("status") == "skipped" and stage_state.get("message"):
            warnings.append(f"{stage_name}: {stage_state['message']}")
        if stage_state.get("status") == "failed" and stage_state.get("error"):
            errors.append(f"{stage_name}: {stage_state['error']}")

    figure_outputs = _prepare_report_figures(config, outputs, report_path.parent)
    warnings.extend(figure_outputs.get("warnings", []) or [])
    figures = _build_figure_payload(figure_outputs, report_path.parent)

    clusters, cluster_summary = _build_cluster_payload(outputs)
    rag_review = _build_rag_review_payload(outputs)
    subcluster_outputs = _resolve_subcluster_outputs(config, outputs)
    subcluster = _build_subcluster_payload(config, outputs)
    parent_outputs = _resolve_parent_annotation_outputs(config, outputs)

    annotation = {
        "best_parent_resolution": parent_outputs.get("best_resolution"),
        "cluster_col": parent_outputs.get("cluster_col"),
        "subcluster_folder": subcluster_outputs.get("subcluster_folder"),
        "tissue_name": config.get("annotation", {}).get("tissue_name"),
    }
    policy = config.get("policy", {}) if isinstance(config.get("policy"), dict) else {}

    return {
        "project_name": config["project"]["name"],
        "run_id": state["run_id"],
        "generated_at": utc_now(),
        "annotation_provider": config["llm"]["annotation"]["provider"],
        "annotation_model": config["llm"]["annotation"]["model"],
        "annotation": annotation,
        "policy": policy,
        "cluster_summary": cluster_summary,
        "rag_review": rag_review,
        "figures": figures,
        "clusters": clusters,
        "subcluster": subcluster,
        "warnings": warnings,
        "errors": errors,
    }


def _render_html_report(context: dict[str, Any], report_path: Path) -> Path:
    env = Environment(autoescape=select_autoescape(enabled_extensions=("html",)))
    template = env.from_string(REPORT_TEMPLATE)
    html = template.render(**context)
    ensure_dir(report_path.parent)
    report_path.write_text(html, encoding="utf-8")
    return report_path


def _font_path(paths: list[str]) -> str | None:
    for item in paths:
        if Path(item).exists():
            return item
    return None


def _load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    path = _font_path(FONT_BOLD_PATHS if bold else FONT_REGULAR_PATHS)
    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text or "Ag", font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    for paragraph in str(text).splitlines():
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            width, _ = _text_size(draw, candidate, font)
            if width <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


class _PDFDoc:
    def __init__(self) -> None:
        self.title_font = _load_font(34, bold=True)
        self.h1_font = _load_font(26, bold=True)
        self.h2_font = _load_font(22, bold=True)
        self.body_font = _load_font(17)
        self.small_font = _load_font(14)
        self.pages: list[Image.Image] = []
        self._new_page()

    def _new_page(self) -> None:
        self.page = Image.new("RGB", (PDF_PAGE_WIDTH, PDF_PAGE_HEIGHT), PDF_BG)
        self.draw = ImageDraw.Draw(self.page)
        self.y = PDF_MARGIN
        self.pages.append(self.page)

    def _line_height(self, font: ImageFont.ImageFont) -> int:
        _, height = _text_size(self.draw, "Ag", font)
        return height + 6

    def _ensure(self, height: int) -> None:
        if self.y + height > PDF_PAGE_HEIGHT - PDF_MARGIN:
            self._new_page()

    def title(self, text: str) -> None:
        self._ensure(80)
        self.draw.text((PDF_MARGIN, self.y), text, fill=PDF_FG, font=self.title_font)
        self.y += 56

    def heading(self, text: str, *, level: int = 1) -> None:
        font = self.h1_font if level == 1 else self.h2_font
        self._ensure(44)
        self.draw.text((PDF_MARGIN, self.y), text, fill=PDF_FG, font=font)
        self.y += 34

    def spacer(self, size: int = 12) -> None:
        self.y += size

    def rule(self) -> None:
        self._ensure(16)
        self.draw.line(
            [(PDF_MARGIN, self.y), (PDF_PAGE_WIDTH - PDF_MARGIN, self.y)],
            fill=PDF_ACCENT,
            width=2,
        )
        self.y += 16

    def paragraph(self, text: str, *, font: ImageFont.ImageFont | None = None, color: str = PDF_FG) -> None:
        active_font = font or self.body_font
        max_width = PDF_PAGE_WIDTH - (2 * PDF_MARGIN)
        lines = _wrap_text(self.draw, text, active_font, max_width)
        line_height = self._line_height(active_font)
        self._ensure((line_height * max(len(lines), 1)) + 4)
        for line in lines:
            self.draw.text((PDF_MARGIN, self.y), line, fill=color, font=active_font)
            self.y += line_height
        self.y += 4

    def kv(self, label: str, value: str) -> None:
        self.paragraph(f"{label}: {value}")

    def bullet_list(self, items: list[str]) -> None:
        for item in items:
            self.paragraph(f"- {item}")

    def image_page(self, title: str, image_path: str | None) -> None:
        if not image_path or not Path(image_path).exists():
            return
        self._new_page()
        self.heading(title, level=1)
        with Image.open(image_path) as source:
            img = source.convert("RGB")
        max_width = PDF_PAGE_WIDTH - (2 * PDF_MARGIN)
        max_height = PDF_PAGE_HEIGHT - self.y - PDF_MARGIN
        ratio = min(max_width / img.width, max_height / img.height)
        new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
        resized = img.resize(new_size)
        x = (PDF_PAGE_WIDTH - resized.width) // 2
        self.page.paste(resized, (x, self.y))
        self.y += resized.height + 20

    def save_pdf(self, report_path: Path) -> Path:
        ensure_dir(report_path.parent)
        pages = [page.convert("RGB") for page in self.pages]
        first, rest = pages[0], pages[1:]
        first.save(report_path, save_all=True, append_images=rest)
        return report_path


def _render_pdf_report(context: dict[str, Any], report_path: Path) -> Path:
    doc = _PDFDoc()
    annotation = context["annotation"]
    policy = context["policy"]
    cluster_summary = context["cluster_summary"]

    doc.title(f"{context['project_name']} - OntoAnno Report")
    doc.paragraph(f"Run ID: {context['run_id']}")
    doc.paragraph(f"Generated: {context['generated_at']}")
    doc.paragraph(f"Dataset: {annotation.get('tissue_name') or 'N/A'}")
    doc.paragraph(f"Model: {context['annotation_provider']}/{context['annotation_model']}")
    doc.paragraph(f"Best parent resolution: {annotation.get('best_parent_resolution') or 'N/A'}")
    doc.paragraph(f"Policy: ontology={policy.get('ontology')}, granularity={policy.get('granularity')}")
    doc.spacer()
    doc.heading("Run Summary", level=1)
    doc.paragraph(f"Parent clusters: {cluster_summary['total_clusters']}")
    doc.paragraph(f"Compared by LLM: {cluster_summary['llm_compared']}")
    doc.paragraph(f"Changed by agent: {cluster_summary['changed_clusters']}")
    doc.paragraph(f"Manual review picks: {cluster_summary['manual_reviewed']}")

    for figure in context["figures"].get("parent", []):
        doc.image_page(figure["title"], figure["path"])

    rag_review = context.get("rag_review", {})
    if rag_review.get("available"):
        doc._new_page()
        doc.heading("RAG Check Review", level=1)
        doc.paragraph(str(rag_review.get("discussion") or ""))
        doc.kv("Review packets", str(rag_review.get("review_packet_count", 0)))
        doc.kv("Flagged initially", str(rag_review.get("initial_flagged_count", 0)))
        doc.kv("LLM compared", str(rag_review.get("llm_compared_count", 0)))
        doc.kv("Human review needed", str(rag_review.get("ask_user_count", 0)))
        for row in rag_review.get("rows", [])[:20]:
            doc.paragraph(
                "Cluster {cluster_id} | {current_label} | {phase} | {next_action} | {recommended_label} | {reason_codes}".format(
                    **{
                        "cluster_id": row.get("cluster_id", ""),
                        "current_label": row.get("current_label", ""),
                        "phase": row.get("phase", ""),
                        "next_action": row.get("next_action", ""),
                        "recommended_label": row.get("recommended_label", ""),
                        "reason_codes": row.get("reason_codes", ""),
                    }
                ),
                font=doc.small_font,
            )
            doc.spacer(4)

    doc._new_page()
    doc.heading("Parent Cluster Overview", level=1)
    for cluster in context["clusters"]:
        summary = (
            f"Cluster {cluster['cluster_id']} | {cluster.get('cell_count') or 'N/A'} cells | "
            f"{cluster.get('initial_label') or 'N/A'} -> {cluster.get('final_label') or cluster.get('initial_label') or 'N/A'}"
        )
        doc.paragraph(summary, font=doc.body_font)
        if cluster.get("marker_text"):
            doc.paragraph(f"Markers: {cluster['marker_text']}", font=doc.small_font, color=PDF_SUBTLE)
        doc.spacer(6)

    for cluster in context["clusters"]:
        doc._new_page()
        doc.heading(
            f"Cluster {cluster['cluster_id']}"
            + (f" - {cluster['initial_label']}" if cluster.get("initial_label") else ""),
            level=1,
        )
        doc.kv("Cells", str(cluster.get("cell_count") or "N/A"))
        doc.kv("Initial annotation", str(cluster.get("initial_label") or "N/A"))
        doc.kv("Final annotation", str(cluster.get("final_label") or cluster.get("initial_label") or "N/A"))
        doc.kv("Selection source", str(cluster.get("selection_source") or "N/A"))
        if cluster.get("marker_text"):
            doc.kv("Top markers", cluster["marker_text"])
        if cluster.get("other_annotations"):
            doc.kv("Other surfaced annotations", cluster["other_annotations"])
        if cluster.get("focus_candidates_text"):
            doc.kv("Compared candidates", cluster["focus_candidates_text"])
        if cluster.get("relation_mode") or cluster.get("consensus_ancestor"):
            text = str(cluster.get("relation_mode") or "N/A")
            if cluster.get("consensus_ancestor"):
                text += f" | shared ancestor: {cluster['consensus_ancestor']}"
            doc.kv("Ontology compare", text)
        if cluster.get("llm_question"):
            doc.kv("LLM question", cluster["llm_question"])
        if cluster.get("llm_reason"):
            doc.kv("LLM reason", cluster["llm_reason"])
        if cluster.get("supporting_markers"):
            doc.kv("Supporting markers", ", ".join(cluster["supporting_markers"]))
        if cluster.get("weakening_markers"):
            doc.kv("Weakening markers", ", ".join(cluster["weakening_markers"]))
        if cluster.get("reference_limitations"):
            doc.kv("Reference limitations", cluster["reference_limitations"])
        if cluster.get("user_note"):
            doc.kv("User note", cluster["user_note"])

    if context.get("subcluster"):
        doc._new_page()
        doc.heading("Subcluster Summary", level=1)
        subcluster = context["subcluster"]
        doc.kv("Subclustering performed", str(subcluster.get("performed")))
        doc.kv("Subclustered parent cell types", str(subcluster.get("parent_celltypes_text") or "N/A"))
        doc.kv("Unique final labels", str(subcluster.get("final_label_count") or "N/A"))
        doc.kv("Unique inherited labels", str(subcluster.get("inherited_label_count") or "N/A"))
        for figure in context["figures"].get("subcluster", []):
            doc.image_page(figure["title"], figure["path"])

    if context["warnings"]:
        doc._new_page()
        doc.heading("Warnings", level=1)
        doc.bullet_list(context["warnings"])

    if context["errors"]:
        doc._new_page()
        doc.heading("Errors", level=1)
        doc.bullet_list(context["errors"])

    return doc.save_pdf(report_path)


def generate_report(config: dict[str, Any], state: dict[str, Any], manifest: dict[str, Any], report_path: Path) -> Path:
    context = _build_report_context(config, state, manifest, report_path)
    report_format = str(config.get("report", {}).get("format") or "html").lower()
    if report_format == "html":
        return _render_html_report(context, report_path)
    return _render_pdf_report(context, report_path)
