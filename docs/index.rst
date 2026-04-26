OntoAnno
========

Start here if you have a Seurat object and want to annotate cell types.

OntoAnno is a local web app for single-cell annotation. You give it a Seurat
``.rds`` file, a small YAML config, and an API key. OntoAnno then runs the
annotation workflow, helps you review the labels, and creates a final HTML
report.

.. image:: ../figures/ontoanno_interface_overview.png
   :alt: OntoAnno interface overview
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

Recommended First Path
----------------------

For most biologists, the easiest path is:

1. Put your Seurat ``.rds`` file in ``data/my_project/``.
2. Copy one config template.
3. Edit only the dataset name, Seurat path, species, and tissue.
4. Start the web app.
5. Type simple requests in the chat, such as ``Run the parent annotation``.
6. Open the generated report.

Go to :doc:`user_guide/index` for the full step-by-step guide.

.. raw:: html

   <div class="summary-grid">
     <a class="summary-card" href="user_guide/index.html">
       <span class="summary-title">Annotate your dataset</span>
       <span class="summary-text">Step-by-step instructions for a new Seurat .rds file.</span>
     </a>
     <a class="summary-card" href="installation.html">
       <span class="summary-title">Install OntoAnno</span>
       <span class="summary-text">Choose Docker, Apptainer, or a local setup.</span>
     </a>
     <a class="summary-card" href="quickstart.html">
       <span class="summary-title">Quick test</span>
       <span class="summary-text">Run the app once and confirm the setup works.</span>
     </a>
     <a class="summary-card" href="troubleshooting.html">
       <span class="summary-title">Troubleshooting</span>
       <span class="summary-text">Fix common path, API key, R, and app launch problems.</span>
     </a>
   </div>

Documentation
-------------

.. toctree::
   :maxdepth: 2
   :caption: Start Here

   user_guide/index
   installation
   quickstart
   troubleshooting

.. toctree::
   :maxdepth: 2
   :caption: More Details

   user_guide/configuration
   user_guide/containers
   user_guide/interface
   user_guide/workflow

.. toctree::
   :maxdepth: 1
   :caption: Advanced

   cli
   development
