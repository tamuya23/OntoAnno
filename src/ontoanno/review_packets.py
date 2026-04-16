from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .utils import dump_json, ensure_dir, load_json


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

    parent_dir = Path(str(config["project"]["work_dir"])) / "annotate_parent"
    annotation_parent_rds = (
        parent_outputs.get("annotation_parent_rds")
        or str(parent_dir / "annotation_parent.rds")
    )
    parent_seurat_rds = (
        parent_outputs.get("parent_seurat_rds")
        or assign_parent_outputs.get("parent_seurat_rds")
        or str(parent_dir / "seurat_parent_annotated.rds")
    )
    annotation_scores_csv = (
        parent_outputs.get("annotation_scores_csv")
        or assign_parent_outputs.get("annotation_scores_csv")
        or str(parent_dir / "annotation_summary_scores.csv")
    )
    markers_dir = parent_outputs.get("markers_dir") or str(parent_dir / "marker_genes")
    prediction_dir = parent_outputs.get("prediction_dir") or str(parent_dir / "prediction")
    best_resolution = (
        parent_outputs.get("best_resolution")
        or assign_parent_outputs.get("best_resolution")
    )
    cluster_col = (
        parent_outputs.get("cluster_col")
        or assign_parent_outputs.get("cluster_col")
    )

    if not best_resolution:
        best_resolution_json = parent_dir / "best_parent_resolution.json"
        if best_resolution_json.exists():
            best_payload = load_json(best_resolution_json)
            best_resolution = best_payload.get("best_resolution")

    required_inputs = {
        "annotation_parent_rds": annotation_parent_rds,
        "parent_seurat_rds": parent_seurat_rds,
        "annotation_scores_csv": annotation_scores_csv,
        "markers_dir": markers_dir,
        "prediction_dir": prediction_dir,
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
