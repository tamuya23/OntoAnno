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
