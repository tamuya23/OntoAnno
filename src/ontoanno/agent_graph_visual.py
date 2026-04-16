from __future__ import annotations

from typing import Any, TypedDict

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - optional dependency for the VSCode visualizer
    END = "__end__"
    START = "__start__"
    StateGraph = None


class VisualState(TypedDict, total=False):
    request_mode: str
    needs_tool: bool
    intent: str
    preference_branch: str
    resolution_branch: str
    visited: list[str]


def _visit(state: VisualState, node_name: str) -> VisualState:
    visited = list(state.get("visited", []))
    visited.append(node_name)
    return {"visited": visited}


def _node(node_name: str):
    def _inner(state: VisualState) -> VisualState:
        return _visit(state, node_name)

    _inner.__name__ = node_name
    return _inner


def route_entry(state: VisualState) -> str:
    if state.get("request_mode") == "manual_worker":
        return "manual_worker_console"
    return "llm_tool_gate"


def route_tool_gate(state: VisualState) -> str:
    if state.get("needs_tool") is False:
        return "direct_answer_no_tool"
    return "llm_tool_router"


def route_intent(state: VisualState) -> str:
    intent = str(state.get("intent") or "run_parent_pipeline")
    intent_map = {
        "run_parent_pipeline": "intent_run_parent_pipeline",
        "run_subcluster_pipeline": "intent_run_subcluster_pipeline",
        "run_RAG_check": "intent_run_RAG_check",
        "change_annotation_preference": "intent_change_annotation_preference",
        "add_external_evidence": "intent_add_external_evidence",
        "extract_external_evidence": "intent_extract_external_evidence",
        "run_report": "intent_run_report",
    }
    return intent_map.get(intent, "intent_run_parent_pipeline")


def route_preference_branch(state: VisualState) -> str:
    if state.get("preference_branch") == "granularity":
        return "pref_update_granularity_config"
    if state.get("resolution_branch") == "new_resolution":
        return "pref_new_resolution_parent_chain"
    return "pref_existing_resolution_select"


def build_graph() -> Any:
    if StateGraph is None:
        return None

    builder = StateGraph(VisualState)

    node_names = [
        "entry_streamlit_or_cli",
        "llm_tool_gate",
        "direct_answer_no_tool",
        "llm_tool_router",
        "chat_record_selected_workflow",
        "apply_agent_request",
        "llm_summary_and_suggest_next",
        "persist_chat_history",
        "manual_worker_console",
        "worker_prerequisite_guard",
        "manual_execute_selected_worker",
        "record_manual_worker_result",
        "intent_run_parent_pipeline",
        "intent_run_subcluster_pipeline",
        "intent_run_RAG_check",
        "intent_change_annotation_preference",
        "intent_add_external_evidence",
        "intent_extract_external_evidence",
        "intent_run_report",
        "parent_preprocess_parent",
        "parent_cluster_parent_markers",
        "parent_annotate_parent_raw",
        "parent_map_parent_ontology",
        "parent_select_parent_resolution",
        "parent_assign_parent_labels",
        "subcluster_update_config_and_memory",
        "subcluster_find_markers",
        "subcluster_annotate_ontology",
        "subcluster_annotate_inheritance",
        "subcluster_finalize_annotations",
        "rag_build_review_packets",
        "rag_decide_initial",
        "rag_build_candidate_map",
        "rag_retrieve_rag_evidence",
        "rag_decide_post_ontology",
        "rag_run_llm_compare",
        "rag_decide_post_compare",
        "rag_human_review",
        "pref_update_config_or_memory",
        "pref_update_granularity_config",
        "pref_existing_resolution_select",
        "pref_existing_resolution_assign_labels",
        "pref_new_resolution_parent_chain",
        "external_evidence_validate_explicit_celltype",
        "external_evidence_write_memory",
        "external_evidence_suggest_RAG_check",
        "extract_external_evidence_placeholder",
        "generate_report",
    ]
    for node_name in node_names:
        builder.add_node(node_name, _node(node_name))

    builder.add_edge(START, "entry_streamlit_or_cli")
    builder.add_conditional_edges(
        "entry_streamlit_or_cli",
        route_entry,
        {
            "llm_tool_gate": "llm_tool_gate",
            "manual_worker_console": "manual_worker_console",
        },
    )

    builder.add_conditional_edges(
        "llm_tool_gate",
        route_tool_gate,
        {
            "direct_answer_no_tool": "direct_answer_no_tool",
            "llm_tool_router": "llm_tool_router",
        },
    )
    builder.add_edge("direct_answer_no_tool", "persist_chat_history")
    builder.add_edge("llm_tool_router", "chat_record_selected_workflow")
    builder.add_edge("chat_record_selected_workflow", "apply_agent_request")

    builder.add_conditional_edges(
        "apply_agent_request",
        route_intent,
        {
            "intent_run_parent_pipeline": "intent_run_parent_pipeline",
            "intent_run_subcluster_pipeline": "intent_run_subcluster_pipeline",
            "intent_run_RAG_check": "intent_run_RAG_check",
            "intent_change_annotation_preference": "intent_change_annotation_preference",
            "intent_add_external_evidence": "intent_add_external_evidence",
            "intent_extract_external_evidence": "intent_extract_external_evidence",
            "intent_run_report": "intent_run_report",
        },
    )

    builder.add_edge("manual_worker_console", "worker_prerequisite_guard")
    builder.add_edge("worker_prerequisite_guard", "manual_execute_selected_worker")
    builder.add_edge("manual_execute_selected_worker", "record_manual_worker_result")
    builder.add_edge("record_manual_worker_result", "persist_chat_history")

    builder.add_edge("intent_run_parent_pipeline", "parent_preprocess_parent")
    builder.add_edge("parent_preprocess_parent", "parent_cluster_parent_markers")
    builder.add_edge("parent_cluster_parent_markers", "parent_annotate_parent_raw")
    builder.add_edge("parent_annotate_parent_raw", "parent_map_parent_ontology")
    builder.add_edge("parent_map_parent_ontology", "parent_select_parent_resolution")
    builder.add_edge("parent_select_parent_resolution", "parent_assign_parent_labels")
    builder.add_edge("parent_assign_parent_labels", "llm_summary_and_suggest_next")

    builder.add_edge("intent_run_subcluster_pipeline", "subcluster_update_config_and_memory")
    builder.add_edge("subcluster_update_config_and_memory", "subcluster_find_markers")
    builder.add_edge("subcluster_find_markers", "subcluster_annotate_ontology")
    builder.add_edge("subcluster_annotate_ontology", "subcluster_annotate_inheritance")
    builder.add_edge("subcluster_annotate_inheritance", "subcluster_finalize_annotations")
    builder.add_edge("subcluster_finalize_annotations", "llm_summary_and_suggest_next")

    builder.add_edge("intent_run_RAG_check", "rag_build_review_packets")
    builder.add_edge("rag_build_review_packets", "rag_decide_initial")
    builder.add_edge("rag_decide_initial", "rag_build_candidate_map")
    builder.add_edge("rag_build_candidate_map", "rag_retrieve_rag_evidence")
    builder.add_edge("rag_retrieve_rag_evidence", "rag_decide_post_ontology")
    builder.add_edge("rag_decide_post_ontology", "rag_run_llm_compare")
    builder.add_edge("rag_run_llm_compare", "rag_decide_post_compare")
    builder.add_edge("rag_decide_post_compare", "rag_human_review")
    builder.add_edge("rag_human_review", "llm_summary_and_suggest_next")

    builder.add_edge("intent_change_annotation_preference", "pref_update_config_or_memory")
    builder.add_conditional_edges(
        "pref_update_config_or_memory",
        route_preference_branch,
        {
            "pref_update_granularity_config": "pref_update_granularity_config",
            "pref_existing_resolution_select": "pref_existing_resolution_select",
            "pref_new_resolution_parent_chain": "pref_new_resolution_parent_chain",
        },
    )
    builder.add_edge("pref_update_granularity_config", "llm_summary_and_suggest_next")
    builder.add_edge("pref_existing_resolution_select", "pref_existing_resolution_assign_labels")
    builder.add_edge("pref_existing_resolution_assign_labels", "llm_summary_and_suggest_next")
    builder.add_edge("pref_new_resolution_parent_chain", "parent_preprocess_parent")

    builder.add_edge("intent_add_external_evidence", "external_evidence_validate_explicit_celltype")
    builder.add_edge("external_evidence_validate_explicit_celltype", "external_evidence_write_memory")
    builder.add_edge("external_evidence_write_memory", "external_evidence_suggest_RAG_check")
    builder.add_edge("external_evidence_suggest_RAG_check", "llm_summary_and_suggest_next")

    builder.add_edge("intent_extract_external_evidence", "extract_external_evidence_placeholder")
    builder.add_edge("extract_external_evidence_placeholder", "llm_summary_and_suggest_next")

    builder.add_edge("intent_run_report", "generate_report")
    builder.add_edge("generate_report", "llm_summary_and_suggest_next")

    builder.add_edge("llm_summary_and_suggest_next", "persist_chat_history")
    builder.add_edge("persist_chat_history", END)

    return builder.compile()


graph = build_graph()


if __name__ == "__main__":
    if graph is None:
        print("langgraph is not installed; install langgraph to compile this visual graph.")
    else:
        print(graph)
