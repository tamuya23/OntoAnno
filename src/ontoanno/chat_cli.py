from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent_router import AgentRouterError, route_agent_request
from .agent_session import reset_agent_session, session_path
from .utils import dump_json, load_json


def _chat_print(message: str) -> None:
    print(f"[OntoAnno] {message}", flush=True)


def _ui_history_path(config: dict[str, Any]) -> Path:
    return Path(str(config["project"]["work_dir"])) / "ontoanno_ui_history.json"


def _load_ui_history(config: dict[str, Any]) -> list[dict[str, str]]:
    path = _ui_history_path(config)
    if not path.exists():
        return []
    payload = load_json(path)
    messages = payload.get("messages", []) if isinstance(payload, dict) else []
    clean: list[dict[str, str]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            clean.append({"role": role, "content": content})
    return clean


def _append_ui_message(
    config: dict[str, Any],
    role: str,
    content: str,
    *,
    dedupe_last: bool = True,
) -> None:
    content = str(content or "").strip()
    role = "user" if role == "user" else "assistant"
    if not content:
        return
    messages = _load_ui_history(config)
    if dedupe_last and messages:
        last = messages[-1]
        if last.get("role") == role and str(last.get("content") or "").strip() == content:
            return
    messages.append({"role": role, "content": content})
    dump_json(_ui_history_path(config), {"messages": messages[-40:]})


def clear_ui_history(config: dict[str, Any]) -> None:
    dump_json(_ui_history_path(config), {"messages": []})


def format_router_result(result: dict[str, Any]) -> str:
    lines: list[str] = []
    if result.get("tool_calls"):
        for item in result["tool_calls"]:
            lines.append(f"Executed tool: {item['tool_name']}")
            lines.append(f"Arguments: {item['arguments']}")
            tool_result = item.get("result") or {}
            if tool_result.get("message"):
                lines.append(f"Result: {tool_result['message']}")
            if tool_result.get("updated_config"):
                lines.append(f"Updated config: {tool_result['updated_config']}")
            if tool_result.get("updated_memory"):
                lines.append(f"Updated memory: {tool_result['updated_memory']}")
            if tool_result.get("executed_workers"):
                lines.append("Executed workers:")
                for worker in tool_result["executed_workers"]:
                    label = worker.get("label") or worker.get("worker") or worker.get("tool")
                    lines.append(f"  - {label}")
            if tool_result.get("next_step"):
                lines.append(f"Suggested next step: {tool_result['next_step']}")
            lines.append("")
    if result.get("suggested_next_tools"):
        lines.append("Suggested next actions:")
        for item in result["suggested_next_tools"]:
            lines.append(f"  - {item['tool_name']}: {item['arguments']}")
        lines.append("")
    if result.get("assistant_message"):
        lines.append(str(result["assistant_message"]))
    elif not result.get("tool_calls"):
        lines.append("No tool call proposed.")
    return "\n".join(lines).strip()


def render_router_result(result: dict[str, Any]) -> None:
    print(format_router_result(result))


def sync_ui_history_turn(config: dict[str, Any], user_message: str, result: dict[str, Any]) -> None:
    _append_ui_message(config, "user", user_message, dedupe_last=False)
    content = format_router_result(result)
    final_text = f"Completed.\n\n{content}" if content else "Completed."
    _append_ui_message(config, "assistant", final_text, dedupe_last=True)


def sync_ui_history_error(config: dict[str, Any], user_message: str, error: str) -> None:
    _append_ui_message(config, "user", user_message, dedupe_last=False)
    _append_ui_message(config, "assistant", f"Agent error: {error}", dedupe_last=True)


def run_chat_session(
    *,
    orchestrator: Any,
    reset_session: bool = False,
) -> int:
    config = orchestrator.config

    if reset_session:
        reset_agent_session(config)
        clear_ui_history(config)

    _chat_print(
        "Starting chat session. Commands: /help, /exit, /quit, /reset."
    )
    _chat_print(f"Mode: apply | Session: {session_path(config)}")

    while True:
        try:
            raw = input("You> ").strip()
        except EOFError:
            print("")
            _chat_print("Session ended.")
            return 0
        except KeyboardInterrupt:
            print("")
            _chat_print("Session interrupted.")
            return 0

        if not raw:
            continue

        lowered = raw.lower()
        if lowered in {"/exit", "/quit"}:
            _chat_print("Session ended.")
            return 0
        if lowered == "/help":
            _chat_print("Commands:")
            _chat_print("  /help    show this help")
            _chat_print("  /reset   clear the current project chat session")
            _chat_print("  /exit    leave chat")
            continue
        if lowered == "/reset":
            reset_agent_session(config)
            clear_ui_history(config)
            _chat_print(f"Session reset: {session_path(config)}")
            continue

        try:
            result = route_agent_request(
                config=config,
                orchestrator=orchestrator,
                user_message=raw,
                apply=True,
                reset_session=False,
            )
        except AgentRouterError as exc:
            _chat_print(f"Agent error: {exc}")
            sync_ui_history_error(config, raw, str(exc))
            continue

        render_router_result(result)
        sync_ui_history_turn(config, raw, result)
        if result.get("session_path"):
            _chat_print(f"Session: {result['session_path']}")
