#!/bin/bash -l
#SBATCH --job-name=ps5-filter
#SBATCH --time=100:00:00
#SBATCH --partition=cluster_long
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --output=/work/rintaro-k/research/PS/js/batch/output/%x/%j/out.out
#SBATCH --error=/work/rintaro-k/research/PS/js/batch/output/%x/%j/err.err

# ============================================
# Usage:
#   # デフォルト (ps4/ps4_all.csv を自動生成して入力)
#   sbatch submit-ps5.sh
#
#   # テスト (先頭 10 件のみ)
#   sbatch submit-ps5.sh --limit 10
#
#   # 出力先を指定
#   sbatch submit-ps5.sh --output ps5/ps5_filtered.csv
# ============================================

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
OUT_DIR="/work/rintaro-k/research/PS/js/batch/output/${SLURM_JOB_NAME:-ps5-filter}/${SLURM_JOB_ID:-local}"
mkdir -p "$OUT_DIR"

cd /work/rintaro-k/research/PS/js

# ============================================
# Job Info
# ============================================
echo "=== Job Info ==="
echo "Job ID:      ${SLURM_JOB_ID:-local}"
echo "Node:        $(hostname)"
echo "Python:      $(which python3) ($(python3 --version))"
echo "Working dir: $(pwd)"
echo "Args:        $*"
echo "================"

# ============================================
# ps4チャンクをマージして ps4/ps4_all.csv を生成
# ============================================
if [ ! -f ps4/ps4_all.csv ]; then
    echo ""
    echo "=== Merging ps4 chunks into ps4/ps4_all.csv ==="
    mapfile -t ps4_files < <(ls ps4/ps4_*.csv 2>/dev/null | sort -V)
    if [ ${#ps4_files[@]} -eq 0 ]; then
        echo "Error: No ps4/ps4_*.csv files found. Run submit.sh (PS2-PS4) first." >&2
        exit 1
    fi
    first=true
    for f in "${ps4_files[@]}"; do
        if $first; then
            cat "$f" > ps4/ps4_all.csv
            first=false
        else
            tail -n +2 "$f" >> ps4/ps4_all.csv
        fi
    done
    echo "Merged ${#ps4_files[@]} files: $(wc -l < ps4/ps4_all.csv) lines (including header)"
else
    echo "ps4/ps4_all.csv already exists, skipping merge."
fi

# ============================================
# Run PS5 filter
# ============================================
echo ""
echo "=== Starting PS5 filter ==="
python3 ps5_filter.py "$@"
echo "=== PS5 filter complete ==="
