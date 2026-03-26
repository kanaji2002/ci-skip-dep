#!/bin/bash -l
#SBATCH --job-name=repo-list-filter-python
#SBATCH --time=100:00:00
#SBATCH --partition=cluster_long
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --output=/work/rintaro-k/research/PS/python/batch/output/%x/%j/out.out
#SBATCH --error=/work/rintaro-k/research/PS/python/batch/output/%x/%j/err.err

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

# ============================================
# Activate virtual environment (py3)
# ============================================
pyenv activate py3

# ============================================
# Setup directories
# ============================================
OUT_DIR="/work/rintaro-k/research/PS/python/batch/output/${SLURM_JOB_NAME}/${SLURM_JOB_ID}"
mkdir -p "$OUT_DIR"

cd /work/rintaro-k/research/PS/python

# ============================================
# Job Info
# ============================================
echo "=== Job Info ==="
echo "Job ID: ${SLURM_JOB_ID}"
echo "Node: $(hostname)"
echo "Python: $(which python3)"
echo "Python version: $(python3 --version)"
echo "Working dir: $(pwd)"
echo "================"

# ============================================
# Run filter pipeline
# ============================================
# 読み込む CSV を指定する（デフォルト: results.csv）
# 例: sbatch --export=ALL,INPUT_CSV=results/results_50k-100k.csv submit.sh
INPUT_CSV="${INPUT_CSV:-results/results_1-50k.csv}"
export INPUT_CSV

echo ""
echo "=== Starting filter pipeline ==="
echo "Input CSV: ${INPUT_CSV}"
python3 ps2_ps4_filter.py
echo "=== Filter pipeline complete ==="
