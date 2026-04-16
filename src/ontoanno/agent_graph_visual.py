from __future__ import annotations

from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph


class VisualState(TypedDict, total=False):
    intent: str
    visited: list[str]


def router(state: VisualState) -> VisualState:
    visited = list(state.get("visited", []))
    visited.append("router")
    return {"visited": visited}


def controller(state: VisualState) -> VisualState:
    visited = list(state.get("visited", []))
    visited.append("controller")
    return {"visited": visited}


def route_intent(
    state: VisualState,
) -> Literal[
    "intent_run_parent_pipeline",
    "intent_run_RAG_check",
    "intent_run_subcluster_pipeline",
    "intent_change_annotation_preference",
    "intent_add_external_evidence",
    "intent_extract_external_evidence",
]:
    intent = state["intent"]
    intent_map = {
        "run_parent_pipeline": "intent_run_parent_pipeline",
        "run_RAG_check": "intent_run_RAG_check",
        "run_subcluster_pipeline": "intent_run_subcluster_pipeline",
        "change_annotation_preference": "intent_change_annotation_preference",
        "add_external_evidence": "intent_add_external_evidence",
        "extract_external_evidence": "intent_extract_external_evidence",
    }
    return intent_map.get(intent, intent)


def route_controller(
    state: VisualState,
) -> Literal[
    "parent_preprocess_parent",
    "rag_build_review_packets",
    "subcluster_queue_subcluster_request",
    "pref_set_preference",
    "knowledge_decide_existing_or_new",
    "knowledge_extract_external_evidence",
]:
    intent = route_intent(state)
    controller_map = {
        "intent_run_parent_pipeline": "parent_preprocess_parent",
        "intent_run_RAG_check": "rag_build_review_packets",
        "intent_run_subcluster_pipeline": "subcluster_queue_subcluster_request",
        "intent_change_annotation_preference": "pref_set_preference",
        "intent_add_external_evidence": "knowledge_decide_existing_or_new",
        "intent_extract_external_evidence": "knowledge_extract_external_evidence",
    }
    return controller_map[intent]


def intent_run_parent_pipeline(state: VisualState) -> VisualState:
    return state


def intent_run_RAG_check(state: VisualState) -> VisualState:
    return state


def intent_run_subcluster_pipeline(state: VisualState) -> VisualState:
    return state


def intent_change_annotation_preference(state: VisualState) -> VisualState:
    return state


def intent_add_external_evidence(state: VisualState) -> VisualState:
    return state


def intent_extract_external_evidence(state: VisualState) -> VisualState:
    return state


def parent_preprocess_parent(state: VisualState) -> VisualState:
    return state


def parent_cluster_parent_markers(state: VisualState) -> VisualState:
    return state


def parent_annotate_parent_raw(state: VisualState) -> VisualState:
    return state


def parent_map_parent_ontology(state: VisualState) -> VisualState:
    return state


def parent_select_parent_resolution(state: VisualState) -> VisualState:
    return state


def parent_assign_parent_labels(state: VisualState) -> VisualState:
    return state


def rag_build_review_packets(state: VisualState) -> VisualState:
    return state


def rag_decide_rag_check(state: VisualState) -> VisualState:
    return state


def rag_build_candidate_map(state: VisualState) -> VisualState:
    return state


def rag_retrieve_rag_evidence(state: VisualState) -> VisualState:
    return state


def rag_run_llm_compare(state: VisualState) -> VisualState:
    return state


def rag_human_review(state: VisualState) -> VisualState:
    return state


def subcluster_queue_subcluster_request(state: VisualState) -> VisualState:
    return state


def subcluster_find_markers(state: VisualState) -> VisualState:
    return state


def subcluster_annotate_ontology(state: VisualState) -> VisualState:
    return state


def subcluster_annotate_inheritance(state: VisualState) -> VisualState:
    return state


def subcluster_finalize_annotations(state: VisualState) -> VisualState:
    return state


def pref_set_preference(state: VisualState) -> VisualState:
    return state


def pref_branch_granularity_or_resolution(state: VisualState) -> VisualState:
    return state


def pref_run_rag_check_recommended(state: VisualState) -> VisualState:
    return state


def knowledge_decide_existing_or_new(state: VisualState) -> VisualState:
    return state


def knowledge_write_custom_memory(state: VisualState) -> VisualState:
    return state


def knowledge_retrieve_rag_evidence_later(state: VisualState) -> VisualState:
    return state


def knowledge_extract_external_evidence(state: VisualState) -> VisualState:
    return state


builder = StateGraph(VisualState)

builder.add_node("router", router)
builder.add_node("controller", controller)

builder.add_node("intent_run_parent_pipeline", intent_run_parent_pipeline)
builder.add_node("intent_run_RAG_check", intent_run_RAG_check)
builder.add_node("intent_run_subcluster_pipeline", intent_run_subcluster_pipeline)
builder.add_node("intent_change_annotation_preference", intent_change_annotation_preference)
builder.add_node("intent_add_external_evidence", intent_add_external_evidence)
builder.add_node("intent_extract_external_evidence", intent_extract_external_evidence)

builder.add_node("parent_preprocess_parent", parent_preprocess_parent)
builder.add_node("parent_cluster_parent_markers", parent_cluster_parent_markers)
builder.add_node("parent_annotate_parent_raw", parent_annotate_parent_raw)
builder.add_node("parent_map_parent_ontology", parent_map_parent_ontology)
builder.add_node("parent_select_parent_resolution", parent_select_parent_resolution)
builder.add_node("parent_assign_parent_labels", parent_assign_parent_labels)

builder.add_node("rag_build_review_packets", rag_build_review_packets)
builder.add_node("rag_decide_rag_check", rag_decide_rag_check)
builder.add_node("rag_build_candidate_map", rag_build_candidate_map)
builder.add_node("rag_retrieve_rag_evidence", rag_retrieve_rag_evidence)
builder.add_node("rag_run_llm_compare", rag_run_llm_compare)
builder.add_node("rag_human_review", rag_human_review)

builder.add_node("subcluster_queue_subcluster_request", subcluster_queue_subcluster_request)
builder.add_node("subcluster_find_markers", subcluster_find_markers)
builder.add_node("subcluster_annotate_ontology", subcluster_annotate_ontology)
builder.add_node("subcluster_annotate_inheritance", subcluster_annotate_inheritance)
builder.add_node("subcluster_finalize_annotations", subcluster_finalize_annotations)

builder.add_node("pref_set_preference", pref_set_preference)
builder.add_node("pref_branch_granularity_or_resolution", pref_branch_granularity_or_resolution)
builder.add_node("pref_run_rag_check_recommended", pref_run_rag_check_recommended)

builder.add_node("knowledge_decide_existing_or_new", knowledge_decide_existing_or_new)
builder.add_node("knowledge_write_custom_memory", knowledge_write_custom_memory)
builder.add_node("knowledge_retrieve_rag_evidence_later", knowledge_retrieve_rag_evidence_later)
builder.add_node("knowledge_extract_external_evidence", knowledge_extract_external_evidence)

builder.add_edge(START, "router")

builder.add_conditional_edges(
    "router",
    route_intent,
    {
        "intent_run_parent_pipeline": "intent_run_parent_pipeline",
        "intent_run_RAG_check": "intent_run_RAG_check",
        "intent_run_subcluster_pipeline": "intent_run_subcluster_pipeline",
        "intent_change_annotation_preference": "intent_change_annotation_preference",
        "intent_add_external_evidence": "intent_add_external_evidence",
        "intent_extract_external_evidence": "intent_extract_external_evidence",
    },
)

builder.add_edge("intent_run_parent_pipeline", "controller")
builder.add_edge("intent_run_RAG_check", "controller")
builder.add_edge("intent_run_subcluster_pipeline", "controller")
builder.add_edge("intent_change_annotation_preference", "controller")
builder.add_edge("intent_add_external_evidence", "controller")
builder.add_edge("intent_extract_external_evidence", "controller")

builder.add_conditional_edges(
    "controller",
    route_controller,
    {
        "parent_preprocess_parent": "parent_preprocess_parent",
        "rag_build_review_packets": "rag_build_review_packets",
        "subcluster_queue_subcluster_request": "subcluster_queue_subcluster_request",
        "pref_set_preference": "pref_set_preference",
        "knowledge_decide_existing_or_new": "knowledge_decide_existing_or_new",
        "knowledge_extract_external_evidence": "knowledge_extract_external_evidence",
    },
)

builder.add_edge("parent_preprocess_parent", "parent_cluster_parent_markers")
builder.add_edge("parent_cluster_parent_markers", "parent_annotate_parent_raw")
builder.add_edge("parent_annotate_parent_raw", "parent_map_parent_ontology")
builder.add_edge("parent_map_parent_ontology", "parent_select_parent_resolution")
builder.add_edge("parent_select_parent_resolution", "parent_assign_parent_labels")
builder.add_edge("parent_assign_parent_labels", END)

builder.add_edge("rag_build_review_packets", "rag_decide_rag_check")
builder.add_edge("rag_decide_rag_check", "rag_build_candidate_map")
builder.add_edge("rag_build_candidate_map", "rag_retrieve_rag_evidence")
builder.add_edge("rag_retrieve_rag_evidence", "rag_run_llm_compare")
builder.add_edge("rag_run_llm_compare", "rag_human_review")
builder.add_edge("rag_human_review", END)

builder.add_edge("subcluster_queue_subcluster_request", "subcluster_find_markers")
builder.add_edge("subcluster_find_markers", "subcluster_annotate_ontology")
builder.add_edge("subcluster_annotate_ontology", "subcluster_annotate_inheritance")
builder.add_edge("subcluster_annotate_inheritance", "subcluster_finalize_annotations")
builder.add_edge("subcluster_finalize_annotations", END)

builder.add_edge("pref_set_preference", "pref_branch_granularity_or_resolution")
builder.add_edge("pref_branch_granularity_or_resolution", "pref_run_rag_check_recommended")
builder.add_edge("pref_run_rag_check_recommended", END)

builder.add_edge("knowledge_decide_existing_or_new", "knowledge_write_custom_memory")
builder.add_edge("knowledge_write_custom_memory", "knowledge_retrieve_rag_evidence_later")
builder.add_edge("knowledge_retrieve_rag_evidence_later", END)

builder.add_edge("knowledge_extract_external_evidence", END)

graph = builder.compile()
