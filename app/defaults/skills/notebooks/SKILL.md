---
name: notebooks
description: Read, edit, run and create Jupyter notebooks (.ipynb). Use when the user has or wants a notebook.
---

# Notebooks

1. nbformat, nbconvert and a Python kernel (ipykernel) ship inside Meshpool; never pip install them. Read a notebook with nbformat: list the cells with their index, type and first line before changing anything.
2. Edit by cell, never by raw JSON text: load with nbformat.read(path, as_version=4), change nb.cells[i].source, insert with nbformat.v4.new_code_cell or new_markdown_cell, and write back with nbformat.write. Keep the original next to it as a copy named with .bak.ipynb the first time.
3. Run it top to bottom with: python3 -m nbconvert --to notebook --execute --inplace file.ipynb (or --output to keep the original). The kernel is Meshpool's own Python, so pandas, matplotlib, openpyxl, Pillow, duckdb, lxml and the other packages the app carries are importable. A notebook that needs a package the app does not carry (scipy, scikit-learn, torch) fails on that cell: tell the user which package, and offer a version of the cell that uses what is there, instead of installing anything. Read the errors with the failing cell's number and fix that cell.
4. For a report, export HTML with nbconvert --to html and open it with view_output.
5. Charts inside cells: save them to a PNG as well so they can be checked with view_output.

Rules: Write in plain sentences, no em dashes. Never delete cells without saying which. Reply with the file path and what changed.
