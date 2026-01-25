#!/bin/bash

# DV-JusticeBench Paper Compilation Script

echo "======================================"
echo "Compiling DV-JusticeBench Paper"
echo "======================================"
echo ""

# Check if required files exist
if [ ! -f "paper.tex" ]; then
    echo "Error: paper.tex not found!"
    exit 1
fi

if [ ! -f "references.bib" ]; then
    echo "Error: references.bib not found!"
    exit 1
fi

# Check for figure files
echo "Checking for required figure files..."
MISSING_FIGS=0
for fig in chart_token_usage chart_ranking chart_pareto_tradeoff chart_heatmap_dimensions chart_errors; do
    if [ ! -f "${fig}.png" ]; then
        echo "  WARNING: ${fig}.png not found"
        MISSING_FIGS=1
    else
        echo "  ✓ ${fig}.png found"
    fi
done

if [ $MISSING_FIGS -eq 1 ]; then
    echo ""
    echo "WARNING: Some figure files are missing!"
    echo "The compilation may produce errors or warnings."
    echo "Please generate the figures and place them in this directory."
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo ""
echo "Starting compilation..."
echo ""

# First pass
echo "==> Running pdflatex (1st pass)..."
pdflatex -interaction=nonstopmode paper.tex > compile_log.txt 2>&1
if [ $? -ne 0 ]; then
    echo "Error in first pdflatex pass. Check compile_log.txt for details."
    tail -20 compile_log.txt
    exit 1
fi

# BibTeX
echo "==> Running bibtex..."
bibtex paper >> compile_log.txt 2>&1
if [ $? -ne 0 ]; then
    echo "Error in bibtex. Check compile_log.txt for details."
    tail -20 compile_log.txt
    # Don't exit - bibliography errors are often non-fatal
fi

# Second pass
echo "==> Running pdflatex (2nd pass)..."
pdflatex -interaction=nonstopmode paper.tex >> compile_log.txt 2>&1
if [ $? -ne 0 ]; then
    echo "Error in second pdflatex pass. Check compile_log.txt for details."
    tail -20 compile_log.txt
    exit 1
fi

# Third pass (to resolve all references)
echo "==> Running pdflatex (3rd pass)..."
pdflatex -interaction=nonstopmode paper.tex >> compile_log.txt 2>&1
if [ $? -ne 0 ]; then
    echo "Error in third pdflatex pass. Check compile_log.txt for details."
    tail -20 compile_log.txt
    exit 1
fi

echo ""
echo "======================================"
echo "Compilation completed successfully!"
echo "======================================"
echo ""
echo "Output file: paper.pdf"
echo "Full log: compile_log.txt"
echo ""

# Clean up auxiliary files (optional)
read -p "Clean up auxiliary files? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Cleaning up..."
    rm -f paper.aux paper.bbl paper.blg paper.log paper.out paper.synctex.gz
    echo "Done!"
fi

echo ""
echo "To view the PDF:"
echo "  open paper.pdf    (macOS)"
echo "  xdg-open paper.pdf    (Linux)"
echo ""
