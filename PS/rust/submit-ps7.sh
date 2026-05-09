#!/bin/bash -l
#SBATCH --job-name=ps7-filter-rust
#SBATCH --time=24:00:00
#SBATCH --partition=cluster_long
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --output=/work/rintaro-k/research/PS/rust/batch/output/%x/%j/out.out
#SBATCH --error=/work/rintaro-k/research/PS/rust/batch/output/%x/%j/err.err

set -euo pipefail

export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
if command -v pyenv &> /dev/null; then
    eval "$(pyenv init -)"
    eval "$(pyenv init --path)" 2>/dev/null || true
fi
pyenv activate py3

OUT_DIR="/work/rintaro-k/research/PS/rust/batch/output/${SLURM_JOB_NAME}/${SLURM_JOB_ID}"
mkdir -p "$OUT_DIR"
cd /work/rintaro-k/research/PS/rust

echo "=== Job Info ==="
echo "Job ID: ${SLURM_JOB_ID}  Node: $(hostname)"
echo "Python: $(python3 --version)  Working dir: $(pwd)"
echo "================"

echo ""
echo "=== PS7 (Rust): tests/ ディレクトリ チェック ==="
python3 ps7_filter.py
echo "=== PS7 complete ==="
