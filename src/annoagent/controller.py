from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .utils import dump_json, ensure_dir, load_json, utc_now


class ControllerError(RuntimeError):
    pass


CONTROLLER_PHASES = ("initial", "post_ontology", "post_compare")


def _cluster_sort_key(cluster_id: str) -> tuple[int, str]:
    try:
        return (0, f"{int(cluster_id):06d}")
    except (TypeError, ValueError):
        return (1, str(cluster_id))


def _read_review_clusters(run_dir: Path) -> dict[str, dict[str, Any]]:
    index_path = run_dir / "review_packets" / "index.json"
    if not index_path.exists():
        raise ControllerError("review_packets/index.json not found; run review-packets first.")

    index_payload = load_json(index_path)
    packet_items = index_payload.get("packets", [])
    clusters: dict[str, dict[str, Any]] = {}

    for packet_item in packet_items:
        packet_json = Path(str(packet_item.get("packet_json") or ""))
        packet_payload = load_json(packet_json) if packet_json.exists() else {}
        summary = packet_payload.get("summary", {})
        review_flags = packet_payload.get("review_flags", {})
        markers = [str(item.get("gene")).strip() for item in packet_payload.get("markers", []) if str(item.get("gene") or "").strip()]
        cluster_id = str(packet_item.get("cluster_id") or summary.get("cluster_id") or "")
        clusters[cluster_id] = {
            "cluster_id": cluster_id,
            "current_label": str(summary.get("assigned_label") or ""),
            "cell_count": summary.get("cell_count"),
            "max_percentage": summary.get("max_percentage"),
            "other_annotations": str(summary.get("other_annotations") or ""),
            "avg_distance": summary.get("avg_distance"),
            "needs_review_flag": bool(review_flags.get("needs_review")),
            "review_notes": [str(item) for item in review_flags.get("notes", []) if str(item).strip()],
            "markers": markers,
            "packet_json": str(packet_json),
        }
    return clusters


def _read_ontology_clusters(run_dir: Path) -> dict[str, dict[str, Any]]:
    index_path = run_dir / "ontology_relations" / "index.json"
    if not index_path.exists():
        return {}
    index_payload = load_json(index_path)
    return {str(item.get("cluster_id") or ""): dict(item) for item in index_payload.get("relations", [])}


def _read_llm_clusters(run_dir: Path) -> dict[str, dict[str, Any]]:
    index_path = run_dir / "llm_compare" / "index.json"
    if not index_path.exists():
        return {}
    index_payload = load_json(index_path)
    clusters: dict[str, dict[str, Any]] = {}
    for item in index_payload.get("results", []):
        cluster_id = str(item.get("cluster_id") or "")
        result_json = Path(str(item.get("result_json") or ""))
        result_payload = load_json(result_json) if result_json.exists() else {}
        result = result_payload.get("result", {}) if isinstance(result_payload, dict) else {}
        clusters[cluster_id] = {
            "status": str(item.get("status") or result_payload.get("status") or ""),
            "decision": str(item.get("decision") or result.get("decision") or ""),
            "best_candidate": str(item.get("best_candidate") or result.get("best_candidate") or ""),
            "reason": str(result.get("reason") or result_payload.get("reason") or result_payload.get("error") or ""),
            "result_json": str(result_json),
        }
    return clusters


def _stable_initial_keep(review_state: dict[str, Any]) -> bool:
    current_label = str(review_state.get("current_label") or "")
    if not current_label:
        return False
    max_percentage = review_state.get("max_percentage")
    try:
        max_percentage_value = float(max_percentage)
    except (TypeError, ValueError):
        max_percentage_value = 0.0
    other_annotations = str(review_state.get("other_annotations") or "").strip()
    needs_review = bool(review_state.get("needs_review_flag"))
    return max_percentage_value >= 95.0 and not other_annotations and not needs_review


def _initial_cluster_state(review_state: dict[str, Any]) -> dict[str, Any]:
    current_label = str(review_state.get("current_label") or "")
    next_action = "ask_user"
    recommended_label: str | None = None
    reason_codes: list[str] = []

    if not current_label:
        next_action = "ask_user"
        reason_codes.append("missing_current_label")
    elif _stable_initial_keep(review_state):
        next_action = "finalize_keep_current"
        recommended_label = current_label
        reason_codes.append("stable_initial_parent_label")
    else:
        next_action = "build_ontology_relations"
        reason_codes.append("needs_cluster_refinement")

    return {
        "cluster_id": review_state["cluster_id"],
        "current_label": current_label,
        "cell_count": review_state.get("cell_count"),
        "markers": review_state.get("markers", []),
        "max_percentage": review_state.get("max_percentage"),
        "other_annotations": review_state.get("other_annotations"),
        "avg_distance": review_state.get("avg_distance"),
        "needs_review_flag": review_state.get("needs_review_flag"),
        "review_notes": review_state.get("review_notes", []),
        "packet_json": review_state.get("packet_json"),
        "phase": "initial",
        "next_action": next_action,
        "recommended_label": recommended_label,
        "focus_candidates": [current_label] if current_label else [],
        "relation_mode": None,
        "focus_strategy": None,
        "policy_granularity": None,
        "needs_llm_compare": None,
        "prompt_ready": None,
        "llm_status": None,
        "llm_decision": None,
        "llm_best_candidate": None,
        "llm_reason": "",
        "relation_json": None,
        "result_json": None,
        "action_history": [
            {
                "phase": "initial",
                "next_action": next_action,
                "reason_codes": list(reason_codes),
            }
        ],
        "reason_codes": reason_codes,
    }


def _merge_ontology_state(state: dict[str, Any], ontology_state: dict[str, Any]) -> dict[str, Any]:
    current_label = str(state.get("current_label") or "")
    focus_candidates = [str(item) for item in ontology_state.get("focus_candidates", []) if str(item).strip()]
    if current_label and current_label not in focus_candidates:
        focus_candidates = [current_label, *focus_candidates]

    next_action = state.get("next_action") or "build_ontology_relations"
    recommended_label = state.get("recommended_label")
    reason_codes: list[str] = []

    if next_action == "finalize_keep_current":
        reason_codes.append("stable_initial_parent_label")
    else:
        needs_llm_compare = bool(ontology_state.get("needs_llm_compare"))
        prompt_ready = bool(ontology_state.get("prompt_ready"))
        if not needs_llm_compare:
            next_action = "finalize_keep_current"
            recommended_label = current_label
            reason_codes.append("no_llm_compare_needed_after_ontology")
        elif prompt_ready:
            next_action = "run_llm_compare"
            recommended_label = None
            reason_codes.append("ontology_candidates_ready_for_llm")
        else:
            next_action = "ask_user"
            recommended_label = None
            reason_codes.append("ontology_candidates_not_prompt_ready")

    merged = dict(state)
    merged.update(
        {
            "phase": "post_ontology",
            "next_action": next_action,
            "recommended_label": recommended_label,
            "focus_candidates": focus_candidates,
            "relation_mode": ontology_state.get("relation_mode"),
            "focus_strategy": ontology_state.get("focus_strategy"),
            "policy_granularity": ontology_state.get("policy_granularity"),
            "needs_llm_compare": ontology_state.get("needs_llm_compare"),
            "prompt_ready": ontology_state.get("prompt_ready"),
            "relation_json": ontology_state.get("relation_json"),
            "reason_codes": reason_codes,
        }
    )
    merged["action_history"] = [
        *state.get("action_history", []),
        {
            "phase": "post_ontology",
            "next_action": next_action,
            "reason_codes": list(reason_codes),
        },
    ]
    return merged


def _merge_llm_state(state: dict[str, Any], llm_state: dict[str, Any]) -> dict[str, Any]:
    llm_status = str(llm_state.get("status") or "")
    llm_decision = str(llm_state.get("decision") or "")
    llm_best_candidate = str(llm_state.get("best_candidate") or "")
    llm_reason = str(llm_state.get("reason") or "")

    next_action = state.get("next_action") or "ask_user"
    recommended_label = state.get("recommended_label")
    reason_codes: list[str] = []

    if next_action == "finalize_keep_current":
        reason_codes.append("no_llm_compare_needed_after_ontology")
    elif llm_status == "skipped":
        next_action = "finalize_keep_current"
        recommended_label = str(state.get("current_label") or "")
        reason_codes.append("llm_compare_skipped")
    elif llm_status == "completed":
        if llm_decision == "choose" and llm_best_candidate:
            next_action = "finalize_llm_choice"
            recommended_label = llm_best_candidate
            reason_codes.append("llm_returned_choice")
        else:
            next_action = "ask_user"
            recommended_label = None
            reason_codes.append("llm_requested_review")
    elif llm_status == "failed":
        next_action = "ask_user"
        recommended_label = None
        reason_codes.append("llm_compare_failed")
    else:
        next_action = "ask_user"
        recommended_label = None
        reason_codes.append("missing_llm_result")

    merged = dict(state)
    merged.update(
        {
            "phase": "post_compare",
            "next_action": next_action,
            "recommended_label": recommended_label,
            "llm_status": llm_status or None,
            "llm_decision": llm_decision or None,
            "llm_best_candidate": llm_best_candidate or None,
            "llm_reason": llm_reason,
            "result_json": llm_state.get("result_json"),
            "reason_codes": reason_codes,
        }
    )
    merged["action_history"] = [
        *state.get("action_history", []),
        {
            "phase": "post_compare",
            "next_action": next_action,
            "reason_codes": list(reason_codes),
        },
    ]
    return merged


def _detect_phase(*, ontology_clusters: dict[str, dict[str, Any]], llm_clusters: dict[str, dict[str, Any]], requested_phase: str) -> str:
    if requested_phase != "auto":
        return requested_phase
    if llm_clusters:
        return "post_compare"
    if ontology_clusters:
        return "post_ontology"
    return "initial"


def plan_next_actions(*, run_dir: Path, phase: str = "auto") -> dict[str, Any]:
    if phase not in {*CONTROLLER_PHASES, "auto"}:
        raise ControllerError(f"Unsupported controller phase: {phase}")

    review_clusters = _read_review_clusters(run_dir)
    ontology_clusters = _read_ontology_clusters(run_dir)
    llm_clusters = _read_llm_clusters(run_dir)
    effective_phase = _detect_phase(
        ontology_clusters=ontology_clusters,
        llm_clusters=llm_clusters,
        requested_phase=phase,
    )

    cluster_ids = sorted(set(review_clusters) | set(ontology_clusters) | set(llm_clusters), key=_cluster_sort_key)
    cluster_states: list[dict[str, Any]] = []

    for cluster_id in cluster_ids:
        review_state = review_clusters.get(cluster_id)
        if review_state is None:
            continue
        state = _initial_cluster_state(review_state)

        if effective_phase in {"post_ontology", "post_compare"}:
            ontology_state = ontology_clusters.get(cluster_id)
            if ontology_state is not None:
                state = _merge_ontology_state(state, ontology_state)

        if effective_phase == "post_compare":
            llm_state = llm_clusters.get(cluster_id)
            if llm_state is not None:
                state = _merge_llm_state(state, llm_state)

        cluster_states.append(state)

    summary = {
        "cluster_count": len(cluster_states),
        "build_ontology_relations_count": sum(1 for item in cluster_states if item["next_action"] == "build_ontology_relations"),
        "run_llm_compare_count": sum(1 for item in cluster_states if item["next_action"] == "run_llm_compare"),
        "finalize_keep_count": sum(1 for item in cluster_states if item["next_action"] == "finalize_keep_current"),
        "finalize_llm_count": sum(1 for item in cluster_states if item["next_action"] == "finalize_llm_choice"),
        "ask_user_count": sum(1 for item in cluster_states if item["next_action"] == "ask_user"),
        "phase": effective_phase,
    }
    return {
        "generated_at": utc_now(),
        "phase": effective_phase,
        "clusters": cluster_states,
        "summary": summary,
    }


def build_controller(
    *,
    config: dict[str, Any],
    run_dir: Path,
    force: bool = False,
    phase: str = "auto",
) -> dict[str, Any]:
    if phase not in {*CONTROLLER_PHASES, "auto"}:
        raise ControllerError(f"Unsupported controller phase: {phase}")

    output_dir = ensure_dir(run_dir / "controller")
    states_dir = ensure_dir(output_dir / "states")
    summary_path = output_dir / "summary.csv"
    index_path = output_dir / "index.json"
    outputs_path = output_dir / "controller.outputs.json"

    if outputs_path.exists() and not force:
        cached = load_json(outputs_path)
        cached_phase = str(cached.get("phase") or "auto")
        if phase == "auto" or cached_phase == phase:
            return cached

    payload = plan_next_actions(run_dir=run_dir, phase=phase)
    state_items: list[dict[str, Any]] = []
    summary_rows: list[dict[str, str]] = []

    for cluster in payload.get("clusters", []):
        cluster_id = str(cluster.get("cluster_id") or "")
        state_path = states_dir / f"cluster-{cluster_id}.json"
        dump_json(state_path, cluster)
        state_items.append(
            {
                "cluster_id": cluster_id,
                "label": cluster.get("current_label"),
                "phase": cluster.get("phase"),
                "next_action": cluster.get("next_action"),
                "recommended_label": cluster.get("recommended_label"),
                "reason_codes": cluster.get("reason_codes", []),
                "state_json": str(state_path),
                "state_uri": str(state_path),
            }
        )
        summary_rows.append(
            {
                "cluster_id": cluster_id,
                "current_label": str(cluster.get("current_label") or ""),
                "phase": str(cluster.get("phase") or ""),
                "focus_candidates": " | ".join(cluster.get("focus_candidates", [])),
                "llm_status": str(cluster.get("llm_status") or ""),
                "llm_decision": str(cluster.get("llm_decision") or ""),
                "llm_best_candidate": str(cluster.get("llm_best_candidate") or ""),
                "next_action": str(cluster.get("next_action") or ""),
                "recommended_label": str(cluster.get("recommended_label") or ""),
                "reason_codes": " | ".join(cluster.get("reason_codes", [])),
                "state_json": str(state_path),
            }
        )

    fieldnames = [
        "cluster_id",
        "current_label",
        "phase",
        "focus_candidates",
        "llm_status",
        "llm_decision",
        "llm_best_candidate",
        "next_action",
        "recommended_label",
        "reason_codes",
        "state_json",
    ]
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)

    index_payload = {
        "project_name": config["project"]["name"],
        "run_id": run_dir.name,
        "generated_at": utc_now(),
        "phase": payload.get("phase"),
        "summary": payload.get("summary", {}),
        "states": state_items,
    }
    dump_json(index_path, index_payload)

    outputs = {
        "output_dir": str(output_dir),
        "summary_csv": str(summary_path),
        "index_json": str(index_path),
        "state_count": len(state_items),
        "phase": payload.get("phase"),
        "summary": payload.get("summary", {}),
    }
    dump_json(outputs_path, outputs)
    return outputs
