from __future__ import annotations

import csv
import os
import shutil
from pathlib import Path
from typing import Any

from .utils import dump_json, ensure_dir, utc_now


LARGE_RESULT_SUFFIXES = {".rds", ".h5ad", ".h5seurat"}


RESULT_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "parent_best_resolution.json": (
        "Parent annotation",
        "Selected parent clustering resolution and cluster column used for parent labels.",
    ),
    "parent_resolution_scores.csv": (
        "Parent annotation",
        "Score table used to compare candidate parent resolutions.",
    ),
    "parent_annotated_seurat.rds": (
        "Parent annotation",
        "Seurat object with assigned parent labels.",
    ),
    "parent_raw_gptanno_annotations.rds": (
        "Parent annotation",
        "Raw GPTAnno annotation results across tested parent resolutions.",
    ),
    "parent_ontology_mapping.csv": (
        "Parent annotation",
        "Ontology mapping table for parent annotation candidates.",
    ),
    "subcluster_metadata.csv": (
        "Subcluster annotation",
        "Per-cell metadata after subcluster annotation and inheritance.",
    ),
    "subcluster_final_seurat.rds": (
        "Subcluster annotation",
        "Seurat object with final subcluster labels.",
    ),
    "rag_review_packets_summary.csv": (
        "RAG review",
        "Per-cluster review packet summary built from parent annotation and markers.",
    ),
    "rag_ontology_candidates.csv": (
        "RAG review",
        "Ontology candidate comparison table for clusters that needed RAG review.",
    ),
    "rag_llm_compare_summary.csv": (
        "RAG review",
        "LLM judge decisions and reasons for clusters sent to LLM comparison.",
    ),
    "rag_controller_summary.csv": (
        "RAG review",
        "Controller summary showing whether each cluster was kept, changed, or sent to human review.",
    ),
    "reviewed_parent_metadata.csv": (
        "Reviewed parent labels",
        "Per-cell metadata with reviewed parent labels.",
    ),
    "reviewed_parent_seurat.rds": (
        "Reviewed parent labels",
        "Seurat object with reviewed parent labels.",
    ),
    "reviewed_cluster_decisions.csv": (
        "Reviewed parent labels",
        "Cluster-level final labels and decision sources after automated or human review.",
    ),
    "reviewed_decisions.json": (
        "Reviewed parent labels",
        "Machine-readable reviewed cluster decisions.",
    ),
    "final_report.html": (
        "Report",
        "Final OntoAnno HTML report.",
    ),
    "final_report.pdf": (
        "Report",
        "Final OntoAnno PDF report.",
    ),
    "external_evidence_memory.json": (
        "External evidence",
        "Stored user-provided and literature-provided marker evidence for this project.",
    ),
}


MANAGED_RESULT_FILES = set(RESULT_DESCRIPTIONS) | {
    "README.md",
    "result_index.csv",
    "result_index.json",
}


def _as_path(value: Any) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    path = Path(text)
    return path if path.exists() else None


def _remove_managed_results(results_dir: Path) -> None:
    for name in MANAGED_RESULT_FILES:
        path = results_dir / name
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def _copy_or_link(src: Path, dest: Path) -> str:
    ensure_dir(dest.parent)
    if dest.is_symlink() or dest.exists():
        if dest.is_dir() and not dest.is_symlink():
            shutil.rmtree(dest)
        else:
            dest.unlink()

    if src.suffix.lower() in LARGE_RESULT_SUFFIXES:
        try:
            rel_src = os.path.relpath(src, dest.parent)
            dest.symlink_to(rel_src, target_is_directory=src.is_dir())
            return "symlink"
        except OSError:
            pass

    shutil.copy2(src, dest)
    return "copy"


def _add_candidate(candidates: dict[str, Path], dest_name: str, source: Any) -> None:
    if dest_name in candidates:
        return
    path = _as_path(source)
    if path is not None and path.is_file():
        candidates[dest_name] = path


def _candidate_sources(
    *,
    config: dict[str, Any],
    run_dir: Path | None,
    manifest: dict[str, Any] | None,
) -> dict[str, Path]:
    work_dir = Path(str(config["project"]["work_dir"]))
    parent_dir = work_dir / "annotate_parent"
    subcluster_dir = work_dir / "annotate_subclusters"
    outputs = manifest.get("outputs", {}) if isinstance(manifest, dict) and isinstance(manifest.get("outputs"), dict) else {}
    gptanno = outputs.get("gptanno_tools", {}) if isinstance(outputs.get("gptanno_tools"), dict) else {}
    assign_parent = gptanno.get("assign_parent_labels", {}) if isinstance(gptanno.get("assign_parent_labels"), dict) else {}
    annotate_parent = gptanno.get("annotate_parent_raw", {}) if isinstance(gptanno.get("annotate_parent_raw"), dict) else {}
    map_parent = gptanno.get("map_parent_ontology", {}) if isinstance(gptanno.get("map_parent_ontology"), dict) else {}
    subcluster_final = (
        gptanno.get("finalize_subcluster_annotations", {})
        if isinstance(gptanno.get("finalize_subcluster_annotations"), dict)
        else {}
    )
    reviewed = outputs.get("reviewed_parent", {}) if isinstance(outputs.get("reviewed_parent"), dict) else {}
    report = outputs.get("report", {}) if isinstance(outputs.get("report"), dict) else {}

    candidates: dict[str, Path] = {}
    _add_candidate(candidates, "parent_best_resolution.json", assign_parent.get("best_parent_resolution_json") or parent_dir / "best_parent_resolution.json")
    _add_candidate(candidates, "parent_resolution_scores.csv", assign_parent.get("annotation_scores_csv") or parent_dir / "annotation_summary_scores.csv")
    _add_candidate(candidates, "parent_annotated_seurat.rds", assign_parent.get("parent_seurat_rds") or parent_dir / "seurat_parent_annotated.rds")
    _add_candidate(candidates, "parent_raw_gptanno_annotations.rds", annotate_parent.get("annotation_parent_rds") or parent_dir / "annotation_parent.rds")
    _add_candidate(candidates, "parent_ontology_mapping.csv", map_parent.get("ontology_mapping_csv") or parent_dir / "parent_ontology_mapping.csv")

    _add_candidate(candidates, "subcluster_metadata.csv", subcluster_final.get("final_metadata_csv") or subcluster_dir / "metadata_final.csv")
    _add_candidate(candidates, "subcluster_final_seurat.rds", subcluster_final.get("final_seurat_rds") or subcluster_final.get("final_annotated_rds") or subcluster_dir / "seurat_final_annotated.rds")

    if run_dir is not None:
        _add_candidate(candidates, "rag_review_packets_summary.csv", run_dir / "review_packets" / "summary.csv")
        _add_candidate(candidates, "rag_ontology_candidates.csv", run_dir / "ontology_relations" / "summary.csv")
        _add_candidate(candidates, "rag_llm_compare_summary.csv", run_dir / "llm_compare" / "summary.csv")
        _add_candidate(candidates, "rag_controller_summary.csv", run_dir / "controller" / "summary.csv")

    _add_candidate(candidates, "reviewed_parent_metadata.csv", reviewed.get("metadata_csv"))
    _add_candidate(candidates, "reviewed_parent_seurat.rds", reviewed.get("seurat_rds"))
    _add_candidate(candidates, "reviewed_cluster_decisions.csv", reviewed.get("cluster_decisions_csv") or reviewed.get("decisions_csv"))
    _add_candidate(candidates, "reviewed_decisions.json", reviewed.get("decisions_json"))

    report_path = report.get("report_path")
    report_html = report.get("report_html") or (report_path if str(report_path or "").lower().endswith(".html") else None)
    report_pdf = report.get("report_pdf") or (report_path if str(report_path or "").lower().endswith(".pdf") else None)
    _add_candidate(candidates, "final_report.html", report_html)
    _add_candidate(candidates, "final_report.pdf", report_pdf)

    _add_candidate(candidates, "external_evidence_memory.json", work_dir / "ontoanno_memory.json")
    return candidates


def _write_results_readme(results_dir: Path, records: list[dict[str, str]]) -> None:
    lines = [
        "# OntoAnno Results",
        "",
        "This folder collects the main user-facing outputs for the project.",
        "Large Seurat files may be symbolic links to avoid duplicating data.",
        "",
        "See `result_index.csv` for the source path and explanation for each file.",
        "",
        "## Files",
        "",
    ]
    for record in records:
        lines.append(f"- `{record['file']}`: {record['description']}")
    if not records:
        lines.append("- No final outputs are available yet.")
    lines.append("")
    (results_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def sync_project_results(
    *,
    config: dict[str, Any],
    run_dir: Path | None = None,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    work_dir = Path(str(config["project"]["work_dir"]))
    results_dir = ensure_dir(work_dir / "results")
    _remove_managed_results(results_dir)

    records: list[dict[str, str]] = []
    for dest_name, source_path in sorted(_candidate_sources(config=config, run_dir=run_dir, manifest=manifest).items()):
        category, description = RESULT_DESCRIPTIONS[dest_name]
        dest_path = results_dir / dest_name
        try:
            method = _copy_or_link(source_path, dest_path)
            status = "available"
            error = ""
        except OSError as exc:
            method = "error"
            status = "error"
            error = str(exc)
        records.append(
            {
                "file": dest_name,
                "category": category,
                "description": description,
                "result_path": str(dest_path),
                "source_path": str(source_path),
                "method": method,
                "status": status,
                "error": error,
            }
        )

    fieldnames = ["file", "category", "description", "result_path", "source_path", "method", "status", "error"]
    index_csv = results_dir / "result_index.csv"
    with index_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    index_json = results_dir / "result_index.json"
    dump_json(
        index_json,
        {
            "project_name": config["project"]["name"],
            "work_dir": str(work_dir),
            "results_dir": str(results_dir),
            "run_dir": str(run_dir) if run_dir else None,
            "generated_at": utc_now(),
            "results": records,
        },
    )
    _write_results_readme(results_dir, records)

    return {
        "results_dir": str(results_dir),
        "index_csv": str(index_csv),
        "index_json": str(index_json),
        "readme": str(results_dir / "README.md"),
        "result_count": len(records),
    }
