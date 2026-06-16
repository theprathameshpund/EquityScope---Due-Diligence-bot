#!/usr/bin/env bash
SNAP="$HOME/.cache/huggingface/hub/models--BAAI--bge-small-en-v1.5/snapshots/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
DEST="$SNAP/pytorch_model.bin"
echo "Downloading pytorch_model.bin ..."
curl -skL --retry 3 -o "$DEST" "https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/main/pytorch_model.bin"
SIZE=$(stat -c%s "$DEST" 2>/dev/null)
echo "Size: $((SIZE/1024/1024))MB ($SIZE bytes)"
if [ "$SIZE" -gt 20000000 ]; then
    echo "SUCCESS"
else
    echo "FAILED - too small"
    exit 1
fi
