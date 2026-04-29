"""Sphinx configuration for the WorldFork documentation."""

project = "WorldFork"
author = "WorldFork contributors"
copyright = "2026, WorldFork contributors"

extensions = [
    "myst_parser",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

master_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"
html_title = "WorldFork"

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
]
