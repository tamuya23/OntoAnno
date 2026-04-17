from __future__ import annotations

from pathlib import Path
from typing import Any

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

AVAILABLE_WORKERS = [
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
        return True

    gptanno_tools = outputs.get("gptanno_tools", {}) if isinstance(outputs.get("gptanno_tools"), dict) else {}
    assign_parent = gptanno_tools.get("assign_parent_labels", {})
    if isinstance(assign_parent, dict) and bool(assign_parent):
        return True

    parent_dir = orchestrator.work_dir / "annotate_parent"
    required_files = [
        parent_dir / "annotation_summary_scores.csv",
        parent_dir / "parent_ontology_mapping.csv",
        parent_dir / "seurat_parent_annotated.rds",
    ]
    return all(path.exists() for path in required_files)


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


def _missing_paths(*paths: Path) -> list[str]:
    return [str(path) for path in paths if not path.exists()]


def _has_review_packets(orchestrator: Any) -> bool:
    return (orchestrator.run_dir / "review_packets" / "index.json").exists()


def _has_controller_index(orchestrator: Any) -> bool:
    return (orchestrator.run_dir / "controller" / "index.json").exists()


def _has_ontology_relations(orchestrator: Any) -> bool:
    outputs = orchestrator.manifest.get("outputs", {}) if isinstance(orchestrator.manifest.get("outputs"), dict) else {}
    if isinstance(outputs.get("ontology_relations"), dict) and outputs.get("ontology_relations"):
        return True
    return (orchestrator.run_dir / "ontology_relations" / "ontology_relations.outputs.json").exists()


def worker_prerequisite_status(orchestrator: Any, worker: str) -> dict[str, Any]:
    parent_dir = orchestrator.work_dir / "annotate_parent"
    subcluster_dir = orchestrator.work_dir / "annotate_subclusters"
    input_rds = Path(str(orchestrator.config.get("inputs", {}).get("seurat_rds") or ""))
    preprocessed_rds = parent_dir / "seurat_preprocessed.rds"
    clustered_rds = parent_dir / "seurat_clustered.rds"
    annotation_parent_rds = parent_dir / "annotation_parent.rds"
    best_parent_resolution_json = parent_dir / "best_parent_resolution.json"
    parent_seurat_rds = parent_dir / "seurat_parent_annotated.rds"
    subcluster_result_rds = subcluster_dir / "subcluster_find_markers.rds"
    ontology_workflow_rds = subcluster_dir / "ontology_workflow.rds"
    inheritance_workflow_rds = subcluster_dir / "marker_inheritance_workflow.rds"
    decisions_json, _ = _interactive_decisions_paths(orchestrator.run_dir)

    missing: list[str] = []
    notes: list[str] = []

    if worker == "preprocess_parent":
        missing = _missing_paths(input_rds)
    elif worker == "cluster_parent_markers":
        missing = _missing_paths(preprocessed_rds)
        notes.append("Run preprocess_parent first.")
    elif worker == "annotate_parent_raw":
        missing = _missing_paths(clustered_rds)
        if not (parent_dir / "marker_genes").exists():
            missing.append(str(parent_dir / "marker_genes"))
        notes.append("Run cluster_parent_markers first.")
    elif worker == "map_parent_ontology":
        missing = _missing_paths(annotation_parent_rds)
        notes.append("Run annotate_parent_raw first.")
    elif worker == "select_parent_resolution":
        missing = _missing_paths(annotation_parent_rds)
        notes.append("Run annotate_parent_raw first.")
    elif worker == "assign_parent_labels":
        missing = _missing_paths(clustered_rds, annotation_parent_rds, best_parent_resolution_json)
        notes.append("Run select_parent_resolution first if best_parent_resolution.json is missing.")
    elif worker == "subcluster_find_markers":
        missing = _missing_paths(parent_seurat_rds)
        targets = orchestrator.config.get("alignment", {}).get("celltypes_to_subcluster")
        if not isinstance(targets, list) or not [item for item in targets if str(item).strip()]:
            missing.append("alignment.celltypes_to_subcluster")
        notes.append("Run assign_parent_labels and configure target cell type(s) first.")
    elif worker in {"subcluster_annotate_ontology", "subcluster_annotate_inheritance"}:
        missing = _missing_paths(subcluster_result_rds)
        notes.append("Run subcluster_find_markers first.")
    elif worker == "finalize_subcluster_annotations":
        missing = _missing_paths(ontology_workflow_rds, inheritance_workflow_rds)
        notes.append("Run both subcluster annotation workers first.")
    elif worker == "build_review_packets":
        if not has_parent_annotation_outputs(orchestrator):
            missing = [str(parent_seurat_rds), str(parent_dir / "parent_ontology_mapping.csv")]
            notes.append(
                "Run parent annotation through assign_parent_labels first, or configure inputs.annotation_parent_rds with existing GPTAnno annotation artifacts."
            )
    elif worker == "decide_rag_check":
        if not _has_review_packets(orchestrator):
            missing = [str(orchestrator.run_dir / "review_packets" / "index.json")]
            notes.append("Run build_review_packets first.")
    elif worker == "build_candidate_map":
        if not _has_controller_index(orchestrator):
            missing = [str(orchestrator.run_dir / "controller" / "index.json")]
            notes.append("Run decide_rag_check first.")
    elif worker == "retrieve_rag_evidence":
        if not _has_ontology_relations(orchestrator):
            missing = [str(orchestrator.run_dir / "ontology_relations" / "ontology_relations.outputs.json")]
            notes.append("Run build_candidate_map first.")
    elif worker == "run_llm_compare":
        if not _has_ontology_relations(orchestrator):
            missing = [str(orchestrator.run_dir / "ontology_relations" / "ontology_relations.outputs.json")]
            notes.append("Run build_candidate_map first.")
    elif worker == "human_review":
        if not _has_controller_index(orchestrator):
            missing = [str(orchestrator.run_dir / "controller" / "index.json")]
            notes.append("Run decide_rag_check post_compare first.")
    elif worker == "export_reviewed_parent_annotations":
        missing = _missing_paths(decisions_json)
        notes.append("Run or save human-review decisions first.")
    elif worker == "generate_report":
        if not has_parent_annotation_outputs(orchestrator):
            missing = [str(parent_seurat_rds)]
            notes.append("Run parent annotation first, or configure inputs.annotation_parent_rds with existing GPTAnno annotation artifacts.")

    missing = [item for item in missing if item]
    ok = len(missing) == 0
    return {
        "ok": ok,
        "status": "ready" if ok else "blocked",
        "missing": missing,
        "notes": [] if ok else notes,
    }


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
    return executed


def run_review_packets_worker(orchestrator: Any, *, force: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    if not has_parent_annotation_outputs(orchestrator):
        outputs = {"missing_prerequisite": "annotate_parent outputs"}
        return outputs, _worker_result(
            worker="build_review_packets",
            implementation="build_review_packets",
            outputs=outputs,
            status="blocked",
            notes=["RAG check requires existing parent annotation outputs. Run the parent pipeline first."],
        )
    outputs = orchestrator.generate_review_packets(force=force)
    return outputs, _worker_result(
        worker="build_review_packets",
        implementation="build_review_packets",
        outputs=outputs,
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

    if not has_parent_annotation_outputs(orchestrator):
        executed.append(
            _worker_result(
                worker="build_review_packets",
                implementation="build_review_packets",
                status="blocked",
                outputs={"missing_prerequisite": "annotate_parent outputs"},
                notes=["RAG check requires existing parent annotation outputs. Run the parent pipeline first."],
            )
        )
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
    return executed, ask_user_count


def _run_candidate_map_with_prereqs(orchestrator: Any, *, force: bool) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not has_parent_annotation_outputs(orchestrator):
        return None, _worker_result(
            worker="build_candidate_map",
            implementation="build_ontology_relations",
            status="blocked",
            outputs={"missing_prerequisite": "annotate_parent outputs"},
            notes=["Candidate-map construction requires existing parent annotation outputs."],
        )
    run_review_packets_worker(orchestrator, force=False)
    run_decide_rag_check_worker(orchestrator, phase="initial", force=False)
    ontology_cluster_ids = _controller_cluster_ids(orchestrator.run_dir, "build_ontology_relations")
    return run_candidate_map_worker(orchestrator, cluster_ids=ontology_cluster_ids, force=force)


def _run_llm_compare_with_prereqs(orchestrator: Any, *, force: bool) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not has_parent_annotation_outputs(orchestrator):
        return None, _worker_result(
            worker="run_llm_compare",
            implementation="build_llm_compare",
            status="blocked",
            outputs={"missing_prerequisite": "annotate_parent outputs"},
            notes=["LLM compare requires existing parent annotation outputs."],
        )
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
    if not decisions_path.exists():
        outputs = {
            "decisions_json": str(decisions_path),
            "decisions_csv": str(decisions_csv),
            "missing_prerequisite": "interactive_decisions.json",
        }
        return outputs, _worker_result(
            worker="export_reviewed_parent_annotations",
            implementation="interactive_cli.export_reviewed_parent_annotations",
            outputs=outputs,
            status="blocked",
            notes=[
                "Reviewed parent export requires saved human-review decisions.",
                "Collect decisions first through `ontoanno agent` or an interactive human-review flow.",
            ],
        )
    decisions = load_json(decisions_path)
    outputs = export_reviewed_parent_annotations(
        config=orchestrator.config,
        run_dir=orchestrator.run_dir,
        decisions=decisions,
    )
    return outputs, _worker_result(
        worker="export_reviewed_parent_annotations",
        implementation="interactive_cli.export_reviewed_parent_annotations",
        outputs=outputs,
    )


def run_generate_report_worker(orchestrator: Any, *, force: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_outputs = orchestrator.manifest.get("outputs", {})
    reviewed_outputs = (
        manifest_outputs.get("reviewed_parent", {})
        if isinstance(manifest_outputs.get("reviewed_parent"), dict)
        else {}
    )
    if not reviewed_outputs.get("seurat_rds"):
        decisions_path, _ = _interactive_decisions_paths(orchestrator.run_dir)
        review_packets_index = orchestrator.run_dir / "review_packets" / "index.json"
        if decisions_path.exists() and review_packets_index.exists():
            run_export_reviewed_parent_worker(orchestrator)

    run_dir = orchestrator.run(from_stage="report", to_stage="report", force=force)
    outputs = orchestrator.manifest.get("outputs", {}).get("report", {})
    outputs = {"run_dir": str(run_dir), **(outputs if isinstance(outputs, dict) else {})}
    return outputs, _worker_result(
        worker="generate_report",
        implementation="orchestrator.run(report)",
        outputs=outputs,
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

    if worker in PARENT_BACKBONE_WORKERS:
        return run_gptanno_worker_chain(orchestrator, [worker], force=True if force else False)[0]

    if worker in SUBCLUSTER_WORKERS:
        return run_gptanno_worker_chain(orchestrator, [worker], force=True if force else False)[0]

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
        run_decide_rag_check_worker(orchestrator, phase="post_compare", force=False)
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
