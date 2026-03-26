#!/bin/bash -l
#SBATCH --job-name=pipeline_all
#SBATCH --time=4-00:00:00
#SBATCH --partition=gpu_long
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=200G
#SBATCH --gres=gpu:1
#SBATCH --output=/work/rintaro-k/research/DC/data-curation-all/batch/output/%x/%j/out.out
#SBATCH --error=/work/rintaro-k/research/DC/data-curation-all/batch/output/%x/%j/err.err

# ============================================
# Usage:
#   # 旧方式: ps4_results_N.csv を 100 件ずつ処理 (PS5 フィルタあり)
#   sbatch submit.sh --repo-list ps4_results_0.csv
#   sbatch submit.sh --repo-list ps4_results_0.csv --stage pipeline
#   sbatch submit.sh --repo-list ps4_results_0.csv --stage filter
#
#   # 新方式: PS5 済み CSV を --batch-index で 100 件ずつ処理 (PS5 フィルタをスキップ)
#   sbatch submit.sh --repo-list /path/to/ps5_filtered.csv --batch-index 0
#   sbatch submit.sh --repo-list /path/to/ps5_filtered.csv --batch-index 1
#   sbatch submit.sh --repo-list /path/to/ps5_filtered.csv --batch-index 5 --stage pipeline
# ============================================

set -euo pipefail

# Ollama を確実に終了させる (CANCEL・エラー時も含む)
OLLAMA_PID=""
cleanup() {
    if [[ -n "${OLLAMA_PID}" ]]; then
        echo "=== Stopping Ollama (PID: ${OLLAMA_PID}) ==="
        kill "${OLLAMA_PID}" 2>/dev/null || true
        wait "${OLLAMA_PID}" 2>/dev/null || true
    fi
    # 同一ノード上の残存 ollama プロセスも念のため終了
    pkill -f "ollama serve" 2>/dev/null || true
}
trap cleanup EXIT

STAGE="all"
LIMIT="all"
REPO_LIST_FILE=""
BATCH_INDEX=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stage)
            STAGE="$2"; shift 2 ;;
        --limit)
            LIMIT="$2"; shift 2 ;;
        --repo-list)
            REPO_LIST_FILE="$2"; shift 2 ;;
        --batch-index)
            BATCH_INDEX="$2"; shift 2 ;;
        *)
            echo "Unknown argument: $1"; shift ;;
    esac
done

# REPO_LIST_PATH を設定
# 絶対パス (/) で始まる場合はそのまま使用、それ以外は REPO_LIST_BASE を付与
REPO_LIST_BASE="/work/rintaro-k/research/PS/js/ps4_results_100row_each"
if [[ -n "$REPO_LIST_FILE" ]]; then
    if [[ "$REPO_LIST_FILE" == /* ]]; then
        export REPO_LIST_PATH="${REPO_LIST_FILE}"
    else
        export REPO_LIST_PATH="${REPO_LIST_BASE}/${REPO_LIST_FILE}"
    fi
    echo "Using repo list: ${REPO_LIST_PATH}"
else
    echo "Error: --repo-list is required."
    echo "  旧方式: sbatch submit.sh --repo-list ps4_results_1.csv"
    echo "  新方式: sbatch submit.sh --repo-list /path/to/ps5_filtered.csv --batch-index 0"
    exit 1
fi

TIME_LOG="/work/rintaro-k/research/DC/data-curation-all/batch/output/${SLURM_JOB_NAME:-pipeline_all}/${SLURM_JOB_ID:-local}/timing.log"
TOTAL_START=$(date +%s)

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
# nvm / Node.js (depcheck, knip 用)
# ============================================
export NVM_DIR="$HOME/.nvm"
if [[ -s "$NVM_DIR/nvm.sh" ]]; then
    source "$NVM_DIR/nvm.sh"
    nvm use 20 2>/dev/null || true
fi

# ============================================
# Setup directories
# ============================================
OUT_DIR="/work/rintaro-k/research/DC/data-curation-all/batch/output/${SLURM_JOB_NAME:-pipeline_all}/${SLURM_JOB_ID:-local}"
mkdir -p "$OUT_DIR"

cd /work/rintaro-k/research/DC/data-curation-all

# ============================================
# Ollama initialization (Singularity)
# ============================================
module load singularity

export OLLAMA_SIF="/work/rintaro-k/research/ollama.sif"
export OLLAMA_MODELS="/work/rintaro-k/research/ollama_models"
export OLLAMA_NUM_CTX=2048

echo "=== Starting Ollama (Singularity) ==="
singularity exec --nv \
    --bind "${OLLAMA_MODELS}:${OLLAMA_MODELS}" \
    "${OLLAMA_SIF}" ollama serve &
OLLAMA_PID=$!
echo "Ollama PID: ${OLLAMA_PID}"

# Ollama が起動するまで待機
sleep 5
for i in $(seq 1 12); do
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "Ollama is ready."
        break
    fi
    echo "Waiting for Ollama... (${i}/12)"
    sleep 5
done

# モデルを事前にプル（未取得の場合のみ）
singularity exec --nv \
    --bind "${OLLAMA_MODELS}:${OLLAMA_MODELS}" \
    "${OLLAMA_SIF}" ollama pull llama3.1:8b || true

singularity exec --nv \
    --bind "${OLLAMA_MODELS}:${OLLAMA_MODELS}" \
    "${OLLAMA_SIF}" ollama pull qwen3.5:4b || true

singularity exec --nv \
    --bind "${OLLAMA_MODELS}:${OLLAMA_MODELS}" \
    "${OLLAMA_SIF}" ollama pull deepseek-coder-v2:16b || true

# ============================================
# Job Info
# ============================================
echo "=== Job Info ==="
echo "Job ID:     ${SLURM_JOB_ID}"
echo "Node:       $(hostname)"
echo "Stage:      ${STAGE}"
echo "Limit:      ${LIMIT}"
echo "Batch idx:  ${BATCH_INDEX:-'(not set, use --limit/--start-from)'}"
echo "Repo list:  ${REPO_LIST_PATH}"
echo "Python:     $(which python3) ($(python3 --version))"
echo "Node:       $(node --version 2>/dev/null || echo 'not available')"
echo "depcheck:   $(depcheck --version 2>/dev/null || echo 'not found')"
echo "Ollama:     llama3.1:8b + qwen3.5:4b + deepseek-coder-v2:16b"
echo "Working dir: $(pwd)"
echo "GPU:        $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'not available')"
echo "================"

# ============================================
# Stage: filter (PS5)
# --batch-index が指定されている場合は入力が PS5 済み CSV のため filter をスキップ
# ============================================
if [ -n "${BATCH_INDEX}" ]; then
    echo ""
    echo "=== Stage: filter (PS5) — SKIPPED (--batch-index specified, input is PS5-filtered) ==="
elif [ "$STAGE" = "all" ] || [ "$STAGE" = "filter" ]; then
    echo ""
    echo "=== Stage: filter (PS5) ==="
    FILTER_START=$(date +%s)
    python3 -m step1_project_selection.filter_projects --input "${REPO_LIST_PATH}"
    FILTER_END=$(date +%s)
    FILTER_ELAPSED=$((FILTER_END - FILTER_START))
    echo "=== Filter complete ==="
fi

# ============================================
# Stage: pipeline (main data collection)
# ============================================
if [ "$STAGE" = "all" ] || [ "$STAGE" = "pipeline" ]; then
    echo ""
    echo "=== Stage: pipeline ==="
    PIPELINE_START=$(date +%s)

    # --batch-index が指定されている場合は優先、なければ --limit を使用
    if [ -n "${BATCH_INDEX}" ]; then
        python3 pipeline_main.py --repo-list "${REPO_LIST_PATH}" --batch-index "${BATCH_INDEX}"
    elif [ "$LIMIT" = "all" ]; then
        python3 pipeline_main.py --repo-list "${REPO_LIST_PATH}"
    else
        python3 pipeline_main.py --repo-list "${REPO_LIST_PATH}" --limit "$LIMIT"
    fi

    PIPELINE_END=$(date +%s)
    PIPELINE_ELAPSED=$((PIPELINE_END - PIPELINE_START))
    echo "=== Pipeline complete ==="
fi


# ============================================
# Timing log
# ============================================
TOTAL_END=$(date +%s)
TOTAL_ELAPSED=$((TOTAL_END - TOTAL_START))
mkdir -p "$(dirname "$TIME_LOG")"
{
    echo "job_id=${SLURM_JOB_ID:-local}"
    echo "stage=${STAGE}"
    echo "repo_list=${REPO_LIST_PATH}"
    echo "start=$(date -d @${TOTAL_START} '+%Y-%m-%d %H:%M:%S')"
    echo "end=$(date -d @${TOTAL_END} '+%Y-%m-%d %H:%M:%S')"
    echo "total_sec=${TOTAL_ELAPSED}"
    [ -n "${BATCH_INDEX:-}" ]      && echo "batch_index=${BATCH_INDEX}"
    [ -n "${FILTER_ELAPSED:-}" ]   && echo "filter_sec=${FILTER_ELAPSED}"
    [ -n "${PIPELINE_ELAPSED:-}" ] && echo "pipeline_sec=${PIPELINE_ELAPSED}"
} > "$TIME_LOG"
echo "=== Timing saved to ${TIME_LOG} ==="
cat "$TIME_LOG"

