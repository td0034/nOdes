#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE="$(dirname "$SCRIPT_DIR")/influxdb_bridge.py"

cd "$SCRIPT_DIR"

# Check for docker
if ! command -v docker &>/dev/null; then
    echo "Docker not found. Install it with:"
    echo "  curl -fsSL https://get.docker.com | sh"
    echo "  sudo usermod -aG docker \$USER"
    echo "Then log out and back in, and re-run this script."
    exit 1
fi

# Check docker compose
if ! docker compose version &>/dev/null; then
    echo "Docker Compose plugin not found. Install with:"
    echo "  sudo apt install docker-compose-plugin"
    exit 1
fi

# Start the stack
echo "Starting InfluxDB + Grafana..."
docker compose up -d

# Wait for InfluxDB health
echo -n "Waiting for InfluxDB"
for i in $(seq 1 30); do
    if docker compose exec -T influxdb influx ping &>/dev/null; then
        echo " ready!"
        break
    fi
    echo -n "."
    sleep 1
done

# Check for Python influxdb-client
if ! python3 -c "import influxdb_client" &>/dev/null; then
    echo "Installing influxdb-client Python package..."
    pip install influxdb-client
fi

echo ""
echo "=== Orb Monitoring Stack ==="
echo "  Grafana:  http://localhost:3000  (admin/admin)"
echo "  InfluxDB: http://localhost:8086  (orb/orbpassword)"
echo ""
echo "Starting bridge (Ctrl+C to stop)..."
exec python3 "$BRIDGE" "$@"
