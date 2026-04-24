#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
ENV_EXAMPLE="${ROOT_DIR}/.env.example"

if [[ ! -f "${ENV_EXAMPLE}" ]]; then
  printf 'Error: missing %s\n' "${ENV_EXAMPLE}" >&2
  exit 1
fi

mkdir -p "${ROOT_DIR}/data" "${ROOT_DIR}/work" "${ROOT_DIR}/runs"

if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${ENV_EXAMPLE}" "${ENV_FILE}"
  printf 'Created %s from .env.example\n' "${ENV_FILE}"
else
  printf 'Using existing %s\n' "${ENV_FILE}"
fi

printf '\nNext step:\n'
printf '1. Edit %s\n' "${ENV_FILE}"
printf '   - OPENAI_API_KEY: your OpenAI API key\n'
printf '   - ONTOANNO_CONFIG: tells OntoAnno which dataset config file to open\n'
printf '     The config file contains your data paths and workflow settings.\n'
printf '2. Put your input data files under %s/data\n' "${ROOT_DIR}"
printf '3. Start OntoAnno with: ./start_ontoanno_docker.sh\n'

