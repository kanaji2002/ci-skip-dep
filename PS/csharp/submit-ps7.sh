#!/bin/bash -l
#SBATCH --job-name=ps7-filter-csharp
#SBATCH --time=100:00:00
#SBATCH --partition=cluster_long
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=32G
#SBATCH --output=/work/rintaro-k/research/PS/csharp/batch/output/%x/%j/out.out
#SBATCH --error=/work/rintaro-k/research/PS/csharp/batch/output/%x/%j/err.err

set -euo pipefail

export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
if command -v pyenv &> /dev/null; then
    eval "$(pyenv init -)"
    eval "$(pyenv init --path)" 2>/dev/null || true
fi
pyenv activate py3

SINGULARITY=/opt/singularity/3.9.6/bin/singularity
SIF=/work/rintaro-k/research/containers/dotnet-sdk8.sif

# コンテナが未取得なら pull
if [ ! -f "$SIF" ]; then
    echo "Pulling dotnet-sdk8 container ..."
    SINGULARITY_TMPDIR=/work/rintaro-k/research/containers/tmp
    mkdir -p "$SINGULARITY_TMPDIR"
    $SINGULARITY pull "$SIF" docker://mcr.microsoft.com/dotnet/sdk:8.0
fi

# coverlet.collector を NuGet キャッシュに事前ダウンロード
# (compute node でネットワーク不要にするため)
COVERLET_PKG="$HOME/.nuget/packages/coverlet.collector"
if [ ! -d "$COVERLET_PKG" ]; then
    echo "Pre-downloading coverlet.collector to NuGet cache ..."
    SEED_DIR=$(mktemp -d)
    $SINGULARITY exec \
        --bind /work/rintaro-k:/work/rintaro-k \
        --pwd "$SEED_DIR" \
        "$SIF" \
        bash -c "dotnet new classlib -n seed --no-restore && \
                 dotnet add seed.csproj package coverlet.collector --no-restore && \
                 dotnet restore seed.csproj -v q" 2>/dev/null || true
    rm -rf "$SEED_DIR"
    echo "coverlet.collector cache: $(ls $COVERLET_PKG 2>/dev/null || echo 'not found')"
fi

OUT_DIR="/work/rintaro-k/research/PS/csharp/batch/output/${SLURM_JOB_NAME}/${SLURM_JOB_ID}"
mkdir -p "$OUT_DIR"
cd /work/rintaro-k/research/PS/csharp

echo "=== Job Info ==="
echo "Job ID: ${SLURM_JOB_ID}  Node: $(hostname)"
echo "Python: $(python3 --version)  Working dir: $(pwd)"
echo "Singularity: $($SINGULARITY --version)"
echo "SIF: $SIF"
echo "NuGet coverlet.collector: $(ls $COVERLET_PKG 2>/dev/null | head -1 || echo 'not cached')"
echo "================"

echo ""
echo "=== PS7 (C#): dotnet test XPlat Code Coverage ==="
python3 ps7_filter.py
echo "=== PS7 complete ==="
