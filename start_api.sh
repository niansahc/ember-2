#!/bin/bash
# Start Ember-2 API server (Mac/Linux)
cd "$(dirname "$0")"
source .venv/bin/activate
export EMBER_HOST="${EMBER_HOST:-127.0.0.1}"
# Read EMBER_HOST from .env if not already set
if [ -f .env ]; then
  ENV_HOST=$(grep '^EMBER_HOST=' .env 2>/dev/null | cut -d= -f2)
  if [ -n "$ENV_HOST" ]; then
    export EMBER_HOST="$ENV_HOST"
  fi
fi
uvicorn src.api.main:app --host "$EMBER_HOST" --port 8000
