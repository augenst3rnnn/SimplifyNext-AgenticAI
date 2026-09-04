#!/usr/bin/env bash
set -u

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="$(cd "${PROJECT_ROOT}/.." && pwd)"
ORCALAB_PREFIX="${NAVILA_ORCALAB_ENV_PREFIX:-${WORKSPACE_ROOT}/.conda/envs/orcalab}"
NAVILA_PREFIX="${NAVILA_ENV_PREFIX:-${WORKSPACE_ROOT}/.conda/envs/navila}"
MODEL_DIR="${NAVVLM_MODEL_PATH:-${WORKSPACE_ROOT}/models/navila-llama3-8b-8f}"
SKIP_MODEL=0
REQUIRE_SERVICES=0
FAILURES=0

usage() {
  cat <<'EOF'
Usage: ./scripts/doctor.sh [--skip-model] [--require-services]

Checks the host, both project-local environments, bundled assets, downloaded
model, GPU visibility, and optionally the two running local services.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-model) SKIP_MODEL=1 ;;
    --require-services) REQUIRE_SERVICES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

pass() {
  printf '[PASS] %s\n' "$1"
}

fail() {
  printf '[FAIL] %s\n' "$1" >&2
  FAILURES=$((FAILURES + 1))
}

print_verification_details() {
  local check_name="$1"
  local diagnostic="$2"

  printf 'Verification details (%s):\n' "${check_name}" >&2
  if [[ -n "${diagnostic}" ]]; then
    printf '%s\n' "${diagnostic}" >&2
  else
    printf '%s\n' '(verification command failed without output)' >&2
  fi
}

check_command() {
  if command -v "$1" >/dev/null 2>&1; then
    pass "$1: $(command -v "$1")"
  else
    fail "$1 is not available"
  fi
}

check_file() {
  if [[ -f "$1" ]]; then
    pass "$2: $1"
  else
    fail "$2 is missing: $1"
  fi
}

check_command git
check_command conda
check_command nvidia-smi

NVIDIA_DIAGNOSTIC=""
if NVIDIA_DIAGNOSTIC="$("${PROJECT_ROOT}/scripts/check_nvidia_driver.sh" 2>&1)"; then
  pass "NVIDIA driver is visible"
  if [[ -n "${NVIDIA_DIAGNOSTIC}" ]]; then
    printf '%s\n' "${NVIDIA_DIAGNOSTIC}" >&2
  fi
else
  fail "NVIDIA driver is not usable"
  printf '%s\n' "${NVIDIA_DIAGNOSTIC}" >&2
fi

check_file "${PROJECT_ROOT}/factory.json" "VLN_Presentation factory layout"
check_file "${PROJECT_ROOT}/src/navila_orca/assets/checkpoints/go2_flat.pt" "Go2 checkpoint"
check_file "${PROJECT_ROOT}/scripts/navila_vlm_server.py" "project-owned NaVILA server"

SYSTEM_DEPS_DIAGNOSTIC=""
if SYSTEM_DEPS_DIAGNOSTIC="$(
  "${PROJECT_ROOT}/scripts/setup_system_deps.sh" --verify 2>&1
)"; then
  pass "OrcaLab GUI system libraries"
else
  fail "OrcaLab GUI system libraries are missing; run scripts/setup_system_deps.sh"
  print_verification_details "OrcaLab GUI system libraries" "${SYSTEM_DEPS_DIAGNOSTIC}"
fi

ORCALAB_ENV_DIAGNOSTIC=""
if ORCALAB_ENV_DIAGNOSTIC="$(
  NAVILA_ORCALAB_ENV_PREFIX="${ORCALAB_PREFIX}" \
    "${PROJECT_ROOT}/scripts/setup_orcalab_env.sh" --verify 2>&1
)"; then
  pass "OrcaLab environment: ${ORCALAB_PREFIX}"
else
  fail "OrcaLab environment is missing or inconsistent; run scripts/setup_orcalab_env.sh"
  print_verification_details "OrcaLab environment" "${ORCALAB_ENV_DIAGNOSTIC}"
fi

NAVILA_ENV_DIAGNOSTIC=""
if NAVILA_ENV_DIAGNOSTIC="$(
  NAVILA_ENV_PREFIX="${NAVILA_PREFIX}" \
    "${PROJECT_ROOT}/scripts/setup_navila_env.sh" --verify 2>&1
)"; then
  pass "NaVILA environment: ${NAVILA_PREFIX}"
else
  fail "NaVILA environment is missing or inconsistent; run scripts/setup_navila_env.sh"
  print_verification_details "NaVILA environment" "${NAVILA_ENV_DIAGNOSTIC}"
fi

if [[ -x "${ORCALAB_PREFIX}/bin/python" ]] && \
  "${ORCALAB_PREFIX}/bin/python" -c 'import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)' \
  >/dev/null 2>&1; then
  pass "OrcaLab PyTorch can access CUDA"
else
  fail "OrcaLab PyTorch cannot access CUDA"
fi

NAVILA_CUDA_DIAGNOSTIC=""
if [[ -x "${NAVILA_PREFIX}/bin/python" ]] && \
  NAVILA_CUDA_DIAGNOSTIC="$("${NAVILA_PREFIX}/bin/python" \
    "${PROJECT_ROOT}/scripts/check_navila_cuda.py" 2>&1)"; then
  pass "NaVILA PyTorch can execute on this GPU"
  printf '%s\n' "${NAVILA_CUDA_DIAGNOSTIC}"
else
  fail "NaVILA PyTorch cannot execute on this GPU"
  if [[ -n "${NAVILA_CUDA_DIAGNOSTIC}" ]]; then
    printf '%s\n' "${NAVILA_CUDA_DIAGNOSTIC}" >&2
  fi
fi

if [[ "${SKIP_MODEL}" -eq 0 ]]; then
  check_file "${MODEL_DIR}/config.json" "NaVILA model config"
  if find "${MODEL_DIR}" -maxdepth 2 -type f \
    \( -name '*.safetensors' -o -name '*.safetensors.index.json' \) \
    -print -quit 2>/dev/null | grep -q .; then
    pass "NaVILA model weights: ${MODEL_DIR}"
  else
    fail "NaVILA safetensors weights are missing: ${MODEL_DIR}"
  fi
fi

if [[ "${REQUIRE_SERVICES}" -eq 1 ]]; then
  if timeout 1 bash -c '</dev/tcp/127.0.0.1/50051' 2>/dev/null; then
    pass "OrcaGym gRPC is listening on 127.0.0.1:50051"
  else
    fail "OrcaGym gRPC is not listening on 127.0.0.1:50051"
  fi
  if timeout 1 bash -c '</dev/tcp/127.0.0.1/54321' 2>/dev/null; then
    pass "NaVILA TCP service is listening on 127.0.0.1:54321"
  else
    fail "NaVILA TCP service is not listening on 127.0.0.1:54321"
  fi
fi

if [[ "${FAILURES}" -ne 0 ]]; then
  printf '\nDoctor found %d problem(s).\n' "${FAILURES}" >&2
  exit 2
fi

printf '\nOrca_VLN installation is ready.\n'
