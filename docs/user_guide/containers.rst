Container Usage
===============

Containers provide the most reproducible way to run OntoAnno because they bundle
the app environment with Python and R dependencies.

Path Conventions
----------------

Docker and Apptainer use the same internal paths:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Host path
     - Container path
   * - ``./data``
     - ``/data``
   * - ``./work``
     - ``/work``
   * - ``./runs``
     - Runtime output area, depending on the launcher and config.

Configs used inside a container must refer to the container paths, not the host
paths.

Docker
------

Prepare the workspace:

.. code-block:: bash

   bash docker_setup.sh

Edit ``.env``:

.. code-block:: text

   OPENAI_API_KEY=...
   ONTOANNO_CONFIG=configs/docker_fresh_template.yaml

Start:

.. code-block:: bash

   ./start_ontoanno_docker.sh

Open the printed local URL, usually ``http://127.0.0.1:8501``.

Apptainer
---------

Prepare the workspace:

.. code-block:: bash

   bash apptainer_setup.sh

Edit ``.env``:

.. code-block:: text

   OPENAI_API_KEY=...
   ONTOANNO_CONFIG=configs/docker_fresh_template.yaml
   ONTOANNO_IMAGE=docker://ghcr.io/tamuya23/ontoanno:latest

Start:

.. code-block:: bash

   ./start_ontoanno_apptainer.sh

The first run pulls the image into ``.apptainer/``. Later runs reuse the local
``.sif`` file unless you refresh it.

Choosing a Template
-------------------

Use ``configs/docker_fresh_template.yaml`` when starting from a raw Seurat RDS.
Use ``configs/docker_import_template.yaml`` when importing an existing
annotation output folder.
