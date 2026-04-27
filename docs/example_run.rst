Example Run
===========

This page shows a typical first OntoAnno session after the app opens in the
browser. Use it as a checklist when testing a new dataset.

Before You Start
----------------

Confirm these items first:

* Your Seurat object is under ``data/``.
* ``configs/demo.yaml`` points to the dataset using a ``/data/...`` path.
* ``project.name`` and ``project.work_dir`` are set for this dataset.
* The web app opens without an error.

Step 1: Check Project State
---------------------------

In the chat box, ask:

.. code-block:: text

   What is the current project state?

Check that the response matches your dataset, species, tissue, parent
resolutions, and selected resolution status.

Step 2: Run Parent Annotation
-----------------------------

Start the parent workflow:

.. code-block:: text

   Run the parent annotation

Watch the ``Status`` tab while it runs. The workflow should move through
``Cluster`` and ``Annotate``. After completion, open ``Artifacts`` and check:

* Parent annotation table.
* Prediction figure.
* Resolution score table.

Step 3: Inspect Resolution
--------------------------

Ask:

.. code-block:: text

   What is the selected resolution?

If the cluster resolution is not what you want, change it directly:

.. code-block:: text

   Change the resolution to 0.3

Use resolution changes when the cluster structure is too coarse or too fine.
Use granularity changes only when the clusters are acceptable but the label
names are too broad or too specific.

Step 4: Add Known Marker Evidence
---------------------------------

If you know a cell type and its markers, add them explicitly:

.. code-block:: text

   Add these markers to pericyte: RGS5, CSPG4, MCAM

Then check ``External Evidence`` to confirm the markers were stored under the
correct cell type.

Step 5: Run RAG Check
---------------------

After parent annotation is available, run:

.. code-block:: text

   Run the RAG check

Open ``Artifacts`` and check the RAG review table. Focus on clusters with
ambiguous labels, low support, or suggested alternatives.

Step 6: Resolve Unclear Clusters
--------------------------------

If OntoAnno reports unresolved clusters, use the manual review controls in the
RAG review panel or ask the agent to continue:

.. code-block:: text

   Continue with human review

Review each unresolved cluster using its marker genes and candidate labels.
Choose the final label or provide a custom label.

Step 7: Subcluster if Needed
----------------------------

If one parent cell type is broad, ask OntoAnno to inspect it more deeply:

.. code-block:: text

   Look deeper into macrophages

After subclustering finishes, inspect the subcluster table and figures in
``Artifacts``.

Step 8: Generate Report
-----------------------

When annotation and review are complete, ask:

.. code-block:: text

   Generate the final report

Open ``Artifacts`` and preview the report. The report should summarize parent
annotations, selected resolution, RAG review, human review decisions,
subcluster outputs, figures, and saved result files.

What a Good Test Looks Like
---------------------------

A successful first run should have:

* Green or completed status for ``Cluster`` and ``Annotate``.
* A selected parent resolution in the left project panel.
* Parent annotation rows in ``Artifacts``.
* A prediction figure that matches the selected resolution.
* RAG review outputs after ``Run the RAG check``.
* A final report preview after ``Generate the final report``.
