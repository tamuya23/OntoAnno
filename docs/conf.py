from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

project = "OntoAnno_Agent"
author = "OntoAnno contributors"
copyright = "2026, OntoAnno contributors"
release = "0.1.0"

extensions = [
    "sphinx.ext.autosectionlabel",
]

if importlib.util.find_spec("sphinx_copybutton") is not None:
    extensions.append("sphinx_copybutton")

autosectionlabel_prefix_document = True
templates_path = ["_templates"]
exclude_patterns = ["_build", "_includes", "Thumbs.db", ".DS_Store"]
source_suffix = ".rst"
master_doc = "index"

html_title = "OntoAnno_Agent"
html_short_title = "OntoAnno_Agent"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_favicon = None
html_show_sourcelink = False
html_sidebars = {
    "**": [],
}

if importlib.util.find_spec("pydata_sphinx_theme") is not None:
    html_theme = "pydata_sphinx_theme"
    html_theme_options = {
        "show_nav_level": 2,
        "navigation_depth": 3,
        "collapse_navigation": False,
        "navbar_align": "left",
        "logo": {
            "text": "OntoAnno_Agent",
        },
        "secondary_sidebar_items": ["page-toc"],
    }
else:
    html_theme = "alabaster"
    html_theme_options = {
        "description": "Ontology-aware single-cell annotation workbench",
        "fixed_sidebar": True,
        "show_related": False,
        "sidebar_width": "260px",
        "page_width": "1120px",
        "font_family": "-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
        "head_font_family": "-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
        "link": "#0f766e",
        "link_hover": "#0b4f4a",
    }
