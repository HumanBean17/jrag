# Architecture paper

This directory holds the LaTeX source and built PDF of the jrag
architecture report:

> **jrag: A Graph-Native Code Intelligence Layer for Agentic
> Navigation of Java Microservice Codebases.** Teriaev & Perplexity Computer,
> May 2026.

The paper describes the three-layer architecture (Extract \& Store / Navigate /
Reason), the five-tool MCP surface, the GPS metaphor (locate \-- inspect \-- walk),
the design principles that drove a v1\->v2 collapse from 9 tools to a small fixed
set (currently five navigation tools), and what
the system deliberately does not do. It contains no empirical evaluation;
testing on real legacy codebases is in progress and the data is not yet ready
to publish.

## Files

| File | Purpose |
|---|---|
| `paper.tex` | Main LaTeX source. Single-file paper, ~320 lines. |
| `references.bib` | BibTeX bibliography. |
| `figures/layers.tex` | TikZ source: three-layer architecture diagram. |
| `figures/gps.tex` | TikZ source: GPS-metaphor / three-primitive diagram. |
| `figures/workflow.tex` | TikZ source: canonical agent-interaction trace. |
| `Makefile` | Build/clean/view targets. |
| `paper.pdf` | Built PDF (10 pages, A4). Checked in for convenience. |

## Build

```bash
# One-time install of tectonic (single-binary, ~50 MB; no TeXLive required):
curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh

# Build the PDF:
cd docs/paper
make
```

The first build downloads required `.tfm` and `.pfb` font files into Tectonic's
cache (~1 minute, one-time). Subsequent builds are sub-5-second.

To rebuild on every save (requires `entr`):

```bash
make watch
```

## Editing

The paper is plain LaTeX. Section structure lives in `paper.tex`; each TikZ
diagram is a self-contained `figures/*.tex` file included via `\input{}`. Add a
new reference by extending `references.bib` and citing with `\cite{key}`.

If a long inline `\texttt{...}` paragraph triggers an overfull hbox warning,
wrap the paragraph in `{\sloppy ... \par}` --- this allows looser inter-word
spacing and prevents the right-margin overflow without uglifying the prose.

## Why LaTeX, not Markdown

Markdown plus Pandoc would have been faster to draft, but the paper is
designed to read like an arxiv-style engineering report --- proper math
support, two-column-ready layout, BibTeX citations, native TikZ diagrams. A
plain Pandoc PDF would have looked like a technical doc, not a paper. The
trade-off is that re-editing requires LaTeX comfort; for one-off prose tweaks
that is acceptable.

## Status

- **First draft**: 2026-05-08, opened as PR (`docs/architecture-paper`).
- **Empirical evaluation**: deferred to a follow-up paper or a §7.1 addition
  once real-codebase testing produces stable numbers.
- **Skills layer specification**: deferred to `docs/superpowers/specs/archive/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md`
  (currently held in PR #59); empirical signals from current testing show
  prose-guide-only is sufficient, so the skills layer is not on the critical
  path.
