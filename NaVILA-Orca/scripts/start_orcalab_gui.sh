#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/orcalab_env.sh"
navila_orca_require_gui

ORCALAB_BIN="${NAVILA_ORCA_ORCALAB_BIN}"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="$(cd "${PROJECT_ROOT}/.." && pwd)"
WORKSPACE="${NAVILA_ORCA_WORKSPACE:-${WORKSPACE_ROOT}/orcalab_workspace}"

if [[ ! -d "${WORKSPACE}" ]]; then
  mkdir -p "${WORKSPACE}"
fi
if [[ ! -f "${WORKSPACE}/.orcalab/config.toml" ]]; then
  "${ORCALAB_BIN}" "${WORKSPACE}" --init-config
fi

# Open the normal editor. Scene selection, layout loading, and starting an
# external simulation are separate actions. Forcing full-screen external mode
# here leaves a fresh installation waiting at its loading spinner.
exec "${ORCALAB_BIN}" "${WORKSPACE}" --verbose "$@"
