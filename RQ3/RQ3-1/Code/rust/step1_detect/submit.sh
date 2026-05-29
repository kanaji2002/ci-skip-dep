#!/bin/bash -l
#SBATCH --job-name=rq3_1_rust_step1
#SBATCH --time=4-00:00:00
#SBATCH --partition=gpu_long
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=64G
#SBATCH --gres=gpu:1
#SBATCH --output=/work/rintaro-k/research/RQ3/RQ3-1/Code/rust/batch_output/%x/%j/out.out
#SBATCH --error=/work/rintaro-k/research/RQ3/RQ3-1/Code/rust/batch_output/%x/%j/err.err

# ============================================
# Usage:
#   sbatch submit.sh --index 0   # idx0 (0〜99件目)
#   sbatch submit.sh --index 1   # idx1 (100〜199件目)
#   sbatch submit.sh --limit 5   # テスト実行
#   sbatch submit.sh --skip 20   # 20件スキップして再開
# ============================================

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

PYTHON_ARGS="$*"

# --batch-index の値をログディレクトリ名に反映
BATCH_INDEX=""
_args=("$@")
for (( i=0; i<${#_args[@]}; i++ )); do
    if [[ "${_args[$i]}" == "--index" ]]; then
        BATCH_INDEX="${_args[$((i+1))]}"
        break
    fi
done
JOB_LABEL="${SLURM_JOB_NAME:-rq3_1_rust_step1}${BATCH_INDEX:+_idx${BATCH_INDEX}}"

OUT_DIR="/work/rintaro-k/research/RQ3/RQ3-1/Code/rust/batch_output/${JOB_LABEL}/${SLURM_JOB_ID:-local}"
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
# Ollama (Singularity)
# ============================================
module load singularity

export OLLAMA_SIF="/work/rintaro-k/research/ollama.sif"
export OLLAMA_MODELS="/work/rintaro-k/research/ollama_models"
export OLLAMA_NUM_CTX=2048

echo "=== Starting Ollama ==="
singularity exec --nv \
    --bind "${OLLAMA_MODELS}:${OLLAMA_MODELS}" \
    "${OLLAMA_SIF}" ollama serve &
OLLAMA_PID=$!

sleep 5
for i in $(seq 1 12); do
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "Ollama ready."; break
    fi
    echo "Waiting for Ollama... (${i}/12)"; sleep 5
done

singularity exec --nv --bind "${OLLAMA_MODELS}:${OLLAMA_MODELS}" "${OLLAMA_SIF}" ollama pull llama3.1:8b                  || true
singularity exec --nv --bind "${OLLAMA_MODELS}:${OLLAMA_MODELS}" "${OLLAMA_SIF}" ollama pull qwen3.5:4b                   || true
singularity exec --nv --bind "${OLLAMA_MODELS}:${OLLAMA_MODELS}" "${OLLAMA_SIF}" ollama pull deepseek-coder:6.7b-instruct || true

# ============================================
# Job info
# ============================================
echo "=== Job Info ==="
echo "Job ID:   ${SLURM_JOB_ID:-local}"
echo "Node:     $(hostname)"
echo "Python:   $(python3 --version)"
echo "GPU:      $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'n/a')"
echo "Args:     ${PYTHON_ARGS:-'(none)'}"
echo "================"

# ============================================
# 実行
# ============================================
TOTAL_START=$(date +%s)

cd /work/rintaro-k/research/RQ3/RQ3-1/Code/rust/step1_detect
python3 run.py ${PYTHON_ARGS}

TOTAL_END=$(date +%s)
TOTAL_ELAPSED=$((TOTAL_END - TOTAL_START))

echo "job_id=${SLURM_JOB_ID:-local}"  > "${OUT_DIR}/timing.log"
echo "total_sec=${TOTAL_ELAPSED}"     >> "${OUT_DIR}/timing.log"
echo "=== Done (${TOTAL_ELAPSED}s) ==="
