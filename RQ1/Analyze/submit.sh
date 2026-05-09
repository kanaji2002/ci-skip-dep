#!/bin/bash -l
#SBATCH --job-name=rq1_count_deps
#SBATCH --partition=cluster_short
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=02:00:00
#SBATCH --output=/work/rintaro-k/research/RQ1/Analyze/logs/%j/out.out
#SBATCH --error=/work/rintaro-k/research/RQ1/Analyze/logs/%j/err.err

set -euo pipefail

mkdir -p "/work/rintaro-k/research/RQ1/Analyze/logs/${SLURM_JOB_ID}"

# pyenv
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
if command -v pyenv &> /dev/null; then
    eval "$(pyenv init -)"
    eval "$(pyenv init --path)" 2>/dev/null || true
fi
pyenv activate py3

echo "=== Job Info ==="
echo "Job ID:  ${SLURM_JOB_ID}"
echo "Node:    $(hostname)"
echo "Python:  $(which python3) ($(python3 --version))"
echo "================"

python3 /work/rintaro-k/research/RQ1/Analyze/count_dependencies.py
