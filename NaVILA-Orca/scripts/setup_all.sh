#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKIP_MODEL=0
VERIFY_ONLY=0

usage() {
  cat <<'EOF'
Usage: ./scripts/setup_all.sh [--skip-model] [--verify]

Creates the project-local OrcaLab and NaVILA Conda environments. By default it
also downloads and verifies the NaVILA checkpoint. No shell activation or
ORCA_VLN_ROOT variable is required.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-model) SKIP_MODEL=1 ;;
    --verify) VERIFY_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "${VERIFY_ONLY}" -eq 1 ]]; then
  exec "${PROJECT_ROOT}/scripts/doctor.sh"
fi

for command in git conda; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "Missing prerequisite: ${command}" >&2
    exit 2
  fi
done
"${PROJECT_ROOT}/scripts/check_nvidia_driver.sh"
"${PROJECT_ROOT}/scripts/setup_system_deps.sh"

"${PROJECT_ROOT}/scripts/setup_orcalab_env.sh"
"${PROJECT_ROOT}/scripts/setup_navila_env.sh"

if [[ "${SKIP_MODEL}" -eq 0 ]]; then
  "${PROJECT_ROOT}/scripts/download_navila_model.sh"
  "${PROJECT_ROOT}/scripts/doctor.sh"
else
  "${PROJECT_ROOT}/scripts/doctor.sh" --skip-model
  echo "Model download skipped. Complete it later with:"
  echo "  ${PROJECT_ROOT}/scripts/download_navila_model.sh"
fi
