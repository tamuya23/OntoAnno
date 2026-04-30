from __future__ import annotations

import os
import copy
import shlex
import shutil
import subprocess
import traceback
from pathlib import Path
from typing import Any, Callable

from .config import ConfigError, validate_config
from .controller import build_controller
from .llm_compare import build_llm_compare
from .ontology_relations import build_ontology_relations
from .review_packets import build_review_packets
from .reporting import generate_report
from .utils import STAGES, dump_json, ensure_dir, load_json, path_uri, slugify, stage_slice, utc_now


class StageError(RuntimeError):
    pass


GPTANNO_TOOL_OUTPUT_STAGES = (
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
)


class Orchestrator:
    def __init__(self, repo_root: Path, config: dict[str, Any]) -> None:
        self.repo_root = repo_root
        self.config = config
        self.project_name = config["project"]["name"]
        self.project_slug = slugify(self.project_name)
        self.work_dir = ensure_dir(Path(config["project"]["work_dir"]))
        self.runs_root = ensure_dir(repo_root / "runs")
        self.pointer_path = self.runs_root / f"{self.project_slug}_latest.json"
        self.run_id, self.run_dir = self._ensure_active_run()
        self.logs_dir = ensure_dir(self.run_dir / "logs")
        self.specs_dir = ensure_dir(self.run_dir / "specs")
        self.state_path = self.run_dir / "state.json"
        self.manifest_path = self.run_dir / "manifest.json"
        self.active_stages = list(STAGES)
        self.state = self._load_state()
        self.manifest = self._load_manifest()
        self._hydrate_manifest_outputs_from_disk()

    def _emit(self, message: str) -> None:
        print(f"[OntoAnno] {message}", flush=True)

    def _ensure_active_run(self) -> tuple[str, Path]:
        if self.pointer_path.exists():
            pointer = load_json(self.pointer_path)
            run_id = pointer["run_id"]
            run_dir = self.runs_root / run_id
            if run_dir.exists():
                return run_id, run_dir
        run_id = f"{self.project_slug}-{utc_now().replace(':', '').replace('-', '')}"
        run_dir = ensure_dir(self.runs_root / run_id)
        dump_json(self.pointer_path, {"project": self.project_name, "run_id": run_id, "run_dir": str(run_dir)})
        return run_id, run_dir

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            return load_json(self.state_path)
        state = {
            "project_name": self.project_name,
            "run_id": self.run_id,
            "config_path": self.config["_meta"]["config_path"],
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "stages": {stage: {"status": "pending"} for stage in STAGES},
        }
        dump_json(self.state_path, state)
        return state

    def _load_manifest(self) -> dict[str, Any]:
        if self.manifest_path.exists():
            manifest = load_json(self.manifest_path)
            outputs = manifest.get("outputs", {})
            if "decision_drafts" in outputs:
                outputs.pop("decision_drafts", None)
                manifest["outputs"] = outputs
                dump_json(self.manifest_path, manifest)
            return manifest
        manifest = {
            "project_name": self.project_name,
            "run_id": self.run_id,
            "work_dir": str(self.work_dir),
            "outputs": {},
            "updated_at": utc_now(),
        }
        dump_json(self.manifest_path, manifest)
        return manifest

    def _save_state(self) -> None:
        self.state["updated_at"] = utc_now()
        dump_json(self.state_path, self.state)

    def _save_manifest(self) -> None:
        self.manifest["updated_at"] = utc_now()
        dump_json(self.manifest_path, self.manifest)

    def _stage_outputs_path(self, stage: str) -> Path | None:
        stage_output_files = {
            "review_packets": self.run_dir / "review_packets" / "parent_review_packets.outputs.json",
            "ontology_relations": self.run_dir / "ontology_relations" / "ontology_relations.outputs.json",
            "llm_compare": self.run_dir / "llm_compare" / "llm_compare.outputs.json",
            "controller": self.run_dir / "controller" / "controller.outputs.json",
            "reviewed_parent": self.run_dir / "reviewed_parent" / "reviewed_parent.outputs.json",
        }
        return stage_output_files.get(stage)

    def _load_stage_outputs_from_disk(self, stage: str) -> dict[str, Any] | None:
        outputs_path = self._stage_outputs_path(stage)
        if outputs_path is None or not outputs_path.exists():
            return None
        try:
            payload = load_json(outputs_path)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _load_gptanno_tool_outputs_from_disk(self, tool: str) -> dict[str, Any] | None:
        outputs_path = self.specs_dir / f"gptanno-tool-{tool}.outputs.json"
        if not outputs_path.exists():
            return None
        try:
            payload = load_json(outputs_path)
        except Exception:
            return None
        if not isinstance(payload, dict) or not payload:
            return None
        log_path = self.logs_dir / f"gptanno-tool-{tool}.log"
        if log_path.exists():
            payload.setdefault("log", str(log_path))
        return payload if self._outputs_artifacts_exist(payload) else None

    def _hydrate_manifest_outputs_from_disk(self) -> bool:
        outputs = self.manifest.setdefault("outputs", {})
        if not isinstance(outputs, dict):
            outputs = {}
            self.manifest["outputs"] = outputs

        updated = False
        gptanno_outputs = outputs.setdefault("gptanno_tools", {})
        if not isinstance(gptanno_outputs, dict):
            gptanno_outputs = {}
            outputs["gptanno_tools"] = gptanno_outputs

        for tool in GPTANNO_TOOL_OUTPUT_STAGES:
            current = gptanno_outputs.get(tool)
            if isinstance(current, dict) and current and self._outputs_artifacts_exist(current):
                continue
            disk_outputs = self._load_gptanno_tool_outputs_from_disk(tool)
            if isinstance(disk_outputs, dict) and disk_outputs:
                gptanno_outputs[tool] = disk_outputs
                updated = True

        for stage in ("review_packets", "ontology_relations", "llm_compare", "controller", "reviewed_parent"):
            current = outputs.get(stage)
            if isinstance(current, dict) and current:
                continue
            disk_outputs = self._load_stage_outputs_from_disk(stage)
            if isinstance(disk_outputs, dict) and disk_outputs:
                outputs[stage] = disk_outputs
                updated = True

        if updated:
            self._save_manifest()
        return updated

    def _record_stage_outputs(self, stage: str, outputs: dict[str, Any]) -> None:
        self.state["stages"][stage]["outputs"] = outputs
        self.manifest["outputs"][stage] = outputs
        self._save_state()
        self._save_manifest()

    def _remove_recorded_outputs(self, stages: tuple[str, ...], paths: tuple[Path, ...] = ()) -> None:
        for path in paths:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()

        outputs = self.manifest.get("outputs")
        if isinstance(outputs, dict):
            for stage in stages:
                outputs.pop(stage, None)
            gptanno_tools = outputs.get("gptanno_tools")
            if isinstance(gptanno_tools, dict):
                for stage in stages:
                    gptanno_tools.pop(stage, None)
        for stage in stages:
            if stage in self.state.get("stages", {}):
                self.state["stages"][stage] = {"status": "pending"}
        self._save_state()
        self._save_manifest()

    def _remove_subcluster_annotation_dirs(self) -> None:
        subcluster_dir = self.work_dir / "annotate_subclusters"
        if not subcluster_dir.exists():
            return
        for path in subcluster_dir.glob("subclusters_res*/*/annotation_ontology"):
            if path.is_dir():
                shutil.rmtree(path)
        for path in subcluster_dir.glob("subclusters_res*/*/annotation_marker_inheritance"):
            if path.is_dir():
                shutil.rmtree(path)

    def clear_report_outputs(self) -> None:
        self._remove_recorded_outputs(
            ("report",),
            (
                self.run_dir / "report_assets",
                self.run_dir / "report.html",
                self.run_dir / "report.pdf",
            ),
        )

    def clear_rag_dependent_outputs(self) -> None:
        self._remove_recorded_outputs(
            ("ontology_relations", "llm_compare", "controller", "reviewed_parent"),
            tuple(self.run_dir / stage for stage in ("ontology_relations", "llm_compare", "controller", "reviewed_parent")),
        )
        self.clear_report_outputs()

    def clear_after_ontology_outputs(self) -> None:
        self._remove_recorded_outputs(
            ("llm_compare", "controller", "reviewed_parent"),
            tuple(self.run_dir / stage for stage in ("llm_compare", "controller", "reviewed_parent")),
        )
        self.clear_report_outputs()

    def clear_after_llm_outputs(self) -> None:
        self._remove_recorded_outputs(
            ("controller", "reviewed_parent"),
            tuple(self.run_dir / stage for stage in ("controller", "reviewed_parent")),
        )
        self.clear_report_outputs()

    def clear_after_controller_outputs(self) -> None:
        self._remove_recorded_outputs(("reviewed_parent",), (self.run_dir / "reviewed_parent",))
        self.clear_report_outputs()

    def clear_subcluster_outputs(self) -> None:
        subcluster_workers = (
            "annotate_subclusters",
            "subcluster_find_markers",
            "subcluster_annotate_ontology",
            "subcluster_annotate_inheritance",
            "finalize_subcluster_annotations",
        )
        self._remove_recorded_outputs(subcluster_workers, (self.work_dir / "annotate_subclusters",))

    def clear_after_subcluster_find_markers_outputs(self) -> None:
        subcluster_dir = self.work_dir / "annotate_subclusters"
        self._remove_subcluster_annotation_dirs()
        self._remove_recorded_outputs(
            (
                "annotate_subclusters",
                "subcluster_annotate_ontology",
                "subcluster_annotate_inheritance",
                "finalize_subcluster_annotations",
            ),
            (
                subcluster_dir / "ontology_workflow.rds",
                subcluster_dir / "marker_inheritance_workflow.rds",
                subcluster_dir / "seurat_ontology_annotated.rds",
                subcluster_dir / "seurat_final_annotated.rds",
                subcluster_dir / "metadata_final.csv",
                subcluster_dir / "DimPlot_celltype_final.pdf",
            ),
        )
        self.clear_report_outputs()

    def clear_after_subcluster_annotation_outputs(self) -> None:
        subcluster_dir = self.work_dir / "annotate_subclusters"
        self._remove_recorded_outputs(
            ("annotate_subclusters", "finalize_subcluster_annotations"),
            (
                subcluster_dir / "seurat_final_annotated.rds",
                subcluster_dir / "metadata_final.csv",
                subcluster_dir / "DimPlot_celltype_final.pdf",
            ),
        )
        self.clear_report_outputs()

    def clear_gptanno_tool_outputs_for_rerun(self, tool: str) -> None:
        parent_dir = self.work_dir / "annotate_parent"
        subcluster_dir = self.work_dir / "annotate_subclusters"
        parent_paths = {
            "preprocessed": parent_dir / "seurat_preprocessed.rds",
            "clustered": parent_dir / "seurat_clustered.rds",
            "markers": parent_dir / "marker_genes",
            "prediction": parent_dir / "prediction",
            "annotation": parent_dir / "annotation_parent.rds",
            "scores": parent_dir / "annotation_summary_scores.csv",
            "ontology_rds": parent_dir / "parent_ontology_mapping.rds",
            "ontology_csv": parent_dir / "parent_ontology_mapping.csv",
            "best_resolution": parent_dir / "best_parent_resolution.json",
            "assigned": parent_dir / "seurat_parent_annotated.rds",
        }
        subcluster_paths = {
            "root": subcluster_dir,
            "ontology_workflow": subcluster_dir / "ontology_workflow.rds",
            "ontology_seurat": subcluster_dir / "seurat_ontology_annotated.rds",
            "inheritance_workflow": subcluster_dir / "marker_inheritance_workflow.rds",
            "final_seurat": subcluster_dir / "seurat_final_annotated.rds",
            "final_metadata": subcluster_dir / "metadata_final.csv",
            "final_dimplot": subcluster_dir / "DimPlot_celltype_final.pdf",
        }
        plans: dict[str, tuple[tuple[str, ...], tuple[Path, ...]]] = {
            "preprocess_parent": (
                (
                    "preprocess_parent",
                    "cluster_parent_markers",
                    "annotate_parent_raw",
                    "map_parent_ontology",
                    "select_parent_resolution",
                    "assign_parent_labels",
                    "annotate_parent",
                ),
                tuple(parent_paths.values()),
            ),
            "cluster_parent_markers": (
                (
                    "cluster_parent_markers",
                    "annotate_parent_raw",
                    "map_parent_ontology",
                    "select_parent_resolution",
                    "assign_parent_labels",
                    "annotate_parent",
                ),
                tuple(parent_paths[key] for key in parent_paths if key != "preprocessed"),
            ),
            "annotate_parent_raw": (
                (
                    "annotate_parent_raw",
                    "map_parent_ontology",
                    "select_parent_resolution",
                    "assign_parent_labels",
                    "annotate_parent",
                ),
                tuple(
                    parent_paths[key]
                    for key in (
                        "prediction",
                        "annotation",
                        "scores",
                        "ontology_rds",
                        "ontology_csv",
                        "best_resolution",
                        "assigned",
                    )
                ),
            ),
            "map_parent_ontology": (
                ("map_parent_ontology",),
                (parent_paths["ontology_rds"], parent_paths["ontology_csv"]),
            ),
            "select_parent_resolution": (
                ("select_parent_resolution", "assign_parent_labels", "annotate_parent"),
                (parent_paths["scores"], parent_paths["best_resolution"], parent_paths["assigned"]),
            ),
            "assign_parent_labels": (
                ("assign_parent_labels", "annotate_parent"),
                (parent_paths["assigned"],),
            ),
            "subcluster_find_markers": (
                (
                    "subcluster_find_markers",
                    "subcluster_annotate_ontology",
                    "subcluster_annotate_inheritance",
                    "finalize_subcluster_annotations",
                    "annotate_subclusters",
                ),
                (subcluster_paths["root"],),
            ),
            "subcluster_annotate_ontology": (
                ("subcluster_annotate_ontology", "finalize_subcluster_annotations", "annotate_subclusters"),
                (
                    subcluster_paths["ontology_workflow"],
                    subcluster_paths["ontology_seurat"],
                    subcluster_paths["final_seurat"],
                    subcluster_paths["final_metadata"],
                    subcluster_paths["final_dimplot"],
                ),
            ),
            "subcluster_annotate_inheritance": (
                ("subcluster_annotate_inheritance", "finalize_subcluster_annotations", "annotate_subclusters"),
                (
                    subcluster_paths["inheritance_workflow"],
                    subcluster_paths["final_seurat"],
                    subcluster_paths["final_metadata"],
                    subcluster_paths["final_dimplot"],
                ),
            ),
            "finalize_subcluster_annotations": (
                ("finalize_subcluster_annotations", "annotate_subclusters"),
                (
                    subcluster_paths["final_seurat"],
                    subcluster_paths["final_metadata"],
                    subcluster_paths["final_dimplot"],
                ),
            ),
        }
        plan = plans.get(tool)
        if plan is not None:
            self._remove_recorded_outputs(*plan)

    def clear_after_parent_tool_outputs(self, tool: str) -> None:
        parent_dir = self.work_dir / "annotate_parent"
        prediction_dir = parent_dir / "prediction"
        if tool == "preprocess_parent":
            self._remove_recorded_outputs(
                (
                    "annotate_parent",
                    "cluster_parent_markers",
                    "annotate_parent_raw",
                    "map_parent_ontology",
                    "select_parent_resolution",
                    "assign_parent_labels",
                ),
                (
                    parent_dir / "seurat_clustered.rds",
                    parent_dir / "marker_genes",
                    prediction_dir,
                    parent_dir / "annotation_parent.rds",
                    parent_dir / "annotation_summary_scores.csv",
                    parent_dir / "parent_ontology_mapping.rds",
                    parent_dir / "parent_ontology_mapping.csv",
                    parent_dir / "best_parent_resolution.json",
                    parent_dir / "seurat_parent_annotated.rds",
                ),
            )
        elif tool == "cluster_parent_markers":
            self._remove_recorded_outputs(
                (
                    "annotate_parent",
                    "annotate_parent_raw",
                    "map_parent_ontology",
                    "select_parent_resolution",
                    "assign_parent_labels",
                ),
                (
                    prediction_dir,
                    parent_dir / "annotation_parent.rds",
                    parent_dir / "annotation_summary_scores.csv",
                    parent_dir / "parent_ontology_mapping.rds",
                    parent_dir / "parent_ontology_mapping.csv",
                    parent_dir / "best_parent_resolution.json",
                    parent_dir / "seurat_parent_annotated.rds",
                ),
            )
        elif tool == "annotate_parent_raw":
            self._remove_recorded_outputs(
                (
                    "annotate_parent",
                    "map_parent_ontology",
                    "select_parent_resolution",
                    "assign_parent_labels",
                ),
                (
                    parent_dir / "annotation_summary_scores.csv",
                    parent_dir / "parent_ontology_mapping.rds",
                    parent_dir / "parent_ontology_mapping.csv",
                    parent_dir / "best_parent_resolution.json",
                    parent_dir / "seurat_parent_annotated.rds",
                ),
            )
        elif tool == "select_parent_resolution":
            self._remove_recorded_outputs(
                ("annotate_parent", "assign_parent_labels"),
                (parent_dir / "seurat_parent_annotated.rds",),
            )
        elif tool == "assign_parent_labels":
            self._remove_recorded_outputs(("annotate_parent",), ())

        self.clear_parent_dependent_outputs()

    def clear_parent_dependent_outputs(self) -> None:
        self.clear_subcluster_outputs()
        self.clear_rag_dependent_outputs()

    def _outputs_artifacts_exist(self, outputs: dict[str, Any]) -> bool:
        path_suffixes = (
            "_rds",
            "_csv",
            "_json",
            "_pdf",
            "_png",
            "_html",
            "_txt",
            "_dir",
            "_folder",
        )
        for key, value in outputs.items():
            if value in (None, ""):
                continue
            if not isinstance(value, str):
                continue
            if key == "log":
                continue
            if key.endswith(path_suffixes) and not Path(value).exists():
                return False
        return True

    def _skip_stage(self, stage: str, message: str) -> None:
        self.state["stages"][stage].update(
            {"status": "skipped", "message": message, "completed_at": utc_now()}
        )
        self._save_state()
        self._emit(f"Stage '{stage}' skipped: {message}")

    def _run_subprocess(
        self,
        command: list[str],
        *,
        log_path: Path,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        ensure_dir(log_path.parent)
        merged_env = os.environ.copy()
        if env:
            merged_env.update({key: value for key, value in env.items() if value not in (None, "")})
        with log_path.open("a", encoding="utf-8") as log_handle:
            log_handle.write(f"$ {' '.join(shlex.quote(part) for part in command)}\n")
            process = subprocess.run(
                command,
                cwd=cwd,
                env=merged_env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        if process.returncode != 0:
            raise StageError(f"Command failed with exit code {process.returncode}: {' '.join(command)}")

    def _run_shell_hook(self, command: str | list[str], *, log_path: Path, cwd: str | None = None) -> None:
        if isinstance(command, str):
            cmd = ["/usr/bin/bash", "-lc", command]
        else:
            cmd = list(command)
        self._run_subprocess(cmd, log_path=log_path, cwd=cwd)

    def validate(self, stages: list[str] | None = None) -> dict[str, list[str]]:
        return validate_config(self.config, stages=stages)

    def run(
        self,
        *,
        from_stage: str | None = None,
        to_stage: str | None = None,
        force: bool = False,
        validation_stages: list[str] | None = None,
    ) -> Path:
        selected = stage_slice(from_stage, to_stage)
        self.active_stages = validation_stages or selected
        stage_map: dict[str, Callable[[bool], dict[str, Any] | None]] = {
            "preflight": self._stage_preflight,
            "annotate_parent": self._stage_annotate_parent,
            "annotate_subclusters": self._stage_annotate_subclusters,
            "pdfmarkers": self._stage_pdfmarkers,
            "evaluate_optional": self._stage_evaluate_optional,
            "report": self._stage_report,
        }

        for stage in selected:
            current = self.state["stages"][stage]
            if current.get("status") == "completed" and not force:
                self._emit(f"Stage '{stage}' already completed; skipping.")
                continue

            self._emit(f"Stage '{stage}' started.")
            self.state["stages"][stage].update(
                {
                    "status": "running",
                    "started_at": utc_now(),
                    "message": None,
                    "error": None,
                }
            )
            self._save_state()

            try:
                outputs = stage_map[stage](force) or {}
                self.state["stages"][stage].update(
                    {"status": "completed", "completed_at": utc_now(), "message": "ok"}
                )
                self._record_stage_outputs(stage, outputs)
                self._emit(f"Stage '{stage}' completed.")
            except Exception as exc:  # noqa: BLE001
                error_log = self.logs_dir / f"{stage}.error.log"
                ensure_dir(error_log.parent)
                error_log.write_text(traceback.format_exc(), encoding="utf-8")
                self.state["stages"][stage].update(
                    {
                        "status": "failed",
                        "completed_at": utc_now(),
                        "error": str(exc),
                        "error_log": str(error_log),
                        "message": "failed",
                    }
                )
                self._save_state()
                self._emit(f"Stage '{stage}' failed: {exc}")
                raise

        return self.run_dir

    def _annotation_stage_spec(self, stage: str) -> tuple[Path, Path]:
        spec_path = self.specs_dir / f"{stage}.json"
        outputs_json = self.specs_dir / f"{stage}.outputs.json"
        return spec_path, outputs_json

    def _gptanno_tool_spec(self, tool: str) -> tuple[Path, Path]:
        spec_path = self.specs_dir / f"gptanno-tool-{tool}.json"
        outputs_json = self.specs_dir / f"gptanno-tool-{tool}.outputs.json"
        return spec_path, outputs_json

    def _redacted_llm_config(self) -> dict[str, Any]:
        llm = copy.deepcopy(self.config.get("llm", {}))
        for section in llm.values():
            if not isinstance(section, dict):
                continue
            section["api_key"] = None
            env = section.get("env")
            if isinstance(env, dict):
                for key in list(env):
                    if "KEY" in str(key).upper() or "TOKEN" in str(key).upper() or "PASSWORD" in str(key).upper():
                        env[key] = None
        return llm

    def _llm_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        for section in (self.config.get("llm", {}) or {}).values():
            if not isinstance(section, dict):
                continue
            api_key = section.get("api_key")
            if api_key and not env.get("OPENAI_API_KEY"):
                env["OPENAI_API_KEY"] = str(api_key)
            section_env = section.get("env")
            if isinstance(section_env, dict):
                for key, value in section_env.items():
                    if value not in (None, ""):
                        env[str(key)] = str(value)
        return env

    def _base_annotation_spec(self, *, stage: str, outputs_json: Path) -> dict[str, Any]:
        return {
            "stage": stage,
            "project_name": self.project_name,
            "gptanno_path": self.config["_runtime"]["gptanno_path"],
            "work_dir": str(self.work_dir),
            "inputs": self.config["inputs"],
            "llm": self._redacted_llm_config(),
            "annotation": self.config["annotation"],
            "alignment": self.config["alignment"],
            "runtime": {
                "ontology_obo": self.config.get("_runtime", {}).get("ontology_obo"),
            },
            "outputs_json": str(outputs_json),
        }

    def _run_gptanno_tool(self, tool: str) -> dict[str, Any]:
        spec_path, outputs_json = self._gptanno_tool_spec(tool)
        spec = self._base_annotation_spec(stage=tool, outputs_json=outputs_json)
        dump_json(spec_path, spec)
        log_path = self.logs_dir / f"gptanno-tool-{tool}.log"
        self._run_subprocess(
            [self.config["_runtime"]["rscript"], self.config["_runtime"]["gptanno_tool_runner"], str(spec_path)],
            log_path=log_path,
            env=self._llm_env(),
        )
        outputs = load_json(outputs_json)
        outputs["log"] = str(log_path)
        return outputs

    def _stage_preflight(self, force: bool) -> dict[str, Any]:
        del force
        validation = self.validate(stages=self.active_stages)
        if validation["errors"]:
            raise ConfigError("; ".join(validation["errors"]))
        return {
            "config_path": self.config["_meta"]["config_path"],
            "work_dir": str(self.work_dir),
            "gptanno_path": self.config["_runtime"]["gptanno_path"],
            "annotation_runner": self.config["_runtime"]["annotation_runner"],
            "evaluation_runner": self.config["_runtime"]["evaluation_runner"],
            "warnings": validation["warnings"],
        }

    def _stage_annotate_parent(self, force: bool) -> dict[str, Any]:
        if force:
            self.clear_gptanno_tool_outputs_for_rerun("preprocess_parent")
        spec_path, outputs_json = self._annotation_stage_spec("annotate_parent")
        spec = self._base_annotation_spec(stage="annotate_parent", outputs_json=outputs_json)
        dump_json(spec_path, spec)
        log_path = self.logs_dir / "annotate_parent.log"
        self._run_subprocess(
            [self.config["_runtime"]["rscript"], self.config["_runtime"]["annotation_runner"], str(spec_path)],
            log_path=log_path,
            env=self._llm_env(),
        )
        outputs = load_json(outputs_json)
        outputs["log"] = str(log_path)
        return outputs

    def _stage_annotate_subclusters(self, force: bool) -> dict[str, Any]:
        if self.state["stages"]["annotate_parent"].get("status") != "completed":
            raise StageError("annotate_parent must complete before annotate_subclusters")
        if force:
            self.clear_gptanno_tool_outputs_for_rerun("subcluster_find_markers")
        spec_path, outputs_json = self._annotation_stage_spec("annotate_subclusters")
        spec = self._base_annotation_spec(stage="annotate_subclusters", outputs_json=outputs_json)
        dump_json(spec_path, spec)
        log_path = self.logs_dir / "annotate_subclusters.log"
        self._run_subprocess(
            [self.config["_runtime"]["rscript"], self.config["_runtime"]["annotation_runner"], str(spec_path)],
            log_path=log_path,
            env=self._llm_env(),
        )
        outputs = load_json(outputs_json)
        outputs["log"] = str(log_path)
        return outputs

    def _resolve_pdf_list(self) -> list[Path]:
        override_files = self.config["_runtime"].get("pdf_override_files", [])
        if override_files:
            return [Path(path).resolve() for path in override_files]

        override_dir = self.config["_runtime"].get("pdf_override_dir")
        if override_dir:
            root = Path(override_dir)
            return sorted(path for path in root.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")

        pdf_dir = self.config["inputs"].get("pdf_dir")
        if not pdf_dir:
            return []
        root = Path(pdf_dir)
        return sorted(path for path in root.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")

    def _stage_pdfmarkers(self, force: bool) -> dict[str, Any]:
        del force
        pdfs = self._resolve_pdf_list()
        if not pdfs:
            self._skip_stage("pdfmarkers", "no PDF inputs configured")
            return {"documents": []}

        pdf_dir = ensure_dir(self.work_dir / "pdfmarkers")
        log_path = self.logs_dir / "pdfmarkers.log"
        extraction_script = Path(self.config["_runtime"]["pdf2markers_path"]) / "paper_extraction_cellNgenes.py"
        filter_script = Path(self.config["_runtime"]["pdf2markers_path"]) / "filterout_cell_ontology.py"
        ontology_csv = Path(self.config["_runtime"]["pdf2markers_path"]) / "cell_ontology" / "GPTCelltype_mapping.csv"
        env = {
            key: str(value)
            for key, value in self.config["llm"].get("pdfmarkers", {}).get("env", {}).items()
            if value not in (None, "")
        }
        model = self.config["llm"].get("pdfmarkers", {}).get("model", "gpt-5-nano")

        documents = []
        for pdf in pdfs:
            pdf_output_dir = ensure_dir(pdf_dir / pdf.stem)
            self._run_subprocess(
                [
                    self.config["_runtime"]["python"],
                    str(extraction_script),
                    "--pdf",
                    str(pdf),
                    "--out",
                    str(pdf_output_dir),
                    "--model",
                    str(model),
                ],
                log_path=log_path,
                env=env,
            )
            self._run_subprocess(
                [
                    self.config["_runtime"]["python"],
                    str(filter_script),
                    "--input-dir",
                    str(pdf_output_dir),
                    "--ontology-csv",
                    str(ontology_csv),
                ],
                log_path=log_path,
            )

            final_csv = next(pdf_output_dir.glob("3-final-*.csv"), None)
            filtered_csv = next(pdf_output_dir.glob("4-final-filtered-*.csv"), None)
            runtime_json = pdf_output_dir / "runtime.json"
            documents.append(
                {
                    "name": pdf.name,
                    "source_pdf": str(pdf),
                    "output_dir": str(pdf_output_dir),
                    "final_csv": str(final_csv) if final_csv else None,
                    "filtered_csv": str(filtered_csv) if filtered_csv else None,
                    "runtime_json": str(runtime_json) if runtime_json.exists() else None,
                }
            )

        return {"documents": documents, "log": str(log_path)}

    def _run_baseline_hooks(self) -> list[dict[str, Any]]:
        results = []
        for baseline in self.config.get("evaluation", {}).get("baselines", []):
            if not baseline.get("enabled", False):
                continue
            name = baseline.get("name", "baseline")
            log_path = self.logs_dir / f"baseline-{slugify(name)}.log"
            status = "skipped"
            error = None
            if baseline.get("command"):
                try:
                    self._run_shell_hook(baseline["command"], log_path=log_path, cwd=baseline.get("cwd"))
                    status = "completed"
                except Exception as exc:  # noqa: BLE001
                    status = "failed"
                    error = str(exc)
            outputs_ok = True
            expected_outputs = baseline.get("expected_outputs", [])
            if expected_outputs:
                outputs_ok = all(Path(path).exists() for path in expected_outputs)
                if status == "completed" and not outputs_ok:
                    status = "failed"
                    error = "Expected outputs missing after hook execution"
            results.append(
                {
                    "name": name,
                    "status": status,
                    "error": error,
                    "expected_outputs": expected_outputs,
                    "metadata_csv": baseline.get("metadata_csv"),
                    "prediction_column": baseline.get("prediction_column"),
                    "rename_to": baseline.get("rename_to"),
                    "join_key": baseline.get("join_key"),
                    "log": str(log_path),
                }
            )
        return results

    def _stage_evaluate_optional(self, force: bool) -> dict[str, Any]:
        del force
        if not self.config["evaluation"]["enabled"]:
            self._skip_stage("evaluate_optional", "evaluation disabled in config")
            return {}
        final_object = self.manifest.get("outputs", {}).get("annotate_subclusters", {}).get("final_seurat_rds")
        if not final_object:
            self._skip_stage("evaluate_optional", "no final annotation object available")
            return {}

        baseline_results = self._run_baseline_hooks()
        spec_path, outputs_json = self._annotation_stage_spec("evaluate_optional")
        spec = {
            "stage": "evaluate_optional",
            "project_name": self.project_name,
            "gptanno_path": self.config["_runtime"]["gptanno_path"],
            "work_dir": str(self.work_dir),
            "final_seurat_rds": final_object,
            "inputs": self.config["inputs"],
            "evaluation": self.config["evaluation"],
            "baseline_results": baseline_results,
            "runtime": {
                "ontology_obo": self.config.get("_runtime", {}).get("ontology_obo"),
            },
            "outputs_json": str(outputs_json),
        }
        dump_json(spec_path, spec)
        log_path = self.logs_dir / "evaluate_optional.log"
        self._run_subprocess(
            [self.config["_runtime"]["rscript"], self.config["_runtime"]["evaluation_runner"], str(spec_path)],
            log_path=log_path,
            env=self._llm_env(),
        )
        outputs = load_json(outputs_json)
        outputs["baseline_results"] = baseline_results
        outputs["log"] = str(log_path)
        return outputs

    def _stage_report(self, force: bool) -> dict[str, Any]:
        del force
        report_format = str(self.config.get("report", {}).get("format") or "html").lower()
        suffix = ".pdf" if report_format == "pdf" else ".html"
        report_path = self.run_dir / f"report{suffix}"
        self._hydrate_manifest_outputs_from_disk()
        generate_report(self.config, self.state, self.manifest, report_path)
        outputs = {
            "report_path": str(report_path),
            "report_uri": path_uri(report_path),
            "report_format": report_format,
        }
        if report_format == "pdf":
            outputs["report_pdf"] = str(report_path)
        else:
            outputs["report_html"] = str(report_path)
        return outputs

    def generate_review_packets(self, *, force: bool = False) -> dict[str, Any]:
        outputs = build_review_packets(
            config=self.config,
            state=self.state,
            manifest=self.manifest,
            run_dir=self.run_dir,
            repo_root=self.repo_root,
            force=force,
        )
        self.manifest["outputs"]["review_packets"] = outputs
        self._save_manifest()
        if force:
            self.clear_rag_dependent_outputs()
        return outputs

    def generate_ontology_relations(
        self,
        *,
        force: bool = False,
        cluster_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        outputs = build_ontology_relations(
            config=self.config,
            run_dir=self.run_dir,
            repo_root=self.repo_root,
            force=force,
            cluster_ids=cluster_ids,
        )
        self.manifest["outputs"]["ontology_relations"] = outputs
        self._save_manifest()
        if force:
            self.clear_after_ontology_outputs()
        return outputs

    def generate_llm_compare(
        self,
        *,
        force: bool = False,
        cluster_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        if "ontology_relations" not in self.manifest.get("outputs", {}):
            self.generate_ontology_relations(force=False)
        outputs = build_llm_compare(
            config=self.config,
            run_dir=self.run_dir,
            force=force,
            cluster_ids=cluster_ids,
        )
        self.manifest["outputs"]["llm_compare"] = outputs
        self._save_manifest()
        if force:
            self.clear_after_llm_outputs()
        return outputs

    def generate_controller(
        self,
        *,
        force: bool = False,
        phase: str = "auto",
    ) -> dict[str, Any]:
        outputs = build_controller(
            config=self.config,
            run_dir=self.run_dir,
            force=force,
            phase=phase,
        )
        self.manifest["outputs"]["controller"] = outputs
        self._save_manifest()
        if force:
            self.clear_after_controller_outputs()
        return outputs

    def generate_gptanno_tool(
        self,
        tool: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        outputs_root = self.manifest.setdefault("outputs", {})
        gptanno_outputs = outputs_root.setdefault("gptanno_tools", {})
        if not force and tool in gptanno_outputs:
            cached_outputs = gptanno_outputs[tool]
            if isinstance(cached_outputs, dict) and self._outputs_artifacts_exist(cached_outputs):
                self._emit(f"GPTAnno tool '{tool}' already completed; reusing recorded outputs.")
                return cached_outputs
            self._emit(f"GPTAnno tool '{tool}' recorded outputs are stale; rerunning.")
        if force:
            self.clear_gptanno_tool_outputs_for_rerun(tool)
        outputs = self._run_gptanno_tool(tool)
        gptanno_outputs[tool] = outputs
        self._save_manifest()
        if force:
            if tool in {
                "preprocess_parent",
                "cluster_parent_markers",
                "annotate_parent_raw",
                "select_parent_resolution",
                "assign_parent_labels",
            }:
                self.clear_after_parent_tool_outputs(tool)
            elif tool == "map_parent_ontology":
                self.clear_rag_dependent_outputs()
            elif tool == "subcluster_find_markers":
                self.clear_after_subcluster_find_markers_outputs()
            elif tool in {"subcluster_annotate_ontology", "subcluster_annotate_inheritance"}:
                self.clear_after_subcluster_annotation_outputs()
            elif tool == "finalize_subcluster_annotations":
                self.clear_report_outputs()
        return outputs


def format_validation_result(result: dict[str, list[str]]) -> str:
    lines = []
    if result["errors"]:
        lines.append("Errors:")
        lines.extend(f"  - {item}" for item in result["errors"])
    if result["warnings"]:
        lines.append("Warnings:")
        lines.extend(f"  - {item}" for item in result["warnings"])
    if not lines:
        lines.append("Validation passed.")
    return "\n".join(lines)
