PYTHON ?= .venv/bin/python
UV ?= uv
PDFLATEX ?= pdflatex

.PHONY: all setup generate labels binlabels inventory boxmap reference site check clean

# One build refreshes every printable document and the GitHub Pages site.
all: setup
	$(PYTHON) scripts/build.py all --pdflatex "$(PDFLATEX)"

setup:
	$(UV) sync --frozen

labels binlabels inventory boxmap reference site: all

generate: setup
	$(PYTHON) scripts/build.py generate

check: setup
	$(PYTHON) -m unittest discover -s tests -v
	$(PYTHON) scripts/build.py check

clean:
	rm -rf .build
