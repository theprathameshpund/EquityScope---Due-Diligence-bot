#!/usr/bin/env bash
set -e

# Install Qdrant
QDRANT_VERSION=$(curl -s "https://api.github.com/repos/qdrant/qdrant/releases/latest" | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])")
echo "Latest Qdrant: $QDRANT_VERSION"
curl -sL "https://github.com/qdrant/qdrant/releases/download/${QDRANT_VERSION}/qdrant-x86_64-unknown-linux-musl.tar.gz" -o /tmp/qdrant.tar.gz
tar -xzf /tmp/qdrant.tar.gz -C /tmp/
mkdir -p "$HOME/.local/bin"
mv /tmp/qdrant "$HOME/.local/bin/qdrant"
chmod +x "$HOME/.local/bin/qdrant"
# Add to PATH for this session if not already
export PATH="$HOME/.local/bin:$PATH"
qdrant --version
echo "Qdrant installed OK"
