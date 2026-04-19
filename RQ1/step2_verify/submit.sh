#!/bin/bash -l
#SBATCH --job-name=rq1_step2_verify
#SBATCH --time=4-04:00:00
#SBATCH --partition=cluster_long
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=64G

#SBATCH --output=/work/rintaro-k/research/RQ1/batch_output/%x/%j/out.out
#SBATCH --error=/work/rintaro-k/research/RQ1/batch_output/%x/%j/err.err

# ============================================
# Usage:
#   sbatch submit.sh
#   sbatch submit.sh --limit 5     # テスト実行
#   sbatch submit.sh --skip 20     # 20件スキップして再開
#
# ※ Step 2 は LLM を使わないため GPU 不要 (--gres=gpu:0)
#   step1_results.csv が存在することが前提
# ============================================

set -euo pipefail

PYTHON_ARGS="$*"

OUT_DIR="/work/rintaro-k/research/RQ1/batch_output/${SLURM_JOB_NAME:-rq1_step2_verify}/${SLURM_JOB_ID:-local}"
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
# nvm / Node.js
# ============================================
export NVM_DIR="$HOME/.nvm"
if [[ -s "$NVM_DIR/nvm.sh" ]]; then
    source "$NVM_DIR/nvm.sh"
    nvm use 20 2>/dev/null || true
fi

# ============================================
# Job info
# ============================================
echo "=== Job Info ==="
echo "Job ID:   ${SLURM_JOB_ID:-local}"
echo "Node:     $(hostname)"
echo "Python:   $(python3 --version)"
echo "Node.js:  $(node --version 2>/dev/null || echo 'n/a')"
echo "Args:     ${PYTHON_ARGS:-'(none)'}"
echo "================"

# step1_results.csv の存在確認
STEP1_CSV="/work/rintaro-k/research/RQ1/output/step1_results.csv"
if [[ ! -f "$STEP1_CSV" ]]; then
    echo "[error] step1_results.csv not found: ${STEP1_CSV}"
    echo "先に step1_detect/submit.sh を実行してください。"
    exit 1
fi

# ============================================
# 実行
# ============================================
TOTAL_START=$(date +%s)

cd /work/rintaro-k/research/RQ1/step2_verify
python3 run.py ${PYTHON_ARGS}

TOTAL_END=$(date +%s)
TOTAL_ELAPSED=$((TOTAL_END - TOTAL_START))

echo "job_id=${SLURM_JOB_ID:-local}"  > "${OUT_DIR}/timing.log"
echo "total_sec=${TOTAL_ELAPSED}"     >> "${OUT_DIR}/timing.log"
echo "=== Done (${TOTAL_ELAPSED}s) ==="
