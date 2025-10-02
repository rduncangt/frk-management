# Makefile to automate the compilation of LaTeX documents
# from the shared data file.

PDFLATEX = pdflatex -interaction=nonstopmode

# Define the data source file that all documents depend on.
DATA_SOURCE = frk_items.tsv

# Define the primary LaTeX source files and their corresponding PDF outputs.
LABELS_SRC = frk-parts-labels-avery.tex
LABELS_PDF = $(LABELS_SRC:.tex=.pdf)

BOXMAP_SRC = frk-parts-boxmap.tex
BOXMAP_PDF = $(BOXMAP_SRC:.tex=.pdf)

INVENTORY_SRC = frk-parts-inventory.tex
INVENTORY_PDF = $(INVENTORY_SRC:.tex=.pdf)

.PHONY: all labels boxmap inventory clean

# Default target: compile all documents.
all: labels boxmap inventory

# Target to compile the labels document.
labels: $(LABELS_PDF)
$(LABELS_PDF): $(LABELS_SRC)
	@echo "Compiling labels..."
	$(PDFLATEX) $(LABELS_SRC)
	$(PDFLATEX) $(LABELS_SRC)

# Target to compile the box map document.
boxmap: $(BOXMAP_PDF)
$(BOXMAP_PDF): $(BOXMAP_SRC)
	@echo "Compiling box map..."
	$(PDFLATEX) $(BOXMAP_SRC)
	$(PDFLATEX) $(BOXMAP_SRC)

# Target to compile the inventory document.
inventory: $(INVENTORY_PDF)
$(INVENTORY_PDF): $(INVENTORY_SRC)
	@echo "Compiling inventory..."
	$(PDFLATEX) $(INVENTORY_SRC)
	$(PDFLATEX) $(INVENTORY_SRC)

# Target to clean up all generated files.
clean:
	@echo "Cleaning up generated files..."
	rm -f *.log *.aux