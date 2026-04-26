Command Line Interface
======================

The ``ontoanno`` command exposes validation, pipeline execution, reporting,
chat, UI launch, and worker-level controls.

Validation
----------

.. code-block:: bash

   ontoanno validate --config configs/chamber_demo.yaml

Pipeline Runs
-------------

Run the configured pipeline:

.. code-block:: bash

   ontoanno run --config configs/chamber_demo.yaml

Run a stage range:

.. code-block:: bash

   ontoanno run --config configs/chamber_demo.yaml --from preflight --to report

Force regeneration:

.. code-block:: bash

   ontoanno run --config configs/chamber_demo.yaml --force

Reports
-------

.. code-block:: bash

   ontoanno report --config configs/chamber_demo.yaml --force

PDF Markers
-----------

Run the PDF marker stage with config defaults:

.. code-block:: bash

   ontoanno pdfmarkers --config configs/chamber_demo.yaml

Override input files:

.. code-block:: bash

   ontoanno pdfmarkers --config configs/chamber_demo.yaml --pdf paper.pdf
   ontoanno pdfmarkers --config configs/chamber_demo.yaml --pdf-dir papers/

Review and Ontology Stages
--------------------------

.. code-block:: bash

   ontoanno review-packets --config configs/chamber_demo.yaml
   ontoanno ontology-relations --config configs/chamber_demo.yaml
   ontoanno llm-compare --config configs/chamber_demo.yaml
   ontoanno controller --config configs/chamber_demo.yaml

Agent and Chat
--------------

Run one natural-language request:

.. code-block:: bash

   ontoanno ask --config configs/chamber_demo.yaml --message "Run the RAG-based check"

Start an interactive terminal chat:

.. code-block:: bash

   ontoanno chat --config configs/chamber_demo.yaml

Start the web UI:

.. code-block:: bash

   ontoanno ui --config configs/chamber_demo.yaml --server-port 8501

Workers
-------

Inspect worker contracts:

.. code-block:: bash

   ontoanno workers --config configs/chamber_demo.yaml

Run one deployed worker:

.. code-block:: bash

   ontoanno worker-run --config configs/chamber_demo.yaml --worker controller
