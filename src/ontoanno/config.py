from __future__ import annotations

import copy
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import yaml

from .policy import normalize_policy, validate_policy
from .utils import slugify


class ConfigError(RuntimeError):
    pass


REMOTE_PROVIDERS = {"openai", "anthropic", "gemini"}
_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")
ANNOTATION_STAGES = {"annotate_parent", "annotate_subclusters"}
EVALUATION_STAGES = {"evaluate_optional"}
PDF_STAGES = {"pdfmarkers"}
DEFAULT_RSCRIPT_CANDIDATES = [
    "/nas/longleaf/rhel9/apps/r/4.4.0/bin/Rscript",
    "/usr/bin/Rscript",
]


def _resolve_env_placeholders(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: os.getenv(match.group(1), ""), value)
    if isinstance(value, list):
        return [_resolve_env_placeholders(item) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_env_placeholders(item) for key, item in value.items()}
    return value


def _resolve_path(value: str | None, *, base_dir: Path) -> str | None:
    if value in (None, ""):
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return str(path)


def _require(config: dict[str, Any], dotted_key: str) -> Any:
    current: Any = config
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ConfigError(f"Missing required config key: {dotted_key}")
        current = current[part]
    return current


def _resolve_rscript() -> str:
    explicit = os.getenv("ONTOANNO_RSCRIPT")
    if explicit:
        return explicit
    discovered = shutil.which("Rscript")
    if discovered:
        return discovered
    for candidate in DEFAULT_RSCRIPT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return "Rscript"


def load_config(config_path: str | Path, repo_root: Path) -> dict[str, Any]:
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise ConfigError(f"Config not found: {path}")

    last_error: Exception | None = None
    raw: Any = None
    for _ in range(5):
        try:
            with path.open("r", encoding="utf-8") as handle:
                raw = yaml.safe_load(handle) or {}
            last_error = None
            break
        except (yaml.YAMLError, RecursionError) as exc:
            last_error = exc
            time.sleep(0.05)
    if last_error is not None:
        raise ConfigError(f"Failed to parse config {path}: {last_error}") from last_error
    if not isinstance(raw, dict):
        raise ConfigError("Top-level config must be a mapping")

    config = _resolve_env_placeholders(copy.deepcopy(raw))
    config["_meta"] = {
        "config_path": str(path),
        "config_dir": str(path.parent),
        "repo_root": str(repo_root),
        "project_slug": slugify(_require(config, "project.name")),
    }

    base_dir = path.parent
    config["project"]["work_dir"] = _resolve_path(_require(config, "project.work_dir"), base_dir=base_dir)

    inputs = config.setdefault("inputs", {})
    inputs["seurat_rds"] = _resolve_path(_require(config, "inputs.seurat_rds"), base_dir=base_dir)
    inputs["manual_labels_csv"] = _resolve_path(inputs.get("manual_labels_csv"), base_dir=base_dir)
    inputs["pdf_dir"] = _resolve_path(inputs.get("pdf_dir"), base_dir=base_dir)
    inputs["marker_genes_dir"] = _resolve_path(inputs.get("marker_genes_dir"), base_dir=base_dir)
    config["policy"] = normalize_policy(config.get("policy"))

    for baseline in config.get("evaluation", {}).get("baselines", []):
        baseline["metadata_csv"] = _resolve_path(baseline.get("metadata_csv"), base_dir=base_dir)
        baseline["cwd"] = _resolve_path(baseline.get("cwd"), base_dir=base_dir)
        baseline["expected_outputs"] = [
            _resolve_path(item, base_dir=base_dir) for item in baseline.get("expected_outputs", [])
        ]

    config["_runtime"] = {
        "gptanno_path": str((repo_root / "GPTAnno").resolve()),
        "pdf2markers_path": str((repo_root / "GPTAnno" / "PDF2markers").resolve()),
        "annotation_runner": str((repo_root / "scripts" / "run_annotation.R").resolve()),
        "gptanno_tool_runner": str((repo_root / "scripts" / "run_gptanno_tool.R").resolve()),
        "evaluation_runner": str((repo_root / "scripts" / "run_evaluation.R").resolve()),
        "review_packet_runner": str((repo_root / "scripts" / "export_parent_review_packets.R").resolve()),
        "ontology_obo": _resolve_path(
            os.getenv("ONTOANNO_CL_OBO"),
            base_dir=base_dir,
        ),
        "rscript": _resolve_rscript(),
        "python": shutil.which("python3") or shutil.which("python") or "python3",
    }
    return config


def validate_config(config: dict[str, Any], stages: list[str] | None = None) -> dict[str, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    selected = set(stages or [])
    validate_all = stages is None
    needs_annotation = validate_all or bool(selected & ANNOTATION_STAGES)
    needs_evaluation = validate_all or bool(selected & EVALUATION_STAGES)
    needs_pdf = bool(selected & PDF_STAGES) or (
        validate_all and bool(config.get("inputs", {}).get("pdf_dir"))
    )

    required_keys = [
        "project.name",
        "project.work_dir",
        "report.format",
    ]
    if needs_annotation:
        required_keys.extend(
            [
                "inputs.seurat_rds",
                "llm.annotation.provider",
                "llm.annotation.model",
                "annotation.parent_res",
                "annotation.sub_res",
                "annotation.preprocess",
                "annotation.min_cell_count",
                "annotation.tissue_name",
                "annotation.n_runs_parent",
                "annotation.n_runs_sub",
                "alignment.on_missing_decision",
            ]
        )
    if needs_evaluation:
        required_keys.extend(["evaluation.enabled", "evaluation.manual_col"])
    for key in required_keys:
        try:
            _require(config, key)
        except ConfigError as exc:
            errors.append(str(exc))

    if errors:
        return {"errors": errors, "warnings": warnings}

    if config["report"]["format"] not in {"html", "pdf"}:
        errors.append("report.format must be 'html' or 'pdf'")
    if needs_annotation and config["alignment"]["on_missing_decision"] != "stop":
        errors.append("alignment.on_missing_decision must be 'stop' for V1")

    policy_validation = validate_policy(config.get("policy"))
    errors.extend(policy_validation["errors"])
    warnings.extend(policy_validation["warnings"])

    if needs_annotation:
        for key in ("inputs.seurat_rds",):
            path = Path(_require(config, key))
            if not path.exists():
                errors.append(f"Missing required file: {path}")
        marker_genes_dir = config["inputs"].get("marker_genes_dir")
        if marker_genes_dir:
            marker_path = Path(marker_genes_dir)
            if not marker_path.exists() or not marker_path.is_dir():
                errors.append(f"Marker genes directory not found: {marker_path}")

    manual_labels_csv = config["inputs"].get("manual_labels_csv")
    if needs_evaluation and manual_labels_csv and not Path(manual_labels_csv).exists():
        errors.append(f"Manual labels file not found: {manual_labels_csv}")

    pdf_override_files = config.get("_runtime", {}).get("pdf_override_files", [])
    pdf_override_dir = config.get("_runtime", {}).get("pdf_override_dir")
    has_pdf_inputs = False
    if needs_pdf:
        if pdf_override_files:
            has_pdf_inputs = True
            for pdf in pdf_override_files:
                pdf_path = Path(pdf)
                if not pdf_path.exists():
                    errors.append(f"PDF input file not found: {pdf_path}")
                elif pdf_path.suffix.lower() != ".pdf":
                    errors.append(f"PDF input file must end with .pdf: {pdf_path}")
        elif pdf_override_dir:
            has_pdf_inputs = True
            pdf_path = Path(pdf_override_dir)
            if not pdf_path.exists():
                errors.append(f"PDF input directory not found: {pdf_path}")
        else:
            pdf_dir = config["inputs"].get("pdf_dir")
            if pdf_dir and not Path(pdf_dir).exists():
                errors.append(f"PDF input directory not found: {pdf_dir}")
                has_pdf_inputs = True
            elif pdf_dir:
                has_pdf_inputs = True
            elif not pdf_dir:
                warnings.append("No PDF inputs configured; pdfmarkers stage will be skipped.")

    gptanno_path = Path(config["_runtime"]["gptanno_path"])
    if not gptanno_path.exists():
        errors.append(f"Vendored GPTAnno package not found: {gptanno_path}")

    pdf2markers_path = Path(config["_runtime"]["pdf2markers_path"])
    if not pdf2markers_path.exists():
        errors.append(f"PDF2markers directory not found: {pdf2markers_path}")

    annotation_runner = Path(config["_runtime"]["annotation_runner"])
    gptanno_tool_runner = Path(config["_runtime"]["gptanno_tool_runner"])
    evaluation_runner = Path(config["_runtime"]["evaluation_runner"])
    review_packet_runner = Path(config["_runtime"]["review_packet_runner"])
    if not annotation_runner.exists():
        errors.append(f"Annotation runner not found: {annotation_runner}")
    if not gptanno_tool_runner.exists():
        errors.append(f"GPTAnno tool runner not found: {gptanno_tool_runner}")
    if not evaluation_runner.exists():
        errors.append(f"Evaluation runner not found: {evaluation_runner}")
    if not review_packet_runner.exists():
        errors.append(f"Review packet runner not found: {review_packet_runner}")

    ontology_obo = config["_runtime"].get("ontology_obo")
    if ontology_obo and not Path(ontology_obo).exists():
        errors.append(
            f"ONTOANNO_CL_OBO points to a missing file: {ontology_obo}"
        )

    if needs_annotation or needs_evaluation:
        rscript = shutil.which(config["_runtime"]["rscript"])
        if not rscript:
            errors.append(
                f"Rscript executable not found: {config['_runtime']['rscript']}. "
                "Set ONTOANNO_RSCRIPT or load R into PATH."
            )

    if needs_annotation:
        provider = config["llm"]["annotation"]["provider"]
        api_key = config["llm"]["annotation"].get("api_key")
        if provider in REMOTE_PROVIDERS and not api_key:
            errors.append(f"Missing API key for annotation provider '{provider}'")

    pdf_env = config["llm"].get("pdfmarkers", {}).get("env", {})
    if needs_pdf and has_pdf_inputs:
        if not isinstance(pdf_env, dict):
            errors.append("llm.pdfmarkers.env must be a mapping")
        elif not pdf_env.get("OPENAI_API_KEY"):
            errors.append("Missing OPENAI_API_KEY in llm.pdfmarkers.env for pdfmarkers stage")

    if needs_annotation:
        for res_key in ("parent_res", "sub_res"):
            values = config["annotation"].get(res_key)
            if not isinstance(values, list) or not values:
                errors.append(f"annotation.{res_key} must be a non-empty list")
                continue
            if not all(isinstance(item, (int, float)) for item in values):
                errors.append(f"annotation.{res_key} must contain only numeric values")

        celltypes_to_subcluster = config["alignment"].get("celltypes_to_subcluster")
        if celltypes_to_subcluster is not None and not isinstance(celltypes_to_subcluster, list):
            errors.append("alignment.celltypes_to_subcluster must be null or a list")

        user_restrict_to = config["alignment"].get("user_restrict_to")
        if user_restrict_to is not None and not isinstance(user_restrict_to, dict):
            errors.append("alignment.user_restrict_to must be a mapping or null")

        manual_resolution_map = config["alignment"].get("manual_resolution_map")
        if manual_resolution_map is not None and not isinstance(manual_resolution_map, dict):
            errors.append("alignment.manual_resolution_map must be a mapping or null")

        species = config["annotation"].get("species")
        if not species:
            warnings.append(
                "annotation.species is not set; reference databases will fall back to tissue_name inference or be disabled."
            )

    if needs_evaluation:
        baselines = config.get("evaluation", {}).get("baselines", [])
        if not isinstance(baselines, list):
            errors.append("evaluation.baselines must be a list")
        else:
            for index, baseline in enumerate(baselines):
                if not isinstance(baseline, dict):
                    errors.append(f"evaluation.baselines[{index}] must be a mapping")
                    continue
                if baseline.get("enabled") and baseline.get("command") and not baseline.get("expected_outputs"):
                    warnings.append(
                        f"Baseline '{baseline.get('name', index)}' has command but no expected_outputs; "
                        "hook execution will be best-effort only."
                    )

    return {"errors": errors, "warnings": warnings}
