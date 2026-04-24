#!/usr/bin/env bash
set -euo pipefail

cd /app

if [[ $# -eq 0 ]]; then
  exec /app/ontoanno ui \
    --config "${ONTOANNO_CONFIG:-/app/configs/docker_import_template.yaml}" \
    --server-address 0.0.0.0 \
    --server-port "${ONTOANNO_PORT:-8501}"
fi

if [[ "$1" == "ui" ]]; then
  shift
  exec /app/ontoanno ui \
    --config "${ONTOANNO_CONFIG:-/app/configs/docker_import_template.yaml}" \
    --server-address 0.0.0.0 \
    --server-port "${ONTOANNO_PORT:-8501}" \
    "$@"
fi

exec /app/ontoanno "$@"
