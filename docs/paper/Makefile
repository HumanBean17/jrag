# Build the architecture paper.
#
# Default target compiles paper.tex -> paper.pdf via tectonic (single-binary
# self-contained TeX engine; auto-fetches required packages).
#
# Why tectonic over pdflatex/latexmk:
#   - Zero TeXLive install required (~3 GB) - tectonic is one ~50 MB binary.
#   - Reproducible builds: package versions pinned to the engine release.
#   - No multi-pass orchestration (latexmk-style) - tectonic re-runs internally
#     until references and citations stabilise.
#
# Install: https://tectonic-typesetting.github.io/en-US/install.html
#   curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh

PAPER := paper
SOURCES := $(PAPER).tex references.bib $(wildcard figures/*.tex)

.PHONY: all clean watch view

all: $(PAPER).pdf

$(PAPER).pdf: $(SOURCES)
	tectonic --keep-logs --synctex $(PAPER).tex

clean:
	rm -f $(PAPER).pdf $(PAPER).log $(PAPER).synctex.gz $(PAPER).aux $(PAPER).bbl $(PAPER).blg $(PAPER).out $(PAPER).toc

# Open the built PDF (macOS / Linux). Best-effort.
view: $(PAPER).pdf
	@(command -v xdg-open >/dev/null && xdg-open $(PAPER).pdf) || \
	 (command -v open >/dev/null && open $(PAPER).pdf) || \
	 echo "Open $(PAPER).pdf manually."

# Watch source files and rebuild on save (requires entr).
watch:
	echo $(SOURCES) | tr ' ' '\n' | entr -c make
