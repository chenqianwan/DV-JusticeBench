# DV-JusticeBench LaTeX Paper

This directory contains the LaTeX source for the DV-JusticeBench paper (10-page version).

## Files

- `paper.tex` - Main LaTeX document
- `references.bib` - BibTeX bibliography
- `README.md` - This file
- `compile.sh` - Compilation script

## Required Figure Files

The paper references the following figure files that need to be placed in this directory:

1. `chart_token_usage.png` - Token Usage Comparison
2. `chart_ranking.png` - Overall Ranking  
3. `chart_pareto_tradeoff.png` - Pareto Trade-off (Quality-Reliability-Cost)
4. `chart_heatmap_dimensions.png` - Model Performance Heatmap
5. `chart_errors.png` - Error Statistics

**NOTE**: You need to generate these charts from your experimental results and place them in the `latex/` directory.

## Compilation Instructions

### Option 1: Using the compile script (Recommended)

```bash
chmod +x compile.sh
./compile.sh
```

### Option 2: Manual compilation

```bash
pdflatex paper.tex
bibtex paper
pdflatex paper.tex
pdflatex paper.tex
```

### Option 3: Using latexmk (if installed)

```bash
latexmk -pdf paper.tex
```

## Before Submitting

1. **Update author information** in `paper.tex`:
   - Replace "Anonymous Authors" with actual author names
   - Add author affiliations, emails, and ORCID IDs
   - Update the `\shortauthors` command

2. **Update conference information** in `paper.tex`:
   - Replace placeholder conference details (dates, location)
   - Update ACM rights information when you receive it
   - Add correct ISBN and DOI

3. **Add figures**:
   - Generate the 5 required chart images from your results
   - Place them in the `latex/` directory
   - Verify they display correctly in the compiled PDF

4. **Review content**:
   - Check all citations are correct
   - Verify all tables and figures are properly referenced
   - Proofread for any formatting issues

5. **Final checks**:
   - Ensure paper length meets conference requirements (10 pages for ICAIL)
   - Run spell-check
   - Verify all URLs are accessible

## Overleaf Upload

To use with Overleaf:

1. Create a new blank project in Overleaf
2. Upload all files from this directory:
   - `paper.tex`
   - `references.bib`
   - All `.png` figure files
3. Set the main document to `paper.tex`
4. Compile using PDFLaTeX

## Notes

- This template uses the ACM SIGCONF format (acmart document class)
- The paper includes Chinese legal citations which are properly handled
- All references are in BibTeX format in `references.bib`
- Figures use the standard LaTeX figure environment with captions

## Troubleshooting

**Missing figures error**: If you get errors about missing `.png` files, either:
- Generate and add the figure files, OR
- Comment out the `\includegraphics` lines temporarily

**Bibliography not showing**: Make sure to run `bibtex` after the first `pdflatex` run

**ACM class not found**: Make sure you have the latest acmart package installed

## Contact

For questions about the paper content, please contact the authors.
