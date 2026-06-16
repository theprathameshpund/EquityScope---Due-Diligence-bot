#!/usr/bin/env bash
# Download bge-small weights ignoring SSL (corporate proxy with self-signed cert)
set -e

SNAP_DIR="$HOME/.cache/huggingface/hub/models--BAAI--bge-small-en-v1.5/snapshots/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
mkdir -p "$SNAP_DIR/1_Pooling"

BASE="https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/main"
CURL="curl -fL --retry 3 -k --silent --show-error"   # -k = ignore SSL

dl() {
    local f="$1"
    local dest="$SNAP_DIR/$f"
    mkdir -p "$(dirname "$dest")"
    if [ -f "$dest" ] && [ "$(stat -c%s "$dest")" -gt 1000 ]; then
        echo "  skip (exists): $f"
        return
    fi
    echo -n "  $f ... "
    $CURL "$BASE/$f" -o "$dest"
    echo "$(stat -c%s "$dest") bytes"
}

dl "config.json"
dl "tokenizer_config.json"
dl "tokenizer.json"
dl "vocab.txt"
dl "special_tokens_map.json"
dl "sentence_bert_config.json"
dl "modules.json"
dl "config_sentence_transformers.json"
dl "1_Pooling/config.json"

# Model weights
echo -n "  model.safetensors ... "
$CURL "$BASE/model.safetensors" -o "$SNAP_DIR/model.safetensors"
SIZE=$(stat -c%s "$SNAP_DIR/model.safetensors")
echo "$((SIZE/1024/1024))MB"

echo ""
if [ "$SIZE" -gt 20000000 ]; then
    echo "SUCCESS: bge-small-en-v1.5 ready ($((SIZE/1024/1024))MB)"
else
    echo "ERROR: model.safetensors too small - download incomplete"
    exit 1
fi
# Convert Windows path to WSL path
WIN_CACHE="$(wslpath "$CACHE_DIR" 2>/dev/null || echo "$HOME/.cache/huggingface/hub/models--BAAI--bge-small-en-v1.5")"
WIN_CACHE="$HOME/.cache/huggingface/hub/models--BAAI--bge-small-en-v1.5"

SNAPSHOT_HASH="57217b1b1c55b4f50d4e2ba0f6d65ce0"
SNAP_DIR="$WIN_CACHE/snapshots/$SNAPSHOT_HASH"

# Try to find existing snapshot hash
EXISTING=$(ls "$WIN_CACHE/snapshots/" 2>/dev/null | head -1)
if [ -n "$EXISTING" ]; then
    SNAP_DIR="$WIN_CACHE/snapshots/$EXISTING"
    echo "Using existing snapshot dir: $SNAP_DIR"
else
    # Get the latest commit hash from HF API
    HASH=$(curl -sf "https://huggingface.co/api/models/BAAI/bge-small-en-v1.5" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['sha'])" 2>/dev/null || echo "")
    if [ -n "$HASH" ]; then
        SNAP_DIR="$WIN_CACHE/snapshots/$HASH"
    fi
    mkdir -p "$SNAP_DIR"
fi

mkdir -p "$SNAP_DIR/1_Pooling"

echo "Downloading model weights to $SNAP_DIR ..."

BASE="https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/main"

download() {
    local file="$1"
    local dest="$SNAP_DIR/$file"
    if [ -f "$dest" ] && [ "$(stat -c%s "$dest")" -gt 1000 ]; then
        echo "  Already exists: $file"
        return
    fi
    echo "  Downloading: $file"
    mkdir -p "$(dirname "$dest")"
    curl -fL --retry 3 "$BASE/$file" -o "$dest"
}

download "config.json"
download "tokenizer_config.json"
download "tokenizer.json"
download "vocab.txt"
download "special_tokens_map.json"
download "sentence_bert_config.json"
download "modules.json"
download "config_sentence_transformers.json"
download "1_Pooling/config.json"

# Download weights - try safetensors first, then pytorch
if ! [ -f "$SNAP_DIR/model.safetensors" ] || [ "$(stat -c%s "$SNAP_DIR/model.safetensors" 2>/dev/null)" -lt 1000000 ]; then
    echo "  Downloading model weights (safetensors)..."
    curl -fL --retry 3 "$BASE/model.safetensors" -o "$SNAP_DIR/model.safetensors" 2>/dev/null || \
    curl -fL --retry 3 "$BASE/pytorch_model.bin" -o "$SNAP_DIR/pytorch_model.bin"
fi

echo ""
echo "Files in snapshot dir:"
ls -lh "$SNAP_DIR" | grep -v "^total"
echo ""

# Test loading
python3 -c "
import sys
sys.path.insert(0, '/mnt/c/Users/Prathamesh.Pund/My project/.venv/Lib/site-packages')
import os
os.environ['HF_HUB_OFFLINE'] = '1'
# Quick size check
import pathlib
snap = pathlib.Path('$SNAP_DIR')
weights = list(snap.glob('*.safetensors')) + list(snap.glob('*.bin'))
if weights:
    size_mb = weights[0].stat().st_size / 1e6
    print(f'Model weights: {weights[0].name} ({size_mb:.1f} MB)')
    if size_mb > 20:
        print('Download COMPLETE - model is ready')
    else:
        print('WARNING: file too small, download may be incomplete')
else:
    print('ERROR: No weight files found')
" 2>/dev/null || echo "Check files above manually"
