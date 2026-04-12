#!/bin/bash
# Ember-2 Launcher (Mac/Linux)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo " Ember-2 Launcher"
echo " ================="
echo ""

# -----------------------------------------------------------
# 1. Docker — start if not running
# -----------------------------------------------------------
echo "[1/4] Checking Docker..."
if docker info >/dev/null 2>&1; then
    echo "      Docker is running."
else
    echo "      Docker is not running. Attempting to start..."

    # macOS: open Docker Desktop app
    if [ "$(uname)" = "Darwin" ]; then
        open -a Docker 2>/dev/null || true
    # Linux: try starting the systemd service
    else
        sudo systemctl start docker 2>/dev/null || true
    fi

    DOCKER_READY=0
    for i in $(seq 1 30); do
        sleep 3
        if docker info >/dev/null 2>&1; then
            DOCKER_READY=1
            echo "      Docker is ready."
            break
        else
            echo "      Waiting for Docker... $i/30"
        fi
    done

    if [ "$DOCKER_READY" -eq 0 ]; then
        echo ""
        echo " ERROR: Docker did not start after 90 seconds."
        echo " Open Docker Desktop manually, wait for it to finish loading,"
        echo " then run this script again."
        echo ""
        exit 1
    fi
fi

# -----------------------------------------------------------
# 2. SearXNG via Docker Compose
# -----------------------------------------------------------
echo "[2/4] Starting SearXNG..."
if docker compose up -d 2>/dev/null; then
    echo "      SearXNG started."
else
    echo "      WARNING: docker compose failed. Web search may not work."
    echo "      Continuing anyway..."
fi

# -----------------------------------------------------------
# 3. Start the API
# -----------------------------------------------------------
echo "[3/4] Starting Ember API..."

if [ ! -f .venv/bin/activate ]; then
    echo ""
    echo " ERROR: Python virtual environment not found at .venv/"
    echo " Run the Ember installer or create it manually:"
    echo "   python3 -m venv .venv"
    echo "   source .venv/bin/activate"
    echo "   pip install -r requirements.txt"
    echo ""
    exit 1
fi

source .venv/bin/activate

EMBER_HOST="0.0.0.0"
if [ -f .env ]; then
    ENV_HOST=$(grep '^EMBER_HOST=' .env 2>/dev/null | cut -d= -f2)
    if [ -n "$ENV_HOST" ]; then
        EMBER_HOST="$ENV_HOST"
    fi
fi

# Start API via watchdog (manages restart/stop signals)
python scripts/watchdog.py --host "$EMBER_HOST" --port 8000 &
WATCHDOG_PID=$!

echo "      API starting via watchdog (PID: $WATCHDOG_PID)..."

# -----------------------------------------------------------
# 4. Health check polling
# -----------------------------------------------------------
echo "[4/4] Waiting for API health check..."

HEALTHY=0
for i in $(seq 1 20); do
    sleep 3
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/health 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        HEALTHY=1
        echo "      API is healthy."
        break
    else
        echo "      Polling... $i/20"
    fi
done

if [ "$HEALTHY" -eq 0 ]; then
    echo ""
    echo " ERROR: API did not respond after 60 seconds."
    echo ""
    echo " Troubleshooting:"
    echo "   1. Check terminal output above for error messages"
    echo "   2. Make sure port 8000 is not in use: lsof -i :8000"
    echo "   3. Try starting manually: source .venv/bin/activate && uvicorn src.api.main:app --host 0.0.0.0 --port 8000"
    echo "   4. Check .env exists and PRIVATE_VAULT_PATH is set"
    echo ""
    exit 1
fi

# -----------------------------------------------------------
# 5. Open browser
# -----------------------------------------------------------
echo ""
echo " Ember is ready. Opening browser..."
echo " http://localhost:8000"
echo ""

if [ "$(uname)" = "Darwin" ]; then
    open http://localhost:8000
else
    xdg-open http://localhost:8000 2>/dev/null || echo "      Open http://localhost:8000 in your browser."
fi

# Keep script alive so watchdog (and API) stay running
wait $WATCHDOG_PID
