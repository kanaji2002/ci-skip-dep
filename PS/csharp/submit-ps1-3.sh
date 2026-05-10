#!/bin/bash -l
#SBATCH --job-name=csharp-ps1-3-filter
#SBATCH --time=100:00:00
#SBATCH --partition=cluster_long
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=32G
#SBATCH --output=/work/rintaro-k/research/PS/csharp/batch/output/%x/%j/out.out
#SBATCH --error=/work/rintaro-k/research/PS/csharp/batch/output/%x/%j/err.err

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
OUT_DIR="/work/rintaro-k/research/PS/csharp/batch/output/${SLURM_JOB_NAME}/${SLURM_JOB_ID}"
mkdir -p "$OUT_DIR"

cd /work/rintaro-k/research/PS/csharp

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
# 読み込む CSV を指定する（デフォルト: ps0/ps0_filtered.csv）
# 例: sbatch --export=ALL,INPUT_CSV=ps0/ps0_filtered.csv submit-ps1-3.sh
INPUT_CSV="${INPUT_CSV:-ps0/ps0_filtered.csv}"
export INPUT_CSV

echo ""
echo "=== Starting filter pipeline (PS1 -> PS2 -> PS3) ==="
echo "Input CSV: ${INPUT_CSV}"
python3 ps1_ps3_filter.py
echo "=== Filter pipeline complete ==="
