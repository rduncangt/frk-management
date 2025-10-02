# Field Repair Kit Inventory Management

Generate PDF documents for managing the inventory of a Saw Kit Field Repair Kit (FRK).

## Overview

This repository contains LaTeX source files that produce three types of printable documents:

- **Labels**: Part labels for physical organization (Avery format)
- **Inventory Sheet**: Single-page inventory checklist
- **Box Map**: Toolbox bin layout diagram

## Requirements

- A LaTeX distribution (e.g., TeX Live or MiKTeX)
- `frk_items.tsv` data file in the repository root

## Data Format

All LaTeX files expect a TSV file named `frk_items.tsv` with the following columns:

| Part Number | Part Name | Count | Saw | Supervision | Bin | Section |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 9512 933 2260 | Needle cage 10x13x10 | 3 | 261 | AS1 | A2 | needle cages |

You must generate this data file from your source of truth before building documents.

## Building Documents

### Using Make

```bash
make all        # Generate all PDFs
make labels     # Generate labels only
make inventory  # Generate inventory sheet only
make boxmap     # Generate box map only
make clean      # Remove generated files
```

### Using `pdflatex` directly

```bash
pdflatex frk-parts-labels-avery.tex  # Labels
pdflatex frk-parts-inventory.tex     # Inventory sheet
pdflatex frk-parts-boxmap.tex        # Box map
```

## Output Files

- `frk-parts-labels-avery.pdf` - Avery-format labels for all parts in the TSV file
- `frk-parts-inventory.pdf`    - Single-page inventory checklist
- `frk-parts-boxmap.pdf`       - Toolbox bin layout (currently with hard-coded labels)
