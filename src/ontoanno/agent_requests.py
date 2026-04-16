from __future__ import annotations

import csv
import os
import re
from pathlib import Path
from typing import Any

import yaml

from .agent_memory import append_memory_entry, load_agent_memory, save_agent_memory
from .utils import load_json, utc_now
from .worker_runtime import (
    PARENT_BACKBONE_WORKERS,
    SUBCLUSTER_WORKERS,
    has_parent_annotation_outputs,
    run_generate_report_worker,
    run_gptanno_worker_chain,
    run_rag_check_workers,
)


GENE_STOPWORDS = {
    "and",
    "annotation",
    "article",
    "balanced",
    "cell",
    "celltype",
    "cells",
    "coarse",
    "compare",
    "define",
    "effect",
    "fine",
    "for",
    "gene",
    "genes",
    "latest",
    "look",
    "marker",
    "markers",
    "more",
    "new",
    "not",
    "paper",
    "papers",
    "policy",
    "resolution",
    "specific",
    "subcluster",
    "the",
    "too",
    "update",
    "want",
    "with",
}


def _config_path(config: dict[str, Any]) -> Path:
    return Path(str(config["_meta"]["config_path"]))


def _load_raw_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"Config is not a mapping: {path}")
    return payload


def _save_raw_config(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def _extract_quoted(text: str) -> list[str]:
    return [item.strip() for item in re.findall(r"['\"]([^'\"]+)['\"]", text) if item.strip()]


def _extract_gene_tokens(text: str) -> list[str]:
    segment = text
    if ":" in text:
        segment = text.split(":", 1)[1]
    tokens = re.findall(r"\b[A-Za-z][A-Za-z0-9-]{1,19}\b", segment)
    result: list[str] = []
    for token in tokens:
        cleaned = token.strip().strip(",.;")
        lower = cleaned.lower()
        if lower in GENE_STOPWORDS:
            continue
        if not (any(char.isupper() for char in cleaned) or any(char.isdigit() for char in cleaned)):
            continue
        if cleaned not in result:
            result.append(cleaned)
    return result


def _extract_celltype(text: str) -> str | None:
    quoted = _extract_quoted(text)
    if quoted:
        return quoted[0]
    patterns = [
        r"(?:interested in|focus on|look at)\s+([A-Za-z][A-Za-z0-9 _/\-]+?)(?:\s|[:,.;]|$)",
        r"(?:look deeper into|drill down into|look inside)\s+([A-Za-z][A-Za-z0-9 _/\-]+?)(?:\s|[:,.;]|$)",
        r"(?:for|about|target|针对|对于|对)\s+([A-Za-z][A-Za-z0-9 _/\-]+?)(?:\s+with|\s*[:,.;]|$)",
        r"(?:to)\s+([A-Za-z][A-Za-z0-9 _/\-]+?)(?:\s+with|\s*[:,.;]|$)",
        r"(?:celltype|cell type|细胞类型)\s+([A-Za-z][A-Za-z0-9 _/\-]+?)(?:\s+with|\s*[:,.;]|$)",
        r"(?:subcluster|sub cluster)\s+([A-Za-z][A-Za-z0-9 _/\-]+?)(?:\s|$)",
        r"(?:感兴趣|关注)\s*([A-Za-z][A-Za-z0-9 _/\-]+?)(?:\s|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip(" ,.;:")
            if value and value.lower() not in {"it", "this", "that", "them"}:
                return value
    return None


def _parse_granularity(text: str) -> str | None:
    lower = text.lower()
    if any(phrase in lower for phrase in ["balanced", "中间", "折中", "not too coarse", "not too fine"]):
        return "balanced"
    if any(
        phrase in lower
        for phrase in [
            "fine",
            "finer",
            "more specific",
            "too coarse",
            "not specific enough",
            "细分",
            "更细",
            "不够细",
            "不够specific",
        ]
    ):
        return "fine"
    if any(
        phrase in lower
        for phrase in [
            "coarse",
            "coarser",
            "too fine",
            "too specific",
            "粗分",
            "更粗",
            "太细",
        ]
    ):
        return "coarse"
    return None


def _extract_resolution_value(text: str) -> float | None:
    match = re.search(r"\b(0\.\d+|1(?:\.0+)?)\b", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _infer_custom_knowledge_mode(
    *,
    celltype: str,
    raw_text: str,
    memory: dict[str, Any],
    requested_mode: str | None,
) -> str:
    mode = (requested_mode or "").strip().lower()
    if mode in {"update_existing", "define_new"}:
        return mode

    lower = raw_text.lower()
    if any(term in lower for term in ["define", "new celltype", "new cell type", "custom celltype", "custom cell type", "自定义", "定义"]):
        return "define_new"

    normalized = celltype.strip().lower()
    for bucket in ("custom_markers", "custom_celltypes"):
        for item in memory.get(bucket, []) if isinstance(memory.get(bucket), list) else []:
            if str(item.get("celltype") or "").strip().lower() == normalized:
                return "update_existing"

    return "update_existing"


def _has_anaphora_reference(text: str) -> bool:
    return bool(re.search(r"\b(it|this|that|them|this one|that one|that cell type|this cell type)\b", text, flags=re.IGNORECASE))


def _resolve_external_evidence_celltype(
    *,
    raw_text: str,
    requested_celltype: str | None,
    context_celltype: str | None,
) -> str | None:
    explicit = _extract_celltype(raw_text)
    if explicit:
        return explicit
    requested = (requested_celltype or "").strip()
    if requested:
        return requested
    if context_celltype and _has_anaphora_reference(raw_text):
        return context_celltype.strip() or None
    return None


def parse_agent_request(text: str) -> dict[str, Any]:
    raw = text.strip()
    lower = raw.lower()

    if any(term in lower for term in ["review", "rag check", "check again", "recheck", "重新检查", "重新review", "再看看"]):
        return {
            "intent_type": "run_RAG_check",
            "raw_text": raw,
        }

    if any(term in lower for term in ["subcluster", "sub cluster", "仔细看看", "深入", "感兴趣", "内部分类", "内部有什么"]):
        return {
            "intent_type": "run_subcluster_pipeline",
            "celltype": _extract_celltype(raw),
            "raw_text": raw,
        }

    if any(term in lower for term in ["marker", "marker gene", "marker genes", "celltype", "cell type", "细胞类型"]):
        markers = _extract_gene_tokens(raw)
        celltype = _extract_celltype(raw)
        if any(term in lower for term in ["new", "update", "marker", "marker gene", "marker genes", "新增", "更新"]):
            return {
                "intent_type": "add_external_evidence",
                "celltype": celltype,
                "markers": markers,
                "knowledge_mode": "define_new" if any(term in lower for term in ["define", "new celltype", "new cell type", "custom celltype", "custom cell type", "自定义", "定义"]) else "auto",
                "raw_text": raw,
            }

    granularity = _parse_granularity(raw)
    if granularity:
        return {
            "intent_type": "change_annotation_preference",
            "preference_type": "granularity",
            "granularity": granularity,
            "raw_text": raw,
        }

    if "resolution" in lower or "分辨率" in lower:
        return {
            "intent_type": "change_annotation_preference",
            "preference_type": "resolution",
            "desired_resolution": _extract_resolution_value(raw),
            "raw_text": raw,
        }

    if any(term in lower for term in ["from scratch", "from the start", "重新跑parent", "run parent pipeline"]):
        return {
            "intent_type": "run_parent_pipeline",
            "raw_text": raw,
        }

    return {
        "intent_type": "unknown",
        "raw_text": raw,
    }


def _resolution_summary(config: dict[str, Any]) -> list[dict[str, str]]:
    work_dir = Path(str(config["project"]["work_dir"]))
    csv_path = work_dir / "annotate_parent" / "annotation_summary_scores.csv"
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _normalize_resolution_name(value: Any) -> str | None:
    if value in (None, "", "NA"):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("res_"):
        return text
    return f"res_{text}"

def apply_agent_request(config: dict[str, Any], intent: dict[str, Any], orchestrator: Any | None = None) -> dict[str, Any]:
    memory = load_agent_memory(config)
    intent_type = intent.get("intent_type")
    config_path = _config_path(config)
    raw_config = _load_raw_config(config_path)
    result: dict[str, Any] = {
        "intent": intent,
        "applied": False,
        "updated_config": None,
        "updated_memory": None,
        "message": "",
        "next_step": "",
    }

    if intent_type == "change_annotation_preference":
        preference_type = str(intent.get("preference_type") or "").strip().lower()
        if preference_type == "granularity":
            target = str(intent.get("granularity") or "").strip().lower()
            raw_config.setdefault("policy", {})
            previous = str(raw_config["policy"].get("granularity") or "balanced")
            raw_config["policy"]["granularity"] = target
            _save_raw_config(config_path, raw_config)
            result.update(
                {
                    "applied": True,
                    "updated_config": str(config_path),
                    "message": f"Updated annotation preference 'granularity' from '{previous}' to '{target}'.",
                    "next_step": "run_RAG_check",
                }
            )
            return result

        if preference_type == "resolution":
            if orchestrator is None:
                result["message"] = "change_annotation_preference(resolution) requires an active orchestrator."
                return result
            desired_resolution = intent.get("desired_resolution")
            desired_resolution_name = _normalize_resolution_name(desired_resolution)
            if desired_resolution_name is None:
                result["message"] = "Changing resolution requires a specific desired_resolution value."
                return result

            entry = {
                "note": str(intent.get("raw_text") or ""),
                "desired_resolution": desired_resolution,
                "added_at": utc_now(),
                "available_scores": _resolution_summary(config),
            }
            append_memory_entry(memory, "resolution_feedback", entry)
            memory_path = save_agent_memory(config, memory)
            raw_config.setdefault("annotation", {})
            available_resolutions = {
                _normalize_resolution_name(row.get("resolution"))
                for row in entry["available_scores"]
                if _normalize_resolution_name(row.get("resolution"))
            }

            if desired_resolution_name in available_resolutions:
                raw_config["annotation"]["forced_parent_resolution"] = str(desired_resolution)
                _save_raw_config(config_path, raw_config)
                executed = run_gptanno_worker_chain(
                    orchestrator,
                    ["select_parent_resolution", "assign_parent_labels"],
                    force=True,
                )
                message = (
                    f"Forced existing parent resolution '{desired_resolution}' and reassigned parent labels."
                )
            else:
                current_parent_res = raw_config["annotation"].get("parent_res")
                if not isinstance(current_parent_res, list):
                    current_parent_res = [current_parent_res] if current_parent_res not in (None, "") else []
                normalized_existing = {_normalize_resolution_name(item) for item in current_parent_res}
                if desired_resolution_name not in normalized_existing:
                    current_parent_res.append(desired_resolution)
                raw_config["annotation"]["parent_res"] = current_parent_res
                raw_config["annotation"]["forced_parent_resolution"] = str(desired_resolution)
                _save_raw_config(config_path, raw_config)
                executed = run_gptanno_worker_chain(
                    orchestrator,
                    PARENT_BACKBONE_WORKERS,
                    force=True,
                )
                message = (
                    f"Added new parent resolution '{desired_resolution}', reran the parent backbone, and forced assignment to that resolution."
                )
            result.update(
                {
                    "applied": True,
                    "updated_config": str(config_path),
                    "updated_memory": str(memory_path),
                    "message": message,
                    "next_step": "run_RAG_check",
                    "executed_workers": executed,
                }
            )
            return result

        result["message"] = "Unsupported annotation preference type."
        return result

    if intent_type == "run_parent_pipeline":
        if orchestrator is None:
            result["message"] = "run_parent_pipeline requires an active orchestrator."
            return result
        executed = run_gptanno_worker_chain(
            orchestrator,
            PARENT_BACKBONE_WORKERS,
            force=True,
        )
        result.update(
            {
                "applied": True,
                "message": "Completed parent backbone pipeline through assigned parent labels.",
                "next_step": "run_RAG_check",
                "executed_workers": executed,
            }
        )
        return result

    if intent_type == "run_RAG_check":
        if orchestrator is None:
            result["message"] = "run_RAG_check requires an active orchestrator."
            return result
        if not has_parent_annotation_outputs(orchestrator):
            result.update(
                {
                    "applied": False,
                    "message": (
                        "RAG check is not available yet because parent annotation outputs do not exist. "
                        "Run the parent pipeline first."
                    ),
                    "next_step": "run_parent_pipeline",
                }
            )
            return result
        executed, ask_user_count = run_rag_check_workers(orchestrator, force=True)
        result.update(
            {
                "applied": True,
                "message": (
                    "Completed automated RAG check through controller post-compare. "
                    f"{ask_user_count} cluster(s) now flow to the default next worker: human_review."
                ),
                "next_step": "human_review" if ask_user_count > 0 else "export_reviewed_parent_annotations",
                "executed_workers": executed,
            }
        )
        return result

    if intent_type == "run_report":
        if orchestrator is None:
            result["message"] = "run_report requires an active orchestrator."
            return result
        outputs, worker_result = run_generate_report_worker(orchestrator, force=True)
        result.update(
            {
                "applied": True,
                "message": "Generated the final report from current project artifacts.",
                "next_step": "",
                "executed_workers": [worker_result],
                "report_outputs": outputs,
            }
        )
        return result

    if intent_type == "add_external_evidence":
        celltype = _resolve_external_evidence_celltype(
            raw_text=str(intent.get("raw_text") or ""),
            requested_celltype=str(intent.get("resolved_celltype") or intent.get("celltype") or "").strip(),
            context_celltype=str(intent.get("context_celltype") or "").strip(),
        )
        markers = [str(item).strip() for item in intent.get("markers", []) if str(item).strip()]
        if not celltype:
            result["message"] = (
                "Could not safely determine which cell type these markers belong to. "
                "Please specify the target cell type explicitly, or refer to a clearly established focus cell type."
            )
            return result
        mode = _infer_custom_knowledge_mode(
            celltype=celltype,
            raw_text=str(intent.get("raw_text") or ""),
            memory=memory,
            requested_mode=intent.get("knowledge_mode"),
        )

        if mode == "update_existing" and not markers:
            result["message"] = "Updating an existing cell type requires at least one marker gene."
            return result

        if mode == "define_new":
            entry = {
                "celltype": celltype,
                "markers": markers,
                "note": str(intent.get("raw_text") or ""),
                "source": "user_custom_celltype",
                "added_at": utc_now(),
            }
            append_memory_entry(memory, "custom_celltypes", entry)
            message = f"Stored custom cell type definition for '{celltype}'."
        else:
            entry = {
                "celltype": celltype,
                "markers": markers,
                "note": str(intent.get("raw_text") or ""),
                "source": "user_marker_update",
                "added_at": utc_now(),
            }
            append_memory_entry(memory, "custom_markers", entry)
            message = f"Stored {len(markers)} custom marker(s) for '{celltype}'."

        memory_path = save_agent_memory(config, memory)
        result.update(
            {
                "applied": True,
                "updated_memory": str(memory_path),
                "message": message,
                "next_step": "run_RAG_check",
                "knowledge_mode": mode,
                "resolved_celltype": celltype,
            }
        )
        return result

    if intent_type == "add_custom_markers":
        intent = dict(intent)
        intent["intent_type"] = "add_external_evidence"
        intent.setdefault("knowledge_mode", "update_existing")
        return apply_agent_request(config, intent, orchestrator)

    if intent_type == "add_custom_celltype":
        intent = dict(intent)
        intent["intent_type"] = "add_external_evidence"
        intent.setdefault("knowledge_mode", "define_new")
        return apply_agent_request(config, intent, orchestrator)

    if intent_type == "run_subcluster_pipeline":
        celltype = str(intent.get("celltype") or "").strip()
        if not celltype:
            result["message"] = "Could not safely extract which cell type should be subclustered."
            return result
        if orchestrator is None:
            result["message"] = "run_subcluster_pipeline requires an active orchestrator."
            return result
        raw_config.setdefault("alignment", {})
        values = raw_config["alignment"].get("celltypes_to_subcluster")
        if not isinstance(values, list):
            values = []
        normalized_existing = {str(item).strip().casefold() for item in values if str(item).strip()}
        if celltype.casefold() not in normalized_existing:
            values.append(celltype)
        raw_config["alignment"]["celltypes_to_subcluster"] = values
        _save_raw_config(config_path, raw_config)
        append_memory_entry(
            memory,
            "subcluster_requests",
            {"celltype": celltype, "note": str(intent.get("raw_text") or ""), "added_at": utc_now()},
        )
        memory_path = save_agent_memory(config, memory)
        executed = run_gptanno_worker_chain(
            orchestrator,
            SUBCLUSTER_WORKERS,
            force=True,
        )
        result.update(
            {
                "applied": True,
                "updated_config": str(config_path),
                "updated_memory": str(memory_path),
                "message": f"Ran targeted subcluster pipeline for '{celltype}'.",
                "next_step": "generate_report",
                "executed_workers": executed,
            }
        )
        return result

    if intent_type == "extract_external_evidence":
        result["message"] = (
            "extract_external_evidence is registered as a top-level intent, but its worker chain is still a placeholder. "
            "Online search / PDF extraction will be connected later."
        )
        return result

    result["message"] = "Could not map the request to a supported agent intent yet."
    return result
