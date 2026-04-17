# OntoAnno

OntoAnno is a Python orchestration layer around the vendored `GPTAnno/` R package. It provides:

- a stage-based CLI for reproducible runs
- an agent router that maps natural-language requests to high-level actions
- a normalized worker runtime for debugging and partial reruns
- a local Streamlit workbench for interactive use

The runtime model is simple:

- Python handles routing, controller logic, state, memory, UI, and subprocess orchestration
- R handles the heavy annotation workers and Seurat-based plotting/export

OntoAnno writes only inside this repository:

- `work/<project>/...` for durable project artifacts
- `runs/<run_id>/...` for run-local state, logs, manifests, review artifacts, and reports

## Environment

OntoAnno needs both a Python environment and an R environment.

The intended setup is:

- one Python environment for `ontoanno`
- one R installation with the packages needed by `GPTAnno`
- `ONTOANNO_RSCRIPT` used to bridge Python to that R environment

### Python

Minimum:

- Python `>=3.11`
- packages from `pyproject.toml`

Install the package in editable mode:

```bash
cd /proj/bzou_lab/projects/OntoAnno
pip install -e .
```

If you want the Streamlit UI:

```bash
pip install -e .[ui]
```

Current Python package dependencies are intentionally small:

- `PyYAML`
- `Jinja2`
- `Pillow`
- optional `streamlit>=1.40`

### R

OntoAnno does not manage the R environment for you. The `Rscript` binary you point at must already have the packages required by:

- `GPTAnno/`
- `scripts/run_annotation.R`
- `scripts/run_gptanno_tool.R`
- `scripts/export_report_figures.R`
- `scripts/export_reviewed_parent_annotations.R`

In practice that means the R side must already be able to run:

- `Seurat`
- `ggplot2`
- `jsonlite`
- and the packages imported by the vendored `GPTAnno` code

Set the R entrypoint explicitly when it is not on `PATH`:

```bash
export ONTOANNO_RSCRIPT=/nas/longleaf/rhel9/apps/r/4.4.0/bin/Rscript
```

### Environment Variables

Required for OpenAI-backed routes/workers:

```bash
export OPENAI_API_KEY=...
```

Optional:

```bash
export OPENAI_BASE_URL=...
export ONTOANNO_CL_OBO=/path/to/cl.obo
```

`ONTOANNO_CL_OBO` avoids re-fetching the Cell Ontology OBO and is the cleanest setup for cluster review and ontology mapping.

### Validate First

Before running anything substantial:

```bash
python3 ontoanno validate --config configs/pdac_sn.yaml
```

This checks:

- required config keys
- core input files
- vendored helper scripts
- policy validity
- PDF inputs if configured

## Configuration

A single YAML file is the user-maintained source of truth. Python loads it, resolves environment variables, and generates stage- or worker-specific specs for R.

Example:

- [configs/pdac_sn.yaml](/proj/bzou_lab/projects/OntoAnno/configs/pdac_sn.yaml)

Important top-level sections:

- `project`
  - `name`
  - `work_dir`
- `inputs`
  - `seurat_rds`
  - `manual_labels_csv`
  - `pdf_dir`
  - `marker_genes_dir`
  - `annotation_output_dir`
  - `annotation_parent_rds`
- `policy`
  - `ontology`
  - `granularity`
  - `fallback`
  - `review_tie`
  - `review_nomatch`
- `llm`
  - `annotation`
  - `pdfmarkers`
- `annotation`
  - `species`
  - `parent_res`
  - `sub_res`
  - `preprocess`
  - `min_cell_count`
  - `tissue_name`
  - `n_runs_parent`
  - `n_runs_sub`
  - `forced_parent_resolution`
- `alignment`
  - `celltypes_to_subcluster`
  - `user_restrict_to`
  - `combine_restrictions`
  - `manual_resolution_map`
  - `on_missing_decision`
- `evaluation`
- `report`

Optional precomputed marker input:

- `inputs.marker_genes_dir`
  - points to a folder containing GPTAnno-style `markers_res_<resolution>.rds` files, for example `markers_res_0.1.rds`
  - lets OntoAnno skip parent clustering and marker recomputation, then continue directly into parent annotation
  - requires the input Seurat object metadata to already contain matching `cluster_res.<resolution>` columns for every configured `annotation.parent_res`

Optional imported parent annotation input:

- `inputs.annotation_output_dir`
  - points to an existing GPTAnno output folder, for example `/proj/bzou_lab/projects/GPTAnno_Experiment/MCA_20/output`
  - this is the cleanest direct-RAG-check input when the folder already contains `annotation_parent*.rds`, `annotation_summary_scores*.csv`, `marker_genes/`, `prediction/`, and optional metadata/Seurat artifacts
- `inputs.annotation_parent_rds`
  - points to an existing GPTAnno `annotation_parent*.rds`, for example `/proj/bzou_lab/projects/GPTAnno_Experiment/PDAC_sn/output/annotation_parent_nonCM.rds`
  - lets OntoAnno skip clustering, marker recomputation, and GPTAnno parent annotation, then start from `build_review_packets` / `run_RAG_check`
  - automatically infers sibling `annotation_summary_scores*.csv`, `marker_genes/`, `prediction/`, `*_metadata.csv`, and `*GPTannotated_parent*.rds` when they sit in the same output folder
  - optional explicit overrides are available as `inputs.annotation_scores_csv`, `inputs.parent_seurat_rds`, `inputs.parent_metadata_csv`, `inputs.markers_dir`, `inputs.prediction_dir`, `inputs.best_resolution`, and `inputs.cluster_col`

Two fields matter especially for current agent behavior:

- `annotation.parent_res`
  - the set of parent resolutions the algorithm will try or has been told to try
- `annotation.forced_parent_resolution`
  - the currently forced/selected parent resolution when applicable

## Quick Start

Typical setup:

```bash
cd /proj/bzou_lab/projects/OntoAnno
export ONTOANNO_RSCRIPT=/nas/longleaf/rhel9/apps/r/4.4.0/bin/Rscript
export OPENAI_API_KEY=...
python3 ontoanno validate --config configs/pdac_sn.yaml
python3 ontoanno ui --config configs/pdac_sn.yaml
```

For most users, the main entrypoint is the local Streamlit workbench. The CLI remains available underneath it, but it is primarily a developer/debugging interface now.

## Streamlit Workbench

Start it with:

```bash
python3 ontoanno ui --config configs/pdac_sn.yaml
```

Useful flags:

```bash
python3 ontoanno ui --config configs/pdac_sn.yaml --reset-session
python3 ontoanno ui --config configs/pdac_sn.yaml --server-port 8502
```

The Streamlit UI is a local workbench, not a separate backend service. It uses the same router, controller, memory, and worker runtime as the CLI.

Main areas:

- `Chat`
  - normal natural-language interaction with the agent
- `Run Status`
  - 5 coarse phases:
    - `Cluster`
    - `Annotate`
    - `Subcluster`
    - `RAG_Check`
    - `Report`
  - current active worker log tail
- `Artifacts`
  - `Parent Annotation`
  - `Subcluster`
  - `RAG Review`
  - `Report`, including a preview of the generated report and the RAG-check discussion section when review artifacts exist
- `External Evidence`
  - user-provided evidence
  - literature-provided evidence placeholder
- `Workers`
  - low-level debugging panel for direct worker execution

## Natural-Language Routes

The router is implemented in [src/ontoanno/agent_router.py](/proj/bzou_lab/projects/OntoAnno/src/ontoanno/agent_router.py). The canonical route registry is [resources/agent_registry.yaml](/proj/bzou_lab/projects/OntoAnno/resources/agent_registry.yaml).

The router turns natural-language requests into one high-level action. The controller then translates that action into a worker chain. The UI uses this path for normal interaction.

Current top-level routes are:

### `run_parent_pipeline`

Purpose:

- run the full parent backbone from preprocessing through assigned parent labels

Current worker chain:

- `preprocess_parent`
- `cluster_parent_markers`
- `annotate_parent_raw`
- `map_parent_ontology`
- `select_parent_resolution`
- `assign_parent_labels`

When to use:

- first full parent run
- adding a genuinely new parent resolution
- refreshing parent annotation artifacts from scratch

### `run_subcluster_pipeline`

Purpose:

- run targeted subclustering for one parent cell type

Current worker chain:

- `subcluster_find_markers`
- `subcluster_annotate_ontology`
- `subcluster_annotate_inheritance`
- `finalize_subcluster_annotations`

When to use:

- drill down into a specific parent population such as `macrophage` or `pericyte`

### `run_RAG_check`

Purpose:

- run the current review/check pipeline on existing annotations

Logical worker chain:

- `build_review_packets`
- `decide_rag_check`
- `build_candidate_map`
- `retrieve_rag_evidence`
- `run_llm_compare`
- `human_review`

Boundary:

- this route stops at review/human-review
- export and report are intentionally separate

### `change_annotation_preference`

Purpose:

- change either `granularity` or `resolution`

Behavior:

- `granularity`
  - updates policy only
  - suggested next step is usually `run_RAG_check`
- `resolution`
  - if the resolution already exists, switch to it
  - if it is new, extend `annotation.parent_res` and rerun the parent backbone

Important distinction:

- `resolution`
  - changes clustering-level state
- `granularity`
  - changes labeling specificity after clusters/markers are fixed

### `add_external_evidence`

Purpose:

- store user-provided evidence such as `celltype -> markers`

Behavior:

- update existing celltype evidence
- or define a new custom celltype entry

Storage:

- internal memory buckets still use:
  - `custom_markers`
  - `custom_celltypes`
- conceptually they are both part of the external-evidence layer

Typical next step:

- `run_RAG_check`

### `extract_external_evidence`

Purpose:

- future route for literature/database extraction

Current state:

- registered placeholder intent
- worker chain intentionally not implemented yet

### `run_report`

Purpose:

- generate the final report from current artifacts

Current worker chain:

- `generate_report`

Current behavior:

- if saved reviewed decisions exist and reviewed outputs have not yet been exported, report generation first attempts reviewed export automatically
- if RAG-check outputs exist, the report includes a dedicated `RAG Check Review` section summarizing flagged clusters, LLM comparisons, accepted changes, and human-review needs

## Worker Inventory

The normalized worker contracts are defined in [resources/agent_registry.yaml](/proj/bzou_lab/projects/OntoAnno/resources/agent_registry.yaml) and formatted by [src/ontoanno/worker_contracts.py](/proj/bzou_lab/projects/OntoAnno/src/ontoanno/worker_contracts.py).

### GPTAnno Backbone Workers

- `preprocess_parent`
  - prepare parent-level Seurat input and runtime context
- `cluster_parent_markers`
  - cluster parent cells at multiple resolutions and compute markers
- `annotate_parent_raw`
  - run raw parent annotation
- `map_parent_ontology`
  - map raw parent labels onto Cell Ontology
- `select_parent_resolution`
  - choose the active parent resolution
- `assign_parent_labels`
  - assign per-cluster and per-cell parent labels

### Subcluster Workers

- `subcluster_find_markers`
  - subset a chosen parent celltype and compute subcluster markers
- `subcluster_annotate_ontology`
  - ontology-constrained subcluster annotation
- `subcluster_annotate_inheritance`
  - parent-marker-inheritance subtype annotation
- `finalize_subcluster_annotations`
  - write final subcluster outputs to `work/<project>/annotate_subclusters`

### RAG Check Workers

- `build_review_packets`
  - package current parent outputs into one review unit per cluster
- `decide_rag_check`
  - wrapped controller decision worker
  - decides which clusters can be finalized versus compared or reviewed
- `build_candidate_map`
  - wrapped ontology-relations worker
  - builds candidate maps under ontology and granularity constraints
- `retrieve_rag_evidence`
  - wrapped evidence retrieval layer
  - currently still embedded inside ontology-relations
- `run_llm_compare`
  - run the LLM judge over prepared candidates
- `human_review`
  - collect saved or direct user decisions for unresolved clusters

### Output Workers

- `export_reviewed_parent_annotations`
  - write reviewed per-cell metadata, reviewed Seurat object, and cluster decisions
- `generate_report`
  - generate final report assets and deliverable report file

## Memory and Session Files

Project-local state lives under `work/<project>/`.

Important files:

- `work/<project>/agent_memory.json`
  - stored external evidence, subcluster requests, resolution feedback
- `work/<project>/agent_session.json`
  - persistent natural-language session state
- `work/<project>/agent_ui_history.json`
  - Streamlit UI chat history rendering state

## Artifacts and Output Layout

Durable project artifacts:

- `work/<project>/annotate_parent`
- `work/<project>/annotate_subclusters`

Run-local artifacts:

- `runs/<run_id>/manifest.json`
- `runs/<run_id>/review_packets`
- `runs/<run_id>/ontology_relations`
- `runs/<run_id>/llm_compare`
- `runs/<run_id>/controller`
- `runs/<run_id>/reviewed_parent`
- `runs/<run_id>/report.html` or `report.pdf`
- `runs/<run_id>/report_assets/figures`

## Typical Workflows

### Full parent run

```bash
python3 ontoanno ask --config configs/pdac_sn.yaml --message "Run the parent pipeline"
```

### RAG review after annotation

```bash
python3 ontoanno ask --config configs/pdac_sn.yaml --message "Run the RAG check"
```

### Change specificity and review again

```bash
python3 ontoanno ask --config configs/pdac_sn.yaml --message "The labels are too coarse. Make them more specific."
python3 ontoanno ask --config configs/pdac_sn.yaml --message "Run the RAG check again."
```

### Run subclustering for a parent cell type

```bash
python3 ontoanno ask --config configs/pdac_sn.yaml --message "I want to look deeper into macrophages."
```

### Add explicit evidence

```bash
python3 ontoanno ask --config configs/pdac_sn.yaml --message "Add external evidence for pericyte: RGS5, CSPG4, MCAM."
```

### Generate the final report

```bash
python3 ontoanno ask --config configs/pdac_sn.yaml --message "Generate report."
```

## Developer CLI

The commands below are still supported, but they are primarily for development, debugging, and recovery rather than normal end-user operation.

### Main Commands

```bash
python3 ontoanno validate --config configs/pdac_sn.yaml
python3 ontoanno run --config configs/pdac_sn.yaml
python3 ontoanno report --config configs/pdac_sn.yaml --force
python3 ontoanno chat --config configs/pdac_sn.yaml
python3 ontoanno ask --config configs/pdac_sn.yaml --message "Run the RAG check"
```

### Review and Analysis

```bash
python3 ontoanno review-packets --config configs/pdac_sn.yaml --force
python3 ontoanno ontology-relations --config configs/pdac_sn.yaml --force
python3 ontoanno llm-compare --config configs/pdac_sn.yaml --force
python3 ontoanno controller --config configs/pdac_sn.yaml --phase post_compare --force
python3 ontoanno agent --config configs/pdac_sn.yaml --force
```

### Decomposed Backbone Workers

```bash
python3 ontoanno gptanno-tool --config configs/pdac_sn.yaml --tool preprocess_parent
python3 ontoanno gptanno-tool --config configs/pdac_sn.yaml --tool cluster_parent_markers
python3 ontoanno gptanno-tool --config configs/pdac_sn.yaml --tool annotate_parent_raw
python3 ontoanno gptanno-tool --config configs/pdac_sn.yaml --tool map_parent_ontology
python3 ontoanno gptanno-tool --config configs/pdac_sn.yaml --tool select_parent_resolution
python3 ontoanno gptanno-tool --config configs/pdac_sn.yaml --tool assign_parent_labels
```

### Worker Introspection

```bash
python3 ontoanno workers --config configs/pdac_sn.yaml
python3 ontoanno worker-run --config configs/pdac_sn.yaml --worker build_review_packets
python3 ontoanno worker-run --config configs/pdac_sn.yaml --worker decide_rag_check --phase initial
```

## Notes

- The authoritative human-readable router/controller/worker registry is [resources/agent_registry.yaml](/proj/bzou_lab/projects/OntoAnno/resources/agent_registry.yaml).
- LangGraph visualization helpers remain in:
  - [src/ontoanno/agent_graph.py](/proj/bzou_lab/projects/OntoAnno/src/ontoanno/agent_graph.py)
  - [src/ontoanno/agent_graph_visual.py](/proj/bzou_lab/projects/OntoAnno/src/ontoanno/agent_graph_visual.py)
  - [langgraph.json](/proj/bzou_lab/projects/OntoAnno/langgraph.json)
- `extract_external_evidence` is intentionally registered before its worker chain exists. That is by design, not drift.
