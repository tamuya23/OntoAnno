Development
===========

Documentation Build
-------------------

Build the HTML documentation locally from the repository root:

.. code-block:: bash

   python -m sphinx -b html docs docs/_build/html

Or use the Makefile:

.. code-block:: bash

   make -C docs html

Open the generated site:

.. code-block:: text

   docs/_build/html/index.html

Optional Theme
--------------

The docs are configured to use ``pydata-sphinx-theme`` when it is installed,
which gives a layout closer to scientific package documentation such as
scvi-tools. If the theme is not installed, the build falls back to Sphinx's
built-in ``alabaster`` theme.

Install optional docs dependencies with:

.. code-block:: bash

   pip install -r docs/requirements.txt

Project Layout
--------------

.. code-block:: text

   OntoAnno/
   |-- src/ontoanno/        Python orchestration and UI code
   |-- GPTAnno/             R package and GPTAnno implementation
   |-- scripts/             R and Python workflow helpers
   |-- configs/             Example dataset configs
   |-- figures/             Documentation and README images
   |-- docs/                Sphinx documentation source
   |-- work/                Local project work directories
   `-- runs/                Runtime outputs

Publishing on GitHub Pages
--------------------------

For a GitHub-hosted documentation site, build the Sphinx output in GitHub
Actions and deploy ``docs/_build/html`` to GitHub Pages. In repository settings,
set Pages to use GitHub Actions as the source.
