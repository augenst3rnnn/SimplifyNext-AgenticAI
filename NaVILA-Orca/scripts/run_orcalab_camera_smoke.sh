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
    echo "不支持的 ${ORCA_DISTRIBUTION} ${INSTALLED_ORCA_VERSION}；常驻 RGB 相机要求 ${EXPECTED_ORCA_VERSION}。" >&2
    echo "执行: ${ORCA_PYTHON} -m pip install --upgrade --force-reinstall 'orca-lab==26.7.1' 'orca-gym==26.7.1'" >&2
    exit 2
  fi
done

# Reuse the one OrcaLab GUI instance that is already running. This script does
# not start another GUI and never republishes/clears the authored layout.
exec "${ORCA_PYTHON}" -m navila_orca.cli run \
  --render-backend orcalab \
  --orcagym-address 127.0.0.1:50051 \
  --orcalab-edit-address 127.0.0.1:50151 \
  --camera-actor-name mujococamera1080 \
  --camera-asset-path prefabs/mujococamera1080 \
  --orcalab-camera-mode mujoco-png \
  --camera-transport grpc-png \
  --no-publish \
  --robot-actor-name auto \
  --anchor-existing-scene \
  --scene-profile orca-train \
  --strict-scene-alignment \
  --manual-xml-override \
  --vlm-backend scripted \
  --scripted-action stop \
  --max-decisions 1 \
  --max-control-steps 2 \
  --output "${PROJECT_ROOT}/outputs/camera_bind_smoke" \
  "$@"
