#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ROOT_DIR}/.ontoanno.env"

print_line() {
  printf '%s\n' "$1"
}

find_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi
  return 1
}

find_rscript() {
  if [[ -n "${ONTOANNO_RSCRIPT:-}" && -x "${ONTOANNO_RSCRIPT}" ]]; then
    printf '%s\n' "${ONTOANNO_RSCRIPT}"
    return 0
  fi
  if command -v Rscript >/dev/null 2>&1; then
    command -v Rscript
    return 0
  fi
  local candidate
  for candidate in \
    /nas/longleaf/rhel9/apps/r/4.4.0/bin/Rscript \
    /usr/bin/Rscript
  do
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

prompt_with_default() {
  local prompt="$1"
  local default_value="$2"
  local reply
  if [[ -n "${default_value}" ]]; then
    read -r -p "${prompt} [${default_value}]: " reply || true
    if [[ -z "${reply}" ]]; then
      reply="${default_value}"
    fi
  else
    read -r -p "${prompt}: " reply || true
  fi
  printf '%s\n' "${reply}"
}

print_line ""
print_line "OntoAnno setup"
print_line "Repository: ${ROOT_DIR}"
print_line ""

if ! PYTHON_BIN="$(find_python)"; then
  print_line "Error: Python was not found. Please install Python 3.11 or newer first."
  exit 1
fi

PY_VERSION="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if ! "${PYTHON_BIN}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  print_line "Error: OntoAnno requires Python 3.11 or newer. Found ${PY_VERSION}."
  exit 1
fi

print_line "Using Python: ${PYTHON_BIN} (${PY_VERSION})"
print_line "Installing OntoAnno and the web UI..."
(
  cd "${ROOT_DIR}"
  "${PYTHON_BIN}" -m pip install -e ".[ui]"
)

DETECTED_RSCRIPT="$(find_rscript || true)"
RSCRIPT_PATH="$(prompt_with_default "Path to Rscript" "${DETECTED_RSCRIPT}")"
if [[ -z "${RSCRIPT_PATH}" || ! -x "${RSCRIPT_PATH}" ]]; then
  print_line "Error: Rscript not found or not executable: ${RSCRIPT_PATH}"
  exit 1
fi

EXISTING_KEY="${OPENAI_API_KEY:-}"
if [[ -z "${EXISTING_KEY}" && -f "${ENV_FILE}" ]]; then
  EXISTING_KEY="$(sed -n 's/^export OPENAI_API_KEY=//p' "${ENV_FILE}" | tail -n 1 | sed 's/^"//; s/"$//')"
fi
print_line ""
print_line "OpenAI API key"
print_line "Press Enter to keep the current value, or paste a new key."
read -r -s -p "OPENAI_API_KEY: " INPUT_KEY || true
printf '\n'
if [[ -n "${INPUT_KEY}" ]]; then
  OPENAI_KEY="${INPUT_KEY}"
else
  OPENAI_KEY="${EXISTING_KEY}"
fi

DEFAULT_CONFIG="$(prompt_with_default "Default config file for the launcher" "configs/pdac_sn.yaml")"
DEFAULT_PORT="$(prompt_with_default "Default web port" "8501")"

cat > "${ENV_FILE}" <<EOF
export ONTOANNO_RSCRIPT="${RSCRIPT_PATH}"
export OPENAI_API_KEY="${OPENAI_KEY}"
export ONTOANNO_DEFAULT_CONFIG="${DEFAULT_CONFIG}"
export ONTOANNO_SERVER_PORT="${DEFAULT_PORT}"
EOF

chmod 600 "${ENV_FILE}"

print_line ""
print_line "Setup complete."
print_line "Saved local settings in ${ENV_FILE}"
print_line ""
print_line "Start OntoAnno with:"
print_line "  ./start_ontoanno.sh"
print_line ""
print_line "Or use another config file:"
print_line "  ./start_ontoanno.sh configs/your_project.yaml"
