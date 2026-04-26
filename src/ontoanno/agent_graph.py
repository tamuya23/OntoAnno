from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, TypedDict

import yaml

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - optional dependency
    END = "__end__"
    START = "__start__"
    StateGraph = None


ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "resources" / "agent_registry.yaml"


class AgentGraphState(TypedDict, total=False):
    intent: str
    controller_action: str
    current_node: str
    current_kind: str
    visited_nodes: list[str]


def _load_registry() -> dict[str, Any]:
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


def _safe_node_id(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name)


def _intent_order(registry: dict[str, Any]) -> list[str]:
    return list(registry.get("top_level_intents", {}).keys())


def _controller_action(registry: dict[str, Any], intent_name: str) -> str:
    intent = registry["top_level_intents"][intent_name]
    return str(intent.get("controller_action") or intent_name)


def _intent_chain(registry: dict[str, Any], intent_name: str) -> list[str]:
    intent = registry["top_level_intents"][intent_name]
    execution = intent.get("current_execution", {})

    if intent_name == "run_parent_pipeline":
        return [str(item) for item in execution.get("workers", [])]

    if intent_name == "run_subcluster_pipeline":
        chain = ["queue_subcluster_request"]
        chain.extend(str(item) for item in execution.get("logical_pipeline", []))
        return chain

    workers = execution.get("workers")
    if isinstance(workers, list) and workers:
        return [str(item) for item in workers]

    if intent_name == "run_RAG_check":
        return [str(item) for item in execution.get("logical_workers", [])]

    if intent_name == "change_annotation_preference":
        return [
            "set_preference",
            "branch_granularity_or_resolution",
            "run_RAG_check_recommended",
        ]

    if intent_name == "add_external_evidence":
        return [
            "decide_existing_or_new_knowledge",
            "write_knowledge_memory",
            "retrieve_rag_evidence_later",
        ]

    if intent_name == "extract_external_evidence":
        return ["extract_external_evidence_placeholder"]

    return []


def build_graph_spec() -> dict[str, Any]:
    registry = _load_registry()
    intents = _intent_order(registry)
    nodes: list[dict[str, str]] = [
        {"id": "start", "label": "START", "kind": "start"},
        {"id": "router", "label": "Router", "kind": "router"},
        {"id": "controller", "label": "Controller", "kind": "controller"},
        {"id": "end", "label": "END", "kind": "end"},
    ]
    edges: list[dict[str, str]] = [
        {"source": "start", "target": "router", "kind": "fixed"},
    ]

    for intent_name in intents:
        intent_id = f"intent__{_safe_node_id(intent_name)}"
        nodes.append({"id": intent_id, "label": intent_name, "kind": "intent"})
        edges.append(
            {
                "source": "router",
                "target": intent_id,
                "kind": "conditional",
                "condition": intent_name,
            }
        )
        edges.append({"source": intent_id, "target": "controller", "kind": "fixed"})

        chain = _intent_chain(registry, intent_name)
        previous = "controller"
        for index, worker_name in enumerate(chain, start=1):
            worker_id = f"{_safe_node_id(intent_name)}__worker_{index}"
            nodes.append({"id": worker_id, "label": worker_name, "kind": "worker"})
            edge_kind = "conditional" if previous == "controller" else "fixed"
            edge: dict[str, str] = {
                "source": previous,
                "target": worker_id,
                "kind": edge_kind,
            }
            if previous == "controller":
                edge["condition"] = intent_name
            edges.append(edge)
            previous = worker_id

        edges.append({"source": previous, "target": "end", "kind": "fixed"})

    return {
        "registry_path": str(REGISTRY_PATH),
        "intents": intents,
        "nodes": nodes,
        "edges": edges,
    }


def _append_history(state: AgentGraphState, node_name: str, node_kind: str, **extra: str) -> AgentGraphState:
    visited = list(state.get("visited_nodes", []))
    visited.append(node_name)
    update: AgentGraphState = {
        "current_node": node_name,
        "current_kind": node_kind,
        "visited_nodes": visited,
    }
    update.update(extra)
    return update


def _router_node(state: AgentGraphState) -> AgentGraphState:
    return _append_history(state, "router", "router")
def _controller_node(state: AgentGraphState) -> AgentGraphState:
    return _append_history(state, "controller", "controller")
def _make_intent_node(intent_name: str, controller_action: str) -> Callable[[AgentGraphState], AgentGraphState]:
    def _node(state: AgentGraphState) -> AgentGraphState:
        return _append_history(
            state,
            intent_name,
            "intent",
            controller_action=controller_action,
        )

    _node.__name__ = f"intent_{_safe_node_id(intent_name)}"
    return _node


def _make_worker_node(worker_name: str) -> Callable[[AgentGraphState], AgentGraphState]:
    def _node(state: AgentGraphState) -> AgentGraphState:
        return _append_history(state, worker_name, "worker")

    _node.__name__ = f"worker_{_safe_node_id(worker_name)}"
    return _node


def _route_intent(state: AgentGraphState) -> str:
    return str(state.get("intent") or "")


def _build_langgraph_builder() -> Any:
    if StateGraph is None:
        raise RuntimeError(
            "langgraph is not installed in this Python environment. "
            "The VSCode visualizer plugin can display graph-oriented files, "
            "but building a real LangGraph object requires the langgraph package."
        )

    registry = _load_registry()
    intents = _intent_order(registry)
    builder = StateGraph(AgentGraphState)
    builder.add_node("router", _router_node)
    builder.add_node("controller", _controller_node)
    builder.add_edge(START, "router")

    route_map: dict[str, str] = {}
    controller_map: dict[str, str] = {}

    for intent_name in intents:
        intent_node_id = f"intent__{_safe_node_id(intent_name)}"
        builder.add_node(
            intent_node_id,
            _make_intent_node(intent_name, _controller_action(registry, intent_name)),
        )
        route_map[intent_name] = intent_node_id
        builder.add_edge(intent_node_id, "controller")

        chain = _intent_chain(registry, intent_name)
        if not chain:
            controller_map[intent_name] = END
            continue

        first_worker_id = f"{_safe_node_id(intent_name)}__worker_1"
        controller_map[intent_name] = first_worker_id

        previous = None
        for index, worker_name in enumerate(chain, start=1):
            worker_id = f"{_safe_node_id(intent_name)}__worker_{index}"
            builder.add_node(worker_id, _make_worker_node(worker_name))
            if previous is not None:
                builder.add_edge(previous, worker_id)
            previous = worker_id

        builder.add_edge(previous, END)

    builder.add_conditional_edges("router", _route_intent, route_map)
    builder.add_conditional_edges("controller", _route_intent, controller_map)
    return builder


def build_langgraph() -> Any:
    return _build_langgraph_builder().compile()


graph = build_langgraph() if StateGraph is not None else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Expose the current OntoAnno architecture as an explicit LangGraph-style graph.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the normalized graph spec derived from resources/agent_registry.yaml.",
    )
    parser.add_argument(
        "--check-langgraph",
        action="store_true",
        help="Attempt to build the LangGraph object and report success.",
    )
    args = parser.parse_args()

    if args.check_langgraph:
        graph = build_langgraph()
        print(f"Compiled LangGraph: {graph!r}")
        return

    spec = build_graph_spec()
    if args.json:
        print(json.dumps(spec, indent=2))
        return

    print(f"Registry: {spec['registry_path']}")
    print(f"Intents: {len(spec['intents'])}")
    print(f"Nodes: {len(spec['nodes'])}")
    print(f"Edges: {len(spec['edges'])}")


if __name__ == "__main__":
    main()
