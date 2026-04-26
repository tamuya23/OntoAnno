Quick Test
==========

Use this page to confirm that OntoAnno starts correctly before spending time and
API credits on a real dataset.

The Purpose of a Quick Test
---------------------------

A quick test answers four questions:

1. Can the app start?
2. Can OntoAnno read the selected config?
3. Can it see the data paths in that config?
4. Can you open the web page in a browser?

Step 1: Open the Repository
---------------------------

.. code-block:: bash

   cd /proj/bzou_lab/projects/OntoAnno

Step 2: Pick a Config
---------------------

For a prepared demo or existing output folder, use:

.. code-block:: text

   configs/chamber_demo.yaml

For your own fresh Seurat object, first create a config by following
:doc:`user_guide/index`.

Step 3: Start the App
---------------------

Local setup:

.. code-block:: bash

   ./start_ontoanno.sh configs/chamber_demo.yaml

Docker setup:

.. code-block:: bash

   bash docker_setup.sh
   ./start_ontoanno_docker.sh

Apptainer setup:

.. code-block:: bash

   bash apptainer_setup.sh
   ./start_ontoanno_apptainer.sh

Step 4: Open the Browser
------------------------

Open the URL printed in the terminal, usually:

.. code-block:: text

   http://127.0.0.1:8501

If you are using VS Code over SSH, use the forwarded port link that VS Code
shows.

Step 5: Check the Page
----------------------

Before running annotation, check:

* The project name in the sidebar.
* The config path.
* The work directory.
* The Status panel.
* The Artifacts panel.

If these look correct, continue to :doc:`user_guide/index` and run your own
dataset.
