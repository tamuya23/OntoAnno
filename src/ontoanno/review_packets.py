from __future__ import annotations

import csv
import subprocess
from pathlib import Path
from typing import Any

from .utils import dump_json, ensure_dir, load_json


def _nonempty(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _existing_path(*candidates: Any) -> str | None:
    for candidate in candidates:
        value = _nonempty(candidate)
        if value and Path(value).exists():
            return value
    return None


def _existing_dir(*candidates: Any) -> str | None:
    for candidate in candidates:
        value = _nonempty(candidate)
        if value and Path(value).is_dir():
            return value
    return None


def _normalize_resolution_name(value: Any) -> str | None:
    text = _nonempty(value)
    if not text:
        return None
    return text if text.startswith("res_") else f"res_{text}"


def _resolution_value(resolution_name: str | None) -> str | None:
    if not resolution_name:
        return None
    return resolution_name.removeprefix("res_")


def _derive_best_resolution(annotation_scores_csv: str | None) -> str | None:
    if not annotation_scores_csv or not Path(annotation_scores_csv).exists():
        return None
    with Path(annotation_scores_csv).open("r", encoding="utf-8", errors="replace", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return None

    def score(row: dict[str, str]) -> float:
        try:
            return float(row.get("composite_score", "nan"))
        except (TypeError, ValueError):
            return float("-inf")

    selected = max(rows, key=score)
    return _normalize_resolution_name(selected.get("resolution"))


def _infer_annotation_scores_path(annotation_parent_path: Path) -> str | None:
    name = annotation_parent_path.name
    candidates = [
        annotation_parent_path.with_name(name.replace("annotation_parent", "annotation_summary_scores")).with_suffix(".csv"),
        annotation_parent_path.parent / "annotation_summary_scores.csv",
        annotation_parent_path.parent / "annotation_summary_scores_nonCM.csv",
    ]
    return _existing_path(*candidates)


def _resolve_annotation_output_dir(inputs: dict[str, Any], bootstrap: dict[str, Any]) -> Path | None:
    explicit = _existing_dir(inputs.get("annotation_output_dir"), bootstrap.get("annotation_output_dir"))
    if explicit:
        path = Path(explicit)
        return path / "output" if (path / "output").is_dir() else path
    parent = _nonempty(inputs.get("annotation_parent_rds") or bootstrap.get("annotation_parent_rds"))
    if parent:
        return Path(parent).parent
    return None


def _infer_annotation_parent_path(output_dir: Path | None) -> str | None:
    if output_dir is None:
        return None
    candidates = [
        output_dir / "annotation_parent_nonCM.rds",
        output_dir / "annotation_parent.rds",
        *sorted(output_dir.glob("annotation_parent*.rds")),
    ]
    return _existing_path(*candidates)


def _infer_parent_seurat_path(annotation_parent_path: Path, config: dict[str, Any]) -> str | None:
    inputs = config.get("inputs", {}) if isinstance(config.get("inputs"), dict) else {}
    explicit = _existing_path(inputs.get("parent_seurat_rds"))
    if explicit:
        return explicit
    sibling_candidates = sorted(annotation_parent_path.parent.glob("*GPTannotated_parent*.rds"))
    sibling_candidates.extend(sorted(annotation_parent_path.parent.glob("*parent_annotated*.rds")))
    sibling_candidates = [
        path for path in sibling_candidates
        if path.name != annotation_parent_path.name and path.stat().st_size > annotation_parent_path.stat().st_size
    ]
    return _existing_path(*sibling_candidates, inputs.get("seurat_rds"))


def _infer_parent_metadata_path(annotation_parent_path: Path) -> str | None:
    candidates = sorted(annotation_parent_path.parent.glob("*_metadata.csv"))
    return _existing_path(*candidates)


def resolve_imported_parent_annotations(config: dict[str, Any]) -> dict[str, Any]:
    inputs = config.get("inputs", {}) if isinstance(config.get("inputs"), dict) else {}
    bootstrap = inputs.get("bootstrap_parent") if isinstance(inputs.get("bootstrap_parent"), dict) else {}
    output_dir = _resolve_annotation_output_dir(inputs, bootstrap)

    annotation_parent_rds = _existing_path(
        inputs.get("annotation_parent_rds"),
        bootstrap.get("annotation_parent_rds"),
        _infer_annotation_parent_path(output_dir),
    )
    if not annotation_parent_rds:
        return {}

    annotation_parent_path = Path(annotation_parent_rds)
    annotation_scores_csv = _existing_path(
        inputs.get("annotation_scores_csv"),
        bootstrap.get("annotation_scores_csv"),
        output_dir / "annotation_summary_scores_nonCM.csv" if output_dir else None,
        output_dir / "annotation_summary_scores.csv" if output_dir else None,
        _infer_annotation_scores_path(annotation_parent_path),
    )
    markers_dir = _existing_dir(
        inputs.get("markers_dir"),
        bootstrap.get("markers_dir"),
        output_dir / "marker_genes" if output_dir else None,
        annotation_parent_path.parent / "marker_genes",
    )
    prediction_dir = _existing_dir(
        inputs.get("prediction_dir"),
        bootstrap.get("prediction_dir"),
        output_dir / "prediction" if output_dir else None,
        annotation_parent_path.parent / "prediction",
    )
    parent_seurat_rds = _existing_path(
        inputs.get("parent_seurat_rds"),
        bootstrap.get("parent_seurat_rds"),
        _infer_parent_seurat_path(annotation_parent_path, config),
    )
    parent_metadata_csv = _existing_path(
        inputs.get("parent_metadata_csv"),
        bootstrap.get("parent_metadata_csv"),
        _infer_parent_metadata_path(annotation_parent_path),
    )
    best_resolution = _normalize_resolution_name(
        inputs.get("best_resolution")
        or bootstrap.get("best_resolution")
        or _derive_best_resolution(annotation_scores_csv)
    )
    best_resolution_value = _resolution_value(best_resolution)
    cluster_col = (
        _nonempty(inputs.get("cluster_col"))
        or _nonempty(bootstrap.get("cluster_col"))
        or (f"cluster_res.{best_resolution_value}" if best_resolution_value else None)
    )

    return {
        "annotation_parent_rds": annotation_parent_rds,
        "annotation_output_dir": str(output_dir) if output_dir else None,
        "annotation_scores_csv": annotation_scores_csv,
        "markers_dir": markers_dir,
        "prediction_dir": prediction_dir,
        "parent_seurat_rds": parent_seurat_rds,
        "parent_metadata_csv": parent_metadata_csv,
        "best_resolution": best_resolution,
        "best_resolution_value": best_resolution_value,
        "cluster_col": cluster_col,
        "bootstrapped": True,
    }


def has_imported_parent_annotation_inputs(config: dict[str, Any]) -> bool:
    imported = resolve_imported_parent_annotations(config)
    required = ("annotation_parent_rds", "markers_dir", "best_resolution", "cluster_col")
    return bool(imported) and all(imported.get(key) for key in required)


def build_review_packets(
    *,
    config: dict[str, Any],
    state: dict[str, Any],
    manifest: dict[str, Any],
    run_dir: Path,
    repo_root: Path,
    force: bool = False,
) -> dict[str, Any]:
    outputs = manifest.get("outputs", {}) if isinstance(manifest.get("outputs"), dict) else {}
    parent_outputs = outputs.get("annotate_parent", {})
    gptanno_tools = outputs.get("gptanno_tools", {}) if isinstance(outputs.get("gptanno_tools"), dict) else {}
    assign_parent_outputs = gptanno_tools.get("assign_parent_labels", {})
    imported_parent = resolve_imported_parent_annotations(config)

    parent_dir = Path(str(config["project"]["work_dir"])) / "annotate_parent"
    annotation_parent_rds = (
        imported_parent.get("annotation_parent_rds")
        or parent_outputs.get("annotation_parent_rds")
        or str(parent_dir / "annotation_parent.rds")
    )
    parent_seurat_rds = (
        imported_parent.get("parent_seurat_rds")
        or parent_outputs.get("parent_seurat_rds")
        or assign_parent_outputs.get("parent_seurat_rds")
        or str(parent_dir / "seurat_parent_annotated.rds")
    )
    annotation_scores_csv = (
        imported_parent.get("annotation_scores_csv")
        or parent_outputs.get("annotation_scores_csv")
        or assign_parent_outputs.get("annotation_scores_csv")
        or str(parent_dir / "annotation_summary_scores.csv")
    )
    markers_dir = imported_parent.get("markers_dir") or parent_outputs.get("markers_dir") or str(parent_dir / "marker_genes")
    prediction_dir = imported_parent.get("prediction_dir") or parent_outputs.get("prediction_dir") or str(parent_dir / "prediction")
    best_resolution = (
        imported_parent.get("best_resolution")
        or parent_outputs.get("best_resolution")
        or assign_parent_outputs.get("best_resolution")
    )
    cluster_col = (
        imported_parent.get("cluster_col")
        or parent_outputs.get("cluster_col")
        or assign_parent_outputs.get("cluster_col")
    )

    if not best_resolution:
        best_resolution_json = parent_dir / "best_parent_resolution.json"
        if best_resolution_json.exists():
            best_payload = load_json(best_resolution_json)
            best_resolution = best_payload.get("best_resolution")

    required_inputs = {
        "annotation_parent_rds": annotation_parent_rds,
        "markers_dir": markers_dir,
        "best_resolution": best_resolution,
        "cluster_col": cluster_col,
    }
    missing = [
        key for key, value in required_inputs.items()
        if value in (None, "") or (key.endswith("_rds") or key.endswith("_csv") or key.endswith("_dir")) and not Path(str(value)).exists()
    ]
    if missing:
        raise RuntimeError(
            "annotate_parent outputs not found; run parent annotation first. Missing: "
            + ", ".join(missing)
        )

    review_dir = ensure_dir(run_dir / "review_packets")
    spec_path = review_dir / "parent_review_packets.spec.json"
    outputs_json = review_dir / "parent_review_packets.outputs.json"
    log_path = review_dir / "parent_review_packets.log"

    if outputs_json.exists() and not force:
        return load_json(outputs_json)

    spec = {
        "project_name": config["project"]["name"],
        "run_id": state["run_id"],
        "policy": config.get("policy", {}),
        "annotation": {
            "tissue_name": config.get("annotation", {}).get("tissue_name"),
            "parent_res": config.get("annotation", {}).get("parent_res"),
            "best_resolution": best_resolution,
            "cluster_col": cluster_col,
        },
        "inputs": {
            "annotation_parent_rds": annotation_parent_rds,
            "parent_seurat_rds": parent_seurat_rds,
            "parent_metadata_csv": imported_parent.get("parent_metadata_csv"),
            "annotation_scores_csv": annotation_scores_csv,
            "markers_dir": markers_dir,
            "prediction_dir": prediction_dir,
            "manual_labels_csv": config.get("inputs", {}).get("manual_labels_csv"),
            "seurat_rds": config.get("inputs", {}).get("seurat_rds"),
        },
        "output_dir": str(review_dir),
        "outputs_json": str(outputs_json),
    }
    dump_json(spec_path, spec)

    helper = repo_root / "scripts" / "export_parent_review_packets.R"
    command = [config["_runtime"]["rscript"], str(helper), str(spec_path)]
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
            f"Parent review packet export failed with exit code {process.returncode}: {' '.join(command)}"
        )

    outputs = load_json(outputs_json)
    outputs["log"] = str(log_path)
    return outputs
