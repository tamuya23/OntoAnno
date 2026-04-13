from __future__ import annotations

from typing import Any

from .agent_router import AgentRouterError, route_agent_request
from .agent_session import reset_agent_session, session_path


def _chat_print(message: str) -> None:
    print(f"[AnnoAgent] {message}", flush=True)


def _render_router_result(result: dict[str, Any]) -> None:
    if result.get("tool_calls"):
        for item in result["tool_calls"]:
            print(f"Executed tool: {item['tool_name']}")
            print(f"Arguments: {item['arguments']}")
            tool_result = item.get("result") or {}
            if tool_result.get("message"):
                print(f"Result: {tool_result['message']}")
            if tool_result.get("updated_config"):
                print(f"Updated config: {tool_result['updated_config']}")
            if tool_result.get("updated_memory"):
                print(f"Updated memory: {tool_result['updated_memory']}")
            if tool_result.get("executed_workers"):
                print("Executed workers:")
                for worker in tool_result["executed_workers"]:
                    label = worker.get("label") or worker.get("worker") or worker.get("tool")
                    print(f"  - {label}")
            if tool_result.get("next_step"):
                print(f"Suggested next step: {tool_result['next_step']}")
            print("")
    if result.get("suggested_next_tools"):
        print("Suggested next actions:")
        for item in result["suggested_next_tools"]:
            print(f"  - {item['tool_name']}: {item['arguments']}")
        print("")
    if result.get("assistant_message"):
        print(result["assistant_message"])
    elif not result.get("tool_calls"):
        print("No tool call proposed.")


def run_chat_session(
    *,
    orchestrator: Any,
    reset_session: bool = False,
) -> int:
    config = orchestrator.config

    if reset_session:
        reset_agent_session(config)

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
            continue

        _render_router_result(result)
        if result.get("session_path"):
            _chat_print(f"Session: {result['session_path']}")
