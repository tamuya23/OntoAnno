from __future__ import annotations

import csv
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .agent_memory import load_agent_memory, marker_memory_matches
from .utils import dump_json, ensure_dir, load_json, utc_now


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


class LLMCompareError(RuntimeError):
    pass


def _append_log(log_path: Path, message: str) -> None:
    ensure_dir(log_path.parent)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


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


def _default_system_prompt() -> str:
    return (
        "You are a careful scRNA-seq annotation judge. "
        "Only compare the explicitly listed candidate labels. "
        "Do not invent new labels. "
        "Reference databases are supportive evidence, not gold standards. "
        "Candidate labels came from dataset-specific annotation and must be considered seriously. "
        "If the evidence is insufficient or internally conflicted, choose review."
    )


def _structured_user_prompt(
    prompt: str,
    focus_candidates: list[str],
    *,
    allowed_candidates: list[str],
    ontology_restricted: bool,
) -> str:
    candidate_text = ", ".join(focus_candidates)
    schema = {
        "decision": "choose or review",
        "best_candidate": f"one of [{candidate_text}] or null if review",
        "reason": "short explanation",
        "supporting_markers": ["marker1", "marker2"],
        "weakening_markers": ["marker1", "marker2"],
        "reference_limitations": "short note about why the reference evidence may be incomplete or imperfect",
    }
    policy_lines: list[str] = []
    evidence_lines = [
        "Treat reference marker databases as supportive evidence only, not as a golden rule.",
        "If external marker memory is present, weigh user-provided evidence strongly and literature/PDF-derived evidence as supportive only.",
        "The candidate labels were generated from this dataset's own marker context and should be weighed seriously.",
        "Lack of overlap with reference markers does not automatically reject a candidate.",
        "Balance three things: dataset-specific cluster markers, ontology-constrained candidate labels, and reference markers.",
        "If the evidence remains mixed, ambiguous, or weak, return decision='review'.",
    ]
    if ontology_restricted:
        allowed_text = ", ".join(allowed_candidates) if allowed_candidates else "none"
        disallowed = [candidate for candidate in focus_candidates if candidate not in allowed_candidates]
        policy_lines.append(
            "Ontology restriction is active: the final best_candidate must be an ontology-mapped candidate."
        )
        policy_lines.append(f"Allowed final candidates: {allowed_text}.")
        if disallowed:
            policy_lines.append(
                "The following candidates are not ontology-mapped and may only be discussed as alternatives; "
                "if they seem better, return decision='review' instead of choosing them: "
                + ", ".join(disallowed)
                + "."
            )
    return (
        f"{prompt}\n\n"
        + "\n".join(evidence_lines)
        + "\n\n"
        + ("\n".join(policy_lines) + "\n\n" if policy_lines else "")
        + "Return strict JSON only. Do not add markdown fences.\n"
        f"Use this schema: {json.dumps(schema, ensure_ascii=False)}"
    )


def _memory_evidence_block(
    *,
    config: dict[str, Any],
    focus_candidates: list[str],
    current_label: str,
) -> tuple[str, list[dict[str, Any]]]:
    memory = load_agent_memory(config)
    matches = marker_memory_matches(
        memory,
        focus_candidates=focus_candidates,
        current_label=current_label,
    )
    if not matches:
        return "", []

    lines = [
        "External marker memory matching current/candidate labels:",
        "User-provided evidence is researcher-curated and should be weighed strongly; literature/PDF-derived evidence is supportive only.",
    ]
    for item in matches:
        markers = ", ".join(item.get("markers", [])[:15]) or "none provided"
        note = str(item.get("note") or "").strip()
        source_type = str(item.get("source_type") or item.get("source") or "").strip()
        evidence_count = item.get("evidence_count")
        source_detail = f"source={source_type}" if source_type else "source=unknown"
        if evidence_count:
            source_detail += f", merged_entries={evidence_count}"
        detail = f"- {item.get('celltype')} ({source_detail}): markers={markers}"
        if note:
            detail += f" | note={note}"
        lines.append(detail)
    return "\n".join(lines), matches


def _extract_message_content(response_payload: dict[str, Any]) -> str:
    choices = response_payload.get("choices", [])
    if not choices:
        raise LLMCompareError("No choices returned from LLM API")
    message = choices[0].get("message", {})
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
        if parts:
            return "\n".join(parts).strip()
    raise LLMCompareError("LLM response did not contain text content")


def _extract_json_block(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[start:])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            continue
    raise LLMCompareError("Could not parse JSON object from LLM response")


def _normalize_result(
    *,
    parsed: dict[str, Any],
    focus_candidates: list[str],
    allowed_candidates: list[str],
    ontology_restricted: bool,
) -> dict[str, Any]:
    decision = str(parsed.get("decision") or "").strip().lower()
    best_candidate = parsed.get("best_candidate")
    if isinstance(best_candidate, str):
        best_candidate = best_candidate.strip()
    if decision not in {"choose", "review"}:
        decision = "review"
    if decision == "choose" and best_candidate not in focus_candidates:
        decision = "review"
        best_candidate = None
    if decision == "choose" and ontology_restricted and best_candidate not in allowed_candidates:
        decision = "review"
        best_candidate = None
    if decision == "review":
        best_candidate = None
    return {
        "decision": decision,
        "best_candidate": best_candidate,
        "reason": str(parsed.get("reason") or "").strip(),
        "supporting_markers": [str(item).strip() for item in parsed.get("supporting_markers", []) if str(item).strip()],
        "weakening_markers": [str(item).strip() for item in parsed.get("weakening_markers", []) if str(item).strip()],
        "reference_limitations": str(parsed.get("reference_limitations") or "").strip(),
    }


def _call_openai_chat(
    *,
    config: dict[str, Any],
    prompt: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    llm_config = config["llm"]["annotation"]
    model = llm_config["model"]
    api_key = llm_config.get("api_key")
    if not api_key:
        raise LLMCompareError("Missing API key for llm.annotation")
    system_prompt = llm_config.get("system_prompt") or _default_system_prompt()
    url = _chat_completions_url(config)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise LLMCompareError(f"LLM API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise LLMCompareError(f"LLM API request failed: {exc.reason}") from exc
    content = _extract_message_content(response_payload)
    parsed = _extract_json_block(content)
    return content, parsed, response_payload


def build_llm_compare(
    *,
    config: dict[str, Any],
    run_dir: Path,
    force: bool = False,
    cluster_ids: list[str] | None = None,
) -> dict[str, Any]:
    provider = str(config["llm"]["annotation"]["provider"]).lower()
    if provider != "openai":
        raise LLMCompareError(
            f"llm-compare currently supports only provider 'openai'; got '{provider}'"
        )

    ontology_outputs = (
        load_json(run_dir / "ontology_relations" / "ontology_relations.outputs.json")
        if (run_dir / "ontology_relations" / "ontology_relations.outputs.json").exists()
        else None
    )
    if not ontology_outputs:
        raise LLMCompareError("ontology_relations outputs not found; run ontology-relations first")

    output_dir = ensure_dir(run_dir / "llm_compare")
    results_dir = ensure_dir(output_dir / "results")
    summary_path = output_dir / "summary.csv"
    index_path = output_dir / "index.json"
    outputs_path = output_dir / "llm_compare.outputs.json"
    log_path = output_dir / "llm_compare.log"
    selected_ids = {str(item) for item in (cluster_ids or []) if str(item).strip()}

    if outputs_path.exists() and not force and not selected_ids:
        return load_json(outputs_path)

    _append_log(
        log_path,
        f"[{utc_now()}] Starting llm_compare | force={force} | selected_clusters={sorted(selected_ids) if selected_ids else 'all'}",
    )

    ontology_index_path = Path(ontology_outputs["index_json"])
    ontology_index = load_json(ontology_index_path)
    relation_items = ontology_index.get("relations", [])
    if selected_ids:
        relation_items = [
            item for item in relation_items
            if str(item.get("cluster_id") or "") in selected_ids
        ]

    if not relation_items and outputs_path.exists():
        return load_json(outputs_path)

    for item in relation_items:
        cluster_id = str(item.get("cluster_id") or "")
        relation_json = Path(str(item.get("relation_json") or ""))
        relation_payload = load_json(relation_json)
        comparison = relation_payload.get("comparison_brief", {})
        reference_compare = relation_payload.get("reference_compare", {})
        focus_candidates = comparison.get("focus_candidates", [])
        mapped_candidates = comparison.get("mapped_candidates", [])
        ontology_restricted = bool(config.get("policy", {}).get("ontology", False))
        allowed_candidates = (
            [candidate for candidate in focus_candidates if candidate in mapped_candidates]
            if ontology_restricted
            else list(focus_candidates)
        )
        prompt_ready = bool(reference_compare.get("prompt_ready"))
        current_label = str(relation_payload.get("current_label") or "")
        result_path = results_dir / f"cluster-{cluster_id}.json"

        if not prompt_ready:
            _append_log(
                log_path,
                f"[{utc_now()}] cluster={cluster_id} skipped | current_label={current_label} | reason=no ontology comparison needed",
            )
            payload = {
                "cluster_id": cluster_id,
                "current_label": current_label,
                "status": "skipped",
                "decision": None,
                "best_candidate": None,
                "reason": "No ontology comparison needed",
                "focus_candidates": focus_candidates,
                "allowed_candidates": allowed_candidates,
                "generated_at": utc_now(),
                "relation_json": str(relation_json),
            }
            dump_json(result_path, payload)
            continue

        prompt = str(reference_compare.get("prompt") or "")
        memory_block, memory_matches = _memory_evidence_block(
            config=config,
            focus_candidates=focus_candidates,
            current_label=current_label,
        )
        if memory_block:
            prompt = f"{prompt}\n\n{memory_block}"
        user_prompt = _structured_user_prompt(
            prompt,
            focus_candidates,
            allowed_candidates=allowed_candidates,
            ontology_restricted=ontology_restricted,
        )
        try:
            _append_log(
                log_path,
                "\n".join(
                    [
                        f"[{utc_now()}] cluster={cluster_id} querying model",
                        f"current_label: {current_label}",
                        f"focus_candidates: {', '.join(focus_candidates)}",
                        "prompt_with_schema:",
                        user_prompt,
                        "",
                    ]
                ),
            )
            raw_text, parsed_json, raw_response = _call_openai_chat(
                config=config,
                prompt=user_prompt,
            )
            _append_log(
                log_path,
                "\n".join(
                    [
                        f"[{utc_now()}] cluster={cluster_id} response",
                        raw_text,
                        "",
                    ]
                ),
            )
            normalized = _normalize_result(
                parsed=parsed_json,
                focus_candidates=focus_candidates,
                allowed_candidates=allowed_candidates,
                ontology_restricted=ontology_restricted,
            )
            payload = {
                "cluster_id": cluster_id,
                "current_label": current_label,
                "status": "completed",
                "focus_candidates": focus_candidates,
                "allowed_candidates": allowed_candidates,
                "prompt": prompt,
                "prompt_with_schema": user_prompt,
                "memory_matches": memory_matches,
                "raw_response_text": raw_text,
                "parsed_response": parsed_json,
                "result": normalized,
                "model": config["llm"]["annotation"]["model"],
                "provider": provider,
                "generated_at": utc_now(),
                "relation_json": str(relation_json),
                "api_response": raw_response,
            }
            dump_json(result_path, payload)
        except Exception as exc:  # noqa: BLE001
            _append_log(
                log_path,
                f"[{utc_now()}] cluster={cluster_id} failed | error={exc}",
            )
            payload = {
                "cluster_id": cluster_id,
                "current_label": current_label,
                "status": "failed",
                "focus_candidates": focus_candidates,
                "allowed_candidates": allowed_candidates,
                "prompt": prompt,
                "memory_matches": memory_matches,
                "error": str(exc),
                "model": config["llm"]["annotation"]["model"],
                "provider": provider,
                "generated_at": utc_now(),
                "relation_json": str(relation_json),
            }
            dump_json(result_path, payload)
    result_paths = sorted(results_dir.glob("cluster-*.json"))
    summary_rows: list[dict[str, str]] = []
    index_relations: list[dict[str, Any]] = []
    completed = 0
    skipped = 0
    failed = 0
    for result_path in result_paths:
        payload = load_json(result_path)
        cluster_id = str(payload.get("cluster_id") or "")
        current_label = str(payload.get("current_label") or "")
        status = str(payload.get("status") or "")
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        decision = str(result.get("decision") or "")
        best_candidate = str(result.get("best_candidate") or "")
        reason = str(result.get("reason") or payload.get("reason") or payload.get("error") or "")
        focus_candidates = [str(item) for item in payload.get("focus_candidates", []) if str(item).strip()]
        summary_rows.append(
            {
                "cluster_id": cluster_id,
                "current_label": current_label,
                "focus_candidates": " | ".join(focus_candidates),
                "status": status,
                "decision": decision,
                "best_candidate": best_candidate,
                "reason": reason,
                "result_json": str(result_path),
            }
        )
        index_relations.append(
            {
                "cluster_id": cluster_id,
                "label": current_label,
                "status": status,
                "decision": decision or None,
                "best_candidate": best_candidate or None,
                "result_json": str(result_path),
                "result_uri": str(result_path),
            }
        )
        if status == "completed":
            completed += 1
        elif status == "skipped":
            skipped += 1
        elif status == "failed":
            failed += 1

    fieldnames = ["cluster_id", "current_label", "focus_candidates", "status", "decision", "best_candidate", "reason", "result_json"]
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)

    index_payload = {
        "project_name": config["project"]["name"],
        "run_id": run_dir.name,
        "generated_at": utc_now(),
        "model": config["llm"]["annotation"]["model"],
        "provider": provider,
        "results": index_relations,
    }
    dump_json(index_path, index_payload)

    outputs = {
        "output_dir": str(output_dir),
        "summary_csv": str(summary_path),
        "index_json": str(index_path),
        "log": str(log_path),
        "result_count": len(index_relations),
        "completed_count": completed,
        "skipped_count": skipped,
        "failed_count": failed,
    }
    dump_json(outputs_path, outputs)
    _append_log(
        log_path,
        f"[{utc_now()}] Completed llm_compare | completed={completed} skipped={skipped} failed={failed}",
    )
    return outputs
