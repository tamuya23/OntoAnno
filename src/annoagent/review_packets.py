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
    parent_outputs = manifest.get("outputs", {}).get("annotate_parent", {})
    if not parent_outputs:
        raise RuntimeError("annotate_parent outputs not found; run parent annotation first.")

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
            "best_resolution": parent_outputs.get("best_resolution"),
            "cluster_col": parent_outputs.get("cluster_col"),
        },
        "inputs": {
            "annotation_parent_rds": parent_outputs.get("annotation_parent_rds"),
            "parent_seurat_rds": parent_outputs.get("parent_seurat_rds"),
            "annotation_scores_csv": parent_outputs.get("annotation_scores_csv"),
            "markers_dir": parent_outputs.get("markers_dir"),
            "prediction_dir": parent_outputs.get("prediction_dir"),
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
