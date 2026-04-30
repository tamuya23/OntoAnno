from __future__ import annotations

import copy
import csv
import os
import re
from pathlib import Path
from typing import Any

import yaml

from .agent_memory import append_memory_entry, load_agent_memory, save_agent_memory
from .config import load_config
from .external_evidence import ExternalEvidenceError, extract_literature_evidence_from_pdf
from .review_packets import resolve_imported_parent_annotations
from .utils import dump_json, ensure_dir, load_json, utc_now
from .worker_runtime import (
    PARENT_BACKBONE_WORKERS,
    SUBCLUSTER_WORKERS,
    clear_parent_dependent_outputs,
    clear_rag_dependent_outputs,
    ensure_parent_annotation_outputs,
    has_parent_annotation_outputs,
    run_export_reviewed_parent_worker,
    run_generate_report_worker,
    run_gptanno_worker_chain,
    run_decide_rag_check_worker,
    run_human_review_worker,
    run_rag_check_workers,
    worker_prerequisite_status,
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


def _refresh_orchestrator_config(orchestrator: Any | None, config_path: Path) -> None:
    if orchestrator is None:
        return
    repo_root = getattr(orchestrator, "repo_root", None)
    if repo_root is None:
        return
    orchestrator.config = load_config(config_path, Path(repo_root))


def _parent_pipeline_mode(intent: dict[str, Any]) -> str:
    mode = str(intent.get("mode") or "").strip().lower()
    if mode in {"resume", "rerun"}:
        return mode
    raw_text = str(intent.get("raw_text") or "").lower()
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
    return "rerun" if any(term in raw_text for term in rerun_terms) else "resume"


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
            if value and value.lower() not in {"it", "this", "that", "them", "this cell type", "that cell type"}:
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


def _mentions_celltype(text: str, celltype: str) -> bool:
    normalized_text = re.sub(r"\s+", " ", text).casefold()
    normalized_celltype = re.sub(r"\s+", " ", celltype).strip().casefold()
    if not normalized_celltype:
        return False
    return normalized_celltype in normalized_text


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
    if requested and _mentions_celltype(raw_text, requested):
        return requested
    if context_celltype and _has_anaphora_reference(raw_text):
        return context_celltype.strip() or None
    return None


def parse_agent_request(text: str) -> dict[str, Any]:
    raw = text.strip()
    lower = raw.lower()

    decision_match = re.search(
        r"\bcluster\s+([A-Za-z0-9_.-]+)\b.*?(?:->|=>|=| as | to |label(?:\s+as)?|标为|标成)\s+(.+)$",
        raw,
        flags=re.IGNORECASE,
    )
    if decision_match:
        final_label = decision_match.group(2).strip().strip(" .。")
        final_label = re.sub(r"^(?:should\s+be|is|:)\s+", "", final_label, flags=re.IGNORECASE).strip()
        return {
            "intent_type": "save_human_review_decision",
            "cluster_id": decision_match.group(1).strip(),
            "final_label": final_label,
            "raw_text": raw,
        }

    if any(term in lower for term in ["finish review", "finalize review", "export reviewed", "导出review", "完成review"]):
        return {
            "intent_type": "finish_review",
            "raw_text": raw,
        }

    if "report" in lower or "报告" in lower:
        return {
            "intent_type": "run_report",
            "format": _requested_report_format({"raw_text": raw}),
            "raw_text": raw,
        }

    if any(term in lower for term in ["reference label", "reference labels", "manual label", "manual labels", "known label", "known labels", "人工标签", "参考标签"]):
        path_tokens = [
            *_extract_quoted(raw),
            *re.findall(r"(?<!\S)(/[^ \t\r\n'\"<>]+\.csv)\b", raw, flags=re.IGNORECASE),
            *re.findall(r"(?<!\S)([^ \t\r\n'\"<>]+\.csv)\b", raw, flags=re.IGNORECASE),
        ]
        manual_col = None
        column_match = re.search(r"(?:column|col|列)\s*[:=]?\s*([A-Za-z_][A-Za-z0-9_.-]*)", raw, flags=re.IGNORECASE)
        if column_match:
            manual_col = column_match.group(1)
        return {
            "intent_type": "configure_reference_labels",
            "reference_labels_csv": path_tokens[0] if path_tokens else None,
            "manual_col": manual_col,
            "enable_evaluation": True,
            "raw_text": raw,
        }

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

    if any(
        term in lower
        for term in [
            "run parent annotation",
            "run parent pipeline",
            "parent annotation",
            "parent pipeline",
            "resume parent",
            "continue parent",
            "keep running",
            "from scratch",
            "from the start",
            "重新跑parent",
        ]
    ):
        return {
            "intent_type": "run_parent_pipeline",
            "mode": _parent_pipeline_mode({"raw_text": raw}),
            "raw_text": raw,
        }

    return {
        "intent_type": "unknown",
        "raw_text": raw,
    }


def _resolution_summary(config: dict[str, Any]) -> list[dict[str, str]]:
    work_dir = Path(str(config["project"]["work_dir"]))
    csv_path = work_dir / "annotate_parent" / "annotation_summary_scores.csv"
    imported = resolve_imported_parent_annotations(config)
    if not csv_path.exists() and imported.get("annotation_scores_csv"):
        csv_path = Path(str(imported["annotation_scores_csv"]))
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


def _requested_report_format(intent: dict[str, Any]) -> str | None:
    requested = str(intent.get("format") or "").strip().lower()
    if requested:
        return requested
    raw_text = str(intent.get("raw_text") or "").lower()
    if re.search(r"\bpdf\b", raw_text):
        return "pdf"
    if re.search(r"\bhtml?\b", raw_text):
        return "html"
    return None


def _clear_report_outputs_if_possible(orchestrator: Any | None) -> None:
    if orchestrator is None:
        return
    clear = getattr(orchestrator, "clear_report_outputs", None)
    if callable(clear):
        clear()


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _cluster_sort_key(cluster_id: str) -> tuple[int, str]:
    try:
        return (0, f"{int(cluster_id):06d}")
    except (TypeError, ValueError):
        return (1, str(cluster_id))


def _review_draft_path(run_dir: Path) -> Path:
    return run_dir / "reviewed_parent" / "interactive_decisions_draft.json"


def _load_review_draft(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = _review_draft_path(run_dir)
    if not path.exists():
        return {}
    payload = load_json(path)
    if not isinstance(payload, list):
        return {}
    return {
        str(item.get("cluster_id") or ""): item
        for item in payload
        if isinstance(item, dict) and str(item.get("cluster_id") or "").strip()
    }


def _save_review_draft(run_dir: Path, decisions: dict[str, dict[str, Any]]) -> Path:
    path = _review_draft_path(run_dir)
    rows = [decisions[key] for key in sorted(decisions, key=_cluster_sort_key)]
    dump_json(path, rows)
    return path


def _load_controller_states(run_dir: Path) -> list[dict[str, Any]]:
    index_path = run_dir / "controller" / "index.json"
    if not index_path.exists():
        return []
    index = load_json(index_path)
    states: list[dict[str, Any]] = []
    for item in index.get("states", []) if isinstance(index.get("states"), list) else []:
        if not isinstance(item, dict):
            continue
        state_path = Path(str(item.get("state_json") or ""))
        state = load_json(state_path) if state_path.exists() else dict(item)
        if isinstance(state, dict):
            states.append(state)
    states.sort(key=lambda state: _cluster_sort_key(str(state.get("cluster_id") or "")))
    return states


def _split_pipe_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value or "").split("|") if part.strip()]


def _decision_from_controller_state(
    state: dict[str, Any],
    *,
    final_label: str,
    selection_source: str,
    user_note: str = "",
) -> dict[str, Any]:
    return {
        "cluster_id": str(state.get("cluster_id") or ""),
        "current_label": str(state.get("current_label") or state.get("label") or ""),
        "final_label": final_label,
        "selection_source": selection_source,
        "status": str(state.get("llm_status") or ""),
        "user_note": user_note,
        "focus_candidates": _split_pipe_values(state.get("focus_candidates")),
        "result_json": str(state.get("result_json") or ""),
    }


def _assemble_review_decisions(
    states: list[dict[str, Any]],
    draft: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    decisions: list[dict[str, Any]] = []
    missing: list[str] = []
    for state in states:
        cluster_id = str(state.get("cluster_id") or "")
        current_label = str(state.get("current_label") or state.get("label") or "")
        next_action = str(state.get("next_action") or "")
        if next_action == "ask_user":
            draft_decision = draft.get(cluster_id)
            if not draft_decision:
                missing.append(cluster_id)
                continue
            decisions.append(draft_decision)
        elif next_action == "finalize_llm_choice":
            final_label = str(state.get("recommended_label") or state.get("llm_best_candidate") or current_label)
            decisions.append(
                _decision_from_controller_state(
                    state,
                    final_label=final_label,
                    selection_source="controller_llm_choose",
                )
            )
        elif next_action == "finalize_keep_current":
            decisions.append(
                _decision_from_controller_state(
                    state,
                    final_label=current_label,
                    selection_source="controller_keep_current",
                )
            )
        else:
            decisions.append(
                _decision_from_controller_state(
                    state,
                    final_label=current_label,
                    selection_source=f"controller_{next_action or 'unknown'}",
                )
            )
    decisions.sort(key=lambda item: _cluster_sort_key(str(item.get("cluster_id") or "")))
    return decisions, missing


def _write_interactive_decision_files(run_dir: Path, decisions: list[dict[str, Any]]) -> tuple[Path, Path]:
    output_dir = ensure_dir(run_dir / "reviewed_parent")
    decisions_json = output_dir / "interactive_decisions.json"
    decisions_csv = output_dir / "interactive_decisions.csv"
    export_decisions: list[dict[str, Any]] = []
    for row in decisions:
        export_decisions.append(
            {
                "cluster_id": row.get("cluster_id", ""),
                "current_label": row.get("current_label", ""),
                "final_label": row.get("final_label", ""),
                "focus_candidates": row.get("focus_candidates", []),
                "result_json": row.get("result_json", ""),
            }
        )
    dump_json(decisions_json, export_decisions)

    fieldnames = ["cluster_id", "current_label", "final_label", "focus_candidates", "result_json"]
    with decisions_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in export_decisions:
            item = dict(row)
            item["focus_candidates"] = " | ".join(item.get("focus_candidates", []))
            writer.writerow(item)
    return decisions_json, decisions_csv


def _extract_pdf_path_candidates(config: dict[str, Any], intent: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    search_text = " ".join(
        str(intent.get(key) or "")
        for key in ("source_hint", "topic", "raw_text", "reason")
    )
    tokens = [
        *_extract_quoted(search_text),
        *re.findall(r"(?<!\S)(/[^ \t\r\n'\"<>]+\.pdf)\b", search_text, flags=re.IGNORECASE),
        *re.findall(r"(?<!\S)([^ \t\r\n'\"<>]+\.pdf)\b", search_text, flags=re.IGNORECASE),
    ]
    config_dir = _config_path(config).parent
    for token in tokens:
        cleaned = token.strip().strip(".,;:()[]{}")
        if not cleaned.lower().endswith(".pdf"):
            continue
        path = Path(cleaned)
        if not path.is_absolute():
            path = config_dir / path
        if path.exists() and path.is_file() and path.suffix.lower() == ".pdf" and path not in candidates:
            candidates.append(path.resolve())

    pdf_dir = config.get("inputs", {}).get("pdf_dir")
    if pdf_dir:
        root = Path(str(pdf_dir))
        if root.exists() and root.is_dir():
            for path in sorted(root.iterdir()):
                if path.is_file() and path.suffix.lower() == ".pdf" and path.resolve() not in candidates:
                    candidates.append(path.resolve())
    return candidates


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

    if intent_type == "configure_reference_labels":
        reference_labels_csv = str(intent.get("reference_labels_csv") or "").strip()
        manual_col = str(intent.get("manual_col") or "").strip()
        if not reference_labels_csv or not manual_col:
            result["message"] = (
                "Configuring reference-label comparison requires both reference_labels_csv and manual_col."
            )
            return result
        enable_evaluation = _as_bool(intent.get("enable_evaluation"), default=True)
        raw_config.setdefault("inputs", {})
        raw_config["inputs"]["reference_labels_csv"] = reference_labels_csv
        raw_config["inputs"]["manual_labels_csv"] = reference_labels_csv
        raw_config.setdefault("evaluation", {})
        raw_config["evaluation"]["enabled"] = enable_evaluation
        raw_config["evaluation"]["manual_col"] = manual_col
        _save_raw_config(config_path, raw_config)
        _refresh_orchestrator_config(orchestrator, config_path)
        _clear_report_outputs_if_possible(orchestrator)

        reference_path = Path(reference_labels_csv).expanduser()
        if not reference_path.is_absolute():
            reference_path = config_path.parent / reference_path
        existence_note = ""
        if not reference_path.exists():
            existence_note = f" The configured CSV does not exist yet at {reference_path}."
        result.update(
            {
                "applied": True,
                "updated_config": str(config_path),
                "message": (
                    "Configured reference-label comparison using "
                    f"'{reference_labels_csv}' with label column '{manual_col}'."
                    f"{existence_note}"
                ),
                "next_step": "run_report",
            }
        )
        return result

    if intent_type == "change_annotation_preference":
        preference_type = str(intent.get("preference_type") or "").strip().lower()
        if preference_type == "granularity":
            target = str(intent.get("granularity") or "").strip().lower()
            if target not in {"coarse", "balanced", "fine"}:
                result["message"] = "Changing granularity requires one of: coarse, balanced, fine."
                return result
            raw_config.setdefault("policy", {})
            previous = str(raw_config["policy"].get("granularity") or "balanced")
            raw_config["policy"]["granularity"] = target
            _save_raw_config(config_path, raw_config)
            _refresh_orchestrator_config(orchestrator, config_path)
            if orchestrator is not None:
                clear_rag_dependent_outputs(orchestrator)
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
            parent_readiness = worker_prerequisite_status(orchestrator, "assign_parent_labels")
            can_run_parent_backbone = bool(parent_readiness.get("ok"))

            if desired_resolution_name in available_resolutions:
                raw_config["annotation"]["forced_parent_resolution"] = str(desired_resolution)
                _save_raw_config(config_path, raw_config)
                _refresh_orchestrator_config(orchestrator, config_path)
                if can_run_parent_backbone:
                    executed = run_gptanno_worker_chain(
                        orchestrator,
                        ["select_parent_resolution", "assign_parent_labels"],
                        force=True,
                    )
                    message = (
                        f"Forced existing parent resolution '{desired_resolution}' and reassigned parent labels."
                    )
                else:
                    clear_parent_dependent_outputs(orchestrator)
                    executed = [
                        {
                            "worker": "select_parent_resolution",
                            "status": "completed",
                            "notes": [
                                "Configured imported parent annotation results to use this existing resolution; parent backbone was not rerun."
                            ],
                        }
                    ]
                    message = f"Selected existing imported parent resolution '{desired_resolution}' for downstream RAG/review."
            else:
                if not can_run_parent_backbone:
                    result.update(
                        {
                            "applied": False,
                            "updated_memory": str(memory_path),
                            "message": (
                                f"Resolution '{desired_resolution}' is not available in current annotation outputs, "
                                "and the parent backbone cannot run without raw Seurat input or inputs.bootstrap_parent."
                            ),
                            "next_step": "run_RAG_check" if has_parent_annotation_outputs(orchestrator) else "",
                            "executed_workers": [
                                {
                                    "worker": "assign_parent_labels",
                                    "status": "blocked",
                                    "notes": parent_readiness.get("notes", []),
                                    "artifacts": {"missing_prerequisites": parent_readiness.get("missing", [])},
                                }
                            ],
                        }
                    )
                    return result
                current_parent_res = raw_config["annotation"].get("parent_res")
                if not isinstance(current_parent_res, list):
                    current_parent_res = [current_parent_res] if current_parent_res not in (None, "") else []
                normalized_existing = {_normalize_resolution_name(item) for item in current_parent_res}
                if desired_resolution_name not in normalized_existing:
                    current_parent_res.append(desired_resolution)
                raw_config["annotation"]["parent_res"] = current_parent_res
                raw_config["annotation"]["forced_parent_resolution"] = str(desired_resolution)
                _save_raw_config(config_path, raw_config)
                _refresh_orchestrator_config(orchestrator, config_path)
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
        mode = _parent_pipeline_mode(intent)
        force = mode == "rerun"
        readiness = worker_prerequisite_status(orchestrator, "assign_parent_labels")
        if not readiness.get("ok"):
            result.update(
                {
                    "applied": False,
                    "message": "Parent pipeline cannot run because required parent-backbone inputs are missing.",
                    "next_step": "run_RAG_check" if has_parent_annotation_outputs(orchestrator) else "",
                    "executed_workers": [
                        {
                            "worker": "assign_parent_labels",
                            "status": "blocked",
                            "notes": readiness.get("notes", []),
                            "artifacts": {"missing_prerequisites": readiness.get("missing", [])},
                        }
                    ],
                }
            )
            return result
        executed = run_gptanno_worker_chain(
            orchestrator,
            PARENT_BACKBONE_WORKERS,
            force=force,
        )
        result.update(
            {
                "applied": True,
                "message": (
                    "Reran parent backbone pipeline through assigned parent labels."
                    if force
                    else "Resumed parent backbone pipeline through assigned parent labels."
                ),
                "mode": mode,
                "next_step": "run_RAG_check",
                "executed_workers": executed,
            }
        )
        return result

    if intent_type == "run_RAG_check":
        if orchestrator is None:
            result["message"] = "run_RAG_check requires an active orchestrator."
            return result
        executed, ask_user_count = run_rag_check_workers(orchestrator, force=True)
        if executed and executed[-1].get("status") == "blocked":
            result.update(
                {
                    "applied": False,
                    "message": "RAG check cannot start because parent annotation outputs are not available.",
                    "next_step": "run_parent_pipeline",
                    "executed_workers": executed,
                }
            )
            return result
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

    if intent_type == "save_human_review_decision":
        if orchestrator is None:
            result["message"] = "save_human_review_decision requires an active orchestrator."
            return result
        cluster_id = str(intent.get("cluster_id") or "").strip()
        final_label = str(intent.get("final_label") or "").strip()
        user_note = str(intent.get("user_note") or intent.get("raw_text") or "").strip()
        if not cluster_id or not final_label:
            result["message"] = "Saving a human-review decision requires both cluster_id and final_label."
            return result
        states = _load_controller_states(orchestrator.run_dir)
        if not states:
            result.update(
                {
                    "applied": False,
                    "message": "No controller review states are available yet.",
                    "next_step": "run_RAG_check" if has_parent_annotation_outputs(orchestrator) else "run_parent_pipeline",
                    "executed_workers": [
                        {
                            "worker": "save_human_review_decision",
                            "status": "blocked",
                            "notes": ["Run RAG check before saving human-review decisions."],
                            "artifacts": {
                                "missing_prerequisites": [str(orchestrator.run_dir / "controller" / "index.json")]
                            },
                        }
                    ],
                }
            )
            return result
        target_state = next((state for state in states if str(state.get("cluster_id") or "") == cluster_id), None)
        if not target_state:
            available = [str(state.get("cluster_id") or "") for state in states if str(state.get("cluster_id") or "")]
            result.update(
                {
                    "applied": False,
                    "message": f"Cluster '{cluster_id}' is not present in the current review states.",
                    "next_step": "human_review",
                    "executed_workers": [
                        {
                            "worker": "save_human_review_decision",
                            "status": "blocked",
                            "artifacts": {"available_clusters": available},
                        }
                    ],
                }
            )
            return result
        if str(target_state.get("next_action") or "") != "ask_user":
            result.update(
                {
                    "applied": False,
                    "message": f"Cluster '{cluster_id}' is not currently waiting for human review.",
                    "next_step": "human_review",
                    "executed_workers": [
                        {
                            "worker": "save_human_review_decision",
                            "status": "blocked",
                            "notes": [f"Current next_action is {target_state.get('next_action') or '(empty)'}."],
                        }
                    ],
                }
            )
            return result

        draft = _load_review_draft(orchestrator.run_dir)
        draft[cluster_id] = _decision_from_controller_state(
            target_state,
            final_label=final_label,
            selection_source="agent_human_review",
            user_note=user_note,
        )
        draft_path = _save_review_draft(orchestrator.run_dir, draft)
        decisions, missing = _assemble_review_decisions(states, draft)
        artifacts: dict[str, Any] = {
            "draft_json": str(draft_path),
            "remaining_clusters": sorted(missing, key=_cluster_sort_key),
        }
        next_step = "human_review"
        message = f"Saved human-review decision for cluster {cluster_id}: {final_label}."
        if not missing:
            decisions_json, decisions_csv = _write_interactive_decision_files(orchestrator.run_dir, decisions)
            artifacts["decisions_json"] = str(decisions_json)
            artifacts["decisions_csv"] = str(decisions_csv)
            _clear_report_outputs_if_possible(orchestrator)
            next_step = "finish_review"
            message += " All unresolved clusters now have saved decisions."
        else:
            message += f" Remaining unresolved cluster(s): {', '.join(sorted(missing, key=_cluster_sort_key))}."
        result.update(
            {
                "applied": True,
                "message": message,
                "next_step": next_step,
                "executed_workers": [
                    {
                        "worker": "save_human_review_decision",
                        "status": "completed",
                        "artifacts": artifacts,
                    }
                ],
            }
        )
        return result

    if intent_type == "finish_review":
        if orchestrator is None:
            result["message"] = "finish_review requires an active orchestrator."
            return result
        readiness = worker_prerequisite_status(orchestrator, "export_reviewed_parent_annotations")
        if not readiness.get("ok"):
            missing_items = [str(item) for item in readiness.get("missing", [])]
            if any("review_packets" in item or "controller" in item for item in missing_items):
                next_step = "run_RAG_check" if has_parent_annotation_outputs(orchestrator) else "run_parent_pipeline"
            else:
                next_step = "human_review" if has_parent_annotation_outputs(orchestrator) else "run_parent_pipeline"
            result.update(
                {
                    "applied": False,
                    "message": "Reviewed parent annotations cannot be exported yet.",
                    "next_step": next_step,
                    "executed_workers": [
                        {
                            "worker": "export_reviewed_parent_annotations",
                            "status": "blocked",
                            "notes": readiness.get("notes", []),
                            "artifacts": {"missing_prerequisites": readiness.get("missing", [])},
                        }
                    ],
                }
            )
            return result
        outputs, worker_result = run_export_reviewed_parent_worker(orchestrator)
        if worker_result.get("status") == "blocked":
            result.update(
                {
                    "applied": False,
                    "message": "Reviewed parent annotations still need saved human-review decisions before export.",
                    "next_step": "human_review",
                    "executed_workers": [worker_result],
                    "reviewed_parent_outputs": outputs,
                }
            )
            return result
        result.update(
            {
                "applied": True,
                "message": "Exported reviewed parent annotations.",
                "next_step": "run_report",
                "executed_workers": [worker_result],
                "reviewed_parent_outputs": outputs,
            }
        )
        return result

    if intent_type == "human_review":
        if orchestrator is None:
            result["message"] = "human_review requires an active orchestrator."
            return result
        readiness = worker_prerequisite_status(orchestrator, "human_review")
        if not readiness.get("ok"):
            result.update(
                {
                    "applied": False,
                    "message": "Human review cannot start because review packets are not available yet.",
                    "next_step": "run_RAG_check" if has_parent_annotation_outputs(orchestrator) else "run_parent_pipeline",
                    "executed_workers": [
                        {
                            "worker": "human_review",
                            "status": "blocked",
                            "notes": readiness.get("notes", []),
                            "artifacts": {"missing_prerequisites": readiness.get("missing", [])},
                        }
                    ],
                }
            )
            return result
        controller_outputs, decide_worker = run_decide_rag_check_worker(
            orchestrator,
            phase="post_compare",
            force=False,
        )
        ask_user_count, human_review_worker = run_human_review_worker(
            controller_outputs,
            run_dir=orchestrator.run_dir,
        )
        if ask_user_count > 0:
            message = (
                f"Human review is ready for {ask_user_count} unresolved cluster(s). "
                "The automated RAG check will not be rerun; review decisions need to be collected explicitly."
            )
            next_step = "save_human_review_decisions"
        else:
            message = "No unresolved clusters currently require human review."
            next_step = "export_reviewed_parent_annotations"
        result.update(
            {
                "applied": True,
                "message": message,
                "next_step": next_step,
                "executed_workers": [decide_worker, human_review_worker],
            }
        )
        return result

    if intent_type == "run_report":
        if orchestrator is None:
            result["message"] = "run_report requires an active orchestrator."
            return result
        updated_report_format = False
        requested_format = _requested_report_format(intent)
        if requested_format:
            if requested_format not in {"html", "pdf"}:
                result["message"] = "Report format must be one of: html, pdf."
                return result
            raw_config.setdefault("report", {})
            previous_format = str(raw_config["report"].get("format") or "html").strip().lower()
            if previous_format != requested_format:
                raw_config["report"]["format"] = requested_format
                _save_raw_config(config_path, raw_config)
                _refresh_orchestrator_config(orchestrator, config_path)
                _clear_report_outputs_if_possible(orchestrator)
                updated_report_format = True
        readiness = worker_prerequisite_status(orchestrator, "generate_report")
        if not readiness.get("ok"):
            result.update(
                {
                    "applied": False,
                    "updated_config": str(config_path) if updated_report_format else None,
                    "message": "Report generation cannot start because parent annotation outputs are not available yet.",
                    "next_step": "run_parent_pipeline",
                    "executed_workers": [
                        {
                            "worker": "generate_report",
                            "status": "blocked",
                            "notes": readiness.get("notes", []),
                            "artifacts": {"missing_prerequisites": readiness.get("missing", [])},
                        }
                    ],
                }
            )
            return result
        outputs, worker_result = run_generate_report_worker(orchestrator, force=True)
        report_format = outputs.get("report_format") or requested_format or orchestrator.config.get("report", {}).get("format", "html")
        message = f"Generated the final {report_format} report from current project artifacts."
        if updated_report_format:
            message = f"Updated report.format to '{requested_format}' and generated the final {report_format} report."
        result.update(
            {
                "applied": True,
                "updated_config": str(config_path) if updated_report_format else None,
                "message": message,
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
        if orchestrator is not None:
            clear_rag_dependent_outputs(orchestrator)
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
        alignment_was_present = "alignment" in raw_config
        original_alignment = copy.deepcopy(raw_config.get("alignment"))
        if not isinstance(raw_config.get("alignment"), dict):
            raw_config["alignment"] = {}

        def restore_alignment_config() -> None:
            if alignment_was_present:
                raw_config["alignment"] = copy.deepcopy(original_alignment)
            else:
                raw_config.pop("alignment", None)

        values = raw_config["alignment"].get("celltypes_to_subcluster")
        if not isinstance(values, list):
            values = []
        original_values = list(values)
        normalized_existing = {str(item).strip().casefold() for item in values if str(item).strip()}
        if celltype.casefold() not in normalized_existing:
            values.append(celltype)
        raw_config["alignment"]["celltypes_to_subcluster"] = values
        _save_raw_config(config_path, raw_config)
        _refresh_orchestrator_config(orchestrator, config_path)
        readiness = worker_prerequisite_status(orchestrator, "subcluster_find_markers")
        if not readiness.get("ok"):
            restore_alignment_config()
            _save_raw_config(config_path, raw_config)
            _refresh_orchestrator_config(orchestrator, config_path)
            result.update(
                {
                    "applied": False,
                    "updated_config": str(config_path),
                    "message": (
                        f"Subcluster pipeline for '{celltype}' is configured but cannot run because required inputs are missing."
                    ),
                    "next_step": "run_RAG_check" if has_parent_annotation_outputs(orchestrator) else "",
                    "executed_workers": [
                        {
                            "worker": "subcluster_find_markers",
                            "status": "blocked",
                            "notes": readiness.get("notes", []),
                            "artifacts": {"missing_prerequisites": readiness.get("missing", [])},
                        }
                    ],
                }
            )
            return result
        append_memory_entry(
            memory,
            "subcluster_requests",
            {"celltype": celltype, "note": str(intent.get("raw_text") or ""), "added_at": utc_now()},
        )
        memory_path = save_agent_memory(config, memory)
        parent_ready, parent_workers, parent_readiness = ensure_parent_annotation_outputs(orchestrator, force=True)
        if not parent_ready:
            restore_alignment_config()
            _save_raw_config(config_path, raw_config)
            _refresh_orchestrator_config(orchestrator, config_path)
            result.update(
                {
                    "applied": False,
                    "updated_config": str(config_path),
                    "updated_memory": str(memory_path),
                    "message": (
                        f"Subcluster pipeline for '{celltype}' cannot start because parent annotation outputs are not available."
                    ),
                    "next_step": "run_parent_pipeline",
                    "executed_workers": parent_workers
                    or [
                        {
                            "worker": "assign_parent_labels",
                            "status": "blocked",
                            "notes": parent_readiness.get("notes", []),
                            "artifacts": {"missing_prerequisites": parent_readiness.get("missing", [])},
                        }
                    ],
                }
            )
            return result
        executed = [
            *parent_workers,
            *run_gptanno_worker_chain(
                orchestrator,
                SUBCLUSTER_WORKERS,
                force=True,
            ),
        ]
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
        pdf_paths = _extract_pdf_path_candidates(config, intent)
        if not pdf_paths:
            result.update(
                {
                    "applied": False,
                    "message": (
                        "No PDF input was found for external-evidence extraction. "
                        "Upload a PDF in the External Evidence panel, provide a PDF path in the request, "
                        "or configure inputs.pdf_dir. Web search is intentionally not implemented."
                    ),
                    "next_step": "upload_pdf_external_evidence",
                    "executed_workers": [
                        {
                            "worker": "extract_external_evidence",
                            "status": "blocked",
                            "notes": [
                                "This intent only extracts evidence from provided PDFs.",
                                "Text evidence uses GPTAnno/PDF2markers; selected pages use the vision LLM.",
                                "Web search/database search is intentionally disabled.",
                            ],
                            "artifacts": {"missing_prerequisites": ["PDF path or inputs.pdf_dir"]},
                        }
                    ],
                }
            )
            return result

        output_summaries = []
        total_text = 0
        total_image = 0
        total_entries = 0
        memory_path = None
        executed_workers = []
        for pdf_path in pdf_paths:
            try:
                extraction = extract_literature_evidence_from_pdf(
                    config=config,
                    pdf_path=pdf_path,
                    pages=[1, 2, 3, 4, 5, 6],
                    dpi=150,
                )
            except ExternalEvidenceError as exc:
                result.update(
                    {
                        "applied": False,
                        "message": f"PDF evidence extraction failed for {pdf_path.name}: {exc}",
                        "next_step": "fix_pdf_evidence_inputs",
                        "executed_workers": [
                            {
                                "worker": "extract_external_evidence",
                                "status": "failed",
                                "notes": [str(exc)],
                                "artifacts": {"pdf": str(pdf_path)},
                            }
                        ],
                    }
                )
                return result
            total_text += int(extraction.get("text_evidence_count") or 0)
            total_image += int(extraction.get("image_evidence_count") or 0)
            total_entries += int(extraction.get("evidence_count") or 0)
            memory_path = extraction.get("memory_path") or memory_path
            output_summaries.append(
                {
                    "pdf": str(pdf_path),
                    "evidence_count": extraction.get("evidence_count"),
                    "text_evidence_count": extraction.get("text_evidence_count"),
                    "image_evidence_count": extraction.get("image_evidence_count"),
                    "memory_path": extraction.get("memory_path"),
                    "pdf2markers_final_csv": extraction.get("pdf2markers_final_csv"),
                    "pdf2markers_filtered_csv": extraction.get("pdf2markers_filtered_csv"),
                    "pdf2markers_log": extraction.get("pdf2markers_log"),
                }
            )
            executed_workers.append(
                {
                    "worker": "extract_external_evidence",
                    "status": "completed",
                    "notes": [
                        "Extracted text evidence with GPTAnno/PDF2markers.",
                        "Extracted figure/page evidence with the vision LLM.",
                    ],
                    "artifacts": {
                        "pdf": str(pdf_path),
                        "pdf2markers_final_csv": extraction.get("pdf2markers_final_csv"),
                        "pdf2markers_filtered_csv": extraction.get("pdf2markers_filtered_csv"),
                        "pdf2markers_log": extraction.get("pdf2markers_log"),
                    },
                }
            )
        if orchestrator is not None:
            clear_rag_dependent_outputs(orchestrator)
        result.update(
            {
                "applied": True,
                "updated_memory": str(memory_path) if memory_path else None,
                "message": (
                    f"Extracted literature evidence from {len(pdf_paths)} PDF(s): "
                    f"{total_entries} total entry/entries "
                    f"({total_text} from PDF2markers text, {total_image} from vision page extraction)."
                ),
                "next_step": "run_RAG_check",
                "executed_workers": executed_workers,
                "external_evidence_outputs": output_summaries,
            }
        )
        return result

    result["message"] = "Could not map the request to a supported agent intent yet."
    return result
