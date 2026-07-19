from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .results_export import sync_project_results
from .review_packets import has_imported_parent_annotation_inputs
from .utils import load_json


PARENT_BACKBONE_WORKERS = [
    "preprocess_parent",
    "cluster_parent_markers",
    "annotate_parent_raw",
    "map_parent_ontology",
    "select_parent_resolution",
    "assign_parent_labels",
]

SUBCLUSTER_WORKERS = [
    "subcluster_find_markers",
    "subcluster_annotate_ontology",
    "subcluster_annotate_inheritance",
    "finalize_subcluster_annotations",
]

RAG_WORKERS = [
    "build_review_packets",
    "decide_rag_check",
    "build_candidate_map",
    "retrieve_rag_evidence",
    "run_llm_compare",
    "human_review",
]

OUTPUT_WORKERS = [
    "export_reviewed_parent_annotations",
    "generate_report",
]

INSPECTION_WORKERS = [
    "inspect_dataset",
]

AVAILABLE_WORKERS = [
    *INSPECTION_WORKERS,
    *PARENT_BACKBONE_WORKERS,
    *SUBCLUSTER_WORKERS,
    *RAG_WORKERS,
    *OUTPUT_WORKERS,
]


def has_parent_annotation_outputs(orchestrator: Any) -> bool:
    if has_imported_parent_annotation_inputs(orchestrator.config):
        return True

    outputs = orchestrator.manifest.get("outputs", {}) if isinstance(orchestrator.manifest.get("outputs"), dict) else {}
    annotate_parent = outputs.get("annotate_parent", {})
    if isinstance(annotate_parent, dict) and bool(annotate_parent):
        annotation_parent_rds = str(annotate_parent.get("annotation_parent_rds") or "")
        markers_dir = str(annotate_parent.get("markers_dir") or "")
        if (
            annotation_parent_rds
            and Path(annotation_parent_rds).exists()
            and markers_dir
            and Path(markers_dir).is_dir()
            and annotate_parent.get("best_resolution")
            and annotate_parent.get("cluster_col")
        ):
            return True

    parent_dir = orchestrator.work_dir / "annotate_parent"
    required_files = [
        parent_dir / "annotation_parent.rds",
        parent_dir / "annotation_summary_scores.csv",
        parent_dir / "parent_ontology_mapping.csv",
        parent_dir / "best_parent_resolution.json",
        parent_dir / "seurat_parent_annotated.rds",
    ]
    if all(path.exists() for path in required_files) and (parent_dir / "marker_genes").is_dir():
        return True

    gptanno_tools = outputs.get("gptanno_tools", {}) if isinstance(outputs.get("gptanno_tools"), dict) else {}
    annotate_parent_raw = gptanno_tools.get("annotate_parent_raw", {})
    cluster_parent = gptanno_tools.get("cluster_parent_markers", {})
    assign_parent = gptanno_tools.get("assign_parent_labels", {})
    annotation_parent_rds = (
        str(annotate_parent_raw.get("annotation_parent_rds") or "")
        if isinstance(annotate_parent_raw, dict)
        else ""
    )
    markers_dir = (
        str(cluster_parent.get("markers_dir") or "")
        if isinstance(cluster_parent, dict)
        else ""
    )
    parent_seurat_rds = (
        str(assign_parent.get("parent_seurat_rds") or "")
        if isinstance(assign_parent, dict)
        else ""
    )
    if (
        annotation_parent_rds
        and Path(annotation_parent_rds).exists()
        and markers_dir
        and Path(markers_dir).is_dir()
        and parent_seurat_rds
        and Path(parent_seurat_rds).exists()
        and assign_parent.get("best_resolution")
        and assign_parent.get("cluster_col")
    ):
        return True

    return False


def _compact_outputs(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    compact: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            if key == "summary":
                compact[key] = value
            continue
        if isinstance(value, list):
            compact[f"{key}_count"] = len(value)
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            compact[key] = value
    return compact


def _worker_result(
    *,
    worker: str,
    implementation: str,
    outputs: dict[str, Any] | None = None,
    status: str = "completed",
    notes: list[str] | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "worker": worker,
        "tool": worker,
        "label": label or worker,
        "implementation": implementation,
        "status": status,
    }
    compact = _compact_outputs(outputs)
    if compact:
        result["artifacts"] = compact
    if notes:
        result["notes"] = list(notes)
    return result


def _sync_project_results(orchestrator: Any) -> dict[str, Any]:
    try:
        return sync_project_results(
            config=orchestrator.config,
            run_dir=orchestrator.run_dir,
            manifest=orchestrator.manifest,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _blocked_worker_result(worker: str, readiness: dict[str, Any]) -> dict[str, Any]:
    missing = [str(item) for item in readiness.get("missing", []) if str(item).strip()]
    notes = [str(item) for item in readiness.get("notes", []) if str(item).strip()]
    if missing:
        notes.append("Missing prerequisite(s): " + ", ".join(missing))
    return _worker_result(
        worker=worker,
        implementation="worker_prerequisite_guard",
        status="blocked",
        outputs={"missing_prerequisites": missing},
        notes=notes or ["This worker is blocked by missing prerequisites."],
    )


def run_inspect_dataset_worker(orchestrator: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    config = orchestrator.config
    project = config.get("project", {}) if isinstance(config.get("project"), dict) else {}
    inputs = config.get("inputs", {}) if isinstance(config.get("inputs"), dict) else {}
    annotation = config.get("annotation", {}) if isinstance(config.get("annotation"), dict) else {}

    seurat_rds = str(inputs.get("seurat_rds") or "").strip()
    seurat_path = Path(seurat_rds) if seurat_rds else None
    summary = {
        "project_name": str(project.get("name") or ""),
        "species": str(annotation.get("species") or "not configured"),
        "tissue_name": str(annotation.get("tissue_name") or "not configured"),
        "seurat_rds": seurat_rds or None,
        "seurat_rds_exists": bool(seurat_path and seurat_path.is_file()),
        "preprocess": bool(annotation.get("preprocess", True)),
        "parent_resolutions": list(annotation.get("parent_res") or []),
        "subcluster_resolutions": list(annotation.get("sub_res") or []),
        "minimum_subcluster_cell_count": annotation.get("min_cell_count"),
        "reference_labels_csv": inputs.get("reference_labels_csv") or inputs.get("manual_labels_csv"),
        "work_dir": str(project.get("work_dir") or ""),
        "run_id": str(getattr(orchestrator, "run_id", "") or ""),
    }
    outputs = {"summary": summary}
    return outputs, _worker_result(
        worker="inspect_dataset",
        implementation="config_dataset_summary",
        outputs=outputs,
        notes=["Read-only inspection; no analysis workers were run and no project state was changed."],
    )


def _missing_paths(*paths: Path) -> list[str]:
    return [str(path) for path in paths if not path.exists()]


def _has_review_packets(orchestrator: Any) -> bool:
    return (orchestrator.run_dir / "review_packets" / "index.json").exists()


def _parent_seurat_from_review_index(run_dir: Path) -> str | None:
    index_path = run_dir / "review_packets" / "index.json"
    if not index_path.exists():
        return None
    index = load_json(index_path)
    shared = index.get("shared", {}) if isinstance(index.get("shared"), dict) else {}
    files = shared.get("files", {}) if isinstance(shared.get("files"), dict) else {}
    parent_seurat = str(files.get("parent_seurat_rds") or "").strip()
    return parent_seurat or None


def _has_r_bootstrap_parent(config: dict[str, Any]) -> bool:
    inputs = config.get("inputs", {}) if isinstance(config.get("inputs"), dict) else {}
    bootstrap = inputs.get("bootstrap_parent") if isinstance(inputs.get("bootstrap_parent"), dict) else {}
    required = ("annotation_parent_rds", "annotation_scores_csv", "parent_seurat_rds", "markers_dir")
    return bool(bootstrap) and all(bootstrap.get(key) and Path(str(bootstrap.get(key))).exists() for key in required)


def _clear_downstream_rag_outputs(orchestrator: Any) -> None:
    """Review packets define the RAG input set; downstream outputs become stale when they change."""
    clear = getattr(orchestrator, "clear_rag_dependent_outputs", None)
    if callable(clear):
        clear()
        return
    stale_stages = ("ontology_relations", "llm_compare", "controller", "reviewed_parent")
    for stage in stale_stages:
        path = orchestrator.run_dir / stage
        if path.exists():
            shutil.rmtree(path)

    outputs = orchestrator.manifest.get("outputs")
    if isinstance(outputs, dict):
        for stage in stale_stages:
            outputs.pop(stage, None)
        save_manifest = getattr(orchestrator, "_save_manifest", None)
        if callable(save_manifest):
            save_manifest()
    _clear_report_outputs(orchestrator)


def _clear_after_ontology_outputs(orchestrator: Any) -> None:
    clear = getattr(orchestrator, "clear_after_ontology_outputs", None)
    if callable(clear):
        clear()
        return
    stale_stages = ("llm_compare", "controller", "reviewed_parent")
    for stage in stale_stages:
        path = orchestrator.run_dir / stage
        if path.exists():
            shutil.rmtree(path)

    outputs = orchestrator.manifest.get("outputs")
    if isinstance(outputs, dict):
        for stage in stale_stages:
            outputs.pop(stage, None)
        save_manifest = getattr(orchestrator, "_save_manifest", None)
        if callable(save_manifest):
            save_manifest()
    _clear_report_outputs(orchestrator)


def _clear_after_llm_outputs(orchestrator: Any) -> None:
    clear = getattr(orchestrator, "clear_after_llm_outputs", None)
    if callable(clear):
        clear()
        return
    stale_stages = ("controller", "reviewed_parent")
    for stage in stale_stages:
        path = orchestrator.run_dir / stage
        if path.exists():
            shutil.rmtree(path)

    outputs = orchestrator.manifest.get("outputs")
    if isinstance(outputs, dict):
        for stage in stale_stages:
            outputs.pop(stage, None)
        save_manifest = getattr(orchestrator, "_save_manifest", None)
        if callable(save_manifest):
            save_manifest()
    _clear_report_outputs(orchestrator)


def _clear_report_outputs(orchestrator: Any) -> None:
    clear = getattr(orchestrator, "clear_report_outputs", None)
    if callable(clear):
        clear()
        return
    for path in [
        orchestrator.run_dir / "report_assets",
        orchestrator.run_dir / "report.html",
        orchestrator.run_dir / "report.pdf",
    ]:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    outputs = orchestrator.manifest.get("outputs")
    if isinstance(outputs, dict):
        outputs.pop("report", None)
        save_manifest = getattr(orchestrator, "_save_manifest", None)
        if callable(save_manifest):
            save_manifest()


def _clear_subcluster_outputs(orchestrator: Any) -> None:
    clear = getattr(orchestrator, "clear_subcluster_outputs", None)
    if callable(clear):
        clear()
        return
    subcluster_dir = orchestrator.work_dir / "annotate_subclusters"
    if subcluster_dir.exists():
        shutil.rmtree(subcluster_dir)

    outputs = orchestrator.manifest.get("outputs")
    if isinstance(outputs, dict):
        outputs.pop("annotate_subclusters", None)
        gptanno_tools = outputs.get("gptanno_tools")
        if isinstance(gptanno_tools, dict):
            for worker in SUBCLUSTER_WORKERS:
                gptanno_tools.pop(worker, None)
        save_manifest = getattr(orchestrator, "_save_manifest", None)
        if callable(save_manifest):
            save_manifest()


def _clear_parent_dependent_outputs(orchestrator: Any) -> None:
    clear = getattr(orchestrator, "clear_parent_dependent_outputs", None)
    if callable(clear):
        clear()
        return
    _clear_subcluster_outputs(orchestrator)
    _clear_downstream_rag_outputs(orchestrator)
    _clear_report_outputs(orchestrator)


def clear_rag_dependent_outputs(orchestrator: Any) -> None:
    _clear_downstream_rag_outputs(orchestrator)


def clear_parent_dependent_outputs(orchestrator: Any) -> None:
    _clear_parent_dependent_outputs(orchestrator)


def worker_prerequisite_status(orchestrator: Any, worker: str) -> dict[str, Any]:
    parent_dir = orchestrator.work_dir / "annotate_parent"
    subcluster_dir = orchestrator.work_dir / "annotate_subclusters"
    input_rds_raw = str(orchestrator.config.get("inputs", {}).get("seurat_rds") or "").strip()
    input_rds = Path(input_rds_raw) if input_rds_raw else None
    has_raw_input = bool(input_rds_raw and input_rds is not None and input_rds.exists())
    has_r_bootstrap_parent = _has_r_bootstrap_parent(orchestrator.config)
    can_run_parent_backbone = has_raw_input or has_r_bootstrap_parent
    parent_backbone_missing = input_rds_raw or "inputs.seurat_rds or inputs.bootstrap_parent"
    has_parent_outputs = has_parent_annotation_outputs(orchestrator)
    parent_seurat_rds = parent_dir / "seurat_parent_annotated.rds"
    decisions_json, _ = _interactive_decisions_paths(orchestrator.run_dir)

    missing: list[str] = []
    notes: list[str] = []

    if worker == "preprocess_parent":
        if not can_run_parent_backbone:
            missing = [parent_backbone_missing]
    elif worker == "cluster_parent_markers":
        if not can_run_parent_backbone:
            missing = [parent_backbone_missing]
        notes.append("This worker auto-runs preprocess_parent if needed.")
    elif worker == "annotate_parent_raw":
        if not can_run_parent_backbone:
            missing = [parent_backbone_missing]
        notes.append("This worker auto-runs preprocess_parent and cluster_parent_markers if needed.")
    elif worker == "map_parent_ontology":
        if not can_run_parent_backbone:
            missing = [parent_backbone_missing]
        notes.append("This worker auto-runs parent annotation prerequisites if needed.")
    elif worker == "select_parent_resolution":
        if not can_run_parent_backbone:
            missing = [parent_backbone_missing]
        notes.append("This worker auto-runs parent annotation prerequisites if needed.")
    elif worker == "assign_parent_labels":
        if not can_run_parent_backbone:
            missing = [parent_backbone_missing]
        notes.append("This worker auto-runs parent annotation and resolution-selection prerequisites if needed.")
    elif worker == "subcluster_find_markers":
        if not can_run_parent_backbone:
            missing = [parent_backbone_missing]
        targets = orchestrator.config.get("alignment", {}).get("celltypes_to_subcluster")
        if not isinstance(targets, list) or not [item for item in targets if str(item).strip()]:
            missing.append("alignment.celltypes_to_subcluster")
        notes.append("This worker auto-runs assign_parent_labels if parent labels are missing.")
    elif worker in {"subcluster_annotate_ontology", "subcluster_annotate_inheritance"}:
        targets = orchestrator.config.get("alignment", {}).get("celltypes_to_subcluster")
        if not isinstance(targets, list) or not [item for item in targets if str(item).strip()]:
            missing.append("alignment.celltypes_to_subcluster")
        if not can_run_parent_backbone:
            missing.append(parent_backbone_missing)
        notes.append("This worker auto-runs subcluster_find_markers if needed.")
    elif worker == "finalize_subcluster_annotations":
        targets = orchestrator.config.get("alignment", {}).get("celltypes_to_subcluster")
        if not isinstance(targets, list) or not [item for item in targets if str(item).strip()]:
            missing.append("alignment.celltypes_to_subcluster")
        if not can_run_parent_backbone:
            missing.append(parent_backbone_missing)
        notes.append("This worker auto-runs both subcluster annotation branches if needed.")
    elif worker == "build_review_packets":
        if not has_parent_outputs:
            if can_run_parent_backbone:
                notes.append("This worker auto-runs parent annotation prerequisites if needed.")
            else:
                missing = [str(parent_seurat_rds), str(parent_dir / "parent_ontology_mapping.csv")]
                notes.append(
                    "Run parent annotation through assign_parent_labels first, or configure inputs.annotation_parent_rds with existing GPTAnno annotation artifacts."
                )
    elif worker == "decide_rag_check":
        if not _has_review_packets(orchestrator):
            missing = [str(orchestrator.run_dir / "review_packets" / "index.json")]
            notes.append("Run build_review_packets first.")
    elif worker == "build_candidate_map":
        if not has_parent_outputs:
            if can_run_parent_backbone:
                notes.append("This worker auto-runs parent annotation prerequisites if needed.")
            else:
                missing = [str(parent_seurat_rds), str(parent_dir / "parent_ontology_mapping.csv")]
        notes.append("This worker auto-runs build_review_packets and decide_rag_check(initial) if needed.")
    elif worker == "retrieve_rag_evidence":
        if not has_parent_outputs:
            if can_run_parent_backbone:
                notes.append("This worker auto-runs parent annotation prerequisites if needed.")
            else:
                missing = [str(parent_seurat_rds), str(parent_dir / "parent_ontology_mapping.csv")]
        notes.append("This proxy worker auto-runs candidate-map prerequisites if needed.")
    elif worker == "run_llm_compare":
        if not has_parent_outputs:
            if can_run_parent_backbone:
                notes.append("This worker auto-runs parent annotation prerequisites if needed.")
            else:
                missing = [str(parent_seurat_rds), str(parent_dir / "parent_ontology_mapping.csv")]
        notes.append("This worker auto-runs review packets, controller, candidate map, and post-ontology planning if needed.")
    elif worker == "human_review":
        if not _has_review_packets(orchestrator):
            missing = [str(orchestrator.run_dir / "review_packets" / "index.json")]
            notes.append("Run run_RAG_check or build_review_packets first; human review does not create review packets by itself.")
        else:
            notes.append("This worker auto-runs decide_rag_check(post_compare) to load the current unresolved clusters.")
    elif worker == "export_reviewed_parent_annotations":
        parent_seurat = _parent_seurat_from_review_index(orchestrator.run_dir)
        if not parent_seurat or not Path(parent_seurat).exists():
            missing = [parent_seurat or "review_packets/index.json:shared.files.parent_seurat_rds"]
            notes.append("Reviewed parent export requires a parent Seurat RDS to write per-cell reviewed labels.")
        elif not decisions_json.exists():
            auto_decisions, blocked_clusters = _automatic_review_decisions(orchestrator.run_dir)
            if blocked_clusters or not auto_decisions:
                missing = _missing_paths(orchestrator.run_dir / "controller" / "index.json") or [str(decisions_json)]
                notes.append(
                    "Run RAG check first and save human-review decisions for unresolved clusters before exporting reviewed annotations."
                )
            else:
                notes.append("No human-review decision file exists, but terminal controller states can be exported automatically.")
    elif worker == "generate_report":
        if not has_parent_annotation_outputs(orchestrator):
            if can_run_parent_backbone:
                notes.append("This worker auto-runs parent annotation prerequisites if needed.")
            else:
                missing = [parent_backbone_missing]
                notes.append(
                    "Run parent annotation first, or configure inputs.annotation_parent_rds with existing GPTAnno annotation artifacts."
                )

    missing = [item for item in missing if item]
    ok = len(missing) == 0
    return {
        "ok": ok,
        "status": "ready" if ok else "blocked",
        "missing": missing,
        "notes": notes,
    }


def ensure_parent_annotation_outputs(
    orchestrator: Any,
    *,
    force: bool = True,
) -> tuple[bool, list[dict[str, Any]], dict[str, Any]]:
    """Create parent annotation artifacts when a downstream worker needs them.

    This covers the marker-genes-only entry point: users may provide cluster marker
    files, skip marker discovery, and still ask for RAG, subcluster, or report.
    """
    if has_parent_annotation_outputs(orchestrator):
        return True, [], {"ok": True, "status": "ready", "missing": [], "notes": []}

    readiness = worker_prerequisite_status(orchestrator, "assign_parent_labels")
    if not readiness.get("ok"):
        return False, [_blocked_worker_result("assign_parent_labels", readiness)], readiness

    executed = run_gptanno_worker_chain(orchestrator, PARENT_BACKBONE_WORKERS, force=force)
    if has_parent_annotation_outputs(orchestrator):
        return True, executed, {
            "ok": True,
            "status": "ready",
            "missing": [],
            "notes": ["Generated parent annotation outputs as downstream prerequisites."],
        }

    readiness = {
        "ok": False,
        "status": "blocked",
        "missing": ["parent annotation outputs"],
        "notes": ["Parent backbone completed, but required parent annotation artifacts were not found."],
    }
    return False, [*executed, _blocked_worker_result("assign_parent_labels", readiness)], readiness


def _controller_cluster_ids(run_dir: Path, action: str) -> list[str]:
    index_path = run_dir / "controller" / "index.json"
    if not index_path.exists():
        return []
    payload = load_json(index_path)
    cluster_ids: list[str] = []
    for item in payload.get("states", []):
        state_path = Path(str(item.get("state_json") or ""))
        if not state_path.exists():
            continue
        state = load_json(state_path)
        if str(state.get("next_action") or "") != action:
            continue
        cluster_id = str(state.get("cluster_id") or "").strip()
        if cluster_id:
            cluster_ids.append(cluster_id)
    return cluster_ids


def _controller_states(run_dir: Path) -> list[dict[str, Any]]:
    index_path = run_dir / "controller" / "index.json"
    if not index_path.exists():
        return []
    payload = load_json(index_path)
    states: list[dict[str, Any]] = []
    for item in payload.get("states", []):
        state_path = Path(str(item.get("state_json") or ""))
        state = load_json(state_path) if state_path.exists() else {}
        if not isinstance(state, dict):
            state = {}
        if item.get("cluster_id") and not state.get("cluster_id"):
            state["cluster_id"] = item.get("cluster_id")
        states.append(state)
    return states


def _automatic_review_decisions(run_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Build export decisions only when controller states are already terminal."""
    decisions: list[dict[str, Any]] = []
    blocked_clusters: list[str] = []
    for state in _controller_states(run_dir):
        cluster_id = str(state.get("cluster_id") or "").strip()
        current_label = str(state.get("current_label") or "")
        focus_candidates = [str(value) for value in state.get("focus_candidates", []) if str(value).strip()]
        if current_label and current_label not in focus_candidates:
            focus_candidates = [current_label, *focus_candidates]

        next_action = str(state.get("next_action") or "")
        recommended_label = str(state.get("recommended_label") or "")
        if next_action == "finalize_keep_current":
            final_label = current_label
            selection_source = "controller_keep_current"
        elif next_action == "finalize_llm_choice" and recommended_label:
            final_label = recommended_label
            selection_source = "controller_llm_choose"
        else:
            blocked_clusters.append(cluster_id or "<unknown>")
            continue

        decisions.append(
            {
                "cluster_id": cluster_id,
                "current_label": current_label,
                "final_label": final_label,
                "focus_candidates": focus_candidates,
                "result_json": str(state.get("result_json") or ""),
                "selection_source": selection_source,
                "status": str(state.get("llm_status") or ""),
                "user_note": "",
            }
        )
    return decisions, blocked_clusters


def run_gptanno_worker_chain(
    orchestrator: Any,
    workers: list[str],
    *,
    force: bool = True,
) -> list[dict[str, Any]]:
    executed: list[dict[str, Any]] = []
    for worker in workers:
        outputs = orchestrator.generate_gptanno_tool(worker, force=force)
        executed.append(
            _worker_result(
                worker=worker,
                implementation=f"gptanno_tool:{worker}",
                outputs=outputs,
            )
        )
    if force and any(worker in PARENT_BACKBONE_WORKERS for worker in workers):
        _clear_parent_dependent_outputs(orchestrator)
    elif force and any(worker in SUBCLUSTER_WORKERS for worker in workers):
        _clear_report_outputs(orchestrator)
    _sync_project_results(orchestrator)
    return executed


def run_review_packets_worker(orchestrator: Any, *, force: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    parent_ready, parent_workers, readiness = ensure_parent_annotation_outputs(orchestrator, force=force)
    if not parent_ready:
        outputs = {"missing_prerequisite": "annotate_parent outputs"}
        return outputs, _worker_result(
            worker="build_review_packets",
            implementation="build_review_packets",
            outputs=outputs,
            status="blocked",
            notes=[
                "RAG check requires parent annotation outputs, and OntoAnno could not create them automatically.",
                *[str(item) for item in readiness.get("notes", [])],
            ],
        )
    outputs = orchestrator.generate_review_packets(force=force)
    if force:
        _clear_downstream_rag_outputs(orchestrator)
    notes = None
    if parent_workers:
        notes = ["Auto-ran parent annotation prerequisites before building review packets."]
    return outputs, _worker_result(
        worker="build_review_packets",
        implementation="build_review_packets",
        outputs=outputs,
        notes=notes,
    )


def run_decide_rag_check_worker(
    orchestrator: Any,
    *,
    phase: str,
    force: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    outputs = orchestrator.generate_controller(force=force, phase=phase)
    label = f"decide_rag_check ({phase})"
    return outputs, _worker_result(
        worker="decide_rag_check",
        implementation=f"build_controller({phase})",
        outputs=outputs,
        label=label,
    )


def run_candidate_map_worker(
    orchestrator: Any,
    *,
    cluster_ids: list[str] | None,
    force: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    cluster_ids = [str(item) for item in (cluster_ids or []) if str(item).strip()]
    if not cluster_ids:
        return None, _worker_result(
            worker="build_candidate_map",
            implementation="build_ontology_relations",
            status="skipped",
            notes=["No clusters require candidate-map construction in the current controller state."],
        )
    outputs = orchestrator.generate_ontology_relations(cluster_ids=cluster_ids, force=force)
    if force:
        _clear_after_ontology_outputs(orchestrator)
    return outputs, _worker_result(
        worker="build_candidate_map",
        implementation="build_ontology_relations",
        outputs=outputs,
        notes=[f"cluster_ids={','.join(cluster_ids)}"],
    )


def run_retrieve_rag_evidence_worker(candidate_map_outputs: dict[str, Any] | None) -> dict[str, Any]:
    if not candidate_map_outputs:
        return _worker_result(
            worker="retrieve_rag_evidence",
            implementation="build_ontology_relations",
            status="skipped",
            notes=["Evidence retrieval is currently embedded inside build_ontology_relations."],
        )
    return _worker_result(
        worker="retrieve_rag_evidence",
        implementation="build_ontology_relations",
        outputs=candidate_map_outputs,
        notes=[
            "Reference-database retrieval is currently embedded inside build_ontology_relations.",
            "Memory-backed evidence should continue to feed this same layer.",
        ],
    )


def run_llm_compare_worker(
    orchestrator: Any,
    *,
    cluster_ids: list[str] | None,
    force: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    cluster_ids = [str(item) for item in (cluster_ids or []) if str(item).strip()]
    if not cluster_ids:
        return None, _worker_result(
            worker="run_llm_compare",
            implementation="build_llm_compare",
            status="skipped",
            notes=["No clusters require LLM compare in the current controller state."],
        )
    outputs = orchestrator.generate_llm_compare(cluster_ids=cluster_ids, force=force)
    if force:
        _clear_after_llm_outputs(orchestrator)
    summary = outputs.get("summary", {}) if isinstance(outputs, dict) else {}
    completed_count = int(
        (summary.get("completed_count", 0) if isinstance(summary, dict) else 0)
        or outputs.get("completed_count", 0)
        or 0
    )
    failed_count = int(
        (summary.get("failed_count", 0) if isinstance(summary, dict) else 0)
        or outputs.get("failed_count", 0)
        or 0
    )
    status = "completed"
    if failed_count > 0 and completed_count <= 0:
        status = "failed"
    elif failed_count > 0:
        status = "partial"
    return outputs, _worker_result(
        worker="run_llm_compare",
        implementation="build_llm_compare",
        outputs=outputs,
        status=status,
        notes=[f"cluster_ids={','.join(cluster_ids)}"],
    )


def _interactive_decisions_paths(run_dir: Path) -> tuple[Path, Path]:
    output_dir = run_dir / "reviewed_parent"
    return output_dir / "interactive_decisions.json", output_dir / "interactive_decisions.csv"


def run_human_review_worker(
    controller_outputs: dict[str, Any] | None,
    *,
    run_dir: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    summary = (controller_outputs or {}).get("summary", {}) if isinstance(controller_outputs, dict) else {}
    ask_user_count = int(summary.get("ask_user_count", 0) or 0)
    status = "needs_user" if ask_user_count > 0 else "skipped"
    outputs: dict[str, Any] = {"summary": summary}
    notes: list[str]
    if ask_user_count > 0:
        notes = [f"{ask_user_count} cluster(s) remain unresolved and require direct human review."]
        if run_dir is not None:
            decisions_json, decisions_csv = _interactive_decisions_paths(run_dir)
            outputs["decisions_json"] = str(decisions_json)
            outputs["decisions_csv"] = str(decisions_csv)
            if decisions_json.exists():
                decisions = load_json(decisions_json)
                decision_count = len(decisions) if isinstance(decisions, list) else 0
                outputs["decision_count"] = decision_count
                status = "completed"
                notes = [f"Loaded {decision_count} saved human-review decision(s)."]
            else:
                notes.append(
                    "No saved human-review decision file exists yet; use `ontoanno agent` or chat/apply flow to collect decisions."
                )
    else:
        notes = ["No unresolved clusters require human review."]
    return ask_user_count, _worker_result(
        worker="human_review",
        implementation="interactive_cli._resolve_cluster_decisions",
        outputs=outputs,
        status=status,
        notes=notes,
    )


def run_rag_check_workers(
    orchestrator: Any,
    *,
    force: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    executed: list[dict[str, Any]] = []
    parent_ready, parent_workers, _ = ensure_parent_annotation_outputs(orchestrator, force=force)
    executed.extend(parent_workers)
    if not parent_ready:
        return executed, 0

    _, review_worker = run_review_packets_worker(orchestrator, force=force)
    executed.append(review_worker)

    _, decide_initial = run_decide_rag_check_worker(orchestrator, phase="initial", force=force)
    executed.append(decide_initial)

    ontology_cluster_ids = _controller_cluster_ids(orchestrator.run_dir, "build_ontology_relations")
    candidate_map_outputs, candidate_map_worker = run_candidate_map_worker(
        orchestrator,
        cluster_ids=ontology_cluster_ids,
        force=force,
    )
    executed.append(candidate_map_worker)
    executed.append(run_retrieve_rag_evidence_worker(candidate_map_outputs))

    _, decide_post_ontology = run_decide_rag_check_worker(orchestrator, phase="post_ontology", force=force)
    executed.append(decide_post_ontology)

    llm_cluster_ids = _controller_cluster_ids(orchestrator.run_dir, "run_llm_compare")
    _, llm_compare_worker = run_llm_compare_worker(
        orchestrator,
        cluster_ids=llm_cluster_ids,
        force=force,
    )
    executed.append(llm_compare_worker)

    controller_post_compare, decide_post_compare = run_decide_rag_check_worker(
        orchestrator,
        phase="post_compare",
        force=force,
    )
    executed.append(decide_post_compare)

    ask_user_count, human_review_worker = run_human_review_worker(
        controller_post_compare,
        run_dir=orchestrator.run_dir,
    )
    executed.append(human_review_worker)
    _sync_project_results(orchestrator)
    return executed, ask_user_count


def _run_candidate_map_with_prereqs(orchestrator: Any, *, force: bool) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    parent_ready, _, readiness = ensure_parent_annotation_outputs(orchestrator, force=force)
    if not parent_ready:
        return None, _blocked_worker_result("build_candidate_map", readiness)
    run_review_packets_worker(orchestrator, force=False)
    run_decide_rag_check_worker(orchestrator, phase="initial", force=False)
    ontology_cluster_ids = _controller_cluster_ids(orchestrator.run_dir, "build_ontology_relations")
    return run_candidate_map_worker(orchestrator, cluster_ids=ontology_cluster_ids, force=force)


def _run_llm_compare_with_prereqs(orchestrator: Any, *, force: bool) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    parent_ready, _, readiness = ensure_parent_annotation_outputs(orchestrator, force=force)
    if not parent_ready:
        return None, _blocked_worker_result("run_llm_compare", readiness)
    _run_candidate_map_with_prereqs(orchestrator, force=False)
    run_decide_rag_check_worker(orchestrator, phase="post_ontology", force=False)
    llm_cluster_ids = _controller_cluster_ids(orchestrator.run_dir, "run_llm_compare")
    return run_llm_compare_worker(orchestrator, cluster_ids=llm_cluster_ids, force=force)


def run_human_review_session_worker(orchestrator: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from .interactive_cli import _resolve_cluster_decisions, _write_decision_files

    decisions = _resolve_cluster_decisions(orchestrator.run_dir)
    output_dir = orchestrator.run_dir / "reviewed_parent"
    decisions_json, decisions_csv = _write_decision_files(output_dir, decisions)
    outputs = {
        "decisions_json": str(decisions_json),
        "decisions_csv": str(decisions_csv),
        "decision_count": len(decisions),
    }
    return decisions, _worker_result(
        worker="human_review",
        implementation="interactive_cli._resolve_cluster_decisions",
        outputs=outputs,
        status="completed",
        notes=["Human review decisions were collected and written to reviewed_parent/interactive_decisions.*"],
    )


def run_export_reviewed_parent_worker(orchestrator: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    from .interactive_cli import export_reviewed_parent_annotations

    decisions_path, decisions_csv = _interactive_decisions_paths(orchestrator.run_dir)
    parent_seurat = _parent_seurat_from_review_index(orchestrator.run_dir)
    if not parent_seurat or not Path(parent_seurat).exists():
        outputs = {
            "parent_seurat_rds": parent_seurat,
            "missing_prerequisite": "parent_seurat_rds",
        }
        return outputs, _worker_result(
            worker="export_reviewed_parent_annotations",
            implementation="interactive_cli.export_reviewed_parent_annotations",
            outputs=outputs,
            status="blocked",
            notes=["Reviewed parent export requires review_packets/index.json to reference an existing parent Seurat RDS."],
        )
    if not decisions_path.exists():
        decisions, blocked_clusters = _automatic_review_decisions(orchestrator.run_dir)
        if decisions and not blocked_clusters:
            outputs = export_reviewed_parent_annotations(
                config=orchestrator.config,
                run_dir=orchestrator.run_dir,
                decisions=decisions,
            )
            orchestrator.manifest.setdefault("outputs", {})["reviewed_parent"] = outputs
            save_manifest = getattr(orchestrator, "_save_manifest", None)
            if callable(save_manifest):
                save_manifest()
            _clear_report_outputs(orchestrator)
            outputs["project_results"] = _sync_project_results(orchestrator)
            if callable(save_manifest):
                save_manifest()
            return outputs, _worker_result(
                worker="export_reviewed_parent_annotations",
                implementation="interactive_cli.export_reviewed_parent_annotations",
                outputs=outputs,
                notes=["No manual decisions were needed; exported terminal controller decisions automatically."],
            )
        outputs = {
            "decisions_json": str(decisions_path),
            "decisions_csv": str(decisions_csv),
            "missing_prerequisite": "interactive_decisions.json",
            "blocked_clusters": blocked_clusters,
        }
        return outputs, _worker_result(
            worker="export_reviewed_parent_annotations",
            implementation="interactive_cli.export_reviewed_parent_annotations",
            outputs=outputs,
            status="blocked",
            notes=[
                "Reviewed parent export requires either terminal controller decisions or saved human-review decisions.",
                "Collect decisions first through the RAG Review UI or an interactive human-review flow.",
            ],
        )
    decisions = load_json(decisions_path)
    outputs = export_reviewed_parent_annotations(
        config=orchestrator.config,
        run_dir=orchestrator.run_dir,
        decisions=decisions,
    )
    orchestrator.manifest.setdefault("outputs", {})["reviewed_parent"] = outputs
    save_manifest = getattr(orchestrator, "_save_manifest", None)
    if callable(save_manifest):
        save_manifest()
    _clear_report_outputs(orchestrator)
    outputs["project_results"] = _sync_project_results(orchestrator)
    if callable(save_manifest):
        save_manifest()
    return outputs, _worker_result(
        worker="export_reviewed_parent_annotations",
        implementation="interactive_cli.export_reviewed_parent_annotations",
        outputs=outputs,
    )


def run_generate_report_worker(orchestrator: Any, *, force: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    parent_ready, parent_workers, readiness = ensure_parent_annotation_outputs(orchestrator, force=force)
    if not parent_ready:
        outputs = {"missing_prerequisite": "annotate_parent outputs"}
        return outputs, _blocked_worker_result("generate_report", readiness)

    manifest_outputs = orchestrator.manifest.get("outputs", {})
    reviewed_outputs = (
        manifest_outputs.get("reviewed_parent", {})
        if isinstance(manifest_outputs.get("reviewed_parent"), dict)
        else {}
    )
    if not reviewed_outputs.get("seurat_rds"):
        review_packets_index = orchestrator.run_dir / "review_packets" / "index.json"
        controller_index = orchestrator.run_dir / "controller" / "index.json"
        if review_packets_index.exists() and controller_index.exists():
            run_export_reviewed_parent_worker(orchestrator)

    run_dir = orchestrator.run(from_stage="report", to_stage="report", force=force)
    outputs = orchestrator.manifest.get("outputs", {}).get("report", {})
    outputs = {"run_dir": str(run_dir), **(outputs if isinstance(outputs, dict) else {})}
    outputs["project_results"] = _sync_project_results(orchestrator)
    orchestrator.manifest.setdefault("outputs", {})["report"] = outputs
    save_manifest = getattr(orchestrator, "_save_manifest", None)
    if callable(save_manifest):
        save_manifest()
    return outputs, _worker_result(
        worker="generate_report",
        implementation="orchestrator.run(report)",
        outputs=outputs,
        notes=["Auto-ran parent annotation prerequisites before report generation."] if parent_workers else None,
    )


def run_named_worker(
    orchestrator: Any,
    worker: str,
    *,
    force: bool = False,
    phase: str = "auto",
) -> dict[str, Any]:
    if worker not in AVAILABLE_WORKERS:
        raise ValueError(f"Unsupported worker: {worker}")

    readiness = worker_prerequisite_status(orchestrator, worker)
    if not readiness.get("ok"):
        return _blocked_worker_result(worker, readiness)

    if worker == "inspect_dataset":
        _, result = run_inspect_dataset_worker(orchestrator)
        return result

    if worker in PARENT_BACKBONE_WORKERS:
        return run_gptanno_worker_chain(orchestrator, [worker], force=True if force else False)[0]

    if worker in SUBCLUSTER_WORKERS:
        parent_ready, parent_workers, parent_readiness = ensure_parent_annotation_outputs(orchestrator, force=True)
        if not parent_ready:
            return _blocked_worker_result(worker, parent_readiness)
        result = run_gptanno_worker_chain(orchestrator, [worker], force=True if force else False)[0]
        if parent_workers:
            result.setdefault("notes", []).append("Auto-ran parent annotation prerequisites first.")
        return result

    if worker == "build_review_packets":
        _, result = run_review_packets_worker(orchestrator, force=force)
        return result

    if worker == "decide_rag_check":
        _, result = run_decide_rag_check_worker(orchestrator, phase=phase, force=force)
        return result

    if worker == "build_candidate_map":
        _, result = _run_candidate_map_with_prereqs(orchestrator, force=force)
        return result

    if worker == "retrieve_rag_evidence":
        candidate_map_outputs, _ = _run_candidate_map_with_prereqs(orchestrator, force=False)
        return run_retrieve_rag_evidence_worker(candidate_map_outputs)

    if worker == "run_llm_compare":
        _, result = _run_llm_compare_with_prereqs(orchestrator, force=force)
        return result

    if worker == "human_review":
        controller_outputs, _ = run_decide_rag_check_worker(orchestrator, phase="post_compare", force=False)
        _, result = run_human_review_worker(controller_outputs, run_dir=orchestrator.run_dir)
        return result

    if worker == "export_reviewed_parent_annotations":
        _, result = run_export_reviewed_parent_worker(orchestrator)
        return result

    if worker == "generate_report":
        _, result = run_generate_report_worker(orchestrator, force=force)
        return result

    raise ValueError(f"Worker dispatch not implemented: {worker}")
