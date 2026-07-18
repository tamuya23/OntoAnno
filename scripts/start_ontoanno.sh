#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.ontoanno.env"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

CONFIG_PATH="${1:-${ONTOANNO_DEFAULT_CONFIG:-}}"
PORT="${ONTOANNO_SERVER_PORT:-8501}"

if [[ -z "${CONFIG_PATH}" ]]; then
  printf 'Error: no config file provided.\n' >&2
  printf 'Run ./scripts/setup.sh first or start with: ./scripts/start_ontoanno.sh configs/your_project.yaml\n' >&2
  exit 1
fi

if [[ ! -f "${ROOT_DIR}/${CONFIG_PATH}" && ! -f "${CONFIG_PATH}" ]]; then
  printf 'Error: config file not found: %s\n' "${CONFIG_PATH}" >&2
  exit 1
fi

if [[ -z "${ONTOANNO_PYTHON:-}" ]]; then
  printf 'Error: OntoAnno setup has not saved a Python interpreter yet.\n' >&2
  printf 'Run: bash scripts/setup.sh\n' >&2
  exit 1
fi

if [[ ! -x "${ONTOANNO_PYTHON}" ]]; then
  printf 'Error: configured Python is not executable: %s\n' "${ONTOANNO_PYTHON}" >&2
  printf 'Run bash scripts/setup.sh again, or set ONTOANNO_PYTHON to Python 3.11 or newer.\n' >&2
  exit 1
fi

cd "${ROOT_DIR}"
exec "${ONTOANNO_PYTHON}" ./ontoanno ui --config "${CONFIG_PATH}" --server-port "${PORT}"
