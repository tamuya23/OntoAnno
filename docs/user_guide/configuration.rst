Configuration for a New Dataset
===============================

This page explains the config file from a user's point of view. For your first
dataset, you should not need to edit every field.

Start From a Template
---------------------

For a new Seurat ``.rds`` file:

.. code-block:: bash

   cp configs/docker_fresh_template.yaml configs/my_project.yaml

For an existing GPTAnno/OntoAnno annotation output folder:

.. code-block:: bash

   cp configs/docker_import_template.yaml configs/my_project.yaml

Only Edit These Fields First
----------------------------

For a new Seurat object, focus on this small set:

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

Path Rules
----------

Path mistakes are the most common problem.

If you are running inside Docker or Apptainer:

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - File on your computer
     - Path to write in the config
   * - ``data/my_project/my_dataset.rds``
     - ``/data/my_project/my_dataset.rds``
   * - ``work/MyProject``
     - ``/work/MyProject``

If you are running locally without a container, use the real local path, for
example:

.. code-block:: yaml

   inputs:
     seurat_rds: /proj/bzou_lab/projects/OntoAnno/data/my_project/my_dataset.rds

Species and Tissue
------------------

Use simple biological words:

.. code-block:: yaml

   annotation:
     species: human
     tissue_name: human pancreatic tumor

Good tissue names:

* ``mouse heart``
* ``human blood``
* ``human pancreatic tumor``
* ``mouse brain cortex``

Avoid vague names like ``sample1`` or ``dataset``.

Resolutions
-----------

For a first test, use a small number of resolutions:

.. code-block:: yaml

   parent_res:
     - 0.1
     - 0.3

For a larger or more complex dataset:

.. code-block:: yaml

   parent_res:
     - 0.1
     - 0.3
     - 0.5

``sub_res`` controls finer annotation inside a parent cell type:

.. code-block:: yaml

   sub_res:
     - 0.1
     - 0.2

Repeated LLM Runs
-----------------

Use fewer runs for testing:

.. code-block:: yaml

   n_runs_parent: 3
   n_runs_sub: 3

Use more runs for the final version:

.. code-block:: yaml

   n_runs_parent: 10
   n_runs_sub: 10

More runs are slower and use more API credits, but they give more stable
reproducibility estimates.

Manual Labels Are Optional
--------------------------

If you already have labels from SingleR, Azimuth, manual curation, or another
method, you can add them for evaluation:

.. code-block:: yaml

   inputs:
     manual_labels_csv: /data/my_project/manual_labels.csv

   evaluation:
     enabled: true
     manual_col: SingleR_labels

If you do not have manual labels, leave:

.. code-block:: yaml

   manual_labels_csv: null
   evaluation:
     enabled: false

Do Not Change These at First
----------------------------

For the first run, keep these sections from the template:

* ``policy``
* ``alignment``
* ``llm.external_evidence``
* ``llm.pdfmarkers``
* ``evaluation.baselines``

Change them later only when the basic annotation workflow is working.
