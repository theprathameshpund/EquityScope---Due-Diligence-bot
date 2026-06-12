#!/usr/bin/env bash
# EquityScope — start all infrastructure services in WSL Ubuntu
# Usage: bash scripts/start_infra.sh
# Run this once before starting the backend.

set -euo pipefail

REDIS_PASSWORD="ee803900cb61f78c895668e0b57cba31c60357683296e6236f65f5939949a3b3"
REDIS_PORT=6379
QDRANT_PORT=6333
QDRANT_DATA="$HOME/.local/share/qdrant"
LOG_DIR="$HOME/.local/log/equityscope"

mkdir -p "$LOG_DIR" "$QDRANT_DATA"

# ─── PostgreSQL ────────────────────────────────────────────────
echo "[infra] Checking PostgreSQL..."
if pg_isready -q -p 5433 2>/dev/null; then
    echo "[infra] PostgreSQL already running on port 5433 ✓"
else
    echo "[infra] Starting PostgreSQL..."
    pg_ctlcluster 18 main start 2>/dev/null || service postgresql start 2>/dev/null || true
    sleep 2
    if pg_isready -q -p 5433 2>/dev/null; then
        echo "[infra] PostgreSQL started ✓"
    else
        echo "[infra] WARNING: PostgreSQL may not be running — check manually"
    fi
fi

# Ensure DB + user exist (idempotent)
sudo -u postgres psql -p 5433 -tc "SELECT 1 FROM pg_roles WHERE rolname='equityscope'" 2>/dev/null | grep -q 1 || \
    sudo -u postgres psql -p 5433 -c "CREATE ROLE equityscope LOGIN PASSWORD 'equityscope';" 2>/dev/null || true
sudo -u postgres psql -p 5433 -tc "SELECT 1 FROM pg_database WHERE datname='equityscope'" 2>/dev/null | grep -q 1 || \
    sudo -u postgres psql -p 5433 -c "CREATE DATABASE equityscope OWNER equityscope;" 2>/dev/null || true

# ─── Redis ─────────────────────────────────────────────────────
echo "[infra] Checking Redis..."
if redis-cli -p $REDIS_PORT -a "$REDIS_PASSWORD" ping 2>/dev/null | grep -q PONG; then
    echo "[infra] Redis already running on port $REDIS_PORT ✓"
else
    echo "[infra] Starting Redis..."
    # Kill any stale redis without password
    redis-cli -p $REDIS_PORT ping 2>/dev/null | grep -q PONG && redis-cli -p $REDIS_PORT shutdown nosave 2>/dev/null || true
    sleep 1
    redis-server \
        --port $REDIS_PORT \
        --requirepass "$REDIS_PASSWORD" \
        --daemonize yes \
        --logfile "$LOG_DIR/redis.log" \
        --save "" \
        --appendonly no
    sleep 1
    if redis-cli -p $REDIS_PORT -a "$REDIS_PASSWORD" ping 2>/dev/null | grep -q PONG; then
        echo "[infra] Redis started ✓"
    else
        echo "[infra] WARNING: Redis may not be running — check $LOG_DIR/redis.log"
    fi
fi

# ─── Qdrant ────────────────────────────────────────────────────
echo "[infra] Checking Qdrant..."
if curl -sf http://localhost:$QDRANT_PORT/readyz >/dev/null 2>&1 || \
   curl -sf http://localhost:$QDRANT_PORT/ 2>/dev/null | grep -q qdrant; then
    echo "[infra] Qdrant already running on port $QDRANT_PORT ✓"
else
    echo "[infra] Starting Qdrant..."
    QDRANT_BIN="$HOME/.local/bin/qdrant"
    if [ ! -f "$QDRANT_BIN" ]; then
        echo "[infra] ERROR: qdrant not found at $QDRANT_BIN"
        echo "[infra] Run: bash scripts/install_qdrant_wsl.sh"
        exit 1
    fi
    nohup "$QDRANT_BIN" \
        --storage-path "$QDRANT_DATA" \
        > "$LOG_DIR/qdrant.log" 2>&1 &
    echo $! > "$LOG_DIR/qdrant.pid"
    sleep 3
    if curl -sf http://localhost:$QDRANT_PORT/ >/dev/null 2>&1; then
        echo "[infra] Qdrant started ✓ (pid $(cat $LOG_DIR/qdrant.pid))"
    else
        echo "[infra] WARNING: Qdrant may still be starting — check $LOG_DIR/qdrant.log"
    fi
fi

echo ""
echo "═══════════════════════════════════════════"
echo " Infrastructure status"
echo "═══════════════════════════════════════════"
pg_isready -q -p 5433 && echo " ✓ PostgreSQL  localhost:5433" || echo " ✗ PostgreSQL  NOT ready"
redis-cli -p $REDIS_PORT -a "$REDIS_PASSWORD" ping 2>/dev/null | grep -q PONG && \
    echo " ✓ Redis       localhost:$REDIS_PORT" || echo " ✗ Redis       NOT ready"
curl -sf http://localhost:$QDRANT_PORT/ >/dev/null 2>&1 && \
    echo " ✓ Qdrant      localhost:$QDRANT_PORT" || echo " ✗ Qdrant      NOT ready"
echo "═══════════════════════════════════════════"
