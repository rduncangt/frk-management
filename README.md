# Field Repair Kit Inventory Management: Documents and Materials

## Materials

These are the materials purchased and used for configuring the FRK:

+ 2"x3" plastic resealable bags
  + each FRK uses approximately 29
  + $4.99 per 100
  + <https://www.amazon.com/dp/B0CMX4BGPT>

+ 3"x4" plastic resealable bags
  + each FRK uses approximately 2
  + $3.32 per 100
  + <https://www.walmart.com/ip/Hello-Hobby-3-x-4-Re-closable-Plastic-Bags-for-Jewelry-and-Craft-Storage-100-Bags-USHH2232/5323687687>

+ 4"x6" plastic resealable bags
  + each FRK uses approximately 23
  + $3.32 per 100
  + <https://www.walmart.com/ip/Hello-Hobby-4-x6-Re-closable-Plastic-Poly-Bags-Jewelry-and-Craft-Storage-Unisex-Gift-bag-Length-6-inches/5323687689>

+ Avery Repositionable Address Labels, Repositionable Adhesive, 1" x 2-5/8", 750 Labels (58160)
  + $15 per 750 labels
  + <https://www.walmart.com/ip/Avery-750-Pack-White-1-x-2-5-8-Repositionable-Address-Labels-for-Inkjet-Printers/14295717>
  + used for parts labeling and toolbox bin labels
    + unique parts count approximately 60
    + bin count 17 plus 2 open areas (toolbox configurations may differ, however)

### Photos

+ FRK parts in bags with labels:
  + <img src="frk-parts-bagged-labeled.jpg" alt="FRK parts in bags with labels" width="50%" height="auto">

+ FRK toolbox open areas populated:
  + <img src="frk-toolbox-open-areas-populated.jpg" alt="FRK toolbox open areas populated" width="50%" height="auto">

+ FRK toolbox populated:
  + <img src="frk-toolbox-populated.jpg" alt="FRK toolbox populated" width="50%" height="auto">

## Documents

This section concerns itself with generation of those PDF documents used in managing the inventory of an individual Field Repair Kit (FRK).

Provided in the repository are the LaTeX source files that produce three types of printable documents:

+ **Labels**: Part labels for physical organization (Avery format)
+ **Bin Labels**: Bin labels for affixing to toolbox bins (Avery format)
+ **Inventory Sheet**: Single-page inventory checklist
+ **Box Map**: Toolbox bin layout diagram

### Requirements

+ A LaTeX distribution (e.g., TeX Live or MiKTeX)
+ [`frk_items.tsv`](frk_items.tsv) data file in the repository root

### Data Format

All LaTeX files expect a TSV file named [`frk_items.tsv`](frk_items.tsv) with the following columns:

| Part Number | Part Name | Count | Saw | Supervision | Bin | Section | Packaging |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 9512 933 2260 | Needle cage 10x13x10 | 3 | 261 | AS1 | A2 | needle cages | 2" x 3" |

You must generate this data file from your source of truth before building documents.

### Building Documents

One can use `make` or `pdflatex` to produce the documents. Each of these should produce the same results.
All document producing calls to `make` simply call `pdflatex` underneath with opinionated options.

#### Using Make

```bash
make all        # Generate all PDFs
make labels     # Generate labels only
make binlabels  # Generate bin labels only
make inventory  # Generate inventory sheet only
make boxmap     # Generate box map only
make clean      # Remove *.log and *.aux files
```

#### Using `pdflatex` directly

```bash
pdflatex frk-parts-labels-avery.tex  # Labels
pdflatex frk-bin-labels-avery.tex    # Bin labels
pdflatex frk-parts-inventory.tex     # Inventory sheet
pdflatex frk-parts-boxmap.tex        # Box map
```

### Output Files

+ [`frk-parts-labels-avery.pdf`](frk-parts-labels-avery.pdf) - Avery-format labels for all parts in the TSV file
+ [`frk-bin-labels-avery.pdf`](frk-bin-labels-avery.pdf) - Avery-format labels for all toolbox bins
+ [`frk-parts-inventory.pdf`](frk-parts-inventory.pdf)    - Single-page inventory checklist
+ [`frk-parts-boxmap.pdf`](frk-parts-boxmap.pdf)       - Toolbox bin layout (currently with hard-coded labels)
