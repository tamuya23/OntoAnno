from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .agent_memory import load_agent_memory
from .agent_requests import _extract_celltype, apply_agent_request
from .agent_session import (
    load_agent_session,
    reset_agent_session,
    save_agent_session,
    session_path,
    update_session_state_from_tool,
)
from .utils import load_json


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


class AgentRouterError(RuntimeError):
    pass


def _chat_completions_url(config: dict[str, Any]) -> str:
    llm_config = config["llm"]["annotation"]
    base = (
        llm_config.get("api_url")
        or os.getenv("OPENAI_BASE_URL")
        or DEFAULT_OPENAI_BASE_URL
    )
    base = str(base).rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _router_system_prompt() -> str:
    return (
        "You are the natural-language controller for OntoAnno. "
        "Your job is to interpret the user's request and choose the most appropriate tool call. "
        "Do not ask the user to manually rewrite config values if a tool can handle it. "
        "Use tools only when the user is asking to change project state, run analysis, or execute a workflow step. "
        "Do not overuse tools for read-only questions. "
        "If the user is primarily asking to inspect, explain, summarize, compare, or show current state, answer directly unless a dedicated tool is truly required. "
        "Prefer a single high-value action unless multiple actions are clearly necessary. "
        "When the request is about parent pipeline reruns, subcluster reruns, RAG-based review/checks, "
        "annotation preference changes such as granularity or resolution, researcher-provided external evidence, "
        "or extracting external evidence from papers/databases, "
        "translate it into a tool call. "
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
        "Requests to look deeper into a specific cell type, drill down into a cell type, or subcluster a cell type do require a tool call. "
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
                "name": "run_parent_pipeline",
                "description": "Run the parent GPTAnno backbone from preprocessing through assigned parent labels.",
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
                "description": "Extract candidate celltype-marker evidence from external sources such as user-specified papers, PDFs, or reference databases. This intent is registered, but its worker chain may still be a placeholder.",
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
    if not csv_path.exists():
        return []
    rows: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8") as handle:
        header = handle.readline().strip().split(",")
        for line in handle:
            values = line.strip().split(",")
            row = {header[i]: values[i] if i < len(values) else "" for i in range(len(header))}
            rows.append(row)
    return rows[:5]


def _current_parent_resolution(config: dict[str, Any]) -> str:
    rows = _resolution_summary(config)
    if rows:
        selected = str(rows[0].get("resolution") or "").strip()
        if selected:
            return selected
    annotation = config.get("annotation", {}) if isinstance(config.get("annotation"), dict) else {}
    forced = str(annotation.get("forced_parent_resolution") or "").strip()
    if forced:
        return forced
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
    session_state = session.get("state", {}) if isinstance(session.get("state"), dict) else {}
    lines = [
        f"Project: {config['project']['name']}",
        f"Current selected parent resolution: {_current_parent_resolution(config)}",
        f"Current policy.granularity: {policy.get('granularity', 'balanced')}",
        "Resolution is the clustering choice; granularity is the label-specificity preference after clusters and marker genes are fixed.",
        f"Ontology restricted: {policy.get('ontology', True)}",
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
    if requested:
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
    api_key = llm_config.get("api_key")
    if not api_key:
        raise AgentRouterError("Missing API key for llm.annotation")
    payload = {
        "model": llm_config["model"],
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    request = urllib.request.Request(
        _chat_completions_url(config),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AgentRouterError(f"LLM API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise AgentRouterError(f"LLM API request failed: {exc.reason}") from exc


def _intent_from_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    user_request: str,
    session: dict[str, Any],
) -> dict[str, Any]:
    if tool_name == "run_parent_pipeline":
        return {
            "intent_type": "run_parent_pipeline",
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
    if tool_name == "run_report":
        return {
            "intent_type": "run_report",
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
    apply: bool = False,
    reset_session: bool = False,
    max_rounds: int = 2,
) -> dict[str, Any]:
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
            "mode": "preview" if not apply else "applied",
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
            "mode": "preview" if not apply else "applied",
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
                if apply:
                    execution_result = apply_agent_request(config, intent, orchestrator=orchestrator)
                else:
                    execution_result = {
                        "intent": intent,
                        "applied": False,
                        "message": "Preview only. Tool call not executed.",
                        "next_step": "",
                        "resolved_celltype": resolved_celltype,
                    }
        elif apply:
            execution_result = apply_agent_request(config, intent, orchestrator=orchestrator)
        else:
            execution_result = {
                "intent": intent,
                "applied": False,
                "message": "Preview only. Tool call not executed.",
                "next_step": "",
            }

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
        "mode": "preview" if not apply else "applied",
        "assistant_message": assistant_text,
        "tool_calls": executed_tools,
        "suggested_next_tools": suggested_next_tools,
        "raw_responses": raw_responses,
        "session_path": str(session_path(config)),
    }
