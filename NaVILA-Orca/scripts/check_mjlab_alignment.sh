#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
source "${SCRIPT_DIR}/orcalab_env.sh"
navila_orca_resolve_runtime
DRIVER_PYTHON="${NAVILA_ORCA_DRIVER_PYTHON:-${NAVILA_ORCA_PYTHON}}"

export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
cd -- "${PROJECT_ROOT}"
exec "${DRIVER_PYTHON}" -m navila_orca.training --python "${DRIVER_PYTHON}" "$@"
