# Collaboration

This repository works best when we treat code and configs as shared source, and
all generated outputs as local artifacts.

## What to commit

- `src/`
- `scripts/`
- `configs/`
- `README.md`
- `COLLABORATION.md`

Do not commit:

- `runs/`
- `work/`
- `backups/`
- temporary logs, notebooks, caches, or local virtual environments

## Best Workflow

Use one shared repository and give each experiment its own config file:

- `configs/pdac_sc.yaml`
- `configs/pdac_sc_res03.yaml`
- `configs/pdac_sc_resweep.yaml`

For each config, use a unique `project.name` and `project.work_dir`. This avoids
collisions in:

- `runs/<project>_latest.json`
- `work/<project>/...`
- reports and review outputs

## Branching

Recommended branch model:

- `main` for stable shared code
- one short-lived branch per task or per person

Examples:

- `yrsong/pdac-res03-review`
- `alice/controller-fix`
- `bob/report-cleanup`

Merge back to `main` after review.

## Running In The Same Directory

If multiple people are working in the same checkout:

- do not share the same `project.name` unless you intentionally want to resume the same run
- prefer one config per experiment
- avoid editing the same config file for unrelated experiments
- pull before starting a new task
- commit small, focused changes

If two people need the same dataset but different settings, create two config
files with different project names rather than reusing one config.

## Practical Recommendation For AnnoAgent

For heavy datasets, keep these separate:

- "source of truth" config in `configs/`
- generated artifacts in `work/` and `runs/`

That way we can collaborate on pipeline logic without stepping on each other's
outputs.
