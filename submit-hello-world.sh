#!/bin/bash
#SBATCH --job-name=hello_world
#SBATCH --time=100:00:00
#SBATCH --partition=cluster_long
#SBATCH --ntasks=1
#SBATCH --array=0-6
#SBATCH --cpus-per-task=1
#SBATCH --output=/work/rintaro-k/research/batch/output/%x/%A/%a/out.out
#SBATCH --error=/work/rintaro-k/research/batch/output/%x/%A/%a/err.err

# === スクリプト・イメージのパス設定 ===
SCRIPT_NAME="/work/rintaro-k/research/hello-world.py"

pyenv local 3.12.5
python "$SCRIPT_NAME" "${SLURM_ARRAY_TASK_ID}" "${SLURM_ARRAY_JOB_ID}"