#!/bin/bash -l
#SBATCH --job-name=ps7-filter
#SBATCH --time=100:00:00
#SBATCH --partition=cluster_long
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=32G
#SBATCH --output=/work/rintaro-k/research/PS/js/batch/output/%x/%j/out.out
#SBATCH --error=/work/rintaro-k/research/PS/js/batch/output/%x/%j/err.err

set -euo pipefail

export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
if command -v pyenv &> /dev/null; then
    eval "$(pyenv init -)"
    eval "$(pyenv init --path)" 2>/dev/null || true
fi
pyenv activate py3

# Node.js (nvm)
if [ -s "$HOME/.nvm/nvm.sh" ]; then
    export NVM_DIR="$HOME/.nvm"
    source "$NVM_DIR/nvm.sh"
    nvm use default 2>/dev/null || true
fi

OUT_DIR="/work/rintaro-k/research/PS/js/batch/output/${SLURM_JOB_NAME}/${SLURM_JOB_ID}"
mkdir -p "$OUT_DIR"
cd /work/rintaro-k/research/PS/js

echo "=== Job Info ==="
echo "Job ID: ${SLURM_JOB_ID}  Node: $(hostname)"
echo "Python: $(python3 --version)"
echo "Node:   $(node --version 2>/dev/null || echo 'not found')"
echo "npm:    $(npm --version  2>/dev/null || echo 'not found')"
echo "Working dir: $(pwd)"
echo "================"

echo ""
echo "=== PS7: nyc 実行チェック ==="
python3 ps7_filter.py
echo "=== PS7 complete ==="
