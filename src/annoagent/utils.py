from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STAGES = [
    "preflight",
    "annotate_parent",
    "annotate_subclusters",
    "pdfmarkers",
    "evaluate_optional",
    "report",
]

GPTANNO_TOOLS = [
    "preprocess_parent",
    "cluster_parent_markers",
    "annotate_parent_raw",
    "map_parent_ontology",
    "select_parent_resolution",
    "assign_parent_labels",
    "subcluster_find_markers",
    "subcluster_annotate_ontology",
    "subcluster_annotate_inheritance",
    "finalize_subcluster_annotations",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "project"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        tmp_path = Path(handle.name)
    tmp_path.replace(path)


def path_uri(path: str | Path | None) -> str | None:
    if not path:
        return None
    return Path(path).resolve().as_uri()


def stage_slice(from_stage: str | None, to_stage: str | None) -> list[str]:
    start = STAGES.index(from_stage) if from_stage else 0
    end = STAGES.index(to_stage) if to_stage else len(STAGES) - 1
    if start > end:
        raise ValueError(f"Invalid stage range: {from_stage} -> {to_stage}")
    return STAGES[start : end + 1]
