Quick Start
===========

This page shows the fastest way to start OntoAnno for your dataset.

Get OntoAnno
------------

First, clone the OntoAnno repository:

.. code-block:: bash

   git clone https://github.com/tamuya23/OntoAnno.git
   cd OntoAnno

Choose Installation Method
--------------------------

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

Install Docker if Needed
------------------------

If ``docker --version`` fails, install Docker before using the Docker setup.

* On a laptop or workstation, install `Docker Desktop <https://docs.docker.com/desktop/>`_.
* On a Linux server where you have admin access, install `Docker Engine <https://docs.docker.com/engine/install/>`_.
* On HPC systems where Docker is not available or not allowed, use the Apptainer setup instead.

After installation, check:

.. code-block:: bash

   docker --version
   docker compose version

Docker Setup
------------

Use Docker if you are on your own computer or a regular server.

1. Prepare folders and set API key:

   .. code-block:: bash

      bash docker_setup.sh
      OPENAI_API_KEY=your_api_key_here

2. Put your data files in ``data/``. Then configure ``configs/demo.yaml``
   using :doc:`data_configure`.

3. Start OntoAnno:

   .. code-block:: bash

      ./start_ontoanno_docker.sh configs/demo.yaml


Apptainer Setup
---------------

Use Apptainer on HPC systems such as Longleaf.

1. Prepare folders and set API key:

   .. code-block:: bash

      bash apptainer_setup.sh
      OPENAI_API_KEY=your_api_key_here
      ONTOANNO_IMAGE=docker://ghcr.io/tamuya23/ontoanno:latest

2. Put your data files in ``data/``. Then configure ``configs/demo.yaml``
   using :doc:`data_configure`.

3. Start OntoAnno:

   .. code-block:: bash

      ./start_ontoanno_apptainer.sh configs/demo.yaml

Open the Web App
----------------

After starting OntoAnno with Docker or Apptainer, open the URL printed in the
terminal, usually ``http://127.0.0.1:8501``.

If you are connected through SSH or HPC, use VS Code port forwarding or forward
port ``8501`` manually.

---------

After installation, follow :doc:`agent_guide` to annotate a new Seurat
dataset.
