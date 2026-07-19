from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from .agent_memory import load_agent_memory
from .agent_requests import _extract_celltype, _has_anaphora_reference, _mentions_celltype, apply_agent_request
from .openai_client import OpenAIRequestError, chat_completions_url, post_openai_json
from .review_packets import resolve_imported_parent_annotations
from .agent_session import (
    load_agent_session,
    reset_agent_session,
    save_agent_session,
    session_path,
    update_session_state_from_tool,
)
from .utils import load_json


class AgentRouterError(RuntimeError):
    pass


def _chat_completions_url(config: dict[str, Any]) -> str:
    llm_config = config["llm"]["annotation"]
    return chat_completions_url(llm_config.get("api_url"))


def _router_system_prompt() -> str:
    return (
        "You are the natural-language controller for OntoAnno. "
        "Your job is to interpret the user's request and choose the most appropriate tool call. "
        "Do not ask the user to manually rewrite config values if a tool can handle it. "
        "Use tools only when the user is asking to change project state, run analysis, or execute a workflow step. "
        "Do not overuse tools for read-only questions. "
        "If the user is primarily asking to inspect, explain, summarize, compare, or show current state, answer directly unless a dedicated tool is truly required. "
        "Use inspect_dataset when the user asks for basic dataset information, dataset metadata, species, tissue, input data path, preprocessing settings, or configured clustering resolutions. "
        "Prefer a single high-value action unless multiple actions are clearly necessary. "
        "When the request is about parent pipeline reruns, subcluster reruns, RAG-based review/checks, "
        "annotation preference changes such as granularity or resolution, researcher-provided external evidence, "
        "reference-label comparison setup, final report format, finishing reviewed annotations, "
        "or extracting external evidence from papers/databases, "
        "translate it into a tool call. "
        "If the previous result suggested human_review and the user confirms with words like yes, sure, proceed, or continue, "
        "choose human_review rather than re-running run_RAG_check. "
        "For parent pipeline requests, use mode='resume' for ordinary run/continue/keep going requests, and mode='rerun' only when the user explicitly asks to restart, rerun, or run from scratch. "
        "If the user gives a final label for a specific human-review cluster, choose save_human_review_decision; do not guess the cluster id or final label. "
        "If the user asks to finish, finalize, export, or close out human-reviewed annotations, choose finish_review. "
        "If the user asks for an HTML or PDF report, choose run_report and pass the requested format. "
        "If the user provides a manual/reference label CSV and label-column name for comparison, choose configure_reference_labels; do not guess the label-column name. "
        "Requests like 'look deeper into pericytes', 'drill down into fibroblasts', 'subcluster this cell type', "
        "or 'look inside this cell type' should normally be treated as run_subcluster_pipeline, not as a request for brainstorming options. "
        "Important distinction: resolution changes the clustering itself and can change cluster membership and marker genes; "
        "granularity changes how specifically cell types are described after clusters and marker genes are already fixed. "
        "For add_external_evidence, never invent a cell type. Only use a cell type that is explicit in the current request, "
        "or a clearly referenced prior focus such as 'it' when the conversation already has an active focus cell type. "
        "If the request is ambiguous or unsafe to execute, explain briefly instead of forcing a tool."
    )


def _tool_gate_prompt() -> str:
    return (
        "Decide whether the user's latest request requires a tool call. "
        "Use a tool only if the request would change project state, write memory/config, or run analysis workers. "
        "Requests to run, resume, continue, restart, or rerun the parent annotation pipeline require a tool call. "
        "Requests to look deeper into a specific cell type, drill down into a cell type, or subcluster a cell type do require a tool call. "
        "Requests to save human-review decisions, configure reference labels, export reviewed annotations, or generate HTML/PDF reports require a tool call. "
        "Requests for basic dataset information, configured species or tissue, input dataset path, preprocessing settings, or configured resolutions require the inspect_dataset tool. "
        "If the request is read-only and can be answered from the provided context, answer it directly and do not request a tool. "
        "If a tool is required, reply with exactly: TOOL_REQUIRED"
    )


def _followup_suggestion_prompt() -> str:
    return (
        "Based on the executed result above, suggest the single most useful next tool call if one is clearly warranted. "
        "Do not repeat the same tool unless it is truly necessary. "
        "These follow-up tool calls are suggestions only and must not be assumed to have been executed."
    )


def _tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "inspect_dataset",
                "description": "Read configured basic dataset information such as species, tissue, input Seurat path, preprocessing mode, and clustering resolutions without running analysis.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                    },
                    "required": ["reason"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_parent_pipeline",
                "description": "Run the parent GPTAnno backbone from preprocessing through assigned parent labels.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                        "mode": {
                            "type": "string",
                            "enum": ["resume", "rerun"],
                            "description": "Use resume for normal run/continue requests; use rerun only for explicit restart/from-scratch requests.",
                        },
                    },
                    "required": ["reason"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_subcluster_pipeline",
                "description": "Run targeted subclustering for one parent cell type of interest.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "celltype": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["celltype", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_report",
                "description": "Generate the final report from currently available artifacts.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "format": {
                            "type": "string",
                            "enum": ["html", "pdf"],
                            "description": "Optional final report format requested by the user.",
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["reason"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_RAG_check",
                "description": "Run the current RAG-based check and review pipeline on the available annotations.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                    },
                    "required": ["reason"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "human_review",
                "description": "Start or resume direct human review for unresolved clusters after automated RAG comparison. Use this when the user confirms a suggested human review step.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                    },
                    "required": ["reason"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finish_review",
                "description": "Export reviewed parent annotations after RAG/human review decisions are available. Use this when the user says to finish, finalize, or export reviewed labels.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                    },
                    "required": ["reason"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "save_human_review_decision",
                "description": "Save one explicit human-review decision for a specific unresolved cluster. Only call this when both cluster_id and final_label are explicit.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cluster_id": {
                            "type": "string",
                            "description": "Cluster id shown in the human-review table.",
                        },
                        "final_label": {
                            "type": "string",
                            "description": "User-approved final label for this cluster.",
                        },
                        "user_note": {
                            "type": "string",
                            "description": "Optional note explaining the user's decision.",
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["cluster_id", "final_label", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "configure_reference_labels",
                "description": "Configure a manual/reference label CSV for later evaluation and report comparison. Only call this when the CSV path and label-column name are both explicit.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reference_labels_csv": {
                            "type": "string",
                            "description": "Path to the CSV that contains known/manual labels.",
                        },
                        "manual_col": {
                            "type": "string",
                            "description": "Column name in the CSV that contains known/manual labels.",
                        },
                        "enable_evaluation": {
                            "type": "boolean",
                            "description": "Whether to enable reference-label evaluation.",
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["reference_labels_csv", "manual_col", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "change_annotation_preference",
                "description": "Change an annotation preference. Resolution changes the cluster partition and may change marker genes. Granularity changes only how specifically annotations are expressed after clusters and markers are fixed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "preference_type": {
                            "type": "string",
                            "enum": ["granularity", "resolution"],
                        },
                        "granularity": {
                            "type": "string",
                            "enum": ["coarse", "balanced", "fine"],
                        },
                        "desired_resolution": {"type": ["number", "null"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["preference_type", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "add_external_evidence",
                "description": "Store researcher-provided external evidence, either by updating an existing cell type with markers or defining a new custom cell type.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "celltype": {"type": "string"},
                        "markers": {"type": "array", "items": {"type": "string"}},
                        "knowledge_mode": {
                            "type": "string",
                            "enum": ["auto", "update_existing", "define_new"],
                        },
                        "note": {"type": "string"},
                    },
                    "required": ["celltype"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "extract_external_evidence",
                "description": "Extract celltype-marker evidence from provided literature PDFs only. Text is processed with GPTAnno/PDF2markers and selected pages are processed with the vision LLM. Do not use this for web search.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "source_hint": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["reason"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def _resolution_summary(config: dict[str, Any]) -> list[dict[str, str]]:
    csv_path = Path(str(config["project"]["work_dir"])) / "annotate_parent" / "annotation_summary_scores.csv"
    imported = resolve_imported_parent_annotations(config)
    if not csv_path.exists() and imported.get("annotation_scores_csv"):
        csv_path = Path(str(imported["annotation_scores_csv"]))
    if not csv_path.exists():
        return []
    rows: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return rows[:5]


def _current_parent_resolution(config: dict[str, Any]) -> str:
    annotation = config.get("annotation", {}) if isinstance(config.get("annotation"), dict) else {}
    forced = str(annotation.get("forced_parent_resolution") or "").strip()
    if forced:
        return forced
    imported = resolve_imported_parent_annotations(config)
    if imported.get("best_resolution_value"):
        return str(imported["best_resolution_value"])
    best_json = Path(str(config["project"]["work_dir"])) / "annotate_parent" / "best_parent_resolution.json"
    if best_json.exists():
        payload = load_json(best_json)
        selected = str(payload.get("best_resolution_value") or payload.get("best_resolution") or "").strip()
        if selected:
            return selected.removeprefix("res_")
    rows = _resolution_summary(config)
    best_row: dict[str, str] | None = None
    best_score: float | None = None
    for row in rows:
        try:
            score = float(row.get("composite_score") or "")
        except ValueError:
            continue
        if best_score is None or score > best_score:
            best_score = score
            best_row = row
    selected = str((best_row or (rows[0] if rows else {})).get("resolution") or "").strip()
    if selected:
        return selected.removeprefix("res_")
    return "unknown"


def _controller_summary(orchestrator: Any) -> dict[str, Any] | None:
    index_path = orchestrator.run_dir / "controller" / "index.json"
    if not index_path.exists():
        return None
    payload = load_json(index_path)
    return payload.get("summary")


def _state_summary(config: dict[str, Any], orchestrator: Any) -> str:
    memory = load_agent_memory(config)
    session = load_agent_session(config)
    policy = config.get("policy", {}) if isinstance(config.get("policy"), dict) else {}
    inputs = config.get("inputs", {}) if isinstance(config.get("inputs"), dict) else {}
    evaluation = config.get("evaluation", {}) if isinstance(config.get("evaluation"), dict) else {}
    report = config.get("report", {}) if isinstance(config.get("report"), dict) else {}
    session_state = session.get("state", {}) if isinstance(session.get("state"), dict) else {}
    lines = [
        f"Project: {config['project']['name']}",
        f"Current selected parent resolution: {_current_parent_resolution(config)}",
        f"Current policy.granularity: {policy.get('granularity', 'balanced')}",
        "Resolution is the clustering choice; granularity is the label-specificity preference after clusters and marker genes are fixed.",
        f"Ontology restricted: {policy.get('ontology', True)}",
        f"Report format: {report.get('format', 'html')}",
        f"Reference labels CSV: {inputs.get('reference_labels_csv') or '(not configured)'}",
        f"Reference-label evaluation enabled: {evaluation.get('enabled', False)}",
        f"Custom marker memory entries: {len(memory.get('custom_markers', []))}",
        f"Custom cell type memory entries: {len(memory.get('custom_celltypes', []))}",
        f"Session turns so far: {session.get('turn_count', 0)}",
    ]
    custom_marker_entries = memory.get("custom_markers", []) if isinstance(memory.get("custom_markers"), list) else []
    custom_celltype_entries = memory.get("custom_celltypes", []) if isinstance(memory.get("custom_celltypes"), list) else []
    if custom_marker_entries:
        lines.append("External evidence marker entries:")
        for entry in custom_marker_entries[:5]:
            if not isinstance(entry, dict):
                continue
            celltype = str(entry.get("celltype") or "").strip()
            markers = [str(item).strip() for item in entry.get("markers", []) if str(item).strip()]
            note = str(entry.get("note") or "").strip()
            marker_text = ", ".join(markers[:6]) if markers else "(no markers listed)"
            line = f"- {celltype}: {marker_text}"
            if note:
                line += f" | note={note}"
            lines.append(line)
    if custom_celltype_entries:
        lines.append("External evidence custom cell type entries:")
        for entry in custom_celltype_entries[:5]:
            if not isinstance(entry, dict):
                continue
            celltype = str(entry.get("celltype") or "").strip()
            markers = [str(item).strip() for item in entry.get("markers", []) if str(item).strip()]
            note = str(entry.get("note") or "").strip()
            marker_text = ", ".join(markers[:6]) if markers else "(no markers listed)"
            line = f"- {celltype}: {marker_text}"
            if note:
                line += f" | note={note}"
            lines.append(line)
    if session_state.get("active_focus_celltype"):
        lines.append(f"Active focus celltype: {session_state.get('active_focus_celltype')}")
    if session_state.get("last_tool_name"):
        lines.append(f"Last tool used: {session_state.get('last_tool_name')}")
    if session_state.get("last_granularity"):
        lines.append(f"Session granularity preference: {session_state.get('last_granularity')}")
    resolution_rows = _resolution_summary(config)
    if resolution_rows:
        lines.append("Recent parent resolution summary:")
        for row in resolution_rows:
            lines.append(
                f"- {row.get('resolution', '')}: composite_score={row.get('composite_score', '')}, "
                f"avg_max_percentage={row.get('avg_max_percentage', '')}"
            )
    controller = _controller_summary(orchestrator)
    if controller:
        lines.append(
            "Current controller summary: "
            f"build_ontology_relations={controller.get('build_ontology_relations_count', 0)}, "
            f"run_llm_compare={controller.get('run_llm_compare_count', 0)}, "
            f"ask_user={controller.get('ask_user_count', 0)}"
        )
    return "\n".join(lines)


def _extract_message_content(response_payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    choices = response_payload.get("choices", [])
    if not choices:
        raise AgentRouterError("No choices returned from LLM API")
    message = choices[0].get("message", {})
    content = message.get("content")
    text_parts: list[str] = []
    if isinstance(content, str):
        text_parts.append(content.strip())
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    text_parts.append(str(text).strip())
    tool_calls = message.get("tool_calls", [])
    return "\n".join(part for part in text_parts if part).strip(), tool_calls if isinstance(tool_calls, list) else []


def _compact_session_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            continue
        if role == "assistant" and message.get("tool_calls"):
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        if role == "user" and text.startswith("Current OntoAnno state:") and "\n\nUser request:\n" in text:
            text = text.split("\n\nUser request:\n", 1)[1].strip()
        compacted.append({"role": role, "content": text})
    return compacted


def _has_anaphora_reference(text: str) -> bool:
    return bool(re.search(r"\b(it|this|that|them|this one|that one|that cell type|this cell type)\b", text, flags=re.IGNORECASE))


def _resolve_external_evidence_target(
    *,
    user_request: str,
    requested_celltype: str | None,
    session: dict[str, Any],
) -> str | None:
    explicit = _extract_celltype(user_request)
    if explicit:
        return explicit
    requested = (requested_celltype or "").strip()
    if requested and _mentions_celltype(user_request, requested):
        return requested
    state = session.get("state", {}) if isinstance(session.get("state"), dict) else {}
    focus = str(state.get("active_focus_celltype") or "").strip()
    if focus and _has_anaphora_reference(user_request):
        return focus
    return None


def _call_openai_router(
    *,
    config: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    llm_config = config["llm"]["annotation"]
    provider = str(llm_config["provider"]).lower()
    if provider != "openai":
        raise AgentRouterError(f"Agent ask/router currently supports only provider 'openai'; got '{provider}'")
    api_key = llm_config.get("api_key") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise AgentRouterError("Missing API key for llm.annotation")
    payload = {
        "model": llm_config["model"],
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    try:
        return post_openai_json(
            url=_chat_completions_url(config),
            payload=payload,
            api_key=str(api_key),
            timeout=180,
        )
    except OpenAIRequestError as exc:
        raise AgentRouterError(str(exc)) from exc


def _infer_parent_pipeline_mode(text: str) -> str:
    lower = text.lower()
    rerun_terms = (
        "from scratch",
        "from the start",
        "restart",
        "rerun",
        "re-run",
        "run again",
        "start over",
        "重新跑",
        "重跑",
        "从头",
    )
    return "rerun" if any(term in lower for term in rerun_terms) else "resume"


def _intent_from_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    user_request: str,
    session: dict[str, Any],
) -> dict[str, Any]:
    if tool_name == "inspect_dataset":
        return {
            "intent_type": "inspect_dataset",
            "raw_text": user_request,
        }
    if tool_name == "run_parent_pipeline":
        return {
            "intent_type": "run_parent_pipeline",
            "mode": arguments.get("mode") or _infer_parent_pipeline_mode(user_request),
            "raw_text": user_request,
        }
    if tool_name == "run_subcluster_pipeline":
        return {
            "intent_type": "run_subcluster_pipeline",
            "celltype": arguments.get("celltype"),
            "raw_text": user_request,
        }
    if tool_name == "run_RAG_check":
        return {
            "intent_type": "run_RAG_check",
            "raw_text": user_request,
        }
    if tool_name == "human_review":
        return {
            "intent_type": "human_review",
            "raw_text": user_request,
        }
    if tool_name == "finish_review":
        return {
            "intent_type": "finish_review",
            "raw_text": user_request,
        }
    if tool_name == "save_human_review_decision":
        return {
            "intent_type": "save_human_review_decision",
            "cluster_id": arguments.get("cluster_id"),
            "final_label": arguments.get("final_label"),
            "user_note": arguments.get("user_note"),
            "raw_text": user_request,
        }
    if tool_name == "run_report":
        return {
            "intent_type": "run_report",
            "format": arguments.get("format"),
            "raw_text": user_request,
        }
    if tool_name == "configure_reference_labels":
        return {
            "intent_type": "configure_reference_labels",
            "reference_labels_csv": arguments.get("reference_labels_csv"),
            "manual_col": arguments.get("manual_col"),
            "enable_evaluation": arguments.get("enable_evaluation", True),
            "raw_text": user_request,
        }
    if tool_name == "change_annotation_preference":
        return {
            "intent_type": "change_annotation_preference",
            "preference_type": arguments.get("preference_type"),
            "granularity": arguments.get("granularity"),
            "desired_resolution": arguments.get("desired_resolution"),
            "raw_text": user_request,
        }
    if tool_name == "add_external_evidence":
        return {
            "intent_type": "add_external_evidence",
            "celltype": arguments.get("celltype"),
            "markers": arguments.get("markers", []),
            "knowledge_mode": arguments.get("knowledge_mode") or "auto",
            "raw_text": user_request,
            "context_celltype": (
                (session.get("state") or {}).get("active_focus_celltype")
                if isinstance(session.get("state"), dict)
                else None
            ),
        }
    if tool_name == "extract_external_evidence":
        return {
            "intent_type": "extract_external_evidence",
            "topic": arguments.get("topic"),
            "source_hint": arguments.get("source_hint"),
            "raw_text": user_request,
        }
    raise AgentRouterError(f"Unsupported tool selected by model: {tool_name}")


def route_agent_request(
    *,
    config: dict[str, Any],
    orchestrator: Any,
    user_message: str,
    apply: bool = True,
    reset_session: bool = False,
    max_rounds: int = 2,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    del apply, max_rounds
    if reset_session:
        reset_agent_session(config)
    session = load_agent_session(config)
    tools = _tool_schemas()
    session_messages = _compact_session_messages(list(session.get("messages", [])))
    current_user_message = {"role": "user", "content": user_message.strip()}
    state_message = {
        "role": "user",
        "content": "Current OntoAnno state:\n" + _state_summary(config, orchestrator),
    }
    conversation_messages: list[dict[str, Any]] = [*session_messages, current_user_message]

    executed_tools: list[dict[str, Any]] = []
    suggested_next_tools: list[dict[str, Any]] = []
    raw_responses: list[dict[str, Any]] = []

    gate_messages = [{"role": "system", "content": _tool_gate_prompt()}, state_message, *conversation_messages]
    gate_payload = _call_openai_router(config=config, messages=gate_messages, tools=None)
    raw_responses.append(gate_payload)
    gate_text, _ = _extract_message_content(gate_payload)
    if gate_text.strip() != "TOOL_REQUIRED":
        conversation_messages.append(
            {
                "role": "assistant",
                "content": gate_text or "",
            }
        )
        session["messages"] = _compact_session_messages(conversation_messages)
        session["turn_count"] = int(session.get("turn_count", 0)) + 1
        save_agent_session(config, session)
        return {
            "mode": "answered",
            "assistant_message": gate_text,
            "tool_calls": executed_tools,
            "raw_responses": raw_responses,
            "session_path": str(session_path(config)),
        }

    request_messages = [{"role": "system", "content": _router_system_prompt()}, state_message, *conversation_messages]
    response_payload = _call_openai_router(config=config, messages=request_messages, tools=tools)
    raw_responses.append(response_payload)
    assistant_text, tool_calls = _extract_message_content(response_payload)
    choice = response_payload.get("choices", [{}])[0]
    assistant_message = choice.get("message", {})

    if not tool_calls:
        conversation_messages.append(
            {
                "role": "assistant",
                "content": assistant_text or "",
            }
        )
        session["messages"] = _compact_session_messages(conversation_messages)
        session["turn_count"] = int(session.get("turn_count", 0)) + 1
        save_agent_session(config, session)
        return {
            "mode": "answered",
            "assistant_message": assistant_text,
            "tool_calls": executed_tools,
            "raw_responses": raw_responses,
            "session_path": str(session_path(config)),
        }

    conversation_messages.append(
        {
            "role": "assistant",
            "content": assistant_message.get("content"),
            "tool_calls": tool_calls,
        }
    )

    for tool_call in tool_calls:
        function = tool_call.get("function", {})
        tool_name = str(function.get("name") or "")
        raw_arguments = str(function.get("arguments") or "{}")
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except json.JSONDecodeError as exc:
            raise AgentRouterError(f"Tool arguments were not valid JSON for {tool_name}: {raw_arguments}") from exc

        intent = _intent_from_tool(
            tool_name,
            arguments,
            user_request=user_message.strip(),
            session=session,
        )
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "selected_tool",
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "intent_type": intent.get("intent_type"),
                }
            )
        if intent.get("intent_type") == "add_external_evidence":
            resolved_celltype = _resolve_external_evidence_target(
                user_request=user_message.strip(),
                requested_celltype=str(arguments.get("celltype") or "").strip(),
                session=session,
            )
            if not resolved_celltype:
                execution_result = {
                    "intent": intent,
                    "applied": False,
                    "message": (
                        "Please specify which cell type these markers belong to. "
                        "I will not assign marker genes to a cell type unless the target is explicit in the request "
                        "or clearly inherited from the current conversation focus."
                    ),
                    "next_step": "",
                }
            else:
                intent["resolved_celltype"] = resolved_celltype
                execution_result = apply_agent_request(config, intent, orchestrator=orchestrator)
        else:
            execution_result = apply_agent_request(config, intent, orchestrator=orchestrator)

        executed_tools.append(
            {
                "tool_name": tool_name,
                "arguments": arguments,
                "intent": intent,
                "result": execution_result,
            }
        )
        update_session_state_from_tool(
            session,
            tool_name=tool_name,
            arguments={
                **arguments,
                **({"celltype": execution_result.get("resolved_celltype")} if execution_result.get("resolved_celltype") else {}),
            },
            intent=intent,
        )

        conversation_messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.get("id"),
                "content": json.dumps(execution_result, ensure_ascii=False),
            }
        )

    suggestion_messages = [
        {"role": "system", "content": _router_system_prompt()},
        state_message,
        *conversation_messages,
        {"role": "user", "content": _followup_suggestion_prompt()},
    ]
    suggestion_payload = _call_openai_router(config=config, messages=suggestion_messages, tools=tools)
    raw_responses.append(suggestion_payload)
    _, suggestion_tool_calls = _extract_message_content(suggestion_payload)
    for tool_call in suggestion_tool_calls:
        function = tool_call.get("function", {})
        tool_name = str(function.get("name") or "")
        raw_arguments = str(function.get("arguments") or "{}")
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except json.JSONDecodeError as exc:
            raise AgentRouterError(f"Suggested tool arguments were not valid JSON for {tool_name}: {raw_arguments}") from exc
        suggested_next_tools.append(
            {
                "tool_name": tool_name,
                "arguments": arguments,
            }
        )

    summary_messages = [{"role": "system", "content": _router_system_prompt()}, state_message, *conversation_messages]
    summary_payload = _call_openai_router(config=config, messages=summary_messages, tools=None)
    raw_responses.append(summary_payload)
    assistant_text, _ = _extract_message_content(summary_payload)
    conversation_messages.append({"role": "assistant", "content": assistant_text or ""})
    session["messages"] = _compact_session_messages(conversation_messages)
    session["turn_count"] = int(session.get("turn_count", 0)) + 1
    save_agent_session(config, session)
    return {
        "mode": "applied",
        "assistant_message": assistant_text,
        "tool_calls": executed_tools,
        "suggested_next_tools": suggested_next_tools,
        "raw_responses": raw_responses,
        "session_path": str(session_path(config)),
    }
