from __future__ import annotations

import csv
import subprocess
from pathlib import Path
from typing import Any

from .utils import dump_json, ensure_dir, load_json, utc_now


class InteractiveReviewError(RuntimeError):
    pass


def _interactive_print(message: str) -> None:
    print(f"[OntoAnno] {message}", flush=True)


def _prompt_granularity(default: str) -> str:
    allowed = {"coarse", "balanced", "fine"}
    _interactive_print(
        "Select ontology comparison policy: [c]oarse, [b]alanced, [f]ine "
        f"(Enter keeps '{default}')"
    )
    while True:
        raw = input("> ").strip().lower()
        if not raw:
            return default
        mapping = {"c": "coarse", "coarse": "coarse", "b": "balanced", "balanced": "balanced", "f": "fine", "fine": "fine"}
        value = mapping.get(raw)
        if value in allowed:
            return value
        _interactive_print("Please enter coarse, balanced, fine, or press Enter.")


def _prompt_choice(
    *,
    cluster_id: str,
    current_label: str,
    options: list[str],
    reason: str,
    markers: list[str],
) -> tuple[str, str]:
    _interactive_print("")
    _interactive_print(f"Cluster {cluster_id} needs review.")
    _interactive_print(f"Current label: {current_label}")
    if markers:
        _interactive_print(f"Top markers: {', '.join(markers[:10])}")
    if reason:
        _interactive_print(f"LLM note: {reason}")
    _interactive_print("Choose the final label:")
    for idx, option in enumerate(options, start=1):
        _interactive_print(f"  {idx}. {option}")

    default_index = 1
    for idx, option in enumerate(options, start=1):
        if option == current_label:
            default_index = idx
            break

    while True:
        raw = input(f"Selection [default {default_index}]: ").strip()
        if not raw:
            selected = options[default_index - 1]
            break
        try:
            index = int(raw)
        except ValueError:
            _interactive_print("Please enter a number from the list or press Enter.")
            continue
        if 1 <= index <= len(options):
            selected = options[index - 1]
            break
        _interactive_print("Selection out of range.")

    note = input("Optional note (press Enter to skip): ").strip()
    return selected, note


def _load_cluster_markers(relation_payload: dict[str, Any]) -> list[str]:
    reference_compare = relation_payload.get("reference_compare", {})
    markers = reference_compare.get("cluster_markers", [])
    return [str(item).strip() for item in markers if str(item).strip()]


def _cluster_sort_key(cluster_id: str) -> tuple[int, str]:
    try:
        return (0, f"{int(cluster_id):06d}")
    except (TypeError, ValueError):
        return (1, str(cluster_id))


def _load_controller_states(run_dir: Path) -> list[dict[str, Any]]:
    controller_index_path = run_dir / "controller" / "index.json"
    if not controller_index_path.exists():
        raise InteractiveReviewError("controller/index.json not found; run controller first.")
    controller_index = load_json(controller_index_path)
    states: list[dict[str, Any]] = []
    for item in controller_index.get("states", []):
        state_path = Path(str(item.get("state_json") or ""))
        if not state_path.exists():
            continue
        states.append(load_json(state_path))
    states.sort(key=lambda state: _cluster_sort_key(str(state.get("cluster_id") or "")))
    return states


def _cluster_ids_for_action(run_dir: Path, action: str) -> list[str]:
    return [
        str(state.get("cluster_id") or "")
        for state in _load_controller_states(run_dir)
        if str(state.get("next_action") or "") == action and str(state.get("cluster_id") or "").strip()
    ]


def _missing_ontology_clusters(run_dir: Path, cluster_ids: list[str]) -> list[str]:
    relations_dir = run_dir / "ontology_relations" / "relations"
    return [
        cluster_id
        for cluster_id in cluster_ids
        if not (relations_dir / f"cluster-{cluster_id}.json").exists()
    ]


def _missing_llm_clusters(run_dir: Path, cluster_ids: list[str]) -> list[str]:
    results_dir = run_dir / "llm_compare" / "results"
    return [
        cluster_id
        for cluster_id in cluster_ids
        if not (results_dir / f"cluster-{cluster_id}.json").exists()
    ]


def _resolve_cluster_decisions(run_dir: Path) -> list[dict[str, Any]]:
    controller_index_path = run_dir / "controller" / "index.json"
    if controller_index_path.exists():
        return _resolve_cluster_decisions_from_controller(run_dir, controller_index_path)

    llm_index_path = run_dir / "llm_compare" / "index.json"
    if not llm_index_path.exists():
        raise InteractiveReviewError("llm_compare/index.json not found; run llm-compare first.")

    llm_index = load_json(llm_index_path)
    decisions: list[dict[str, Any]] = []

    for item in llm_index.get("results", []):
        result_path = Path(str(item.get("result_json") or ""))
        payload = load_json(result_path)
        cluster_id = str(payload.get("cluster_id") or "")
        current_label = str(payload.get("current_label") or "")
        focus_candidates = [str(value) for value in payload.get("focus_candidates", []) if str(value).strip()]
        if current_label and current_label not in focus_candidates:
            focus_candidates = [current_label, *focus_candidates]

        status = str(payload.get("status") or "")
        if status == "skipped":
            final_label = current_label
            selection_source = "skipped_keep_current"
            user_note = ""
            _interactive_print(f"Cluster {cluster_id}: single-candidate keep -> {final_label}")
        elif status == "completed":
            result = payload.get("result", {})
            decision = str(result.get("decision") or "")
            best_candidate = str(result.get("best_candidate") or "")
            if decision == "choose" and best_candidate:
                final_label = best_candidate
                selection_source = "llm_choose"
                user_note = ""
                _interactive_print(f"Cluster {cluster_id}: LLM choose -> {final_label}")
            else:
                relation_json = Path(str(payload.get("relation_json") or ""))
                relation_payload = load_json(relation_json) if relation_json.exists() else {}
                markers = _load_cluster_markers(relation_payload)
                reason = str(result.get("reason") or payload.get("reason") or "LLM returned review.")
                final_label, user_note = _prompt_choice(
                    cluster_id=cluster_id,
                    current_label=current_label,
                    options=focus_candidates or [current_label],
                    reason=reason,
                    markers=markers,
                )
                selection_source = "interactive_review"
                _interactive_print(f"Cluster {cluster_id}: user selected -> {final_label}")
        elif status == "failed":
            relation_json = Path(str(payload.get("relation_json") or ""))
            relation_payload = load_json(relation_json) if relation_json.exists() else {}
            markers = _load_cluster_markers(relation_payload)
            reason = str(payload.get("error") or "LLM compare failed.")
            final_label, user_note = _prompt_choice(
                cluster_id=cluster_id,
                current_label=current_label,
                options=focus_candidates or [current_label],
                reason=reason,
                markers=markers,
            )
            selection_source = "interactive_review_after_failure"
            _interactive_print(f"Cluster {cluster_id}: user selected after failure -> {final_label}")
        else:
            raise InteractiveReviewError(f"Unexpected llm-compare status for cluster {cluster_id}: {status}")

        decisions.append(
            {
                "cluster_id": cluster_id,
                "current_label": current_label,
                "final_label": final_label,
                "selection_source": selection_source,
                "status": status,
                "user_note": user_note,
                "focus_candidates": focus_candidates,
                "result_json": str(result_path),
            }
        )

    return decisions


def _resolve_cluster_decisions_from_controller(run_dir: Path, controller_index_path: Path) -> list[dict[str, Any]]:
    controller_index = load_json(controller_index_path)
    decisions: list[dict[str, Any]] = []

    for item in controller_index.get("states", []):
        state_path = Path(str(item.get("state_json") or ""))
        payload = load_json(state_path) if state_path.exists() else {}
        cluster_id = str(payload.get("cluster_id") or item.get("cluster_id") or "")
        current_label = str(payload.get("current_label") or "")
        focus_candidates = [str(value) for value in payload.get("focus_candidates", []) if str(value).strip()]
        if current_label and current_label not in focus_candidates:
            focus_candidates = [current_label, *focus_candidates]

        next_action = str(payload.get("next_action") or "")
        recommended_label = str(payload.get("recommended_label") or "")
        llm_reason = str(payload.get("llm_reason") or "")
        llm_status = str(payload.get("llm_status") or "")

        if next_action == "finalize_keep_current":
            final_label = current_label
            selection_source = "controller_keep_current"
            user_note = ""
            _interactive_print(f"Cluster {cluster_id}: controller keep -> {final_label}")
        elif next_action == "finalize_llm_choice" and recommended_label:
            final_label = recommended_label
            selection_source = "controller_llm_choose"
            user_note = ""
            _interactive_print(f"Cluster {cluster_id}: controller finalize LLM choice -> {final_label}")
        elif next_action in {"ask_user", "run_llm_compare"}:
            relation_json = Path(str(payload.get("relation_json") or ""))
            relation_payload = load_json(relation_json) if relation_json.exists() else {}
            markers = _load_cluster_markers(relation_payload)
            reason = llm_reason or "Controller requested review."
            final_label, user_note = _prompt_choice(
                cluster_id=cluster_id,
                current_label=current_label,
                options=focus_candidates or [current_label],
                reason=reason,
                markers=markers,
            )
            suffix = "_after_failure" if llm_status == "failed" else ""
            selection_source = f"interactive_review{suffix}"
            _interactive_print(f"Cluster {cluster_id}: user selected -> {final_label}")
        else:
            raise InteractiveReviewError(f"Unexpected controller next_action for cluster {cluster_id}: {next_action}")

        decisions.append(
            {
                "cluster_id": cluster_id,
                "current_label": current_label,
                "final_label": final_label,
                "selection_source": selection_source,
                "status": llm_status or "",
                "user_note": user_note,
                "focus_candidates": focus_candidates,
                "result_json": str(payload.get("result_json") or ""),
            }
        )

    return decisions


def _write_decision_files(output_dir: Path, decisions: list[dict[str, Any]]) -> tuple[Path, Path]:
    decisions_json = output_dir / "interactive_decisions.json"
    decisions_csv = output_dir / "interactive_decisions.csv"
    export_decisions: list[dict[str, Any]] = []
    for row in decisions:
        export_decisions.append(
            {
                "cluster_id": row.get("cluster_id", ""),
                "current_label": row.get("current_label", ""),
                "final_label": row.get("final_label", ""),
                "focus_candidates": row.get("focus_candidates", []),
                "result_json": row.get("result_json", ""),
            }
        )
    dump_json(decisions_json, export_decisions)

    fieldnames = [
        "cluster_id",
        "current_label",
        "final_label",
        "focus_candidates",
        "result_json",
    ]
    with decisions_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in export_decisions:
            item = dict(row)
            item["focus_candidates"] = " | ".join(item.get("focus_candidates", []))
            writer.writerow(item)
    return decisions_json, decisions_csv


def export_reviewed_parent_annotations(
    *,
    config: dict[str, Any],
    run_dir: Path,
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    review_index_path = run_dir / "review_packets" / "index.json"
    if not review_index_path.exists():
        raise InteractiveReviewError("review_packets/index.json not found; run review-packets first.")

    review_index = load_json(review_index_path)
    shared = review_index.get("shared", {})
    files = shared.get("files", {})
    cluster_col = shared.get("cluster_col")
    parent_seurat_rds = files.get("parent_seurat_rds")
    if not cluster_col or not parent_seurat_rds:
        raise InteractiveReviewError("Missing parent Seurat context in review packet index.")

    output_dir = ensure_dir(run_dir / "reviewed_parent")
    decisions_json, decisions_csv = _write_decision_files(output_dir, decisions)
    spec_path = output_dir / "reviewed_parent.spec.json"
    outputs_json = output_dir / "reviewed_parent.outputs.json"
    log_path = output_dir / "reviewed_parent.log"

    spec = {
        "project_name": config["project"]["name"],
        "run_id": run_dir.name,
        "parent_seurat_rds": parent_seurat_rds,
        "cluster_col": cluster_col,
        "decisions_json": str(decisions_json),
        "output_dir": str(output_dir),
        "outputs_json": str(outputs_json),
    }
    dump_json(spec_path, spec)

    helper = Path(config["_meta"]["repo_root"]) / "scripts" / "export_reviewed_parent_annotations.R"
    command = [config["_runtime"]["rscript"], str(helper), str(spec_path)]
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write(f"$ {' '.join(command)}\n")
        process = subprocess.run(
            command,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if process.returncode != 0:
        raise InteractiveReviewError(
            f"Reviewed parent annotation export failed with exit code {process.returncode}"
        )

    outputs = load_json(outputs_json)
    outputs["decisions_json"] = str(decisions_json)
    outputs["decisions_csv"] = str(decisions_csv)
    outputs["log"] = str(log_path)
    outputs["generated_at"] = utc_now()
    return outputs


def run_interactive_review(
    *,
    orchestrator: Any,
    force: bool = False,
) -> dict[str, Any]:
    config = orchestrator.config
    config.setdefault("policy", {})
    current_granularity = str(config.get("policy", {}).get("granularity") or "balanced")
    selected_granularity = _prompt_granularity(current_granularity)
    config["policy"]["granularity"] = selected_granularity
    _interactive_print(f"Using policy.granularity = {selected_granularity}")
    refresh_policy_outputs = force or selected_granularity != current_granularity
    refresh_review_packets = refresh_policy_outputs

    _interactive_print(
        "Ensuring the main annotation pipeline is available..."
        " (preflight -> annotate_parent; completed stages will be reused)"
    )
    orchestrator.run(from_stage="preflight", to_stage="annotate_parent", force=False)

    _interactive_print("Building review packets...")
    orchestrator.generate_review_packets(force=refresh_review_packets)

    _interactive_print("Initializing cluster-level controller state...")
    controller_outputs = orchestrator.generate_controller(force=True, phase="initial")
    controller_summary = controller_outputs.get("summary", {})
    _interactive_print(
        "Initial controller plan: "
        f"{controller_summary.get('build_ontology_relations_count', 0)} cluster(s) need ontology compare, "
        f"{controller_summary.get('finalize_keep_count', 0)} can keep current label directly, "
        f"{controller_summary.get('ask_user_count', 0)} need direct user review."
    )

    ontology_cluster_ids = _cluster_ids_for_action(orchestrator.run_dir, "build_ontology_relations")
    ontology_run_ids = (
        ontology_cluster_ids
        if refresh_policy_outputs
        else _missing_ontology_clusters(orchestrator.run_dir, ontology_cluster_ids)
    )
    if ontology_run_ids:
        _interactive_print(
            "Running ontology comparison for selected clusters: "
            + ", ".join(ontology_run_ids)
        )
        orchestrator.generate_ontology_relations(cluster_ids=ontology_run_ids, force=refresh_policy_outputs)
    else:
        if ontology_cluster_ids:
            _interactive_print("Reusing existing ontology comparison results for selected clusters.")
        else:
            _interactive_print("No clusters require ontology comparison at this stage.")

    _interactive_print("Updating controller after ontology compare...")
    controller_outputs = orchestrator.generate_controller(force=True, phase="post_ontology")
    controller_summary = controller_outputs.get("summary", {})
    _interactive_print(
        "Post-ontology controller plan: "
        f"{controller_summary.get('run_llm_compare_count', 0)} cluster(s) need LLM compare, "
        f"{controller_summary.get('finalize_keep_count', 0)} can finalize keep-current, "
        f"{controller_summary.get('ask_user_count', 0)} need direct user review."
    )

    llm_cluster_ids = _cluster_ids_for_action(orchestrator.run_dir, "run_llm_compare")
    llm_run_ids = (
        llm_cluster_ids
        if refresh_policy_outputs
        else _missing_llm_clusters(orchestrator.run_dir, llm_cluster_ids)
    )
    if llm_run_ids:
        _interactive_print(
            "Running LLM compare for selected clusters: "
            + ", ".join(llm_run_ids)
        )
        orchestrator.generate_llm_compare(cluster_ids=llm_run_ids, force=refresh_policy_outputs)
    else:
        if llm_cluster_ids:
            _interactive_print("Reusing existing LLM compare results for selected clusters.")
        else:
            _interactive_print("No clusters require LLM compare at this stage.")

    _interactive_print("Updating controller after LLM compare...")
    controller_outputs = orchestrator.generate_controller(force=True, phase="post_compare")
    controller_summary = controller_outputs.get("summary", {})
    _interactive_print(
        "Post-compare controller plan: "
        f"{controller_summary.get('finalize_keep_count', 0)} keep-current, "
        f"{controller_summary.get('finalize_llm_count', 0)} finalize-from-LLM, "
        f"{controller_summary.get('ask_user_count', 0)} ask-user."
    )

    _interactive_print("Resolving final cluster labels...")
    decisions = _resolve_cluster_decisions(orchestrator.run_dir)

    _interactive_print(
        "Exporting reviewed parent annotations to per-cell outputs..."
        " This may take a while because the reviewed Seurat object is being written."
    )
    outputs = export_reviewed_parent_annotations(
        config=config,
        run_dir=orchestrator.run_dir,
        decisions=decisions,
    )
    orchestrator.manifest["outputs"]["reviewed_parent"] = outputs
    orchestrator._save_manifest()

    _interactive_print("Refreshing report...")
    orchestrator.run(from_stage="report", to_stage="report", force=True)
    return outputs
