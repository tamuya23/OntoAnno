# PDAC_sn Test Checklist

This checklist is the current end-to-end regression plan for AnnoAgent using:

- Config: [configs/pdac_sn.yaml](/proj/bzou_lab/projects/AnnoAgent/configs/pdac_sn.yaml)
- Dataset: `PDAC_sn`

The goal is to catch:

- UI issues in the Streamlit workbench
- router / intent routing issues
- session / memory issues
- controller-chain issues
- worker runtime issues

This checklist is split into:

1. UI-first integration tests
2. Full worker coverage tests

## 1. Environment Setup

Run these first in a fresh shell:

```bash
cd /proj/bzou_lab/projects/AnnoAgent
export ANNOAGENT_RSCRIPT=/nas/longleaf/rhel9/apps/r/4.4.0/bin/Rscript
export OPENAI_API_KEY=YOUR_KEY
```

Basic environment checks:

```bash
python3 annoagent validate --config configs/pdac_sn.yaml
python3 annoagent workers --config configs/pdac_sn.yaml
```

Expected checks:

- `validate` should not report missing config keys
- `validate` should not report missing `Rscript`
- `workers` should list the full worker inventory without crashing

## 2. Start the UI

Launch the local workbench:

```bash
python3 annoagent ui --config configs/pdac_sn.yaml
```

Open the local Streamlit page in your browser.

Before testing:

- Confirm the sidebar shows `Project: PDAC_sn`
- Confirm the sidebar shows a session path under `work/PDAC_sn`
- Confirm the app does not throw a Streamlit exception on load
- UI now executes requests directly; use a test config or a disposable run when needed

## 3. UI Smoke Test

Check these UI areas before any agent testing:

- `Chat` pane renders
- `Status` tab renders
- `Workers` tab renders
- `Artifacts` tab renders
- `Logs` tab renders
- No raw Streamlit traceback is visible

Expected:

- `Artifacts` should show structured sections, not a raw manifest blob
- Image preview may be empty if no figure is selected yet; that is fine

## 4. Intent Coverage From the UI

These are the main top-level intents currently expected:

- `run_parent_pipeline`
- `run_subcluster_pipeline`
- `run_RAG_check`
- `change_annotation_preference`
- `add_external_evidence`
- `extract_external_evidence` (placeholder)

Run the following first from a fresh session.

### 4.1 Read-only Questions Should Not Over-trigger Tools

Prompt:

```text
show the custom knowledge (Pass)
```

Check:

- The agent should answer or summarize existing evidence
- It should not incorrectly trigger `run_RAG_check`

Prompt:

```text
what is the current resolution chosen (Pass)
```

Check:

- The agent should answer directly
- It should distinguish `resolution` from `granularity`

Prompt:

```text
what is the current granularity (Pass)
```

Check:

- The agent should answer directly

Prompt:

```text
explain the difference between resolution and granularity (Pass)
```

Check:

- `resolution` should be described as cluster-level change
- `granularity` should be described as label-expression / candidate-map-level change

### 4.2 `change_annotation_preference`

Prompt:

```text
The labels are too coarse. Make them more specific.
```

Check:

- Routed to `change_annotation_preference` (Pass)
- `preference_type` should be `granularity`
- Suggested next action should typically be `run_RAG_check`

Prompt:

```text
Use resolution 0.5 instead. (Fail)
```

Check:

- Routed to `change_annotation_preference`
- `preference_type` should be `resolution`
- If `0.5` already exists in PDAC_sn outputs, it should behave as existing-resolution selection
- Suggested next action should typically be `run_RAG_check`

### 4.3 `add_external_evidence`

Prompt:

```text
Add external evidence for pericyte: RGS5, CSPG4, MCAM. (Pass)
```

Check:

- Routed to `add_external_evidence`
- The tool should use `celltype=pericyte`
- It should not guess a different cell type
- Suggested next action should typically be `run_RAG_check`

Negative prompt:

```text
Add these markers: RGS5, CSPG4, MCAM. (Pass)
```

Check:

- The agent should not invent a cell type
- It should ask for clarification or refuse to store ambiguous evidence

### 4.4 `run_RAG_check`

Prompt:

```text
Run the RAG check again.
```

Check:

- Routed to `run_RAG_check`
- The result should mention the review/check chain
- If unresolved clusters exist, the default next worker should be `human_review`

### 4.5 `run_subcluster_pipeline`

Prompt:

```text
I want to look deeper into pericytes.
```

Check:

- Routed to `run_subcluster_pipeline`
- Uses `celltype=pericyte` or `Pericytes`
- Should not merely queue silently; it should represent immediate pipeline execution semantics

### 4.6 `run_parent_pipeline`

Prompt:

```text
Run the full parent pipeline from the start.
```

Check:

- Routed to `run_parent_pipeline`
- Should describe the parent backbone, not RAG review

### 4.7 `extract_external_evidence` Placeholder

Prompt:

```text
Look for recent papers about PDAC pericyte markers and extract candidate evidence.
```

Check:

- Router may choose `extract_external_evidence`
- Current expected behavior is placeholder / not-yet-implemented
- The app must not crash
- The response should make clear that extraction/search is not fully implemented yet

## 5. UI Apply-Mode Tests

Re-run these prompts one by one and inspect the UI after each one.

### 5.1 Apply `add_external_evidence`

Prompt:

```text
Add external evidence for pericyte: RGS5, CSPG4, MCAM.
```

Check:

- Chat result should say evidence was stored
- `Status` / sidebar memory counts should update after refresh
- No duplicate execution should appear

### 5.2 Apply `change_annotation_preference` for granularity

Prompt:

```text
The labels are too coarse. Make them more specific.
```

Check:

- Granularity should become `fine`
- Sidebar should reflect new granularity after refresh
- It should not silently trigger unrelated workers

### 5.3 Apply `change_annotation_preference` for resolution

Prompt:

```text
Use resolution 0.5 instead.
```

Check:

- If `0.5` is already available, it should switch to that resolution path
- If not, it should rerun the parent backbone
- The result should clearly state which branch happened

### 5.4 Apply `run_RAG_check`

Prompt:

```text
Run the RAG check again.
```

Check:

- Chat result should list the worker chain
- `Artifacts` should update:
  - `Reviewed Parent`
  - `RAG Check Outputs`
- `Logs` should show fresh activity

### 5.5 Apply `run_subcluster_pipeline`

Prompt:

```text
I want to look deeper into pericytes.
```

Check:

- Should execute the subcluster chain immediately
- UI must remain responsive after the run completes
- No router/session crash

## 6. Multi-turn Session Tests From the UI

Start from a fresh session first.

Test this conversation:

Prompt 1:

```text
I am especially interested in pericytes.
```

Prompt 2:

```text
Add external evidence for it: RGS5, CSPG4, MCAM.
```

Prompt 3:

```text
Run the RAG check again.
```

Check:

- `it` should resolve to `pericyte`
- Session context should persist across turns
- The follow-up tool suggestions should remain coherent

Then test:

Prompt 4:

```text
What is the current granularity?
```

Check:

- The agent should answer directly
- It should not route into a tool unless truly necessary

## 7. UI Regression Checks

While using the app, explicitly watch for:

- duplicate tool execution on a single prompt
- accidental `run_RAG_check` on read-only questions
- invented cell types when markers are ambiguous
- Streamlit exceptions
- stale sidebar/session state after refresh
- `Artifacts` sections showing empty or obviously wrong previews
- `Logs` tab crashing when no log file exists

## 8. Full Worker Coverage Tests

UI is the primary integration surface, but worker coverage is still best done through CLI.

Run these from the shell:

### 8.1 Parent Backbone Workers

```bash
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker preprocess_parent
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker cluster_parent_markers
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker annotate_parent_raw
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker map_parent_ontology
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker select_parent_resolution
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker assign_parent_labels
```

Check for each:

- Command exits cleanly
- Status is sensible
- Reported artifacts exist

### 8.2 Subcluster Workers

```bash
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker subcluster_find_markers
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker subcluster_annotate_ontology
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker subcluster_annotate_inheritance
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker finalize_subcluster_annotations
```

Check:

- No hidden prompt or interactive block
- Expected subcluster outputs are created or reused

### 8.3 RAG Check Workers

```bash
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker build_review_packets
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker decide_rag_check --phase initial
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker decide_rag_check --phase post_ontology
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker decide_rag_check --phase post_compare
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker build_candidate_map
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker retrieve_rag_evidence
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker run_llm_compare
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker human_review
```

Check:

- `decide_rag_check` returns phase-appropriate status
- `run_llm_compare` returns a truthful status, not a fake `completed`
- `human_review` does not unexpectedly block for new input under `worker-run`

### 8.4 Output Workers

```bash
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker export_reviewed_parent_annotations
python3 annoagent worker-run --config configs/pdac_sn.yaml --worker generate_report
```

Check:

- Export worker refuses cleanly if required review decisions are missing
- Report worker finishes without crashing

## 9. CLI Full-flow Sanity Tests

These are optional but useful after the UI pass.

```bash
python3 annoagent ask --config configs/pdac_sn.yaml --message "Run the RAG check again."
python3 annoagent ask --config configs/pdac_sn.yaml --message "Add external evidence for pericyte: RGS5, CSPG4, MCAM."
python3 annoagent ask --config configs/pdac_sn.yaml --message "Use resolution 0.5 instead."
```

Check:

- CLI output should match UI behavior
- No mismatch between `ask/chat` and `ui`

## 10. Pass Criteria

This test round is considered acceptable if:

- The UI loads without Streamlit exceptions
- All read-only questions avoid unnecessary tool execution
- All main intents route correctly
- `add_external_evidence` never invents a cell type
- `change_annotation_preference` distinguishes resolution vs granularity correctly
- `run_RAG_check` runs through its intended chain and surfaces `human_review` when unresolved
- `worker-run` covers all deployed workers without hidden interactive failures
- `extract_external_evidence` remains non-crashing even though it is still a placeholder

## 11. Bug Log Template

For each bug, record:

- Prompt or command
- Config used
- Execution mode
- Expected behavior
- Actual behavior
- Screenshot if UI bug
- Relevant log snippet
- Whether the failure is:
  - Router
  - Controller
  - Worker
  - UI
  - Environment
