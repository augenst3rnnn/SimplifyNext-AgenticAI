#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_DIR="${PROJECT_ROOT}/scripts"
source "${SCRIPT_DIR}/orcalab_env.sh"
navila_orca_resolve_runtime
ORCA_PYTHON="${NAVILA_ORCA_PYTHON}"
export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

EXPECTED_ORCA_VERSION="26.7.1"
for ORCA_DISTRIBUTION in orca-lab orca-gym; do
  if ! INSTALLED_ORCA_VERSION="$("${ORCA_PYTHON}" -c "from importlib.metadata import version; print(version('${ORCA_DISTRIBUTION}'))" 2>/dev/null)"; then
    echo "未在 ${ORCA_PYTHON} 中找到 ${ORCA_DISTRIBUTION}；需要 ${EXPECTED_ORCA_VERSION}。" >&2
    exit 2
  fi
  if [[ "${INSTALLED_ORCA_VERSION}" != "${EXPECTED_ORCA_VERSION}" ]]; then
    echo "不支持的 ${ORCA_DISTRIBUTION} ${INSTALLED_ORCA_VERSION}；需要 ${EXPECTED_ORCA_VERSION}。" >&2
    exit 2
  fi
done

exec "${ORCA_PYTHON}" -m navila_orca.apply_scene_profile "$@"
