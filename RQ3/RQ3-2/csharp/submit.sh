#!/bin/bash -l
#SBATCH --job-name=rq3_csharp
#SBATCH --time=4-00:00:00
#SBATCH --partition=isgpu4h200_long
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=100G
#SBATCH --gres=gpu:1
#SBATCH --output=/work/rintaro-k/research/RQ3/RQ3-2/csharp/batch/output/%x/%j/out.out
#SBATCH --error=/work/rintaro-k/research/RQ3/RQ3-2/csharp/batch/output/%x/%j/err.err

# Usage:
#   sbatch submit.sh --repo-list /work/rintaro-k/research/PS/csharp/ps6/ps6_filtered.csv --batch-index 0

set -euo pipefail

OLLAMA_PID=""
cleanup() {
    if [[ -n "${OLLAMA_PID}" ]]; then
        echo "=== Stopping Ollama (PID: ${OLLAMA_PID}) ==="
        kill "${OLLAMA_PID}" 2>/dev/null || true
        wait "${OLLAMA_PID}" 2>/dev/null || true
    fi
    pkill -f "ollama serve" 2>/dev/null || true
}
trap cleanup EXIT

REPO_LIST_FILE=""
BATCH_INDEX=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo-list)   REPO_LIST_FILE="$2"; shift 2 ;;
        --batch-index) BATCH_INDEX="$2";    shift 2 ;;
        *) echo "Unknown argument: $1"; shift ;;
    esac
done

if [[ -z "$REPO_LIST_FILE" ]]; then
    echo "Error: --repo-list is required."
    echo "  Example: sbatch submit.sh --repo-list /work/rintaro-k/research/PS/csharp/ps6/ps6_filtered.csv --batch-index 0"
    exit 1
fi
export REPO_LIST_PATH="${REPO_LIST_FILE}"

TIME_LOG="/work/rintaro-k/research/RQ3/RQ3-2/csharp/batch/output/${SLURM_JOB_NAME:-rq3_csharp}/${SLURM_JOB_ID:-local}/timing.log"
TOTAL_START=$(date +%s)

# ── pyenv ──
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
if command -v pyenv &> /dev/null; then
    eval "$(pyenv init -)"
    eval "$(pyenv init --path)" 2>/dev/null || true
fi
pyenv activate py3

# ── 作業ディレクトリ ──
OUT_DIR="/work/rintaro-k/research/RQ3/RQ3-2/csharp/batch/output/${SLURM_JOB_NAME:-rq3_csharp}/${SLURM_JOB_ID:-local}"
mkdir -p "$OUT_DIR"
cd /work/rintaro-k/research/RQ3/RQ3-2/csharp

# ── Ollama ──
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

sleep 5
for i in $(seq 1 12); do
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "Ollama is ready."
        break
    fi
    echo "Waiting for Ollama... (${i}/12)"
    sleep 5
done

singularity exec --nv --bind "${OLLAMA_MODELS}:${OLLAMA_MODELS}" "${OLLAMA_SIF}" ollama pull llama3.1:8b    || true
singularity exec --nv --bind "${OLLAMA_MODELS}:${OLLAMA_MODELS}" "${OLLAMA_SIF}" ollama pull qwen3.5:4b     || true
singularity exec --nv --bind "${OLLAMA_MODELS}:${OLLAMA_MODELS}" "${OLLAMA_SIF}" ollama pull deepseek-coder:6.7b-instruct || true

# ── Job Info ──
echo "=== Job Info ==="
echo "Job ID:    ${SLURM_JOB_ID}"
echo "Node:      $(hostname)"
echo "Language:  C#"
echo "Repo list: ${REPO_LIST_PATH}"
echo "Batch idx: ${BATCH_INDEX:-'(not set)'}"
echo "Python:    $(which python3) ($(python3 --version))"
echo "GPU:       $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'not available')"
echo "================"

# ── Pipeline ──
echo ""
echo "=== Stage: pipeline ==="
PIPELINE_START=$(date +%s)

if [[ -n "${BATCH_INDEX}" ]]; then
    python3 pipeline_main.py --repo-list "${REPO_LIST_PATH}" --batch-index "${BATCH_INDEX}"
else
    python3 pipeline_main.py --repo-list "${REPO_LIST_PATH}"
fi

PIPELINE_END=$(date +%s)
PIPELINE_ELAPSED=$((PIPELINE_END - PIPELINE_START))
echo "=== Pipeline complete ==="

# ── Timing ──
TOTAL_END=$(date +%s)
TOTAL_ELAPSED=$((TOTAL_END - TOTAL_START))
mkdir -p "$(dirname "$TIME_LOG")"
{
    echo "job_id=${SLURM_JOB_ID:-local}"
    echo "language=csharp"
    echo "repo_list=${REPO_LIST_PATH}"
    echo "start=$(date -d @${TOTAL_START} '+%Y-%m-%d %H:%M:%S')"
    echo "end=$(date -d @${TOTAL_END} '+%Y-%m-%d %H:%M:%S')"
    echo "total_sec=${TOTAL_ELAPSED}"
    [[ -n "${BATCH_INDEX:-}" ]]      && echo "batch_index=${BATCH_INDEX}"
    [[ -n "${PIPELINE_ELAPSED:-}" ]] && echo "pipeline_sec=${PIPELINE_ELAPSED}"
} > "$TIME_LOG"
echo "=== Timing saved to ${TIME_LOG} ==="
cat "$TIME_LOG"
