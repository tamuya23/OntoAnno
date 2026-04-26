Troubleshooting
===============

Most first-run problems are simple path, config, or API-key issues.

The App Opens the Wrong Dataset
-------------------------------

Check ``.env``:

.. code-block:: text

   ONTOANNO_CONFIG=configs/my_project.yaml

Then stop and restart the app.

If you start locally with a command, make sure you passed the right config:

.. code-block:: bash

   ./start_ontoanno.sh configs/my_project.yaml

The Seurat File Cannot Be Found
-------------------------------

If you are using Docker or Apptainer, the config should use ``/data/...``.

Correct:

.. code-block:: yaml

   inputs:
     seurat_rds: /data/my_project/my_dataset.rds

Usually wrong inside a container:

.. code-block:: yaml

   inputs:
     seurat_rds: /proj/bzou_lab/projects/OntoAnno/data/my_project/my_dataset.rds

The host file should exist here:

.. code-block:: text

   data/my_project/my_dataset.rds

The API Key Is Missing
----------------------

Open ``.env`` and check:

.. code-block:: text

   OPENAI_API_KEY=your_api_key_here

Do not use quotes. Do not leave spaces around ``=``.

After editing ``.env``, restart the app.

The Web Page Does Not Open
--------------------------

Look for the URL printed in the terminal:

.. code-block:: text

   http://127.0.0.1:8501

If you are using VS Code over SSH, use the forwarded-port link from VS Code.

If port ``8501`` is already in use, start with a different port in local mode:

.. code-block:: bash

   ontoanno ui --config configs/my_project.yaml --server-port 8502

R Jobs Fail
-----------

This usually means the R environment or ``Rscript`` path is not ready.

For local setup, run:

.. code-block:: bash

   bash setup.sh

For Docker or Apptainer, make sure you are using the container launcher, not the
local launcher.

The First Run Is Too Slow or Expensive
--------------------------------------

Use fewer repeated LLM runs while testing:

.. code-block:: yaml

   n_runs_parent: 3
   n_runs_sub: 3

After the workflow is correct, increase to ``10`` for the final run.

The Labels Are Too Broad or Too Fine
------------------------------------

First check the selected resolution in the app:

.. code-block:: text

   What is the current selected resolution?

If labels are too broad, try adding a higher parent resolution, such as ``0.5``.
If labels are too fragmented, try using lower parent resolutions, such as
``0.1`` and ``0.3`` only.

The Page Looks Stale
--------------------

Click ``Refresh runtime state`` in the sidebar.

The Chat Seems Confused
-----------------------

Click ``Reset agent session`` in the sidebar, then ask again.

I Need to Check the Config Before Running
-----------------------------------------

Run:

.. code-block:: bash

   ontoanno validate --config configs/my_project.yaml

Fix any missing path or required field shown in the output.
