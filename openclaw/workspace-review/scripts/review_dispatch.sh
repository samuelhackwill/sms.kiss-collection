#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: review_dispatch.sh '<message>'" >&2
  exit 2
fi

MESSAGE="$1"
PROJECT_ROOT="/home/bot/ia-kissing-pipeline"

cd "$PROJECT_ROOT"
export DB_PATH="${DB_PATH:-/home/bot/ia-kissing-pipeline/data/pipeline.db}"
uv run python -m ia_kissing_pipeline.main init-db >/dev/null
exec uv run python -m ia_kissing_pipeline.main review-dispatch "$MESSAGE" --channel telegram --target 1155836070
