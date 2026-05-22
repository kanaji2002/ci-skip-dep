#!/bin/bash -l
#SBATCH --job-name=rq3_1_rust_step2
#SBATCH --time=4-00:00:00
#SBATCH --partition=cluster_long
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=64G
#SBATCH --output=/work/rintaro-k/research/RQ3/RQ3-1/Code/rust/batch_output/%x/%j/out.out
#SBATCH --error=/work/rintaro-k/research/RQ3/RQ3-1/Code/rust/batch_output/%x/%j/err.err

# ============================================
# Usage:
#   sbatch submit.sh
#   sbatch submit.sh --limit 5     # テスト実行
#   sbatch submit.sh --skip 20     # 20件スキップして再開
#
# ※ Step 2 は LLM を使わないため GPU 不要
#   step1_results.csv が存在することが前提
# ============================================

set -euo pipefail

PYTHON_ARGS="$*"

OUT_DIR="/work/rintaro-k/research/RQ3/RQ3-1/Code/rust/batch_output/${SLURM_JOB_NAME:-rq3_1_rust_step2}/${SLURM_JOB_ID:-local}"
mkdir -p "$OUT_DIR"

# ============================================
# pyenv
# ============================================
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
if command -v pyenv &> /dev/null; then
    eval "$(pyenv init -)"
    eval "$(pyenv init --path)" 2>/dev/null || true
fi
pyenv activate py3

# ============================================
# Singularity
# ============================================
module load singularity

# ============================================
# Job info
# ============================================
echo "=== Job Info ==="
echo "Job ID:   ${SLURM_JOB_ID:-local}"
echo "Node:     $(hostname)"
echo "Python:   $(python3 --version)"
echo "Container: /work/rintaro-k/research/containers/rust-tarpaulin.sif"
echo "Args:     ${PYTHON_ARGS:-'(none)'}"
echo "================"

# step1_results.csv の存在確認
STEP1_CSV="/work/rintaro-k/research/RQ3/RQ3-1/Code/rust/output/step1_result-400-466.csv"
if [[ ! -f "$STEP1_CSV" ]]; then
    echo "[error] step1_result-400-466.csv not found: ${STEP1_CSV}"
    echo "先に step1_detect/submit.sh を実行してください。"
    exit 1
fi

# ============================================
# 実行
# ============================================
TOTAL_START=$(date +%s)

cd /work/rintaro-k/research/RQ3/RQ3-1/Code/rust/step2_verify
python3 run.py ${PYTHON_ARGS}

TOTAL_END=$(date +%s)
TOTAL_ELAPSED=$((TOTAL_END - TOTAL_START))

echo "job_id=${SLURM_JOB_ID:-local}"  > "${OUT_DIR}/timing.log"
echo "total_sec=${TOTAL_ELAPSED}"     >> "${OUT_DIR}/timing.log"
echo "=== Done (${TOTAL_ELAPSED}s) ==="
