from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import dump_json, ensure_dir, load_json, utc_now


def _normalize_label(value: str) -> str:
    text = str(value).strip().lower()
    text = " ".join(text.replace("_", " ").replace("-", " ").split())
    if text.endswith(" cells"):
        text = text[:-1]
    return text


def _default_memory(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_name": config["project"]["name"],
        "updated_at": utc_now(),
        "custom_markers": [],
        "custom_celltypes": [],
        "resolution_feedback": [],
        "literature_tasks": [],
        "subcluster_requests": [],
    }


def memory_path(config: dict[str, Any]) -> Path:
    work_dir = Path(str(config["project"]["work_dir"]))
    return work_dir / "agent_memory.json"


def load_agent_memory(config: dict[str, Any]) -> dict[str, Any]:
    path = memory_path(config)
    if not path.exists():
        return _default_memory(config)
    payload = load_json(path)
    if not isinstance(payload, dict):
        return _default_memory(config)
    default = _default_memory(config)
    default.update(payload)
    return default


def save_agent_memory(config: dict[str, Any], payload: dict[str, Any]) -> Path:
    path = memory_path(config)
    ensure_dir(path.parent)
    payload = dict(payload)
    payload["project_name"] = config["project"]["name"]
    payload["updated_at"] = utc_now()
    dump_json(path, payload)
    return path


def append_memory_entry(memory: dict[str, Any], bucket: str, entry: dict[str, Any]) -> None:
    items = memory.setdefault(bucket, [])
    if not isinstance(items, list):
        items = []
        memory[bucket] = items
    items.append(entry)


def marker_memory_matches(
    memory: dict[str, Any],
    *,
    focus_candidates: list[str],
    current_label: str = "",
) -> list[dict[str, Any]]:
    normalized_targets = {
        _normalize_label(value)
        for value in [*focus_candidates, current_label]
        if str(value).strip()
    }
    matches: list[dict[str, Any]] = []
    for bucket in ("custom_markers", "custom_celltypes"):
        for entry in memory.get(bucket, []):
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("celltype") or "").strip()
            if not label:
                continue
            if _normalize_label(label) not in normalized_targets:
                continue
            matches.append(
                {
                    "bucket": bucket,
                    "celltype": label,
                    "markers": [str(item).strip() for item in entry.get("markers", []) if str(item).strip()],
                    "note": str(entry.get("note") or "").strip(),
                    "source": str(entry.get("source") or "user"),
                    "added_at": str(entry.get("added_at") or ""),
                }
            )
    return matches


def external_evidence_matches(
    memory: dict[str, Any],
    *,
    cluster_markers: list[str],
) -> list[dict[str, Any]]:
    marker_set = {str(item).strip().upper() for item in cluster_markers if str(item).strip()}
    if not marker_set:
        return []

    matches: list[dict[str, Any]] = []
    for bucket in ("custom_markers", "custom_celltypes"):
        for entry in memory.get(bucket, []):
            if not isinstance(entry, dict):
                continue
            celltype = str(entry.get("celltype") or "").strip()
            if not celltype:
                continue
            entry_markers = [str(item).strip() for item in entry.get("markers", []) if str(item).strip()]
            overlap = [marker for marker in entry_markers if marker.upper() in marker_set]
            if not overlap:
                continue
            matches.append(
                {
                    "bucket": bucket,
                    "celltype": celltype,
                    "markers": entry_markers,
                    "overlap_markers": overlap,
                    "overlap_count": len(overlap),
                    "note": str(entry.get("note") or "").strip(),
                    "source": str(entry.get("source") or "user"),
                    "added_at": str(entry.get("added_at") or ""),
                }
            )

    matches.sort(key=lambda item: (-int(item.get("overlap_count", 0) or 0), str(item.get("celltype") or "").lower()))
    return matches
