# AnnoAgent

AnnoAgent is a Python-orchestrated CLI wrapper around the vendored `GPTAnno/` R package and `GPTAnno/PDF2markers/` Python pipeline.

V1 is designed for configuration-driven single-dataset runs:

- input Seurat RDS
- optional preprocessing
- multi-resolution clustering
- parent annotation
- subcluster annotation
- optional PDF marker extraction
- optional evaluation hooks
- HTML report generation

The agent only writes inside this repository:

- `work/<project>/...` for analysis artifacts
- `runs/<run_id>/...` for run state, logs, manifests, and reports

## Quick Start

```bash
python3 annoagent validate --config configs/agingv2.yaml
python3 annoagent run --config configs/agingv2.yaml
python3 annoagent report --config configs/agingv2.yaml
python3 annoagent pdfmarkers --config configs/agingv2.yaml --pdf path/to/paper.pdf
python3 annoagent agent --config configs/agingv2.yaml
python3 annoagent ask --config configs/agingv2.yaml --message "labels are too coarse, make them more specific"
python3 annoagent chat --config configs/agingv2.yaml
python3 annoagent ui --config configs/agingv2.yaml
python3 annoagent gptanno-tool --config configs/agingv2.yaml --tool select_parent_resolution
python3 annoagent workers --config configs/agingv2.yaml
python3 annoagent worker-run --config configs/agingv2.yaml --worker build_review_packets
```

`annoagent run` uses `Rscript` from `PATH` by default. If R is installed elsewhere, set `ANNOAGENT_RSCRIPT=/path/to/Rscript`.

`ontology-relations` will use a local Cell Ontology OBO first when available. To
avoid network downloads, set:

```bash
export ANNOAGENT_CL_OBO=/path/to/cl.obo
```

For LLM-backed stages, set the required environment variables before running:

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=...   # optional
```

The default report output is:

- `runs/<run_id>/report.html`

## Natural-Language Requests

`ask` is an LLM-driven controller entry. It reads the current AnnoAgent state,
lets the model choose a tool call, and then executes that tool to
update config or agent memory. It also keeps a small per-project conversation
session so follow-up requests can refer to earlier turns.

`chat` uses the same LLM router and session store, but keeps you inside a
persistent CLI conversation. Tool calls execute immediately.

```bash
python3 annoagent chat --config configs/agingv2.yaml
```

## Streamlit Workbench

AnnoAgent also includes a lightweight local Streamlit workbench. It uses the
same Python/R backend as the CLI:

- Python runs the router, controller, memory, and worker dispatch
- R workers are still executed locally through `Rscript`

Start it with:

```bash
python3 annoagent ui --config configs/agingv2.yaml
```

Useful flags:

```bash
python3 annoagent ui --config configs/agingv2.yaml --reset-session
python3 annoagent ui --config configs/agingv2.yaml --server-port 8502
```

If Streamlit is missing in your current Python environment, install the optional
UI dependency set:

```bash
pip install -e .[ui]
```

Example:

```bash
python3 annoagent ask --config configs/agingv2.yaml --message "the labels are too coarse, make them more specific"
```

Reset the per-project session if you want to start a fresh conversation:

```bash
python3 annoagent ask --config configs/agingv2.yaml --reset-session --message "start over"
```

Apply the parsed action:

```bash
python3 annoagent ask --config configs/agingv2.yaml --message "I have new marker genes for pericyte: RGS5, CSPG4, MCAM"
```

Current supported intent families:

- `run_parent_pipeline`: rerun the full parent backbone
- `run_RAG_check`: rerun the current RAG-based check/review flow on available annotations
- `run_subcluster_pipeline`: run a targeted subcluster analysis for one parent cell type
- `change_annotation_preference`: change granularity, force an existing resolution, or add a new resolution and rerun the parent backbone
- `add_external_evidence`: add user-provided external evidence by updating an existing cell type with markers or defining a new custom cell type
- `extract_external_evidence`: registered placeholder intent for future paper/database evidence extraction

The authoritative registry for top-level intents, controller actions, and
worker chains is stored at:

- `resources/agent_registry.yaml`

You can print the current worker contract inventory directly from the CLI:

```bash
python3 annoagent workers --config configs/agingv2.yaml
```

You can also run one deployed worker directly through the normalized worker runtime:

```bash
python3 annoagent worker-run --config configs/agingv2.yaml --worker build_review_packets
python3 annoagent worker-run --config configs/agingv2.yaml --worker decide_rag_check --phase initial
python3 annoagent worker-run --config configs/agingv2.yaml --worker build_candidate_map
```

An explicit LangGraph-style architecture definition is stored at:

- `src/annoagent/agent_graph.py`
- `src/annoagent/agent_graph_visual.py`

The repository root also includes:

- `langgraph.json`

These files do not replace the runtime controller. They exist so the current
Router / Controller / Worker architecture is explicitly modeled in Python for
graph visualization tooling.

- `agent_graph.py` is registry-driven and easier to keep in sync with the architecture registry.
- `agent_graph_visual.py` is an explicit, visualization-oriented graph definition for tools that prefer literal node/edge declarations.

The `langgraph.json` file currently points visualization tools at the
module-level `graph` object exported by `src/annoagent/agent_graph_visual.py`.

Current conceptual workers inside `run_RAG_check` are:

- `build_review_packets`
- `decide_rag_check`
- `build_candidate_map`
- `retrieve_rag_evidence`
- `run_llm_compare`
- `human_review`

Persistent agent memory is stored at:

- `work/<project>/agent_memory.json`

Conversation/session state is stored at:

- `work/<project>/agent_session.json`

User-provided marker memory is later injected into `llm-compare` as additional
researcher-curated supportive evidence.

## GPTAnno Backbone Tools

The original vendored GPTAnno backbone is now exposed as decomposed worker
tools without changing the existing top-level `run` stages. These tools are
useful for debugging, future agent control, and strict chain-of-custody around
which part of the backbone is being rerun.

```bash
python3 annoagent gptanno-tool --config configs/agingv2.yaml --tool preprocess_parent
python3 annoagent gptanno-tool --config configs/agingv2.yaml --tool cluster_parent_markers
python3 annoagent gptanno-tool --config configs/agingv2.yaml --tool annotate_parent_raw
python3 annoagent gptanno-tool --config configs/agingv2.yaml --tool map_parent_ontology
python3 annoagent gptanno-tool --config configs/agingv2.yaml --tool select_parent_resolution
python3 annoagent gptanno-tool --config configs/agingv2.yaml --tool assign_parent_labels
```

Available parent/subcluster backbone tools:

- `preprocess_parent`
- `cluster_parent_markers`
- `annotate_parent_raw`
- `map_parent_ontology`
- `select_parent_resolution`
- `assign_parent_labels`
- `subcluster_find_markers`
- `subcluster_annotate_ontology`
- `subcluster_annotate_inheritance`
- `finalize_subcluster_annotations`

These write their own JSON outputs under:

- `runs/<run_id>/specs/gptanno-tool-*.outputs.json`

## Policy

The main pipeline is unchanged. Policy is applied only after the first-pass parent
annotation, inside `ontology-relations`, where it shapes the ontology candidate
neighborhood that will later be compared by `llm-compare`.

```yaml
policy:
  ontology: true
  granularity: balanced
  fallback: up
  review_tie: true
  review_nomatch: true
```

Current `granularity` semantics in `ontology-relations`:

- `coarse`: compare `parent + self`
- `balanced`: compare surfaced ontology candidates; if only one mapped candidate is present, expand to `self + sibling`
- `fine`: compare `self + child`

## Review Packets

`review-packets` is a read-only helper for later decision modules. It scans the
existing parent annotation outputs and writes one packet per parent cluster from
the best parent resolution:

- `runs/<run_id>/review_packets/summary.csv`
- `runs/<run_id>/review_packets/index.json`
- `runs/<run_id>/review_packets/packets/*.json`

`index.json` stores the shared parent context:

- policy
- best parent resolution
- shared artifact paths
- global resolution score summary

Each cluster packet is lightweight and only keeps cluster-specific review data:

- assigned parent label
- cluster size / agreement summary
- top markers
- review flags

## Ontology Relations

`ontology-relations` is the ontology-local comparison layer. It reads the parent
review packets, maps the current label and competing labels to CL terms, applies
the active policy to construct `focus_candidates`, and exports:

- a compact candidate mapping summary
- a filtered `consensus_ancestor`
- `policy_granularity`
- `focus_strategy`
- `needs_llm_compare`
- a short `llm_question` for later LLM judging
- when a local PanglaoDB file is present under `resources/reference_db/`, a reference-backed prompt with marker overlaps

The current implementation filters out overly broad common ancestors such as very
high-level ontology nodes before selecting a `consensus_ancestor`.

When reference databases are available, AnnoAgent filters them by dataset species.
Set `annotation.species` to `mouse` or `human` in config to make this explicit.
If the current dataset species has no supported reference entries, reference
evidence mode is disabled automatically and the report will warn you.

This is intended as the input to a later constrained LLM judge, not as a final
automatic relabeling step.

## LLM Compare

`llm-compare` reads the prompts prepared by `ontology-relations` and runs an
ontology-constrained, reference-assisted comparison only for clusters with
`prompt_ready = true`.

It does not modify annotation results. It writes:

- `runs/<run_id>/llm_compare/summary.csv`
- `runs/<run_id>/llm_compare/index.json`
- `runs/<run_id>/llm_compare/results/*.json`

Each result stores:

- the final compare prompt
- the raw model response
- a normalized structured decision
- `best_candidate` or `review`

## Agent Review

`agent` is the recommended parent-level refinement entry point. It first ensures
the initial parent annotation exists through `annotate_parent`, then hands
control to the cluster-level controller for the parent refinement steps.

It will:

- prompt for `policy.granularity`
- build or reuse `review-packets`
- initialize the controller right after parent annotation
- dispatch `ontology-relations` only for clusters whose next action is `build_ontology_relations`
- dispatch `llm-compare` only for clusters whose next action is `run_llm_compare`
- rebuild controller state after each worker phase
- stop only on true `review` / failed compare cases and ask the user to pick a final label
- export final per-cell parent annotations

The final reviewed outputs are written to:

- `runs/<run_id>/reviewed_parent/metadata_parent_reviewed.csv`
- `runs/<run_id>/reviewed_parent/seurat_parent_reviewed.rds`
- `runs/<run_id>/reviewed_parent/cluster_decisions.csv`

Clusters with `No ontology comparison needed` are skipped automatically to save
tokens.

## Layout

- `GPTAnno/`: vendored annotation engine and PDF2markers pipeline
- `src/annoagent/`: Python CLI and orchestration layer
- `scripts/`: thin R wrappers used by the orchestrator
- `configs/`: YAML configs
- `work/`: dataset-scoped artifacts
- `runs/`: run-scoped state and reports
