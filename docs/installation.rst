Installation
============

This page helps you choose the simplest setup. If someone in your lab already
prepared OntoAnno on a shared server, you may only need to edit a config and run
the start script.

Which Setup Should I Use?
-------------------------

.. list-table::
   :header-rows: 1
   :widths: 24 38 38

   * - Setup
     - Use this when
     - Main command
   * - Docker
     - You are on a laptop, workstation, or Linux server with Docker.
     - ``./start_ontoanno_docker.sh``
   * - Apptainer
     - You are on an HPC system where Docker is not allowed.
     - ``./start_ontoanno_apptainer.sh``
   * - Local
     - Python, R, and GPTAnno dependencies are already installed.
     - ``./start_ontoanno.sh``

Most users should choose Docker or Apptainer.

Prepare an API Key
------------------

OntoAnno needs access to an LLM provider. For the current templates, prepare an
OpenAI API key.

For Docker and Apptainer, the key goes in ``.env``:

.. code-block:: text

   OPENAI_API_KEY=your_api_key_here

Keep ``.env`` private.

Docker Setup
------------

Use Docker if you are on your own computer or a regular server.

1. Open the repository:

   .. code-block:: bash

      cd /proj/bzou_lab/projects/OntoAnno

2. Prepare folders and ``.env``:

   .. code-block:: bash

      bash docker_setup.sh

3. Edit ``.env``:

   .. code-block:: text

      OPENAI_API_KEY=your_api_key_here
      ONTOANNO_CONFIG=configs/my_project.yaml

4. Put your data in ``data/`` and use ``/data/...`` in the config.

5. Start:

   .. code-block:: bash

      ./start_ontoanno_docker.sh

Apptainer Setup
---------------

Use Apptainer on HPC systems such as Longleaf.

1. Open the repository:

   .. code-block:: bash

      cd /proj/bzou_lab/projects/OntoAnno

2. Prepare folders and ``.env``:

   .. code-block:: bash

      bash apptainer_setup.sh

3. Edit ``.env``:

   .. code-block:: text

      OPENAI_API_KEY=your_api_key_here
      ONTOANNO_CONFIG=configs/my_project.yaml
      ONTOANNO_IMAGE=docker://ghcr.io/tamuya23/ontoanno:latest

4. Put your data in ``data/`` and use ``/data/...`` in the config.

5. Start:

   .. code-block:: bash

      ./start_ontoanno_apptainer.sh

The first Apptainer launch may take longer because it pulls the image.

Local Setup
-----------

Use local setup only if the required Python and R environments are available.

.. code-block:: bash

   cd /proj/bzou_lab/projects/OntoAnno
   bash setup.sh
   ./start_ontoanno.sh configs/my_project.yaml

Next Step
---------

After installation, follow :doc:`user_guide/index` to annotate a new Seurat
dataset.
