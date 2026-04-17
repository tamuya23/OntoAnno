from __future__ import annotations

import base64
import csv
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .agent_memory import append_memory_entry, compact_custom_marker_memory, load_agent_memory, save_agent_memory
from .utils import dump_json, ensure_dir, utc_now


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


class ExternalEvidenceError(RuntimeError):
    pass


def _chat_completions_url(config: dict[str, Any]) -> str:
    annotation_config = config["llm"]["annotation"]
    base = (
        annotation_config.get("api_url")
        or os.getenv("OPENAI_BASE_URL")
        or DEFAULT_OPENAI_BASE_URL
    )
    base = str(base).rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _pdf_llm_config(config: dict[str, Any]) -> tuple[str, str]:
    external_config = config.get("llm", {}).get("external_evidence", {})
    pdf_config = config.get("llm", {}).get("pdfmarkers", {})
    annotation_config = config["llm"]["annotation"]
    model = str(external_config.get("model") or annotation_config.get("model") or pdf_config.get("model") or "gpt-5")
    pdf_env = pdf_config.get("env", {}) if isinstance(pdf_config.get("env"), dict) else {}
    external_env = external_config.get("env", {}) if isinstance(external_config.get("env"), dict) else {}
    api_key = (
        str(external_env.get("OPENAI_API_KEY") or "").strip()
        or str(pdf_env.get("OPENAI_API_KEY") or "").strip()
        or str(annotation_config.get("api_key") or "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    if not api_key:
        raise ExternalEvidenceError("Missing OPENAI_API_KEY for PDF literature evidence extraction.")
    return model, api_key


def _pdfmarkers_llm_config(config: dict[str, Any]) -> tuple[str, dict[str, str]]:
    pdf_config = config.get("llm", {}).get("pdfmarkers", {})
    annotation_config = config["llm"]["annotation"]
    model = str(pdf_config.get("model") or annotation_config.get("model") or "gpt-5-nano")
    pdf_env = pdf_config.get("env", {}) if isinstance(pdf_config.get("env"), dict) else {}
    env = {
        key: str(value)
        for key, value in pdf_env.items()
        if value not in (None, "")
    }
    if not env.get("OPENAI_API_KEY") and annotation_config.get("api_key"):
        env["OPENAI_API_KEY"] = str(annotation_config["api_key"])
    if not env.get("OPENAI_BASE_URL") and os.getenv("OPENAI_BASE_URL"):
        env["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
    if not env.get("OPENAI_API_KEY"):
        raise ExternalEvidenceError("Missing OPENAI_API_KEY for PDF2markers text extraction.")
    return model, env


def _extract_message_content(response_payload: dict[str, Any]) -> str:
    choices = response_payload.get("choices", [])
    if not choices:
        raise ExternalEvidenceError("No choices returned from LLM API.")
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
    raise ExternalEvidenceError("LLM response did not contain text content.")


def _extract_json_payload(text: str) -> Any:
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[start:])
            return payload
        except json.JSONDecodeError:
            continue
    raise ExternalEvidenceError("Could not parse JSON from LLM response.")


def _parse_page_ranges(raw: str, *, default_max_pages: int = 6) -> list[int]:
    text = str(raw or "").strip()
    if not text:
        return list(range(1, default_max_pages + 1))
    pages: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            try:
                start = int(left.strip())
                end = int(right.strip())
            except ValueError:
                continue
            if start > end:
                start, end = end, start
            pages.update(range(max(1, start), max(1, end) + 1))
        else:
            try:
                pages.add(max(1, int(part)))
            except ValueError:
                continue
    return sorted(pages)


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return token or "uploaded_pdf"


def save_uploaded_literature_pdf(
    *,
    config: dict[str, Any],
    filename: str,
    data: bytes,
) -> Path:
    upload_dir = ensure_dir(Path(str(config["project"]["work_dir"])) / "external_evidence" / "uploads")
    stem = _safe_stem(filename)
    path = upload_dir / f"{stem}.pdf"
    if path.exists():
        path = upload_dir / f"{stem}_{utc_now().replace(':', '').replace('+', '_')}.pdf"
    path.write_bytes(data)
    return path


def render_pdf_pages(
    pdf_path: Path,
    *,
    output_dir: Path,
    pages: list[int],
    dpi: int = 150,
) -> list[Path]:
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise ExternalEvidenceError("`pdftoppm` is required to render PDF pages, but it was not found on PATH.")
    rendered: list[Path] = []
    ensure_dir(output_dir)
    for page in pages:
        prefix = output_dir / f"page_{page}"
        command = [
            pdftoppm,
            "-png",
            "-r",
            str(dpi),
            "-f",
            str(page),
            "-l",
            str(page),
            str(pdf_path),
            str(prefix),
        ]
        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if process.returncode != 0:
            continue
        page_outputs = sorted(output_dir.glob(f"page_{page}-*.png"))
        rendered.extend(page_outputs)
    if not rendered:
        raise ExternalEvidenceError("No PDF pages were rendered. Check the PDF file and selected page range.")
    return rendered


def extract_pdf_text(
    pdf_path: Path,
    *,
    output_path: Path,
    pages: list[int],
    max_chars: int = 18000,
) -> str:
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return ""
    ensure_dir(output_path.parent)
    page_chunks: list[str] = []
    for page in pages:
        page_text_path = output_path.with_name(f"{output_path.stem}_page_{page}.txt")
        command = [
            pdftotext,
            "-layout",
            "-enc",
            "UTF-8",
            "-f",
            str(page),
            "-l",
            str(page),
            str(pdf_path),
            str(page_text_path),
        ]
        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if process.returncode != 0 or not page_text_path.exists():
            continue
        text = page_text_path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            page_chunks.append(f"[PDF text page {page}]\n{text}")
    merged = "\n\n".join(page_chunks)
    if max_chars > 0 and len(merged) > max_chars:
        merged = merged[:max_chars] + "\n\n[truncated]"
    output_path.write_text(merged, encoding="utf-8")
    return merged


def _image_content_items(image_paths: list[Path]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in image_paths:
        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
        items.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{encoded}",
                    "detail": "high",
                },
            }
        )
    return items


def _extraction_prompt(config: dict[str, Any], pdf_path: Path, pages: list[int]) -> str:
    annotation = config.get("annotation", {}) if isinstance(config.get("annotation"), dict) else {}
    species = str(annotation.get("species") or "unknown")
    tissue = str(annotation.get("tissue_name") or "unknown")
    return (
        "Extract cell type to marker gene evidence from the attached PDF page images. "
        "A separate strict text-extraction pipeline handles plain PDF text; your job is to recover evidence that is visible in figures, dot plots, heatmaps, UMAP labels, legends, and small marker panels. "
        "Read visual labels carefully and extract marker genes only when the figure or caption supports a celltype-marker relationship. "
        "Do not infer unsupported markers from general biological knowledge.\n"
        "Celltype label rule: use the most literal cell type label supported by the local evidence sentence, caption, or figure label. "
        "Do not add disease, tumor, inflammatory, activation-state, tissue-context, or functional qualifiers unless those exact qualifiers are explicitly present in the local evidence. "
        "For example, if the local evidence says 'NLRP3+ macrophages surrounding invaded nerves', return celltype='macrophages' and marker='NLRP3'; do not rewrite it as 'tumor-associated macrophage' or 'TAM' unless the evidence explicitly says tumor-associated macrophage or TAM.\n\n"
        f"Project species context: {species}\n"
        f"Project tissue context: {tissue}\n"
        f"PDF file name: {pdf_path.name}\n"
        f"Rendered pages: {', '.join(str(page) for page in pages)}\n\n"
        "Return strict JSON only, with this schema:\n"
        "{\n"
        '  "evidence": [\n'
        "    {\n"
        '      "celltype": "cell type label",\n'
        '      "markers": ["GENE1", "GENE2"],\n'
        '      "species": "species if stated or inferred from paper context",\n'
        '      "tissue": "tissue if stated",\n'
        '      "figure_or_page": "Fig. 2C or page 4",\n'
        '      "evidence_summary": "short support summary",\n'
        '      "celltype_evidence_text": "short exact phrase supporting the celltype label",\n'
        '      "confidence": "high|medium|low"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "If no reliable marker evidence is visible, return {\"evidence\": []}."
    )


def _call_vision_llm(
    *,
    config: dict[str, Any],
    prompt: str,
    image_paths: list[Path],
) -> tuple[str, Any, dict[str, Any]]:
    model, api_key = _pdf_llm_config(config)
    url = _chat_completions_url(config)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You extract structured scRNA-seq literature evidence from biomedical paper text and images. "
                    "Prioritize cell type-marker gene relationships visible in figures, captions, tables, and extracted text. "
                    "Do not normalize or enrich cell type labels with contextual qualifiers unless the local evidence explicitly uses those qualifiers. "
                    "Return only strict JSON."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    *_image_content_items(image_paths),
                ],
            },
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
        with urllib.request.urlopen(request, timeout=240) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ExternalEvidenceError(f"LLM API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ExternalEvidenceError(f"LLM API request failed: {exc.reason}") from exc
    content = _extract_message_content(response_payload)
    parsed = _extract_json_payload(content)
    return content, parsed, response_payload


def _clean_celltype_against_local_evidence(celltype: str, support_text: str) -> tuple[str, str]:
    label = " ".join(str(celltype or "").strip().split())
    support = str(support_text or "").lower()
    original = label

    tumor_context_present = any(
        token in support
        for token in [
            "tumor-associated",
            "tumour-associated",
            "tumor associated",
            "tumour associated",
            "cancer-associated",
            "cancer associated",
            " tam ",
            "(tam",
            "tams",
        ]
    )
    if not tumor_context_present:
        label = re.sub(r"(?i)\b(tumou?r|cancer)[- ]associated\s+", "", label).strip()
        label = re.sub(r"(?i)\s*\((?:TAMs?|tumou?r[- ]associated macrophages?)\)", "", label).strip()
        if re.fullmatch(r"(?i)TAMs?", label):
            label = "macrophages"

    normalization_note = ""
    if label != original:
        normalization_note = (
            f"Celltype normalized from '{original}' because the local evidence did not explicitly support the added tumor/TAM qualifier."
        )
    return label or original, normalization_note


def _normalize_evidence_entries(parsed: Any, *, pdf_path: Path) -> list[dict[str, Any]]:
    if isinstance(parsed, dict):
        raw_entries = parsed.get("evidence", [])
    elif isinstance(parsed, list):
        raw_entries = parsed
    else:
        raw_entries = []
    entries: list[dict[str, Any]] = []
    if not isinstance(raw_entries, list):
        return entries
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        celltype = str(item.get("celltype") or "").strip()
        markers = [str(marker).strip() for marker in item.get("markers", []) if str(marker).strip()]
        if not celltype or not markers:
            continue
        local_support = " ".join(
            str(item.get(key) or "")
            for key in ["celltype_evidence_text", "evidence_summary", "figure_or_page"]
        )
        celltype, normalization_note = _clean_celltype_against_local_evidence(celltype, local_support)
        note = str(item.get("evidence_summary") or "").strip()
        if normalization_note:
            note = f"{note} {normalization_note}".strip()
        entries.append(
            {
                "celltype": celltype,
                "original_celltype": str(item.get("celltype") or "").strip(),
                "markers": markers,
                "species": str(item.get("species") or "").strip(),
                "tissue": str(item.get("tissue") or "").strip(),
                "source": "literature",
                "source_type": "uploaded_pdf_image_llm",
                "source_file": str(pdf_path),
                "figure_or_page": str(item.get("figure_or_page") or "").strip(),
                "note": note,
                "celltype_evidence_text": str(item.get("celltype_evidence_text") or "").strip(),
                "confidence": str(item.get("confidence") or "").strip().lower(),
                "added_at": utc_now(),
            }
        )
    return entries


def _split_marker_field(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = [str(item) for item in value]
    else:
        raw = re.split(r"[,;|]", str(value or ""))
    return [item.strip() for item in raw if item.strip()]


def _pdf2markers_entries_from_csv(csv_path: Path, *, pdf_path: Path) -> list[dict[str, Any]]:
    if not csv_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            celltype = str(row.get("full_cell_type_name") or row.get("celltype") or "").strip()
            markers = _split_marker_field(row.get("marker_genes") or row.get("markers"))
            if not celltype or not markers:
                continue
            entries.append(
                {
                    "celltype": celltype,
                    "original_celltype": celltype,
                    "markers": markers,
                    "species": "",
                    "tissue": "",
                    "source": "literature",
                    "source_type": "uploaded_pdf_text_pdf2markers",
                    "source_file": str(pdf_path),
                    "figure_or_page": "PDF text",
                    "note": "Extracted by GPTAnno/PDF2markers text pipeline.",
                    "celltype_evidence_text": "",
                    "confidence": "medium",
                    "added_at": utc_now(),
                }
            )
    return entries


def run_pdf2markers_text_extraction(
    *,
    config: dict[str, Any],
    pdf_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    model, env = _pdfmarkers_llm_config(config)
    repo_root = Path(str(config["_meta"]["repo_root"]))
    extraction_script = Path(str(config["_runtime"]["pdf2markers_path"])) / "paper_extraction_cellNgenes.py"
    filter_script = Path(str(config["_runtime"]["pdf2markers_path"])) / "filterout_cell_ontology.py"
    ontology_csv = Path(str(config["_runtime"]["pdf2markers_path"])) / "cell_ontology" / "GPTCelltype_mapping.csv"
    python = str(config["_runtime"].get("python") or "python3")
    ensure_dir(output_dir)
    log_path = output_dir / "pdf2markers_text.log"
    runtime_env = {**os.environ, **env}

    commands = [
        [
            python,
            str(extraction_script),
            "--pdf",
            str(pdf_path),
            "--out",
            str(output_dir),
            "--model",
            str(model),
        ],
        [
            python,
            str(filter_script),
            "--input-dir",
            str(output_dir),
            "--ontology-csv",
            str(ontology_csv),
        ],
    ]
    with log_path.open("w", encoding="utf-8") as handle:
        for command in commands:
            handle.write(f"$ {' '.join(command)}\n")
            process = subprocess.run(
                command,
                cwd=str(repo_root),
                env=runtime_env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            if process.returncode != 0:
                raise ExternalEvidenceError(
                    f"PDF2markers text extraction failed with exit code {process.returncode}; see {log_path}"
                )

    final_csv = next(output_dir.glob("3-final-*.csv"), None)
    if final_csv is None:
        final_csv = next(output_dir.glob("*/3-final-*.csv"), None)
    filtered_csv = next(output_dir.glob("4-final-filtered-*.csv"), None)
    if filtered_csv is None:
        filtered_csv = next(output_dir.glob("*/4-final-filtered-*.csv"), None)
    entries = _pdf2markers_entries_from_csv(final_csv, pdf_path=pdf_path) if final_csv else []
    return {
        "entries": entries,
        "final_csv": str(final_csv) if final_csv else None,
        "filtered_csv": str(filtered_csv) if filtered_csv else None,
        "log": str(log_path),
        "model": model,
    }


def extract_literature_evidence_from_pdf(
    *,
    config: dict[str, Any],
    pdf_path: Path,
    pages: list[int],
    dpi: int = 150,
) -> dict[str, Any]:
    project_dir = Path(str(config["project"]["work_dir"])) / "external_evidence" / "literature_pdf" / _safe_stem(pdf_path.name)
    text_dir = ensure_dir(project_dir / "pdf2markers_text")
    image_dir = ensure_dir(project_dir / "pages")
    text_result = run_pdf2markers_text_extraction(
        config=config,
        pdf_path=pdf_path,
        output_dir=text_dir,
    )
    rendered_pages = render_pdf_pages(pdf_path, output_dir=image_dir, pages=pages, dpi=dpi)
    prompt = _extraction_prompt(config, pdf_path, pages)
    raw_text, parsed, response_payload = _call_vision_llm(
        config=config,
        prompt=prompt,
        image_paths=rendered_pages,
    )
    image_entries = _normalize_evidence_entries(parsed, pdf_path=pdf_path)
    text_entries = text_result["entries"]
    entries = [*text_entries, *image_entries]

    memory = load_agent_memory(config)
    for entry in entries:
        append_memory_entry(memory, "custom_markers", entry)
    merged_duplicate_count = compact_custom_marker_memory(memory)
    memory_path = save_agent_memory(config, memory)

    result = {
        "pdf_path": str(pdf_path),
        "pages": pages,
        "text_evidence_count": len(text_entries),
        "image_evidence_count": len(image_entries),
        "pdf2markers_final_csv": text_result.get("final_csv"),
        "pdf2markers_filtered_csv": text_result.get("filtered_csv"),
        "pdf2markers_log": text_result.get("log"),
        "rendered_pages": [str(path) for path in rendered_pages],
        "evidence_count": len(entries),
        "merged_duplicate_count": merged_duplicate_count,
        "evidence": entries,
        "memory_path": str(memory_path),
        "text_model": text_result.get("model"),
        "image_model": response_payload.get("model"),
        "generated_at": utc_now(),
    }
    dump_json(project_dir / "literature_evidence_result.json", result)
    dump_json(project_dir / "literature_evidence_raw_response.json", response_payload)
    (project_dir / "literature_evidence_raw_text.txt").write_text(raw_text, encoding="utf-8")
    return result


__all__ = [
    "ExternalEvidenceError",
    "extract_literature_evidence_from_pdf",
    "save_uploaded_literature_pdf",
    "_parse_page_ranges",
]
