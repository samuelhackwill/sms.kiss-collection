#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/bot/ia-kissing-pipeline}"
SERVICE_NAME="${SERVICE_NAME:-ia-kissing-web.service}"
export PATH="/home/linuxbrew/.linuxbrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

cd "$APP_DIR"

uv sync --extra dev
uv run pytest tests/test_webapp.py

sudo systemctl restart "$SERVICE_NAME"

for attempt in 1 2 3 4 5; do
  if curl -fsS -I http://127.0.0.1:8000/films >/dev/null; then
    exit 0
  fi
  sleep "$attempt"
done

curl -fsS -I http://127.0.0.1:8000/films >/dev/null
