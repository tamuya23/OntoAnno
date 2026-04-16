from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _registry_path(repo_root: Path) -> Path:
    return repo_root / "resources" / "agent_registry.yaml"


def load_worker_contracts(repo_root: Path) -> dict[str, dict[str, Any]]:
    path = _registry_path(repo_root)
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    workers = payload.get("workers", {})
    if not isinstance(workers, dict):
        return {}
    return {str(name): dict(spec or {}) for name, spec in workers.items()}


def format_worker_contracts(repo_root: Path) -> str:
    workers = load_worker_contracts(repo_root)
    if not workers:
        return "No worker contracts found."

    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for name, spec in workers.items():
        group = str(spec.get("group") or "ungrouped")
        grouped.setdefault(group, []).append((name, spec))

    lines: list[str] = []
    for group in sorted(grouped):
        lines.append(f"{group}:")
        for name, spec in sorted(grouped[group], key=lambda item: item[0]):
            interface_status = str(spec.get("interface_status") or "unknown")
            deployment_status = str(spec.get("deployment_status") or "unknown")
            needs_user = bool(spec.get("needs_user_judgment"))
            description = str(spec.get("description") or "").strip()
            entry = str(spec.get("current_entry") or "")
            lines.append(
                f"  - {name} | interface={interface_status} | deploy={deployment_status} | user_judgment={str(needs_user).lower()}"
            )
            if description:
                lines.append(f"    description: {description}")
            if entry:
                lines.append(f"    entry: {entry}")
            implemented_by = str(spec.get("implemented_by") or "")
            if implemented_by:
                lines.append(f"    implemented_by: {implemented_by}")
            inputs = spec.get("inputs") or []
            if isinstance(inputs, list):
                inputs_text = ", ".join(str(item).strip() for item in inputs if str(item).strip())
                if inputs_text:
                    lines.append(f"    inputs: {inputs_text}")
            depends_on = spec.get("depends_on") or []
            if isinstance(depends_on, list):
                depends_text = ", ".join(str(item).strip() for item in depends_on if str(item).strip())
                if depends_text:
                    lines.append(f"    depends_on: {depends_text}")
            produces = spec.get("produces") or []
            if isinstance(produces, list):
                produces_text = ", ".join(str(item).strip() for item in produces if str(item).strip())
                if produces_text:
                    lines.append(f"    produces: {produces_text}")
            notes = spec.get("notes") or []
            if isinstance(notes, list):
                for note in notes:
                    text = str(note).strip()
                    if text:
                        lines.append(f"    note: {text}")
        lines.append("")
    return "\n".join(lines).rstrip()
