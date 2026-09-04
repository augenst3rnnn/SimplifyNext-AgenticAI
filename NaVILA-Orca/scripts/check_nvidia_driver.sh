#!/usr/bin/env bash
set -u

# Diagnose the host driver before creating GPU environments. This script never
# installs or changes a driver.

NVIDIA_SMI_BIN="${NVIDIA_SMI_BIN:-nvidia-smi}"

if ! command -v "${NVIDIA_SMI_BIN}" >/dev/null 2>&1; then
  echo "NVIDIA preflight failed: nvidia-smi is not installed." >&2
  echo "Install the host NVIDIA driver, reboot, then rerun setup_all.sh." >&2
  exit 2
fi

if NVIDIA_OUTPUT="$("${NVIDIA_SMI_BIN}" -L 2>&1)"; then
  exit 0
fi

# CUDA/Conda paths can put a stub or foreign NVML ahead of the system library.
if [[ -n "${LD_LIBRARY_PATH:-}" ]] && \
  CLEAN_OUTPUT="$(env -u LD_LIBRARY_PATH "${NVIDIA_SMI_BIN}" -L 2>&1)"; then
  echo "NVIDIA preflight passed only after removing LD_LIBRARY_PATH." >&2
  echo "Run: unset LD_LIBRARY_PATH" >&2
  echo "Then rerun: ./NaVILA-Orca/scripts/setup_all.sh" >&2
  exit 2
fi

echo "NVIDIA preflight failed:" >&2
printf '%s\n' "${NVIDIA_OUTPUT}" >&2

if [[ "${NVIDIA_OUTPUT}" == *"Driver/library version mismatch"* ]]; then
  KERNEL_VERSION="unknown"
  if [[ -r /proc/driver/nvidia/version ]]; then
    KERNEL_VERSION="$(
      sed -n 's/.*Kernel Module  \([^ ]*\).*/\1/p' /proc/driver/nvidia/version |
        head -1
    )"
    KERNEL_VERSION="${KERNEL_VERSION:-unknown}"
  fi
  echo >&2
  echo "The host NVML library and loaded NVIDIA kernel module differ." >&2
  echo "Loaded kernel module: ${KERNEL_VERSION}" >&2
  echo "This commonly happens after the OS updates the NVIDIA driver." >&2
  echo "Do not delete or reinstall the project Conda environments." >&2
  echo "Reboot the computer once, then run:" >&2
  echo "  nvidia-smi" >&2
  echo "  ./NaVILA-Orca/scripts/setup_all.sh" >&2
else
  echo >&2
  echo "The host NVIDIA driver is not usable. Fix nvidia-smi first; the project" >&2
  echo "installer cannot replace or reload a host kernel driver." >&2
fi

exit 2
