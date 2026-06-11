#!/usr/bin/env bash
# Start EquityScope infrastructure inside WSL (no Docker required):
#   - Redis on :6379
#   - PostgreSQL on :5432 (user/db: equityscope/equityscope)
#   - Qdrant on :6333 (binary in /opt/qdrant)
# Usage (from Windows):  wsl -d Ubuntu -u root -- bash /mnt/<path>/scripts/infra_wsl.sh
set -e

echo "── Redis ──────────────────────────────"
if ! redis-cli ping >/dev/null 2>&1; then
    redis-server --daemonize yes --bind 0.0.0.0 --protected-mode no
    sleep 1
fi
redis-cli ping

echo "── PostgreSQL ─────────────────────────"
PG_VER=$(ls /usr/lib/postgresql | head -1)
PG_CONF_DIR="/etc/postgresql/${PG_VER}/main"
# Port 5433: a native Windows postgres may already occupy 5432 on the host.
sed -i "s/^#\?port = .*/port = 5433/" "${PG_CONF_DIR}/postgresql.conf"
service postgresql start || true
sleep 2
PSQL="psql -p 5433"
su - postgres -c "${PSQL} -tc \"SELECT 1 FROM pg_roles WHERE rolname='equityscope'\"" | grep -q 1 || \
    su - postgres -c "${PSQL} -c \"CREATE USER equityscope WITH PASSWORD 'equityscope'\""
su - postgres -c "${PSQL} -tc \"SELECT 1 FROM pg_database WHERE datname='equityscope'\"" | grep -q 1 || \
    su - postgres -c "createdb -p 5433 -O equityscope equityscope"
su - postgres -c "${PSQL} -c \"ALTER USER equityscope WITH PASSWORD 'equityscope'\"" >/dev/null
# Allow password auth from the Windows host (WSL localhost forwarding).
if ! grep -q "0.0.0.0/0" "${PG_CONF_DIR}/pg_hba.conf"; then
    echo "host all all 0.0.0.0/0 scram-sha-256" >> "${PG_CONF_DIR}/pg_hba.conf"
    sed -i "s/^#listen_addresses.*/listen_addresses = '*'/" "${PG_CONF_DIR}/postgresql.conf"
    service postgresql restart
    sleep 2
fi
su - postgres -c "${PSQL} -tc 'SELECT version()'" | head -1

echo "── Qdrant ─────────────────────────────"
if ! curl -s http://localhost:6333/readyz >/dev/null 2>&1; then
    mkdir -p /opt/qdrant/storage
    cd /opt/qdrant
    nohup ./qdrant > /opt/qdrant/qdrant.log 2>&1 &
    for i in $(seq 1 30); do
        curl -s http://localhost:6333/readyz >/dev/null 2>&1 && break
        sleep 1
    done
fi
curl -s http://localhost:6333/readyz && echo

echo "All infrastructure up: redis :6379, postgres :5432, qdrant :6333"
