Annotation Workflow
===================

OntoAnno combines GPTAnno's ontology-guided annotation with a Python control
layer and a review-oriented web interface.

Project Initialization
----------------------

Each project starts from a YAML config. At minimum, the config provides a
project name, a work directory, input files or imported annotation outputs, LLM
settings, annotation parameters, alignment policy, evaluation settings, and
report format.

Validate a config before running:

.. code-block:: bash

   ontoanno validate --config configs/chamber_demo.yaml

Parent Annotation
-----------------

The parent annotation stage identifies major cell types across candidate
clustering resolutions. GPTAnno can repeatedly query the configured LLM to
estimate reproducibility and score resolutions with ontology-aware metrics.

Run the full configured pipeline:

.. code-block:: bash

   ontoanno run --config configs/chamber_demo.yaml

Run only a bounded stage range:

.. code-block:: bash

   ontoanno run --config configs/chamber_demo.yaml --from preflight --to annotate_parent

Review Packets
--------------

Review packets summarize cluster-level candidates, ontology matches, and
candidate conflicts for downstream checking.

.. code-block:: bash

   ontoanno review-packets --config configs/chamber_demo.yaml

Ontology Relations and LLM Compare
----------------------------------

The ontology relation stage maps candidates onto Cell Ontology relationships.
The LLM compare stage uses reference-assisted checks for ambiguous or
conflicting candidates.

.. code-block:: bash

   ontoanno ontology-relations --config configs/chamber_demo.yaml
   ontoanno llm-compare --config configs/chamber_demo.yaml

Controller and Workers
----------------------

The controller builds the next-action plan from current ontology and LLM
outputs. Advanced users can inspect available workers or run a named worker.

.. code-block:: bash

   ontoanno controller --config configs/chamber_demo.yaml
   ontoanno workers --config configs/chamber_demo.yaml
   ontoanno worker-run --config configs/chamber_demo.yaml --worker controller

Subclustering
-------------

Subclustering refines broad parent labels into more specific populations when a
cell type is large enough or has useful Cell Ontology descendants. The policy
can restrict predictions to ontology terms, use parent marker inheritance, or
combine evidence depending on the config.

Evidence and RAG Review
-----------------------

OntoAnno tracks user-provided evidence and literature-derived evidence. In the
web app, add evidence through the External Evidence tab or by asking the agent
to remember markers for a cell type.

Final Report
------------

Reports can be rebuilt from the latest run without re-running the full
annotation workflow:

.. code-block:: bash

   ontoanno report --config configs/chamber_demo.yaml --force

The report is written into the project run directory as ``report.html`` or
``report.pdf``, depending on ``report.format``.
