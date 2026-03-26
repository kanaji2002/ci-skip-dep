#!/bin/bash -l
#SBATCH --job-name=repo-list-filter
#SBATCH --time=100:00:00
#SBATCH --partition=cluster_long
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --output=/work/rintaro-k/research/PS/js/batch/output/%x/%A_%a/out.out
#SBATCH --error=/work/rintaro-k/research/PS/js/batch/output/%x/%A_%a/err.err
#SBATCH --array=0-5
# ※ ps1/js-repo.csv の行数に合わせて --array と CHUNK_SIZE を調整する
#    例: 300,000行 / CHUNK_SIZE=50000 → --array=0-5 (6ジョブ)
#        300,000行 / CHUNK_SIZE=100000 → --array=0-2 (3ジョブ)

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
OUT_DIR="/work/rintaro-k/research/PS/js/batch/output/${SLURM_JOB_NAME}/${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
mkdir -p "$OUT_DIR"

cd /work/rintaro-k/research/PS/js

# ============================================
# Job Info
# ============================================
echo "=== Job Info ==="
echo "Array Job ID: ${SLURM_ARRAY_JOB_ID}  Task ID: ${SLURM_ARRAY_TASK_ID}"
echo "Node: $(hostname)"
echo "Python: $(which python3) ($(python3 --version))"
echo "Working dir: $(pwd)"
echo "================"

# ============================================
# Run filter pipeline
# ============================================
CHUNK_SIZE=${CHUNK_SIZE:-50000}
OFFSET=$(( SLURM_ARRAY_TASK_ID * CHUNK_SIZE ))

echo ""
echo "=== Starting filter pipeline ==="
echo "Input:    ps1/js-repo.csv"
echo "Offset:   ${OFFSET}  Limit: ${CHUNK_SIZE}  Job ID: ${SLURM_ARRAY_TASK_ID}"

python3 ps2_ps4_filter.py \
    --input ps1/js-repo.csv \
    --offset "${OFFSET}" \
    --limit "${CHUNK_SIZE}" \
    --job-id "${SLURM_ARRAY_TASK_ID}"

echo "=== Filter pipeline complete ==="
