#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

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

cd "${ROOT_DIR}"
exec "${DOCKER_BIN}" compose up "$@"
