#!/bin/bash
# コンテナイメージを SIF 形式で取得する (一度だけ実行すれば OK)
SINGULARITY=/opt/singularity/3.9.6/bin/singularity
DIR="$(cd "$(dirname "$0")" && pwd)"

# /tmp は容量不足になりやすいため work 以下を使う
export SINGULARITY_TMPDIR="$DIR/tmp"
mkdir -p "$SINGULARITY_TMPDIR"

echo "=== Pulling Rust/tarpaulin container ==="
$SINGULARITY pull --force "$DIR/rust-tarpaulin.sif" \
    docker://xd009642/tarpaulin:develop-nightly

echo "=== Pulling .NET SDK 8 container ==="
$SINGULARITY pull --force "$DIR/dotnet-sdk8.sif" \
    docker://mcr.microsoft.com/dotnet/sdk:8.0

echo "=== Done ==="
ls -lh "$DIR"/*.sif
