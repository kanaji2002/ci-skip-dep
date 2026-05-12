#!/bin/bash -l
#SBATCH --job-name=js-ps2-4-filter
#SBATCH --time=100:00:00
#SBATCH --partition=cluster_long
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=64G
#SBATCH --output=/work/rintaro-k/research/PS/js/batch/output/%x/%j/out.out
#SBATCH --error=/work/rintaro-k/research/PS/js/batch/output/%x/%j/err.err

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
OUT_DIR="/work/rintaro-k/research/PS/js/batch/output/${SLURM_JOB_NAME}/${SLURM_JOB_ID}"
mkdir -p "$OUT_DIR"

cd /work/rintaro-k/research/PS/js

# ============================================
# Job Info
# ============================================
echo "=== Job Info ==="
echo "Job ID: ${SLURM_JOB_ID}"
echo "Node: $(hostname)"
echo "Python: $(which python3)"
echo "Python version: $(python3 --version)"
echo "Working dir: $(pwd)"
echo "Args: $*"
echo "================"

# ============================================
# Run filter pipeline
# ============================================
# 読み込む CSV を指定する（デフォルト: ps1/js-repo.csv）
# 例: sbatch --export=ALL,PS1_CSV=ps1/js-repo.csv submit-ps2-4.sh
# 例: sbatch submit-ps2-4.sh --offset 0 --limit 50000 --job-id 0
PS1_CSV="${PS1_CSV:-ps1/js-repo.csv}"
export PS1_CSV

echo ""
echo "=== Starting filter pipeline (PS2 -> PS3 -> PS4) ==="
echo "Input CSV: ${PS1_CSV}"
python3 ps2_ps4_filter.py "$@"
echo "=== Filter pipeline complete ==="
