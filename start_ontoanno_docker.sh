#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

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
      printf 'Error: Docker can only see config files under configs/, data/, or work/.\n' >&2
      printf 'Move the file under configs/ and run: ./start_ontoanno_docker.sh configs/%s\n' "$(basename "${host_path}")" >&2
      return 1
      ;;
  esac
}

find_docker() {
  local candidate
  for candidate in \
    "$(command -v docker 2>/dev/null || true)" \
    /usr/local/bin/docker \
    /opt/homebrew/bin/docker \
    /Applications/Docker.app/Contents/Resources/bin/docker
  do
    if [[ -f "${candidate}" && -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

if [[ ! -f "${ENV_FILE}" ]]; then
  printf 'Error: %s not found.\n' "${ENV_FILE}" >&2
  printf 'Run: bash docker_setup.sh\n' >&2
  exit 1
fi

if ! DOCKER_BIN="$(find_docker)"; then
  printf 'Error: Docker was not found as an executable command.\n' >&2
  printf 'Install Docker Desktop, start it, and make sure the docker command is available in your terminal.\n' >&2
  exit 1
fi

if [[ $# -gt 0 ]]; then
  case "$1" in
    *.yaml|*.yml|/app/configs/*.yaml|/app/configs/*.yml|/data/*.yaml|/data/*.yml|/work/*.yaml|/work/*.yml)
      ONTOANNO_CONFIG="$(container_config_path "$1")"
      export ONTOANNO_CONFIG
      shift
      ;;
  esac
fi

cd "${ROOT_DIR}"
exec "${DOCKER_BIN}" compose up "$@"
