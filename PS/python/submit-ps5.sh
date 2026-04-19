#!/bin/bash -l
#SBATCH --job-name=ps5-filter-python
#SBATCH --time=100:00:00
#SBATCH --partition=cluster_long
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --output=/work/rintaro-k/research/PS/python/batch/output/%x/%j/out.out
#SBATCH --error=/work/rintaro-k/research/PS/python/batch/output/%x/%j/err.err

# ============================================
# Usage:
#   sbatch submit-ps5.sh
#   sbatch submit-ps5.sh --limit 10
# ============================================

set -euo pipefail

# ============================================
# pyenv initialization
# ============================================
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"

if command -v pyenv &> /dev/null; then
    eval "$(pyenv init -)"
    eval "$(pyenv init --path)" 2>/dev/null || true
fi

pyenv activate py3

# ============================================
# Setup directories
# ============================================
OUT_DIR="/work/rintaro-k/research/PS/python/batch/output/${SLURM_JOB_NAME:-ps5-filter-python}/${SLURM_JOB_ID:-local}"
mkdir -p "$OUT_DIR"

cd /work/rintaro-k/research/PS/python

# ============================================
# Job Info
# ============================================
echo "=== Job Info ==="
echo "Job ID:      ${SLURM_JOB_ID:-local}"
echo "Node:        $(hostname)"
echo "Python:      $(which python3) ($(python3 --version))"
echo "Working dir: $(pwd)"
echo "Args:        $*"
echo "================"

# ============================================
# Run PS5 filter
# ============================================
echo ""
echo "=== Starting PS5 filter (Python / pyproject.toml) ==="
python3 ps5_filter.py "$@"
echo "=== PS5 filter complete ==="
