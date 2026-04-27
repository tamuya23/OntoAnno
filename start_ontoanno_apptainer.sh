#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
DEFAULT_SIF="${ROOT_DIR}/.apptainer/ontoanno_latest.sif"

container_config_path() {
  local config_arg="$1"
  local host_path
  local rel_path

  case "${config_arg}" in
    /app/configs/*|/data/*|/work/*)
      printf '%s\n' "${config_arg}"
      return 0
      ;;
  esac

  if [[ "${config_arg}" = /* ]]; then
    host_path="${config_arg}"
  else
    host_path="${ROOT_DIR}/${config_arg}"
  fi

  if [[ ! -f "${host_path}" ]]; then
    printf 'Error: config file not found: %s\n' "${config_arg}" >&2
    return 1
  fi

  case "${host_path}" in
    "${ROOT_DIR}/configs/"*)
      rel_path="${host_path#"${ROOT_DIR}/configs/"}"
      printf '/app/configs/%s\n' "${rel_path}"
      ;;
    "${ROOT_DIR}/data/"*)
      rel_path="${host_path#"${ROOT_DIR}/data/"}"
      printf '/data/%s\n' "${rel_path}"
      ;;
    "${ROOT_DIR}/work/"*)
      rel_path="${host_path#"${ROOT_DIR}/work/"}"
      printf '/work/%s\n' "${rel_path}"
      ;;
    *)
      printf 'Error: Apptainer can only see config files under configs/, data/, or work/.\n' >&2
      printf 'Move the file under configs/ and run: ./start_ontoanno_apptainer.sh configs/%s\n' "$(basename "${host_path}")" >&2
      return 1
      ;;
  esac
}

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

if [[ $# -gt 0 ]]; then
  case "$1" in
    *.yaml|*.yml|/app/configs/*.yaml|/app/configs/*.yml|/data/*.yaml|/data/*.yml|/work/*.yaml|/work/*.yml)
      ONTOANNO_CONFIG="$(container_config_path "$1")"
      export ONTOANNO_CONFIG
      shift
      ;;
  esac
fi

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
