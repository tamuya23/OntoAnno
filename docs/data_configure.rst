Data Configure
==============

Before starting OntoAnno, open ``configs/demo.yaml`` and fill in the fields for
your dataset. This is the minimum template for a normal first run.

If you want optional features such as reference-label comparison, PDF evidence,
or precomputed marker genes, use ``configs/demo_optional.yaml`` as the example
template.

Required Fields
---------------

For a first run, you usually only need to edit these fields:

.. code-block:: yaml

   project:
     name: MyProject
     work_dir: /work/MyProject

   inputs:
     seurat_rds: /data/my_project/my_dataset.rds

   llm:
     annotation:
       api_key: ${OPENAI_API_KEY}

   annotation:
     species: human
     tissue_name: human pancreatic tumor
     parent_res:
       - 0.1
       - 0.3


.. list-table::
   :header-rows: 1
   :widths: 28 42 30

   * - Field
     - What it controls
     - Example
   * - ``project.name``
     - A short name for this OntoAnno run. Use letters, numbers, or underscores.
     - ``PDAC_sn``
   * - ``project.work_dir``
     - Where OntoAnno saves memory, intermediate files, reviewed labels, and reports.
     - ``/work/PDAC_sn``
   * - ``inputs.seurat_rds``
     - The Seurat ``.rds`` file to annotate.
     - ``/data/pdac/pdac_sn.rds``
   * - ``llm.annotation.api_key``
     - OpenAI API key reference. Set ``OPENAI_API_KEY`` in the environment; do not paste the real key into the YAML file.
     - ``${OPENAI_API_KEY}``
   * - ``annotation.species``
     - The species used for ontology and marker evidence lookup.
     - ``human`` or ``mouse``
   * - ``annotation.tissue_name``
     - A biological description of the dataset tissue or disease context.
     - ``human pancreatic tumor`` or ``mouse brain cortex``
   * - ``annotation.parent_res``
     - Clustering resolutions OntoAnno will test for parent annotation.
     - ``0.1``, ``0.3``, ``0.5``

Optional Fields
---------------

These fields are shown in ``configs/demo_optional.yaml``. Change them only when
you want to use the specific feature or intentionally change pipeline behavior.

Optional Input Sources
~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 28 42 30

   * - Field
     - What it controls
     - Example / options
   * - ``inputs.reference_labels_csv``
     - Optional labels from another method. This does not change OntoAnno annotation; it is used only when ``evaluation.enabled`` is ``true``.
     - ``/data/project/labels.csv`` or ``null``. CSV should include a cell ID/barcode column and a label column such as ``celltype``.
   * - ``inputs.pdf_dir``
     - Folder of literature PDFs for marker evidence extraction. Empty or ``null`` skips PDF extraction.
     - ``/data/project/pdfs`` or ``null``. Put ``.pdf`` files directly in this folder.
   * - ``inputs.marker_genes_dir``
     - Existing cluster marker files if you want to skip marker detection and start from annotation. The Seurat object must already contain matching ``cluster_res.<resolution>`` metadata columns.
     - ``/data/project/marker_genes`` or ``null``

Annotation Behavior
~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 28 42 30

   * - Field
     - What it controls
     - Example / options
   * - ``annotation.preprocess``
     - Whether OntoAnno should run Seurat normalization, variable feature selection, scaling, PCA, UMAP, and clustering before annotation. Set ``false`` only for an already processed object with matching cluster metadata.
     - ``true`` or ``false``
   * - ``annotation.sub_res``
     - Resolutions used only when you ask OntoAnno to subcluster a parent cell type.
     - ``0.1``, ``0.2``
   * - ``annotation.min_cell_count``
     - Minimum number of cells required before running subclustering for a parent cell type.
     - ``3000``
   * - ``annotation.n_runs_parent``
     - Number of repeated LLM calls for parent annotation. Larger values cost more and take longer but can stabilize labels.
     - ``3`` for testing, ``10`` for final runs
   * - ``annotation.n_runs_sub``
     - Number of repeated LLM calls for subcluster annotation. Ignored if no subclustering is requested.
     - ``3`` for testing, ``10`` for final runs

Ontology And Review Policy
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 28 42 30

   * - Field
     - What it controls
     - Example / options
   * - ``policy.ontology``
     - Whether RAG review should prefer ontology-mapped candidate labels when possible.
     - ``true`` or ``false``
   * - ``policy.granularity``
     - Label specificity during review. This does not change clustering resolution.
     - ``coarse``, ``balanced``, or ``fine``
   * - ``policy.review_tie``
     - Whether tied label decisions should go to human review.
     - ``true`` or ``false``
   * - ``policy.review_nomatch``
     - Whether unknown or poorly matched labels should go to human review.
     - ``true`` or ``false``

LLM And PDF Models
~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 28 42 30

   * - Field
     - What it controls
     - Example / options
   * - ``llm.annotation.model``
     - Model used for main annotation and review. Changing it can change labels, cost, and runtime.
     - ``gpt-5``
   * - ``llm.pdfmarkers.model``
     - Model used only when ``inputs.pdf_dir`` contains PDFs.
     - ``gpt-5-nano``

Subclustering
~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 28 42 30

   * - Field
     - What it controls
     - Example / options
   * - ``alignment.celltypes_to_subcluster``
     - Parent cell types to split into finer subclusters. Keep ``null`` to skip preset subclustering; you can also ask the agent later.
     - ``null`` or ``["macrophage", "T cell"]``

Evaluation And Report
~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 28 42 30

   * - Field
     - What it controls
     - Example / options
   * - ``evaluation.enabled``
     - Whether to compare OntoAnno labels against ``inputs.reference_labels_csv``. A CSV path alone does not enable evaluation.
     - ``true`` or ``false``
   * - ``evaluation.manual_col``
     - Column name in ``reference_labels_csv`` that contains the known labels.
     - ``celltype`` or ``SingleR_labels``
   * - ``report.format``
     - Final report format. ``html`` is the safest default.
     - ``html`` or ``pdf``

Advanced Fields Not Shown In demo_optional.yaml
-----------------------------------------------

Most users should not edit these. They are still supported by the config loader
when needed.

.. list-table::
   :header-rows: 1
   :widths: 28 42 30

   * - Field
     - When it matters
     - Notes
   * - ``inputs.annotation_output_dir``
     - Import an existing GPTAnno parent annotation output for review/RAG/report workflows.
     - Advanced import path. It is not a replacement for ``inputs.seurat_rds`` in a normal full run.
   * - ``llm.annotation.api_url``
     - Use a custom OpenAI-compatible gateway.
     - Keep default ``null`` for normal OpenAI.
   * - ``llm.annotation.system_prompt``
     - Override the model's system instruction.
     - Can change annotation behavior; leave unset unless you know why.
   * - ``alignment.user_restrict_to`` / ``alignment.manual_resolution_map``
     - Force advanced subcluster label or resolution choices.
     - Developer or expert use only.
   * - ``evaluation.baselines``
     - Run external baseline comparison commands.
     - Advanced and potentially executes shell commands.
