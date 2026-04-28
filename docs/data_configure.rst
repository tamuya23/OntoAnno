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

   annotation:
     species: human
     tissue_name: human pancreatic tumor
     parent_res:
       - 0.1
       - 0.3
     preprocess: true


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
   * - ``annotation.species``
     - The species used for ontology and marker evidence lookup.
     - ``human`` or ``mouse``
   * - ``annotation.tissue_name``
     - A biological description of the dataset tissue or disease context.
     - ``human pancreatic tumor`` or ``mouse brain cortex``
   * - ``annotation.parent_res``
     - Clustering resolutions OntoAnno will test for parent annotation.
     - ``0.1``, ``0.3``, ``0.5``
   * - ``annotation.preprocess``
     - Whether OntoAnno should run Seurat normalization, variable feature selection, scaling, PCA, and UMAP before clustering.
     - ``true``

Optional Fields
---------------

These fields are shown in ``configs/demo_optional.yaml``. Change them only when
you want to use the specific feature or intentionally change pipeline behavior.

.. list-table::
   :header-rows: 1
   :widths: 28 42 30

   * - Field
     - What it controls
     - Example / options
   * - ``inputs.reference_labels_csv``
     - Optional labels from another method, used only for evaluation/comparison.
     - ``/data/project/labels.csv`` or ``null``
   * - ``inputs.pdf_dir``
     - Folder of literature PDFs for external marker evidence extraction.
     - ``/data/project/pdfs`` or ``null``
   * - ``inputs.marker_genes_dir``
     - Existing cluster marker files if you want to skip marker detection and start from annotation. The folder should contain files such as ``markers_res_0.1.rds`` for each parent resolution.
     - ``/data/project/marker_genes`` or ``null``
   * - ``annotation.sub_res``
     - Resolutions used when subclustering one parent cell type.
     - ``0.1``, ``0.2``
   * - ``annotation.min_cell_count``
     - Minimum cells required before running annotation.
     - ``3000``
   * - ``annotation.n_runs_parent``
     - Number of repeated LLM annotation runs for parent annotation.
     - ``3`` for testing, ``10`` for final runs
   * - ``annotation.n_runs_sub``
     - Number of repeated LLM annotation runs for subcluster annotation.
     - ``3`` for testing, ``10`` for final runs
   * - ``policy``
     - Ontology restriction, granularity preference, and review rules.
     - ``ontology: true``, ``granularity: balanced``
   * - ``llm``
     - OpenAI model settings and API key references.
     - ``model: gpt-5``, ``api_key: ${OPENAI_API_KEY}``
   * - ``alignment``
     - Ontology alignment and subcluster inheritance behavior.
     - ``combine_restrictions: true``, ``on_missing_decision: stop``
   * - ``evaluation``
     - Optional comparison against external or manual labels.
     - ``enabled: false``
   * - ``report``
     - Final report format.
     - ``html``
