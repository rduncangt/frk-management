# Makefile to automate the compilation of LaTeX documents
# from the shared data file.

PDFLATEX = pdflatex -interaction=nonstopmode

# Define the data source file that all documents depend on.
DATA_SOURCE = frk_items.tsv

# Define the primary LaTeX source files and their corresponding PDF outputs.
LABELS_SRC = frk-parts-labels-avery.tex
LABELS_PDF = $(LABELS_SRC:.tex=.pdf)

BIN_LABELS_SRC = frk-bin-labels-avery.tex
BIN_LABELS_PDF = $(BIN_LABELS_SRC:.tex=.pdf)

BOXMAP_SRC = frk-parts-boxmap.tex
BOXMAP_PDF = $(BOXMAP_SRC:.tex=.pdf)

INVENTORY_SRC = frk-parts-inventory.tex
INVENTORY_PDF = $(INVENTORY_SRC:.tex=.pdf)

.PHONY: all labels binlabels boxmap inventory clean

# Default target: compile all documents.
all: labels binlabels boxmap inventory

# Target to compile the labels document.
labels: $(LABELS_PDF)
$(LABELS_PDF): $(LABELS_SRC) $(DATA_SOURCE)
	@echo "Compiling labels..."
	$(PDFLATEX) $(LABELS_SRC)
	$(PDFLATEX) $(LABELS_SRC)

# Target to compile the bin labels document.
binlabels: $(BIN_LABELS_PDF)
$(BIN_LABELS_PDF): $(BIN_LABELS_SRC) $(DATA_SOURCE)
	@echo "Compiling bin labels..."
	$(PDFLATEX) $(BIN_LABELS_SRC)
	$(PDFLATEX) $(BIN_LABELS_SRC)

# Target to compile the box map document.
boxmap: $(BOXMAP_PDF)
$(BOXMAP_PDF): $(BOXMAP_SRC)
	@echo "Compiling box map..."
	$(PDFLATEX) $(BOXMAP_SRC)
	$(PDFLATEX) $(BOXMAP_SRC)

# Target to compile the inventory document.
inventory: $(INVENTORY_PDF)
$(INVENTORY_PDF): $(INVENTORY_SRC) $(DATA_SOURCE)
	@echo "Compiling inventory..."
	$(PDFLATEX) $(INVENTORY_SRC)
	$(PDFLATEX) $(INVENTORY_SRC)

# Target to clean up all generated files.
clean:
	@echo "Cleaning up generated files..."
	rm -f *.log *.aux