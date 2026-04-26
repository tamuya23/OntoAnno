Annotate a New Seurat Dataset
=============================

This guide is written for a biologist who has a Seurat object and wants cell
type annotation. You do not need to understand the internal Python or R code to
follow it.

The goal is simple:

1. Start with one Seurat ``.rds`` file.
2. Tell OntoAnno where the file is and what tissue/species it is.
3. Open the OntoAnno web app.
4. Ask the app to run annotation.
5. Review the labels and download or open the final report.

Step 0: Know What File You Need
--------------------------------

OntoAnno expects a Seurat object saved as an RDS file.

If your object is already in R as ``seurat_obj``, save it like this:

.. code-block:: r

   saveRDS(seurat_obj, "my_dataset.rds")

The object should contain:

* Cells as columns and genes as features.
* Gene names in the feature names.
* Cell metadata in the Seurat metadata table.
* Enough cells per major population for annotation to be meaningful.

If the object has not already been normalized or reduced, set
``annotation.preprocess: true`` in the config. If the object is already
preprocessed and clustered, you can still let OntoAnno run its own annotation
workflow.

Step 1: Choose How You Will Run OntoAnno
-----------------------------------------

There are three ways to run OntoAnno:

.. list-table::
   :header-rows: 1
   :widths: 24 38 38

   * - Option
     - Best for
     - What you need
   * - Docker
     - Laptop, workstation, or regular Linux server
     - Docker or Docker Desktop
   * - Apptainer
     - HPC systems such as Longleaf
     - Apptainer and a published OntoAnno image
   * - Local setup
     - A machine where Python and R packages are already configured
     - Python 3.11, R, GPTAnno dependencies

If you are unsure, use Docker on your own computer and Apptainer on an HPC
cluster.

Step 2: Put Your Data in the Project Folder
--------------------------------------------

From the OntoAnno repository:

.. code-block:: bash

   cd /proj/bzou_lab/projects/OntoAnno
   mkdir -p data/my_project

Copy your Seurat file into that folder and give it a simple name:

.. code-block:: text

   data/my_project/my_dataset.rds

If you are using Docker or Apptainer, this file will be seen inside the
container as:

.. code-block:: text

   /data/my_project/my_dataset.rds

This difference is important. In container configs, use ``/data/...`` paths.

Step 3: Create Your Own Config File
------------------------------------

Copy the fresh dataset template:

.. code-block:: bash

   cp configs/docker_fresh_template.yaml configs/my_project.yaml

Open ``configs/my_project.yaml`` in a text editor.

For a first run, only edit the fields shown below:

.. code-block:: yaml

   project:
     name: MyProject
     work_dir: /work/MyProject

   inputs:
     seurat_rds: /data/my_project/my_dataset.rds
     manual_labels_csv: null
     pdf_dir: null

   annotation:
     species: human
     parent_res:
       - 0.1
       - 0.3
     sub_res:
       - 0.1
       - 0.2
     preprocess: true
     min_cell_count: 3000
     tissue_name: human pancreatic tumor
     n_runs_parent: 3
     n_runs_sub: 3

   report:
     format: html

What these fields mean:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Field
     - What to put there
   * - ``project.name``
     - A short name for your dataset. Avoid spaces for the first test.
   * - ``project.work_dir``
     - Where intermediate outputs are saved. In containers, use ``/work/...``.
   * - ``inputs.seurat_rds``
     - The path to your Seurat ``.rds`` file. In containers, use ``/data/...``.
   * - ``annotation.species``
     - Usually ``human`` or ``mouse``.
   * - ``annotation.tissue_name``
     - A plain-language tissue description, such as ``mouse heart``.
   * - ``annotation.parent_res``
     - Clustering resolutions for major cell types. Start with two values.
   * - ``annotation.preprocess``
     - Use ``true`` if you want OntoAnno to run preprocessing.
   * - ``n_runs_parent`` and ``n_runs_sub``
     - Number of repeated LLM annotation runs. Use ``3`` for testing and ``10``
       for a final run.

Keep the other fields unchanged for your first dataset.

Step 4: Add Your API Key
-------------------------

If you use Docker or Apptainer, edit ``.env``:

.. code-block:: text

   OPENAI_API_KEY=your_api_key_here
   ONTOANNO_CONFIG=configs/my_project.yaml

If you are using Apptainer, also set:

.. code-block:: text

   ONTOANNO_IMAGE=docker://ghcr.io/tamuya23/ontoanno:latest

Do not put quotation marks around the API key. Do not commit ``.env`` to GitHub.

Step 5: Start OntoAnno
-----------------------

For Docker:

.. code-block:: bash

   bash docker_setup.sh
   ./start_ontoanno_docker.sh

For Apptainer:

.. code-block:: bash

   bash apptainer_setup.sh
   ./start_ontoanno_apptainer.sh

For a local setup:

.. code-block:: bash

   bash setup.sh
   ./start_ontoanno.sh configs/my_project.yaml

The terminal should print a local web address, usually:

.. code-block:: text

   http://127.0.0.1:8501

Open that address in your browser.

Step 6: Check That the Correct Dataset Loaded
---------------------------------------------

In the left sidebar, check:

* The project name is your project.
* The config path points to ``configs/my_project.yaml``.
* The species and tissue are correct.
* The selected work directory is the one you expected.

If the wrong dataset appears, stop the app, check ``ONTOANNO_CONFIG`` in
``.env``, and start again.

Step 7: Run the Annotation
--------------------------

Use the chat box in the center of the app. For a first run, type exactly:

.. code-block:: text

   Run the parent annotation

Wait until the job finishes. The right panel shows terminal output and status.

Then ask:

.. code-block:: text

   What is the current selected resolution?

Look at the prediction plots and artifacts. If the major labels look reasonable,
continue:

.. code-block:: text

   Run the RAG-based check

Then generate the report:

.. code-block:: text

   Generate the final report

Step 8: Find Your Results
-------------------------

For a container run with ``project.work_dir: /work/MyProject``, the results are
saved under the host folder:

.. code-block:: text

   work/MyProject/
   runs/

The final report is written in the active run directory as:

.. code-block:: text

   runs/<your_project_run_id>/report.html

In the web app, use the ``Artifacts`` panel to open plots, tables, reviewed
outputs, and the report.

Step 9: Run the Final Version
-----------------------------

For the first test, ``n_runs_parent: 3`` and ``n_runs_sub: 3`` are faster and
cheaper. After the workflow looks correct, change them to:

.. code-block:: yaml

   n_runs_parent: 10
   n_runs_sub: 10

Then run annotation again for the final result.

Common First-Run Choices
------------------------

For a small or medium dataset:

.. code-block:: yaml

   parent_res: [0.1, 0.3]
   sub_res: [0.1, 0.2]
   n_runs_parent: 3
   n_runs_sub: 3

For a larger dataset with many expected cell types:

.. code-block:: yaml

   parent_res: [0.1, 0.3, 0.5]
   sub_res: [0.1, 0.2, 0.3]
   n_runs_parent: 3
   n_runs_sub: 3

For a final publication-quality run:

.. code-block:: yaml

   n_runs_parent: 10
   n_runs_sub: 10

If Something Goes Wrong
-----------------------

Most first-run problems are one of these:

* The Seurat file path is wrong.
* The config uses a host path instead of a container path.
* The API key is missing.
* The R environment inside the chosen setup is not ready.
* The app is reading a different config than the one you edited.

See :doc:`../troubleshooting` for fixes.

More Detail
-----------

.. toctree::
   :maxdepth: 1

   configuration
   containers
   interface
   workflow
