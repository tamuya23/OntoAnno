# OntoAnno

> Ontology-aware single-cell annotation workbench with chat-based control, RAG review, and report generation.

OntoAnno is a local web app for running and reviewing single-cell annotation workflows. It is designed for researchers who want to work through an interface, not through a long list of commands.

![OntoAnno Interface Overview](figures/ontoanno_interface_overview.png)

## Container Quick Start

OntoAnno supports two container runtimes:

- `Docker`: recommended for laptops, workstations, and regular Linux servers
- `Apptainer`: recommended for HPC environments such as Longleaf

Both runtimes are meant to use the same container image.

## Docker Quick Start

If you want a containerized setup with both Python and R bundled together:

### Step 1: Prepare the Docker workspace

```bash
bash docker_setup.sh
```

This will:

- create `.env` if it does not exist
- create `data/`, `work/`, and `runs/`
- prepare the folder layout for Docker

### Step 2: Edit `.env`

Set at least:

- `OPENAI_API_KEY`
- `ONTOANNO_CONFIG`

### Step 3: Put your data in `./data`

- host folder `./data` is mounted inside the container as `/data`
- host folder `./work` is mounted inside the container as `/work`

### Step 4: Choose a config template

- [`docker_import_template.yaml`](/proj/bzou_lab/projects/OntoAnno/configs/docker_import_template.yaml): start from an existing annotation output folder
- [`docker_fresh_template.yaml`](/proj/bzou_lab/projects/OntoAnno/configs/docker_fresh_template.yaml): start from a raw Seurat object

Inside Docker, your config must use container paths such as:

- `/data/...`
- `/work/...`

### Step 5: Start OntoAnno

```bash
./start_ontoanno_docker.sh
```

Then open:

```text
http://127.0.0.1:8501
```

Important:

- the container already includes its own Python and R environments
- the first Docker build may take a while because it installs R packages

## Apptainer Quick Start

If your system uses `apptainer` instead of `docker`:

### Step 1: Prepare the Apptainer workspace

```bash
bash apptainer_setup.sh
```

This will:

- create `.env` if it does not exist
- create `data/`, `work/`, `runs/`, and `.apptainer/`
- prepare the folder layout for Apptainer

### Step 2: Edit `.env`

Set at least:

- `OPENAI_API_KEY`
- `ONTOANNO_CONFIG`
- `ONTOANNO_IMAGE`

`ONTOANNO_IMAGE` should point to a published Docker image, for example:

```text
docker://ghcr.io/tamuya23/ontoanno:latest
```

### Step 3: Put your data in `./data`

- host folder `./data` is mounted inside the container as `/data`
- host folder `./work` is mounted inside the container as `/work`

### Step 4: Start OntoAnno

```bash
./start_ontoanno_apptainer.sh
```

The first run will pull the image into `.apptainer/ontoanno_latest.sif`.

Then open:

```text
http://127.0.0.1:8501
```

Important:

- paths inside the container must use `/data/...` and `/work/...`
- Apptainer is the better option for HPC systems that do not allow Docker
- the image only needs to be pulled once unless you want to refresh it

## Publish the Container Image

If you push this repository to GitHub, the container image can be built automatically by GitHub Actions.

The workflow file is:

- [.github/workflows/build-container.yml](/proj/bzou_lab/projects/OntoAnno/.github/workflows/build-container.yml)

By default, it publishes to:

```text
ghcr.io/tamuya23/ontoanno:latest
```

How it works:

1. Push to the `main` branch, or manually trigger the workflow in GitHub Actions.
2. GitHub builds the Docker image.
3. GitHub publishes the image to GitHub Container Registry.
4. Docker users can pull the image directly.
5. Apptainer users can pull the same image as a `.sif`.

## Quick Start

### Step 1: Open the project folder

```bash
cd /proj/bzou_lab/projects/OntoAnno
```

### Step 2: Run the one-time setup

```bash
bash setup.sh
```

This will:

- install OntoAnno
- install the web interface
- automatically detect your `Rscript` path and let you confirm or edit it
- ask for your OpenAI API key
- save these settings for later

### Step 3: Launch the app

```bash
./start_ontoanno.sh
```

The terminal will print a local web link, usually:

```text
http://127.0.0.1:8501
```

If you use VS Code with SSH, it will usually offer the forwarded link automatically.

## Start a Specific Dataset

To open a different dataset config:

```bash
./start_ontoanno.sh configs/chamber_demo.yaml
```

Example config files are in [`configs/`](/proj/bzou_lab/projects/OntoAnno/configs).

## What You Can Do in OntoAnno

Use the app to:

- run parent annotation
- inspect labels and prediction plots
- run RAG-based review
- change resolution or granularity
- subcluster a cell population
- add user-provided or literature-provided evidence
- generate a final report

## How to Use the Interface

### 1. Project Summary

The left sidebar shows the current project state:

- project name
- run ID
- tested parent resolutions
- selected resolution
- granularity
- ontology restriction status
- evidence memory counts

Use this panel to confirm that you are looking at the correct dataset and settings.

### 2. Session Controls

The sidebar buttons help manage the session:

- `Reset agent session`: clear the current conversation state
- `Refresh runtime state`: reload saved outputs and runtime status

Use `Refresh runtime state` when the page looks stale. Use `Reset agent session` only when you want to start over.

### 3. Agent Chat

The center panel is the main workspace.

Type normal instructions such as:

- `Run the parent annotation`
- `What is the current selected resolution?`
- `Run the RAG-based check`
- `Look deeper into macrophages`
- `Add these markers to pericyte: RGS5, CSPG4, MCAM`
- `Generate the final report`

You do not need to remember worker names or internal commands.

### 4. Status and Output

The right panel helps you monitor the workflow:

- `Status` shows the main pipeline stages
- `Terminal Output` shows live output from the currently running worker
- `Artifacts` shows plots, tables, reviewed outputs, and reports

Use this panel whenever a job is running or when you need to inspect results.

### 5. Evidence and Logs

Additional tabs provide:

- `External Evidence`: user and literature evidence
- `Workers`: advanced manual worker execution
- `Logs`: saved runtime logs

Most users will mainly use `Status`, `Artifacts`, and `External Evidence`.

## Typical Workflow

For most projects, the workflow is:

1. Run the parent annotation.
2. Review labels and plots.
3. Run the RAG check.
4. Adjust resolution or granularity if needed.
5. Subcluster a population if needed.
6. Add evidence if needed.
7. Generate the final report.

## Requirements

Before using OntoAnno, you need:

- a working Python environment
- a working R installation with the GPTAnno-related packages
- an OpenAI API key
- a YAML config file for your dataset

If you use Docker instead, the Python and R environments are provided inside the container.

## Troubleshooting

### The app does not start

Run setup again:

```bash
bash setup.sh
```

### R jobs fail

The `Rscript` path is usually wrong, or required R packages are missing. Run `bash setup.sh` again and confirm the R path.

### OpenAI calls fail

The API key is usually missing or invalid. Run `bash setup.sh` again and enter the key again.

### The page opens but the project does not work

The YAML config usually points to missing files. Check the dataset paths in your config file.

## Useful Files

- [Dockerfile](/proj/bzou_lab/projects/OntoAnno/Dockerfile): container image definition
- [compose.yaml](/proj/bzou_lab/projects/OntoAnno/compose.yaml): Docker Compose launcher
- [.env.example](/proj/bzou_lab/projects/OntoAnno/.env.example): example container environment file
- [docker_setup.sh](/proj/bzou_lab/projects/OntoAnno/docker_setup.sh): prepares the Docker workspace
- [start_ontoanno_docker.sh](/proj/bzou_lab/projects/OntoAnno/start_ontoanno_docker.sh): starts the Docker version
- [apptainer_setup.sh](/proj/bzou_lab/projects/OntoAnno/apptainer_setup.sh): prepares the Apptainer workspace
- [start_ontoanno_apptainer.sh](/proj/bzou_lab/projects/OntoAnno/start_ontoanno_apptainer.sh): starts the Apptainer version
- [setup.sh](/proj/bzou_lab/projects/OntoAnno/setup.sh): one-time installer
- [start_ontoanno.sh](/proj/bzou_lab/projects/OntoAnno/start_ontoanno.sh): app launcher
- [chamber_demo.yaml](/proj/bzou_lab/projects/OntoAnno/configs/chamber_demo.yaml): example dataset config
- [`configs/`](/proj/bzou_lab/projects/OntoAnno/configs): more example configs

## In One Line

Run `bash setup.sh` once, then launch OntoAnno with `./start_ontoanno.sh`.
