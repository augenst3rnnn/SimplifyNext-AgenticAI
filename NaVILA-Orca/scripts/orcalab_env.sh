#!/usr/bin/env bash

# Shared OrcaLab runtime resolution. Source this file from launchers; it must
# prefer the project-owned prefix so a NavVLM environment activated in the
# current terminal cannot accidentally become the OrcaLab runtime.

NAVILA_ORCA_ENV_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAVILA_ORCA_ENV_PROJECT_ROOT="$(cd "${NAVILA_ORCA_ENV_SCRIPT_DIR}/.." && pwd)"
NAVILA_ORCA_ENV_WORKSPACE_ROOT="$(cd "${NAVILA_ORCA_ENV_PROJECT_ROOT}/.." && pwd)"

navila_orca_resolve_runtime() {
  local active_python=""
  local resolved_python="${NAVILA_ORCA_PYTHON:-}"
  local resolved_orcalab="${NAVILA_ORCA_ORCALAB_BIN:-}"
  local default_prefix="${NAVILA_ORCALAB_ENV_PREFIX:-${NAVILA_ORCA_ENV_WORKSPACE_ROOT}/.conda/envs/orcalab}"
  local default_python="${default_prefix}/bin/python"

  if [[ -z "${resolved_python}" && -x "${default_python}" ]]; then
    resolved_python="${default_python}"
  fi

  if [[ -z "${resolved_python}" && -n "${CONDA_PREFIX:-}" ]]; then
    active_python="${CONDA_PREFIX}/bin/python"
    if [[ -x "${active_python}" ]] && "${active_python}" -c \
      'from importlib.metadata import version; assert version("orca-lab") == "26.7.1"' \
      >/dev/null 2>&1; then
      resolved_python="${active_python}"
    fi
  fi

  if [[ -z "${resolved_python}" ]]; then
    echo "No OrcaLab Python selected." >&2
    echo "Expected the project environment at: ${default_python}" >&2
    echo "Run ./scripts/setup_orcalab_env.sh once, or set NAVILA_ORCA_PYTHON to a reviewed OrcaLab 26.7.1 Python." >&2
    return 2
  fi
  if [[ ! -x "${resolved_python}" ]]; then
    echo "OrcaLab Python does not exist or is not executable: ${resolved_python}" >&2
    return 2
  fi
  if ! "${resolved_python}" -c \
    'from importlib.metadata import version; assert version("orca-lab") == "26.7.1"; assert version("orca-gym") == "26.7.1"; assert version("orcalab-pyside") == "26.7.1"; assert version("patchelf") == "0.17.2.4"' \
    >/dev/null 2>&1; then
    echo "Selected Python is missing part of the reviewed OrcaLab 26.7.1 runtime: ${resolved_python}" >&2
    echo "Run ${NAVILA_ORCA_ENV_PROJECT_ROOT}/scripts/setup_orcalab_env.sh to repair it." >&2
    return 2
  fi

  if [[ -z "${resolved_orcalab}" ]]; then
    resolved_orcalab="$(dirname "${resolved_python}")/orcalab"
  fi

  export NAVILA_ORCA_PYTHON="${resolved_python}"
  export NAVILA_ORCA_ORCALAB_BIN="${resolved_orcalab}"
  export PATH="$(dirname "${resolved_python}"):${PATH}"
  export SKIP_PATCHELF=1
  # Conda/CUDA library paths from the calling shell can inject a different
  # libGLdispatch or NVML into OrcaLab's native viewport process.
  unset LD_LIBRARY_PATH
}

navila_orca_require_gui() {
  navila_orca_resolve_runtime
  if [[ ! -x "${NAVILA_ORCA_ORCALAB_BIN}" ]]; then
    echo "OrcaLab executable does not exist or is not executable: ${NAVILA_ORCA_ORCALAB_BIN}" >&2
    echo "Set NAVILA_ORCA_ORCALAB_BIN to the executable in the same OrcaLab environment." >&2
    return 2
  fi
}
