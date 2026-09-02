OntoAnno_Agent
==============

Start here if you have a Seurat object and want to annotate cell types.

OntoAnno is a local web app for single-cell annotation. You give it a Seurat
``.rds`` file, a small YAML config, and an API key. OntoAnno then runs the
annotation workflow, helps you review the labels, and creates a final HTML
report.

Acknowledgement
---------------

OntoAnno builds on the GPTAnno annotation workflow. For the original GPTAnno
repository, see `GPTAnno <https://github.com/yrsong001/GPTAnno>`_.

.. image:: ../figures/OntoAnno.png
   :alt: OntoAnno overview
   :class: hero-image

What You Need
-------------

Before starting, prepare these three things:

.. list-table::
   :header-rows: 1
   :widths: 28 72

   * - Item
     - What it means
   * - A Seurat object
     - One ``.rds`` file saved from R, for example ``my_dataset.rds``.
   * - Dataset information
     - Species, tissue name, and where you want results to be saved.
   * - LLM API key
     - Usually an OpenAI API key stored in ``OPENAI_API_KEY`` or in ``.env``.

Workflow Guidelines
-------------------

For most users, follow these three steps in order:

1. Install OntoAnno.
2. Configure your dataset.
3. Use the agent to run annotation and review labels.

.. toctree::
   :maxdepth: 1
   :hidden:

   quick_start
   data_configure
   agent_guide
