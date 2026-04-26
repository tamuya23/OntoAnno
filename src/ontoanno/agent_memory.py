from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import dump_json, ensure_dir, load_json, utc_now


def _normalize_label(value: str) -> str:
    text = str(value).strip().lower()
    text = " ".join(text.replace("_", " ").replace("-", " ").split())
    if text.endswith(" cells"):
        text = text[:-1]
    words = text.split()
    if words and words[-1].endswith("s") and not words[-1].endswith("ss") and len(words[-1]) > 3:
        words[-1] = words[-1][:-1]
        text = " ".join(words)
    return text


def _source_type(entry: dict[str, Any]) -> str:
    return str(entry.get("source_type") or entry.get("source") or "user").strip()


def is_literature_evidence(entry: dict[str, Any]) -> bool:
    source = str(entry.get("source") or "").strip().lower()
    source_type = _source_type(entry).lower()
    return source in {"literature", "pdfmarker"} or source_type.startswith("uploaded_pdf") or source_type == "merged_literature"


def _marker_key(marker: str) -> str:
    return " ".join(str(marker).strip().upper().split())


def _merge_markers(existing: list[str], incoming: list[str]) -> list[str]:
    by_key = {_marker_key(marker): marker for marker in existing if _marker_key(marker)}
    for marker in incoming:
        key = _marker_key(marker)
        if key and key not in by_key:
            by_key[key] = str(marker).strip()
    return [by_key[key] for key in sorted(by_key)]


def _compact_note(notes: list[str], count: int, kind: str) -> str:
    unique_notes: list[str] = []
    seen: set[str] = set()
    for note in notes:
        clean = " ".join(str(note).strip().split())
        if not clean or clean in seen:
            continue
        seen.add(clean)
        unique_notes.append(clean)
    prefix = f"Merged {count} {kind} evidence entr{'y' if count == 1 else 'ies'}."
    if not unique_notes:
        return prefix
    return f"{prefix} " + " | ".join(unique_notes[:4])


def compact_custom_marker_memory(memory: dict[str, Any]) -> int:
    """Merge duplicate marker evidence entries by source class and normalized cell type.

    User-provided and literature/PDF evidence remain separate because downstream
    controller logic treats them differently.
    """
    items = memory.get("custom_markers", [])
    if not isinstance(items, list):
        return 0

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    passthrough: list[Any] = []
    counts: dict[tuple[str, str], int] = {}

    for item in items:
        if not isinstance(item, dict):
            passthrough.append(item)
            continue
        celltype = str(item.get("celltype") or "").strip()
        if not celltype:
            passthrough.append(item)
            continue
        normalized = _normalize_label(celltype)
        if not normalized:
            passthrough.append(item)
            continue

        kind = "literature" if is_literature_evidence(item) else "user"
        key = (kind, normalized)
        markers = [str(marker).strip() for marker in item.get("markers", []) if str(marker).strip()]
        if key not in merged:
            merged[key] = dict(item)
            merged[key]["celltype"] = celltype
            merged[key]["markers"] = _merge_markers([], markers)
            merged[key]["source"] = "literature" if kind == "literature" else str(item.get("source") or "user")
            merged[key]["source_type"] = "merged_literature" if kind == "literature" else str(item.get("source_type") or item.get("source") or "user")
            merged[key]["merged_celltype_variants"] = [celltype]
            merged[key]["source_files"] = [str(item.get("source_file") or "")] if str(item.get("source_file") or "").strip() else []
            merged[key]["figure_or_pages"] = [str(item.get("figure_or_page") or "")] if str(item.get("figure_or_page") or "").strip() else []
            merged[key]["_notes"] = [str(item.get("note") or "").strip()]
            counts[key] = 1
            continue

        target = merged[key]
        target["markers"] = _merge_markers(list(target.get("markers", [])), markers)
        variant = celltype
        variants = list(target.get("merged_celltype_variants", []))
        if variant and variant not in variants:
            variants.append(variant)
        target["merged_celltype_variants"] = variants
        source_file = str(item.get("source_file") or "").strip()
        source_files = list(target.get("source_files", []))
        if source_file and source_file not in source_files:
            source_files.append(source_file)
        target["source_files"] = source_files
        figure_or_page = str(item.get("figure_or_page") or "").strip()
        figure_or_pages = list(target.get("figure_or_pages", []))
        if figure_or_page and figure_or_page not in figure_or_pages:
            figure_or_pages.append(figure_or_page)
        target["figure_or_pages"] = figure_or_pages
        notes = list(target.get("_notes", []))
        note = str(item.get("note") or "").strip()
        if note:
            notes.append(note)
        target["_notes"] = notes
        counts[key] = counts.get(key, 1) + 1

    compacted: list[Any] = [*passthrough]
    for key, item in merged.items():
        kind, _ = key
        count = counts.get(key, 1)
        item["evidence_count"] = count
        item["note"] = _compact_note(list(item.pop("_notes", [])), count, kind)
        if count == 1:
            variants = item.get("merged_celltype_variants")
            if variants == [item.get("celltype")]:
                item.pop("merged_celltype_variants", None)
        compacted.append(item)

    memory["custom_markers"] = compacted
    return len(items) - len(compacted)


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
    return work_dir / "ontoanno_memory.json"


def _legacy_memory_path(config: dict[str, Any]) -> Path:
    work_dir = Path(str(config["project"]["work_dir"]))
    return work_dir / "agent_memory.json"


def load_agent_memory(config: dict[str, Any]) -> dict[str, Any]:
    path = memory_path(config)
    legacy_path = _legacy_memory_path(config)
    if not path.exists() and legacy_path.exists():
        path = legacy_path
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
    compact_custom_marker_memory(payload)
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
                    "source_type": _source_type(entry),
                    "source_file": str(entry.get("source_file") or ""),
                    "evidence_count": entry.get("evidence_count"),
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
                    "source_type": _source_type(entry),
                    "source_file": str(entry.get("source_file") or ""),
                    "evidence_count": entry.get("evidence_count"),
                    "is_literature": is_literature_evidence(entry),
                    "added_at": str(entry.get("added_at") or ""),
                }
            )

    matches.sort(key=lambda item: (-int(item.get("overlap_count", 0) or 0), str(item.get("celltype") or "").lower()))
    return matches
