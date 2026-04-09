from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

from .utils import dump_json, ensure_dir, load_json, utc_now


DEFAULT_EXCLUDED_ANCESTOR_LABELS = [
    "cell",
    "native cell",
    "animal cell",
    "eukaryotic cell",
    "somatic cell",
    "nucleate cell",
    "motile cell",
    "connective tissue cell",
    "leukocyte",
    "mononuclear leukocyte",
    "mononuclear cell",
    "hematopoietic cell",
]

SUPPORTED_REFERENCE_SPECIES = {"mouse", "human"}


def _cluster_sort_key(cluster_id: str) -> tuple[int, str]:
    try:
        return (0, f"{int(cluster_id):06d}")
    except (TypeError, ValueError):
        return (1, str(cluster_id))


def _safe_float(value: str | None) -> float:
    if value in (None, "", "NA"):
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _normalize_celltype_name(value: str) -> str:
    text = value.lower().strip()
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"[-_/]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text.endswith("s") and not text.endswith("ss"):
        text = text[:-1]
    return text.strip()


def _celltype_aliases(value: str) -> set[str]:
    normalized = _normalize_celltype_name(value)
    aliases = {normalized}
    if normalized.endswith(" cell"):
        aliases.add(normalized[: -len(" cell")].strip())
    return {alias for alias in aliases if alias}


def _normalize_species_label(value: str | None) -> str | None:
    if value in (None, "", "NA"):
        return None
    text = str(value).strip().lower()
    aliases = {
        "mouse": "mouse",
        "murine": "mouse",
        "mus musculus": "mouse",
        "mm": "mouse",
        "human": "human",
        "homo sapiens": "human",
        "hs": "human",
    }
    return aliases.get(text, text)


def _infer_dataset_species(config: dict[str, Any]) -> tuple[str | None, str]:
    annotation = config.get("annotation", {})
    explicit = _normalize_species_label(annotation.get("species"))
    if explicit:
        return explicit, "annotation.species"
    tissue_name = str(annotation.get("tissue_name") or "").lower()
    if "mouse" in tissue_name or "murine" in tissue_name:
        return "mouse", "annotation.tissue_name"
    if "human" in tissue_name or "homo sapiens" in tissue_name:
        return "human", "annotation.tissue_name"
    return None, "unknown"


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _discover_panglaodb(repo_root: Path) -> Path | None:
    reference_dir = repo_root / "resources" / "reference_db"
    if not reference_dir.exists():
        return None
    candidates = sorted(reference_dir.glob("*PanglaoDB*.tsv"))
    return candidates[0] if candidates else None


def _discover_cellmarker(repo_root: Path) -> Path | None:
    reference_dir = repo_root / "resources" / "reference_db"
    if not reference_dir.exists():
        return None
    candidates = sorted(reference_dir.glob("*Cell*marker*.xlsx")) + sorted(reference_dir.glob("*Cell*Marker*.xlsx"))
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped[0] if deduped else None


def _panglao_species_matches(row_species: str, dataset_species: str | None) -> bool:
    species = row_species.strip()
    if dataset_species == "mouse":
        return species in {"Mm", "Mm Hs"}
    if dataset_species == "human":
        return species in {"Hs", "Mm Hs"}
    return False


def _cellmarker_species_matches(row_species: str, dataset_species: str | None) -> bool:
    species = _normalize_species_label(row_species)
    return bool(dataset_species and species == dataset_species)


def _reference_source_info(
    *,
    name: str,
    path: Path | None,
    dataset_species: str | None,
    enabled: bool,
    reason: str | None,
    matched_rows: int,
) -> dict[str, Any]:
    return {
        "name": name,
        "path": str(path) if path is not None else None,
        "dataset_species": dataset_species,
        "enabled": enabled,
        "reason": reason,
        "matched_rows": matched_rows,
    }


def _load_panglaodb(path: Path, dataset_species: str | None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if dataset_species not in SUPPORTED_REFERENCE_SPECIES:
        return {}, _reference_source_info(
            name="PanglaoDB",
            path=path,
            dataset_species=dataset_species,
            enabled=False,
            reason="unsupported dataset species for PanglaoDB filtering",
            matched_rows=0,
        )
    groups: dict[str, dict[str, Any]] = {}
    matched_rows = 0
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            if not _panglao_species_matches(str(row.get("species") or ""), dataset_species):
                continue
            cell_type = (row.get("cell type") or "").strip()
            gene = (row.get("official gene symbol") or "").strip()
            if not cell_type or not gene:
                continue
            matched_rows += 1
            group = groups.setdefault(
                cell_type,
                {
                    "display_label": cell_type,
                    "normalized_label": _normalize_celltype_name(cell_type),
                    "aliases": sorted(_celltype_aliases(cell_type)),
                    "rows": [],
                },
            )
            group["rows"].append(row)

    for group in groups.values():
        rows = group["rows"]

        def sort_key(row: dict[str, str]) -> tuple[float, float, float, float, int]:
            species = row.get("species") or ""
            if dataset_species == "mouse":
                species_score = 2.0 if species == "Mm" else (1.0 if species == "Mm Hs" else 0.0)
            else:
                species_score = 2.0 if species == "Hs" else (1.0 if species == "Mm Hs" else 0.0)
            canonical = 1.0 if (row.get("canonical marker") or "") == "1" else 0.0
            specificity = max(_safe_float(row.get("specificity_mouse")), _safe_float(row.get("specificity_human")))
            sensitivity = max(_safe_float(row.get("sensitivity_mouse")), _safe_float(row.get("sensitivity_human")))
            ubiquitousness = -_safe_float(row.get("ubiquitousness index"))
            return (canonical, species_score, specificity, sensitivity, ubiquitousness)

        sorted_rows = sorted(rows, key=sort_key, reverse=True)
        seen_genes: set[str] = set()
        top_markers: list[str] = []
        canonical_markers: list[str] = []
        species_values = sorted({row.get("species") or "" for row in rows if row.get("species")})
        organs = sorted({row.get("organ") or "" for row in rows if row.get("organ")})
        for row in sorted_rows:
            gene = (row.get("official gene symbol") or "").strip()
            if not gene or gene in seen_genes:
                continue
            seen_genes.add(gene)
            top_markers.append(gene)
            if (row.get("canonical marker") or "") == "1":
                canonical_markers.append(gene)
        group["top_markers"] = top_markers[:15]
        group["canonical_markers"] = canonical_markers[:10]
        group["species"] = species_values
        group["organs"] = organs
    return groups, _reference_source_info(
        name="PanglaoDB",
        path=path,
        dataset_species=dataset_species,
        enabled=bool(groups),
        reason=None if groups else "no species-matched PanglaoDB rows found",
        matched_rows=matched_rows,
    )


def _normalize_clid(value: str | None) -> str | None:
    if value in (None, "", "NA"):
        return None
    text = str(value).strip().replace("_", ":")
    return text if text else None


def _load_cellmarker(path: Path, dataset_species: str | None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if dataset_species not in SUPPORTED_REFERENCE_SPECIES:
        return {}, _reference_source_info(
            name="CellMarker",
            path=path,
            dataset_species=dataset_species,
            enabled=False,
            reason="unsupported dataset species for CellMarker filtering",
            matched_rows=0,
        )
    df = pd.read_excel(
        path,
        sheet_name="All",
        usecols=[
            "species",
            "tissue_type",
            "cell_name",
            "cellontology_id",
            "Symbol",
            "PMID",
        ],
    )
    df = df[df["species"].astype(str).map(lambda item: _cellmarker_species_matches(item, dataset_species))]
    groups: dict[str, dict[str, Any]] = {}
    for row in df.to_dict(orient="records"):
        cell_name = str(row.get("cell_name") or "").strip()
        gene = str(row.get("Symbol") or "").strip()
        if not cell_name or not gene or gene.lower() == "nan":
            continue
        group = groups.setdefault(
            cell_name,
            {
                "display_label": cell_name,
                "normalized_label": _normalize_celltype_name(cell_name),
                "aliases": sorted(_celltype_aliases(cell_name)),
                "rows": [],
            },
        )
        group["rows"].append(row)

    for group in groups.values():
        rows = group["rows"]
        gene_counts: dict[str, int] = {}
        tissues: set[str] = set()
        species_values: set[str] = set()
        clids: set[str] = set()
        pmids: set[str] = set()
        for row in rows:
            gene = str(row.get("Symbol") or "").strip()
            if gene and gene.lower() != "nan":
                gene_counts[gene] = gene_counts.get(gene, 0) + 1
            tissue = str(row.get("tissue_type") or "").strip()
            if tissue and tissue.lower() != "nan":
                tissues.add(tissue)
            species = str(row.get("species") or "").strip()
            if species and species.lower() != "nan":
                species_values.add(species)
            clid = _normalize_clid(row.get("cellontology_id"))
            if clid:
                clids.add(clid)
            pmid = str(row.get("PMID") or "").strip()
            if pmid and pmid.lower() != "nan":
                pmids.add(pmid)
        top_markers = [
            gene for gene, _ in sorted(gene_counts.items(), key=lambda item: (-item[1], item[0]))
        ][:15]
        group["top_markers"] = top_markers
        group["canonical_markers"] = top_markers[:10]
        group["species"] = sorted(species_values)
        group["organs"] = sorted(tissues)
        group["clids"] = sorted(clids)
        group["pmid_count"] = len(pmids)
        group["paper_count"] = len(pmids)
    return groups, _reference_source_info(
        name="CellMarker",
        path=path,
        dataset_species=dataset_species,
        enabled=bool(groups),
        reason=None if groups else "no species-matched CellMarker rows found",
        matched_rows=int(len(df)),
    )


def _select_reference_entry(candidate: str, reference_groups: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    candidate_aliases = _celltype_aliases(candidate)
    candidate_norm = _normalize_celltype_name(candidate)
    matches: list[tuple[int, int, dict[str, Any]]] = []
    for group in reference_groups.values():
        aliases = set(group["aliases"])
        if not (candidate_aliases & aliases):
            continue
        exact_norm = 1 if group["normalized_label"] == candidate_norm else 0
        generic_preference = 1 if "(" not in group["display_label"] else 0
        matches.append((exact_norm, generic_preference, group))
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], item[1], -len(item[2]["display_label"])), reverse=True)
    return matches[0][2]


def _select_reference_entry_with_clid(
    *,
    candidate: str,
    candidate_clid: str | None,
    reference_groups: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    candidate_aliases = _celltype_aliases(candidate)
    candidate_norm = _normalize_celltype_name(candidate)
    matches: list[tuple[int, int, int, dict[str, Any]]] = []
    for group in reference_groups.values():
        aliases = set(group.get("aliases", []))
        if not (candidate_aliases & aliases):
            continue
        clid_match = 1 if candidate_clid and candidate_clid in set(group.get("clids", [])) else 0
        exact_norm = 1 if group["normalized_label"] == candidate_norm else 0
        generic_preference = 1 if "(" not in group["display_label"] else 0
        matches.append((clid_match, exact_norm, generic_preference, group))
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], item[1], item[2], -len(item[3]["display_label"])), reverse=True)
    return matches[0][3]


def _load_review_packet_map(run_dir: Path) -> dict[str, Path]:
    index_path = run_dir / "review_packets" / "index.json"
    if not index_path.exists():
        return {}
    index = load_json(index_path)
    packet_map: dict[str, Path] = {}
    for item in index.get("packets", []):
        cluster_id = str(item.get("cluster_id"))
        packet_json = item.get("packet_json")
        if cluster_id and packet_json:
            packet_map[cluster_id] = Path(packet_json)
    return packet_map


def _extract_cluster_markers(packet_path: Path) -> list[str]:
    packet = load_json(packet_path)
    markers = packet.get("markers", [])
    genes = []
    for marker in markers:
        gene = marker.get("gene")
        if gene:
            genes.append(str(gene))
    return genes


def _build_reference_compare(
    relation_payload: dict[str, Any],
    cluster_markers: list[str],
    reference_groups: dict[str, dict[str, Any]],
    reference_path: Path,
    cellmarker_groups: dict[str, dict[str, Any]] | None = None,
    cellmarker_path: Path | None = None,
    dataset_species: str | None = None,
    source_infos: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    comparison = relation_payload.get("comparison_brief", {})
    seen_focus: set[str] = set()
    focus_candidates: list[str] = []
    for item in _listify(comparison.get("focus_candidates")):
        text = str(item).strip()
        if not text or text in seen_focus:
            continue
        seen_focus.add(text)
        focus_candidates.append(text)
    cluster_markers_upper = {gene.upper() for gene in cluster_markers}
    candidate_payload = []
    hits = 0
    cellmarker_hits = 0
    candidate_clid_map = {
        str(item.get("cl_label") or item.get("raw_label") or "").strip(): _normalize_clid(item.get("clid"))
        for item in relation_payload.get("candidates", [])
    }
    for candidate in focus_candidates:
        reference = _select_reference_entry(candidate, reference_groups)
        cellmarker_reference = (
            _select_reference_entry_with_clid(
                candidate=candidate,
                candidate_clid=candidate_clid_map.get(candidate),
                reference_groups=cellmarker_groups,
            )
            if cellmarker_groups
            else None
        )
        if cellmarker_reference is not None:
            cellmarker_hits += 1
        if reference is None:
            panglao_payload = {
                "reference_label": None,
                "species": [],
                "organs": [],
                "canonical_markers": [],
                "top_markers": [],
                "overlap_genes": [],
                "overlap_count": 0,
            }
        else:
            hits += 1
            top_markers = reference["top_markers"]
            overlap = [gene for gene in top_markers if gene.upper() in cluster_markers_upper]
            panglao_payload = {
                "reference_label": reference["display_label"],
                "species": reference["species"],
                "organs": reference["organs"],
                "canonical_markers": reference["canonical_markers"],
                "top_markers": top_markers,
                "overlap_genes": overlap,
                "overlap_count": len(overlap),
            }

        if cellmarker_reference is None:
            cellmarker_payload = {
                "reference_label": None,
                "species": [],
                "organs": [],
                "canonical_markers": [],
                "top_markers": [],
                "overlap_genes": [],
                "overlap_count": 0,
                "clids": [],
                "paper_count": 0,
            }
        else:
            cm_markers = cellmarker_reference["top_markers"]
            cm_overlap = [gene for gene in cm_markers if gene.upper() in cluster_markers_upper]
            cellmarker_payload = {
                "reference_label": cellmarker_reference["display_label"],
                "species": cellmarker_reference["species"],
                "organs": cellmarker_reference["organs"],
                "canonical_markers": cellmarker_reference["canonical_markers"],
                "top_markers": cm_markers,
                "overlap_genes": cm_overlap,
                "overlap_count": len(cm_overlap),
                "clids": cellmarker_reference.get("clids", []),
                "paper_count": cellmarker_reference.get("paper_count", 0),
            }

        candidate_payload.append(
            {
                "candidate": candidate,
                "panglaodb": panglao_payload,
                "cellmarker": cellmarker_payload,
            }
        )

    needs_llm = bool(comparison.get("needs_llm_compare"))
    source_infos = source_infos or []
    enabled_sources = [info["name"] for info in source_infos if info.get("enabled")]
    disabled_sources = [info for info in source_infos if not info.get("enabled")]
    prompt = None
    if needs_llm:
        blocks = [
            "You are comparing candidate cell type annotations for one scRNA-seq cluster.",
            f"Current label: {relation_payload.get('current_label', '')}",
            f"Cluster top markers: {', '.join(cluster_markers)}",
            "Important: the candidate labels came from the dataset-specific annotation workflow.",
            "Reference marker databases are supportive evidence for evidence balancing, not gold standards.",
        ]
        consensus = relation_payload.get("consensus_ancestor") or {}
        consensus_label = consensus.get("label")
        if consensus_label:
            blocks.append(f"Shared ontology ancestor: {consensus_label}")
        policy_granularity = str(comparison.get("policy_granularity") or "").strip()
        focus_strategy = str(comparison.get("focus_strategy") or "").strip()
        if policy_granularity:
            blocks.append(f"Policy granularity: {policy_granularity}")
        if focus_strategy:
            blocks.append(f"Policy focus strategy: {focus_strategy}")
        blocks.append("Only choose from these candidate labels:")
        blocks.extend(f"- {candidate}" for candidate in focus_candidates)
        if enabled_sources:
            blocks.append(
                "Reference evidence from "
                + " and ".join(enabled_sources)
                + " (supportive evidence, not a golden rule):"
            )
            for item in candidate_payload:
                panglao = item["panglaodb"]
                cellmarker = item["cellmarker"]
                if "PanglaoDB" in enabled_sources:
                    if panglao["reference_label"]:
                        blocks.append(
                            f"- {item['candidate']} | PanglaoDB reference: {panglao['reference_label']} | "
                            f"canonical markers: {', '.join(panglao['canonical_markers'][:8]) or 'NA'} | "
                            f"top markers: {', '.join(panglao['top_markers'][:10]) or 'NA'} | "
                            f"overlap with cluster: {', '.join(panglao['overlap_genes']) or 'none'}"
                        )
                    else:
                        blocks.append(f"- {item['candidate']} | no PanglaoDB hit found")
                if "CellMarker" in enabled_sources:
                    if cellmarker["reference_label"]:
                        blocks.append(
                            f"  CellMarker reference: {cellmarker['reference_label']} | "
                            f"cell ontology ids: {', '.join(cellmarker['clids']) or 'NA'} | "
                            f"papers: {cellmarker['paper_count']} | "
                            f"markers: {', '.join(cellmarker['top_markers'][:10]) or 'NA'} | "
                            f"overlap with cluster: {', '.join(cellmarker['overlap_genes']) or 'none'}"
                        )
                    else:
                        blocks.append("  CellMarker reference: no hit found")
        else:
            species_text = dataset_species or "unknown"
            blocks.append(
                f"No species-matched reference evidence is available for dataset species '{species_text}'. "
                "Reference set mode is disabled for this comparison."
            )
        if disabled_sources:
            disabled_text = "; ".join(
                f"{info['name']}: {info.get('reason')}" for info in disabled_sources if info.get("reason")
            )
            if disabled_text:
                blocks.append(f"Reference availability note: {disabled_text}.")
        blocks.append(
            "Task: weigh dataset-specific markers, ontology-constrained candidates, and reference markers together. "
            "Do not treat the reference database as the final authority. "
            "Choose the best candidate among the listed options, explain which cluster markers support or weaken each option, "
            "note any reference limitations, and say 'review' if the evidence is insufficient."
        )
        prompt = "\n".join(blocks)

    return {
        "sources": [
            {
                "name": "PanglaoDB",
                "path": str(reference_path),
            },
            {
                "name": "CellMarker",
                "path": str(cellmarker_path) if cellmarker_path else None,
            },
        ],
        "reference_path": str(reference_path),
        "cellmarker_path": str(cellmarker_path) if cellmarker_path else None,
        "candidate_reference_hits": hits,
        "candidate_cellmarker_hits": cellmarker_hits,
        "dataset_species": dataset_species,
        "reference_mode_enabled": bool(enabled_sources),
        "source_infos": source_infos,
        "prompt_ready": needs_llm,
        "cluster_markers": cluster_markers,
        "candidates": candidate_payload,
        "prompt": prompt,
    }


def _slim_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = ["raw_label", "matched_variant", "cl_label", "clid", "mapping_status", "percentage", "source"]
    slimmed: list[dict[str, Any]] = []
    for item in candidates:
        slimmed.append({key: item.get(key) for key in keep if key in item})
    return slimmed


def _slim_relation_payload(relation_payload: dict[str, Any]) -> dict[str, Any]:
    comparison = relation_payload.get("comparison_brief", {})
    consensus = relation_payload.get("consensus_ancestor") or {}
    reference_compare = relation_payload.get("reference_compare")
    slim_candidates = _slim_candidates(relation_payload.get("candidates", []))
    mapped_candidates = _dedupe_keep_order(_listify(comparison.get("mapped_candidates")))
    unmapped_candidates = _dedupe_keep_order(_listify(comparison.get("unmapped_candidates")))
    focus_candidates = _dedupe_keep_order(_listify(comparison.get("focus_candidates")))
    relation_mode = comparison.get("relation_mode")
    needs_llm_compare = bool(comparison.get("needs_llm_compare"))
    llm_question = comparison.get("llm_question")
    if not llm_question and not needs_llm_compare:
        llm_question = f"No ontology comparison needed. Keep '{relation_payload.get('current_label')}' unless marker evidence suggests otherwise."
    slimmed = {
        "project_name": relation_payload.get("project_name"),
        "run_id": relation_payload.get("run_id"),
        "generated_at": relation_payload.get("generated_at"),
        "cluster_id": relation_payload.get("cluster_id"),
        "current_label": relation_payload.get("current_label"),
        "candidates": slim_candidates,
        "comparison_brief": {
            "policy_granularity": comparison.get("policy_granularity"),
            "focus_strategy": comparison.get("focus_strategy"),
            "relation_mode": relation_mode,
            "mapped_candidates": mapped_candidates,
            "unmapped_candidates": unmapped_candidates,
            "focus_candidates": focus_candidates,
            "informative_shared_ancestor": comparison.get("informative_shared_ancestor"),
            "llm_question": llm_question,
            "needs_llm_compare": needs_llm_compare,
        },
        "consensus_ancestor": {
            "clid": consensus.get("clid"),
            "label": consensus.get("label"),
        },
        "review_packet_json": relation_payload.get("review_packet_json"),
    }
    if reference_compare is not None:
        slimmed["reference_compare"] = reference_compare
    return slimmed


def _enrich_with_reference_db(
    *,
    outputs: dict[str, Any],
    run_dir: Path,
    repo_root: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    dataset_species, species_source = _infer_dataset_species(config)
    reference_path = _discover_panglaodb(repo_root)
    cellmarker_path = _discover_cellmarker(repo_root)
    if reference_path is None and cellmarker_path is None:
        outputs["reference_db"] = None
        return outputs

    source_infos: list[dict[str, Any]] = []
    reference_groups: dict[str, dict[str, Any]] = {}
    cellmarker_groups: dict[str, dict[str, Any]] = {}
    if reference_path is not None:
        reference_groups, info = _load_panglaodb(reference_path, dataset_species)
        source_infos.append(info)
    if cellmarker_path is not None:
        cellmarker_groups, info = _load_cellmarker(cellmarker_path, dataset_species)
        source_infos.append(info)

    warnings: list[str] = []
    if dataset_species is None:
        warnings.append(
            "Could not infer dataset species from config; reference set mode disabled until annotation.species is set."
        )
    elif dataset_species not in SUPPORTED_REFERENCE_SPECIES:
        warnings.append(
            f"Dataset species '{dataset_species}' is not supported by the current reference loaders; reference set mode disabled."
        )
    for info in source_infos:
        if not info.get("enabled") and info.get("reason"):
            warnings.append(f"{info['name']} disabled: {info['reason']}.")
    packet_map = _load_review_packet_map(run_dir)
    summary_path = Path(outputs["summary_csv"])
    summary_rows: list[dict[str, str]] = []
    with summary_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            summary_rows.append(row)

    for row in summary_rows:
        cluster_id = str(row.get("cluster_id"))
        relation_path = Path(row["relation_json"])
        relation_payload = load_json(relation_path)
        packet_path = packet_map.get(cluster_id)
        cluster_markers = _extract_cluster_markers(packet_path) if packet_path else []
        reference_compare = _build_reference_compare(
            relation_payload,
            cluster_markers,
            reference_groups,
            reference_path if reference_path is not None else Path(""),
            cellmarker_groups=cellmarker_groups,
            cellmarker_path=cellmarker_path,
            dataset_species=dataset_species,
            source_infos=source_infos,
        )
        relation_payload["reference_compare"] = reference_compare
        relation_payload = _slim_relation_payload(relation_payload)
        dump_json(relation_path, relation_payload)

        comparison = relation_payload.get("comparison_brief", {})
        consensus = relation_payload.get("consensus_ancestor", {})
        row.clear()
        row.update(
            {
                "cluster_id": str(relation_payload.get("cluster_id") or ""),
                "current_label": str(relation_payload.get("current_label") or ""),
                "policy_granularity": str(comparison.get("policy_granularity") or ""),
                "focus_strategy": str(comparison.get("focus_strategy") or ""),
                "relation_mode": str(comparison.get("relation_mode") or ""),
                "focus_candidates": " | ".join(comparison.get("focus_candidates", [])),
                "consensus_ancestor": str(consensus.get("label") or ""),
                "llm_question": str(comparison.get("llm_question") or ""),
                "reference_db": (
                    "+".join(info["name"] for info in source_infos if info.get("enabled"))
                    if any(info.get("enabled") for info in source_infos)
                    else "disabled"
                ),
                "reference_hits": str(reference_compare["candidate_reference_hits"]),
                "cellmarker_hits": str(reference_compare["candidate_cellmarker_hits"]),
                "prompt_ready": str(reference_compare["prompt_ready"]).lower(),
                "relation_json": str(relation_path),
            }
        )

    fieldnames = [
        "cluster_id",
        "current_label",
        "policy_granularity",
        "focus_strategy",
        "relation_mode",
        "focus_candidates",
        "consensus_ancestor",
        "llm_question",
        "reference_db",
        "reference_hits",
        "cellmarker_hits",
        "prompt_ready",
        "relation_json",
    ]

    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)

    index_path = Path(outputs["index_json"])
    index_payload = load_json(index_path)
    index_payload["reference_db"] = {
        "dataset_species": dataset_species,
        "species_source": species_source,
        "sources": [
            {
                "name": info["name"],
                "path": info.get("path"),
                "enabled": info.get("enabled"),
                "reason": info.get("reason"),
                "matched_rows": info.get("matched_rows"),
            }
            for info in source_infos
        ],
    }
    index_payload["warnings"] = warnings
    for item in index_payload.get("relations", []):
        relation_json = str(item.get("relation_json") or "")
        relation_payload = load_json(Path(relation_json))
        comparison = relation_payload.get("comparison_brief", {})
        consensus = relation_payload.get("consensus_ancestor", {})
        reference_compare = relation_payload.get("reference_compare", {})
        item.clear()
        item.update(
            {
                "cluster_id": relation_payload.get("cluster_id"),
                "label": relation_payload.get("current_label"),
                "policy_granularity": comparison.get("policy_granularity"),
                "focus_strategy": comparison.get("focus_strategy"),
                "relation_mode": comparison.get("relation_mode"),
                "focus_candidates": comparison.get("focus_candidates", []),
                "consensus_ancestor": consensus.get("label"),
                "needs_llm_compare": comparison.get("needs_llm_compare"),
                "prompt_ready": reference_compare.get("prompt_ready"),
                "reference_hits": reference_compare.get("candidate_reference_hits"),
                "cellmarker_hits": reference_compare.get("candidate_cellmarker_hits"),
                "relation_json": relation_json,
                "relation_uri": relation_json,
            }
        )
    dump_json(index_path, index_payload)

    outputs["reference_db"] = {
        "dataset_species": dataset_species,
        "species_source": species_source,
        "sources": [
            {
                "name": info["name"],
                "path": info.get("path"),
                "enabled": info.get("enabled"),
                "reason": info.get("reason"),
                "matched_rows": info.get("matched_rows"),
            }
            for info in source_infos
        ],
    }
    outputs["warnings"] = warnings
    return outputs


def _write_filtered_review_index(
    *,
    review_index_path: Path,
    cluster_ids: list[str],
    target_dir: Path,
) -> Path:
    review_index = load_json(review_index_path)
    allowed = set(cluster_ids)
    filtered_index = dict(review_index)
    filtered_index["packets"] = [
        item
        for item in review_index.get("packets", [])
        if str(item.get("cluster_id") or "") in allowed
    ]
    filtered_path = target_dir / "review_index.filtered.json"
    dump_json(filtered_path, filtered_index)
    return filtered_path


def _run_ontology_helper(
    *,
    config: dict[str, Any],
    repo_root: Path,
    run_dir: Path,
    review_index_json: Path,
    output_dir: Path,
    log_path: Path,
) -> dict[str, Any]:
    spec_path = output_dir / "ontology_relations.spec.json"
    outputs_json = output_dir / "ontology_relations.outputs.json"
    helper = repo_root / "scripts" / "export_ontology_relations.R"
    spec = {
        "project_name": config["project"]["name"],
        "run_id": run_dir.name,
        "gptanno_path": config["_runtime"]["gptanno_path"],
        "review_index_json": str(review_index_json),
        "output_dir": str(output_dir),
        "outputs_json": str(outputs_json),
        "ontology_url": "https://purl.obolibrary.org/obo/cl.obo",
        "ontology_cache": str((run_dir / "ontology_relations" / "cache" / "cl.obo").resolve()),
        "ontology_obo": config["_runtime"].get("ontology_obo"),
        "ancestor_min_depth": 6,
        "excluded_ancestor_labels": DEFAULT_EXCLUDED_ANCESTOR_LABELS,
        "policy": config.get("policy", {}),
    }
    dump_json(spec_path, spec)
    command = [config["_runtime"]["rscript"], str(helper), str(spec_path)]
    ensure_dir(log_path.parent)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"$ {' '.join(command)}\n")
        process = subprocess.run(
            command,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if process.returncode != 0:
        raise RuntimeError(
            "Ontology relation export failed "
            f"(exit code {process.returncode}). See log: {log_path}"
        )
    return load_json(outputs_json)


def _rebuild_ontology_outputs(
    *,
    config: dict[str, Any],
    run_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    relations_dir = ensure_dir(output_dir / "relations")
    relation_paths = sorted(relations_dir.glob("cluster-*.json"))
    summary_path = output_dir / "summary.csv"
    index_path = output_dir / "index.json"
    outputs_path = output_dir / "ontology_relations.outputs.json"

    summary_rows: list[dict[str, str]] = []
    index_relations: list[dict[str, Any]] = []

    for relation_path in relation_paths:
        relation_payload = load_json(relation_path)
        comparison = relation_payload.get("comparison_brief") or {}
        if not isinstance(comparison, dict):
            comparison = {}
        consensus = relation_payload.get("consensus_ancestor") or {}
        if not isinstance(consensus, dict):
            consensus = {}
        reference_compare = relation_payload.get("reference_compare") or {}
        if not isinstance(reference_compare, dict):
            reference_compare = {}
        summary_rows.append(
            {
                "cluster_id": str(relation_payload.get("cluster_id") or ""),
                "current_label": str(relation_payload.get("current_label") or ""),
                "policy_granularity": str(comparison.get("policy_granularity") or ""),
                "focus_strategy": str(comparison.get("focus_strategy") or ""),
                "relation_mode": str(comparison.get("relation_mode") or ""),
                "focus_candidates": " | ".join(comparison.get("focus_candidates", [])),
                "consensus_ancestor": str(consensus.get("label") or ""),
                "llm_question": str(comparison.get("llm_question") or ""),
                "reference_db": "",
                "reference_hits": str(reference_compare.get("candidate_reference_hits") or ""),
                "cellmarker_hits": str(reference_compare.get("candidate_cellmarker_hits") or ""),
                "prompt_ready": str(reference_compare.get("prompt_ready") or comparison.get("needs_llm_compare") or "").lower(),
                "relation_json": str(relation_path),
            }
        )
        index_relations.append(
            {
                "cluster_id": relation_payload.get("cluster_id"),
                "label": relation_payload.get("current_label"),
                "policy_granularity": comparison.get("policy_granularity"),
                "focus_strategy": comparison.get("focus_strategy"),
                "relation_mode": comparison.get("relation_mode"),
                "focus_candidates": comparison.get("focus_candidates", []),
                "consensus_ancestor": consensus.get("label"),
                "needs_llm_compare": comparison.get("needs_llm_compare"),
                "prompt_ready": reference_compare.get("prompt_ready", comparison.get("needs_llm_compare")),
                "reference_hits": reference_compare.get("candidate_reference_hits"),
                "cellmarker_hits": reference_compare.get("candidate_cellmarker_hits"),
                "relation_json": str(relation_path),
                "relation_uri": str(relation_path),
            }
        )

    fieldnames = [
        "cluster_id",
        "current_label",
        "policy_granularity",
        "focus_strategy",
        "relation_mode",
        "focus_candidates",
        "consensus_ancestor",
        "llm_question",
        "reference_db",
        "reference_hits",
        "cellmarker_hits",
        "prompt_ready",
        "relation_json",
    ]
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)

    index_payload = {
        "project_name": config["project"]["name"],
        "run_id": run_dir.name,
        "generated_at": load_json(relation_paths[0]).get("generated_at") if relation_paths else utc_now(),
        "relations": index_relations,
    }
    dump_json(index_path, index_payload)

    outputs = {
        "output_dir": str(output_dir),
        "relations_dir": str(relations_dir),
        "index_json": str(index_path),
        "summary_csv": str(summary_path),
        "ontology_path": str((output_dir / "cache" / "cl.obo").resolve()) if (output_dir / "cache" / "cl.obo").exists() else None,
        "relation_count": len(relation_paths),
        "relations": [str(path) for path in relation_paths],
    }
    dump_json(outputs_path, outputs)
    return outputs


def build_ontology_relations(
    *,
    config: dict[str, Any],
    run_dir: Path,
    repo_root: Path,
    force: bool = False,
    cluster_ids: list[str] | None = None,
) -> dict[str, Any]:
    review_dir = run_dir / "review_packets"
    review_index_path = review_dir / "index.json"
    if not review_index_path.exists():
        raise RuntimeError("review_packets/index.json not found; generate review packets first.")

    output_dir = ensure_dir(run_dir / "ontology_relations")
    outputs_json = output_dir / "ontology_relations.outputs.json"
    log_path = output_dir / "ontology_relations.log"
    relations_dir = ensure_dir(output_dir / "relations")
    selected_ids = sorted({str(item) for item in (cluster_ids or []) if str(item).strip()}, key=_cluster_sort_key)

    if outputs_json.exists() and not force and not selected_ids:
        outputs = load_json(outputs_json)
        outputs = _enrich_with_reference_db(
            outputs=outputs,
            run_dir=run_dir,
            repo_root=repo_root,
            config=config,
        )
        outputs["log"] = str(log_path)
        return outputs

    if selected_ids:
        filtered_review_index = _write_filtered_review_index(
            review_index_path=review_index_path,
            cluster_ids=selected_ids,
            target_dir=ensure_dir(output_dir / "_targeted"),
        )
        temp_output_dir = ensure_dir(output_dir / "_targeted" / "run")
        temp_outputs = _run_ontology_helper(
            config=config,
            repo_root=repo_root,
            run_dir=run_dir,
            review_index_json=filtered_review_index,
            output_dir=temp_output_dir,
            log_path=log_path,
        )
        temp_relations_dir = Path(temp_outputs["relations_dir"])
        for relation_path in temp_relations_dir.glob("cluster-*.json"):
            shutil.copy2(relation_path, relations_dir / relation_path.name)
    else:
        temp_output_dir = ensure_dir(output_dir / "_full")
        temp_outputs = _run_ontology_helper(
            config=config,
            repo_root=repo_root,
            run_dir=run_dir,
            review_index_json=review_index_path,
            output_dir=temp_output_dir,
            log_path=log_path,
        )
        for existing in relations_dir.glob("cluster-*.json"):
            existing.unlink()
        temp_relations_dir = Path(temp_outputs["relations_dir"])
        for relation_path in temp_relations_dir.glob("cluster-*.json"):
            shutil.copy2(relation_path, relations_dir / relation_path.name)
        cache_source = temp_output_dir / "cache" / "cl.obo"
        if cache_source.exists():
            ensure_dir(output_dir / "cache")
            shutil.copy2(cache_source, output_dir / "cache" / "cl.obo")

    outputs = _rebuild_ontology_outputs(
        config=config,
        run_dir=run_dir,
        output_dir=output_dir,
    )
    outputs = _enrich_with_reference_db(
        outputs=outputs,
        run_dir=run_dir,
        repo_root=repo_root,
        config=config,
    )
    outputs["log"] = str(log_path)
    return outputs
