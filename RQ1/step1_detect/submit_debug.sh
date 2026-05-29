#!/bin/bash -l
#SBATCH --job-name=rq1_debug_deepseek
#SBATCH --time=00:30:00
#SBATCH --partition=gpu_short
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=32G
#SBATCH --gres=gpu:1
#SBATCH --output=/work/rintaro-k/research/RQ1/batch_output/%x/%j/out.out
#SBATCH --error=/work/rintaro-k/research/RQ1/batch_output/%x/%j/err.err

set -euo pipefail

OLLAMA_PID=""
cleanup() {
    [[ -n "${OLLAMA_PID}" ]] && kill "${OLLAMA_PID}" 2>/dev/null || true
    pkill -f "ollama serve" 2>/dev/null || true
}
trap cleanup EXIT

export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
if command -v pyenv &>/dev/null; then
    eval "$(pyenv init -)"
    eval "$(pyenv init --path)" 2>/dev/null || true
fi
pyenv activate py3

module load singularity
export OLLAMA_SIF="/work/rintaro-k/research/ollama.sif"
export OLLAMA_MODELS="/work/rintaro-k/research/ollama_models"

singularity exec --nv \
    --bind "${OLLAMA_MODELS}:${OLLAMA_MODELS}" \
    "${OLLAMA_SIF}" ollama serve &
OLLAMA_PID=$!

sleep 5
for i in $(seq 1 12); do
    curl -s http://localhost:11434/api/tags >/dev/null 2>&1 && echo "Ollama ready." && break
    echo "Waiting... (${i}/12)"; sleep 5
done

echo "Node: $(hostname) | GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null)"

OUT_DIR="/work/rintaro-k/research/RQ1/batch_output/${SLURM_JOB_NAME}/${SLURM_JOB_ID}"
mkdir -p "$OUT_DIR"

cd /work/rintaro-k/research/RQ1/step1_detect
python3 debug_deepseek.py 2>&1 | tee "${OUT_DIR}/debug_deepseek_output.txt"

echo "=== Done ==="
