#!/bin/bash -l
#SBATCH --job-name=ps7-filter-rust
#SBATCH --time=100:00:00
#SBATCH --partition=cluster_long
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
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

SINGULARITY=/opt/singularity/3.9.6/bin/singularity
SIF=/work/rintaro-k/research/containers/rust-tarpaulin.sif

# コンテナが未取得なら pull
if [ ! -f "$SIF" ]; then
    echo "Pulling rust-tarpaulin container ..."
    $SINGULARITY pull "$SIF" docker://xd009642/tarpaulin:develop-nightly
fi

OUT_DIR="/work/rintaro-k/research/PS/rust/batch/output/${SLURM_JOB_NAME}/${SLURM_JOB_ID}"
mkdir -p "$OUT_DIR"
cd /work/rintaro-k/research/PS/rust

echo "=== Job Info ==="
echo "Job ID: ${SLURM_JOB_ID}  Node: $(hostname)"
echo "Python: $(python3 --version)  Working dir: $(pwd)"
echo "Singularity: $($SINGULARITY --version)"
echo "SIF: $SIF"
echo "================"

echo ""
echo "=== PS7 (Rust): cargo-tarpaulin 実行チェック ==="
python3 ps7_filter.py
echo "=== PS7 complete ==="
