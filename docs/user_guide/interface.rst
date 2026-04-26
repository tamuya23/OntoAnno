Workbench Interface
===================

The Streamlit app is the main interactive workspace.

Project Summary
---------------

The left sidebar shows the current project state:

* Project name.
* Run ID.
* Tested parent resolutions.
* Selected resolution.
* Granularity policy.
* Ontology restriction status.
* Evidence memory counts.

Use this panel to confirm that the app is attached to the expected dataset and
config.

Session Controls
----------------

The sidebar controls manage the live session:

* ``Reset agent session`` clears the current conversation state.
* ``Refresh runtime state`` reloads saved outputs and status from disk.

Refresh when the page looks stale. Reset only when you want to start a new
conversation context.

Agent Chat
----------

The center panel accepts natural-language requests. Common examples:

.. code-block:: text

   Run the parent annotation
   Show me the current artifacts
   Run the RAG-based check
   Look deeper into macrophages
   Generate the final report

The router maps these requests onto configured workers and pipeline commands.

Status and Output
-----------------

The right panel helps monitor active work:

* ``Status`` shows pipeline stages.
* ``Terminal Output`` shows live output from the running worker.
* ``Artifacts`` shows plots, tables, reviewed outputs, and reports.

Use this panel while a job is running or when checking whether expected outputs
were generated.

Evidence and Logs
-----------------

Additional tabs expose:

* ``External Evidence`` for user and literature evidence.
* ``Workers`` for advanced manual worker execution.
* ``Logs`` for saved runtime logs.

Most routine sessions use the chat, Status, Artifacts, and External Evidence
views.
