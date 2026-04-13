from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import dump_json, ensure_dir, load_json, utc_now


MAX_SESSION_MESSAGES = 16


def session_path(config: dict[str, Any]) -> Path:
    work_dir = Path(str(config["project"]["work_dir"]))
    return work_dir / "agent_session.json"


def _default_session(config: dict[str, Any]) -> dict[str, Any]:
    policy = config.get("policy", {}) if isinstance(config.get("policy"), dict) else {}
    return {
        "project_name": config["project"]["name"],
        "updated_at": utc_now(),
        "messages": [],
        "turn_count": 0,
        "state": {
            "active_focus_celltype": None,
            "last_granularity": policy.get("granularity"),
            "last_tool_name": None,
            "last_intent_type": None,
            "last_resolution_feedback": None,
            "last_literature_topic": None,
        },
    }


def load_agent_session(config: dict[str, Any]) -> dict[str, Any]:
    path = session_path(config)
    if not path.exists():
        return _default_session(config)
    payload = load_json(path)
    if not isinstance(payload, dict):
        return _default_session(config)
    default = _default_session(config)
    default.update(payload)
    default["state"] = {
        **_default_session(config)["state"],
        **(payload.get("state") if isinstance(payload.get("state"), dict) else {}),
    }
    default["messages"] = list(payload.get("messages", [])) if isinstance(payload.get("messages"), list) else []
    return default


def save_agent_session(config: dict[str, Any], payload: dict[str, Any]) -> Path:
    path = session_path(config)
    ensure_dir(path.parent)
    payload = dict(payload)
    payload["project_name"] = config["project"]["name"]
    payload["updated_at"] = utc_now()
    messages = payload.get("messages", [])
    if isinstance(messages, list):
        payload["messages"] = messages[-MAX_SESSION_MESSAGES:]
    dump_json(path, payload)
    return path


def reset_agent_session(config: dict[str, Any]) -> Path:
    payload = _default_session(config)
    return save_agent_session(config, payload)


def update_session_state_from_tool(
    session: dict[str, Any],
    *,
    tool_name: str,
    arguments: dict[str, Any],
    intent: dict[str, Any],
) -> None:
    state = session.setdefault("state", {})
    state["last_tool_name"] = tool_name
    state["last_intent_type"] = intent.get("intent_type")

    if tool_name == "change_annotation_preference":
        preference_type = arguments.get("preference_type")
        if preference_type == "granularity":
            state["last_granularity"] = arguments.get("granularity")
        elif preference_type == "resolution":
            state["last_resolution_feedback"] = arguments.get("desired_resolution")
    if tool_name in {"add_external_evidence", "run_subcluster_pipeline"}:
        celltype = arguments.get("celltype")
        if celltype:
            state["active_focus_celltype"] = celltype
