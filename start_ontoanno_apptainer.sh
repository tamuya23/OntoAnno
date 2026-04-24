#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
DEFAULT_SIF="${ROOT_DIR}/.apptainer/ontoanno_latest.sif"

load_apptainer_if_needed() {
  if command -v apptainer >/dev/null 2>&1; then
    return 0
  fi

  if [[ -f /usr/share/lmod/lmod/init/bash ]]; then
    # shellcheck disable=SC1091
    source /usr/share/lmod/lmod/init/bash
  elif [[ -f /etc/profile.d/modules.sh ]]; then
    # shellcheck disable=SC1091
    source /etc/profile.d/modules.sh
  fi

  if command -v module >/dev/null 2>&1; then
    module load apptainer >/dev/null 2>&1 || true
  fi

  command -v apptainer >/dev/null 2>&1
}

if [[ ! -f "${ENV_FILE}" ]]; then
  printf 'Error: %s not found.\n' "${ENV_FILE}" >&2
  printf 'Run: bash apptainer_setup.sh\n' >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"

if ! load_apptainer_if_needed; then
  printf 'Error: apptainer is not available in this shell.\n' >&2
  printf 'If your system uses environment modules, try: module load apptainer\n' >&2
  exit 1
fi

if [[ -z "${ONTOANNO_IMAGE:-}" && -z "${ONTOANNO_APPTAINER_SIF:-}" ]]; then
  printf 'Error: set ONTOANNO_IMAGE or ONTOANNO_APPTAINER_SIF in %s\n' "${ENV_FILE}" >&2
  exit 1
fi

mkdir -p "${ROOT_DIR}/data" "${ROOT_DIR}/work" "${ROOT_DIR}/runs" "${ROOT_DIR}/.apptainer"

SIF_PATH="${ONTOANNO_APPTAINER_SIF:-${DEFAULT_SIF}}"

if [[ -n "${ONTOANNO_IMAGE:-}" && ! -f "${SIF_PATH}" ]]; then
  printf 'Pulling container image to %s\n' "${SIF_PATH}"
  apptainer pull "${SIF_PATH}" "${ONTOANNO_IMAGE}"
fi

if [[ ! -f "${SIF_PATH}" ]]; then
  printf 'Error: container image not found: %s\n' "${SIF_PATH}" >&2
  exit 1
fi

export APPTAINERENV_OPENAI_API_KEY="${OPENAI_API_KEY:-}"
export APPTAINERENV_OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
export APPTAINERENV_ONTOANNO_CL_OBO="${ONTOANNO_CL_OBO:-}"
export APPTAINERENV_ONTOANNO_CONFIG="${ONTOANNO_CONFIG:-/app/configs/docker_import_template.yaml}"
export APPTAINERENV_ONTOANNO_PORT="${ONTOANNO_PORT:-8501}"
export APPTAINERENV_STREAMLIT_BROWSER_GATHER_USAGE_STATS="false"

cd "${ROOT_DIR}"
exec apptainer run --cleanenv \
  --bind "${ROOT_DIR}/data:/data" \
  --bind "${ROOT_DIR}/work:/work" \
  --bind "${ROOT_DIR}/runs:/app/runs" \
  --bind "${ROOT_DIR}/configs:/app/configs" \
  "${SIF_PATH}" "$@"
