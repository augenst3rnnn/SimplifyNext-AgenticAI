#!/usr/bin/env bash
set -euo pipefail

# Create the inference environment used by the local NaVILA TCP service.
# This is deliberately separate from OrcaLab: NaVILA requires CPython 3.10
# and its own CUDA-enabled PyTorch stack. PyTorch 2.7 + CUDA 12.8 is the
# reviewed wheel pair used for Ada and Blackwell GPUs.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="$(cd "${PROJECT_ROOT}/.." && pwd)"
ENV_PREFIX="${NAVILA_ENV_PREFIX:-${WORKSPACE_ROOT}/.conda/envs/navila}"
NAVILA_SOURCE="${NAVILA_SOURCE:-${WORKSPACE_ROOT}/NaVILA}"
NAVILA_REVISION="${NAVILA_REVISION:-76b98f233dd0fff05dfcd69435eec6740febff9d}"
PYTHON="${ENV_PREFIX}/bin/python"
CONSTRAINTS="${PROJECT_ROOT}/constraints/navila-rss2025.txt"
TORCH_VERSION="2.7.0"
TORCHVISION_VERSION="0.22.0"
PYTORCH_INDEX="https://download.pytorch.org/whl/cu128"
FLASH_ATTN_VERSION="2.8.3"
FLASH_ATTN_WHEEL="https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3%2Bcu12torch2.7cxx11abiTRUE-cp310-cp310-linux_x86_64.whl#sha256=ce91e246f21d61ad66b1a7555340dbaa28e4aa86edcf00c18f0837422939b529"
TRANSFORMERS_REVISION="345b9b1a6a308a1fa6559251eb33ead2211240ac"

usage() {
  cat <<'EOF'
Usage: ./scripts/setup_navila_env.sh [--verify]

Creates or repairs the dedicated NaVILA inference environment. Override paths
with NAVILA_ENV_PREFIX and NAVILA_SOURCE, or select a reviewed source revision
with NAVILA_REVISION, when the workspace layout differs.
EOF
}

verify() {
  if [[ ! -x "${PYTHON}" ]]; then
    echo "NaVILA Python does not exist: ${PYTHON}" >&2
    return 2
  fi
  if [[ ! -d "${NAVILA_SOURCE}/llava" ]]; then
    echo "NaVILA source does not exist: ${NAVILA_SOURCE}" >&2
    return 2
  fi
  if [[ "$(git -C "${NAVILA_SOURCE}" rev-parse HEAD)" != "${NAVILA_REVISION}" ]]; then
    echo "NaVILA source is not the reviewed revision: ${NAVILA_REVISION}" >&2
    return 2
  fi

  "${PYTHON}" - \
    "${NAVILA_SOURCE}" \
    "${TORCH_VERSION}" \
    "${TORCHVISION_VERSION}" \
    "${FLASH_ATTN_VERSION}" <<'PY'
import sys
from importlib import metadata
from pathlib import Path

def require_equal(name, actual, expected):
    if actual != expected:
        raise SystemExit(
            f"{name} version mismatch: found {actual}, expected {expected}. "
            "Run ./NaVILA-Orca/scripts/setup_navila_env.sh to repair it."
        )

source = Path(sys.argv[1]).resolve()
expected_torch = sys.argv[2]
expected_torchvision = sys.argv[3]
expected_flash_attn = sys.argv[4]
require_equal("Python", sys.version_info[:2], (3, 10))

import flash_attn
import llava
import torch
import torchvision
import transformers
from llava.model.builder import load_pretrained_model

require_equal("torch", torch.__version__.split('+', 1)[0], expected_torch)
require_equal("torchvision", torchvision.__version__.split('+', 1)[0], expected_torchvision)
if torch.version.cuda is None:
    raise SystemExit("PyTorch is a CPU build; the CUDA 12.8 wheel is required")
if tuple(int(part) for part in torch.version.cuda.split(".")[:2]) < (12, 8):
    raise SystemExit(
        f"CUDA runtime mismatch: found {torch.version.cuda}, expected CUDA 12.8 or newer"
    )
require_equal("transformers", transformers.__version__, "4.37.2")
require_equal("flash-attn", flash_attn.__version__, expected_flash_attn)
require_equal("deepspeed", metadata.version("deepspeed"), "0.9.5")
require_equal("accelerate", metadata.version("accelerate"), "0.27.2")
require_equal("numpy", metadata.version("numpy"), "1.26.0")
require_equal("opencv-python", metadata.version("opencv-python"), "4.8.0.74")
require_equal("setuptools", metadata.version("setuptools"), "81.0.0")
if not Path(llava.__file__).resolve().is_relative_to(source):
    raise SystemExit(f"llava resolves outside the reviewed checkout: {llava.__file__}")

replacement = source / "llava/train/transformers_replace/modeling_utils.py"
installed = Path(transformers.__file__).resolve().parent / "modeling_utils.py"
if replacement.read_bytes() != installed.read_bytes():
    raise SystemExit(
        "NaVILA Transformers patch is missing. "
        "Run ./NaVILA-Orca/scripts/setup_navila_env.sh to repair it."
    )
print("NaVILA runtime verified")
print(f"python={sys.executable}")
print(f"torch={torch.__version__}; torch CUDA={torch.version.cuda}; torchvision={torchvision.__version__}; transformers={transformers.__version__}")
print(f"flash-attn={flash_attn.__version__}; deepspeed={metadata.version('deepspeed')}; llava={Path(llava.__file__).resolve()}")
PY
}

case "${1:-}" in
  "") ;;
  --verify) verify; exit $? ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if ! command -v conda >/dev/null 2>&1; then
  echo "conda is required to create ${ENV_PREFIX}." >&2
  exit 2
fi

if [[ ! -d "${NAVILA_SOURCE}/.git" ]]; then
  if [[ -e "${NAVILA_SOURCE}" ]]; then
    echo "NaVILA source is not a Git checkout: ${NAVILA_SOURCE}" >&2
    exit 2
  fi
  git clone https://github.com/AnjieCheng/NaVILA.git "${NAVILA_SOURCE}"
fi

CURRENT_REVISION="$(git -C "${NAVILA_SOURCE}" rev-parse HEAD)"
if [[ "${CURRENT_REVISION}" != "${NAVILA_REVISION}" ]]; then
  if [[ -n "$(git -C "${NAVILA_SOURCE}" status --porcelain)" ]]; then
    echo "NaVILA source has local changes and is not the reviewed revision ${NAVILA_REVISION}." >&2
    echo "Use a clean checkout, or explicitly set NAVILA_REVISION to the source revision you reviewed." >&2
    exit 2
  fi
  git -C "${NAVILA_SOURCE}" checkout --detach "${NAVILA_REVISION}"
fi

if [[ ! -x "${PYTHON}" ]]; then
  conda create --yes --prefix "${ENV_PREFIX}" python=3.10 pip
fi

"${PYTHON}" -m pip install --upgrade \
  "pip==26.1.2" "setuptools==81.0.0" "wheel==0.47.0"
# An older editable NaVILA install declares exact torch 2.3 metadata. Remove
# that metadata before upgrading the runtime; the reviewed checkout is
# reinstalled with --no-deps below.
"${PYTHON}" -m pip uninstall --yes vila >/dev/null 2>&1 || true
"${PYTHON}" -m pip install --index-url "${PYTORCH_INDEX}" \
  "torch==${TORCH_VERSION}" "torchvision==${TORCHVISION_VERSION}"
"${PYTHON}" -m pip install --force-reinstall --no-deps "${FLASH_ATTN_WHEEL}"
"${PYTHON}" -m pip install \
  --requirement "${CONSTRAINTS}"
"${PYTHON}" -m pip install --no-deps --editable "${NAVILA_SOURCE}"
"${PYTHON}" -m pip install --force-reinstall --no-deps \
  "git+https://github.com/huggingface/transformers@${TRANSFORMERS_REVISION}"

SITE_PACKAGES="$("${PYTHON}" -c 'import site; print(site.getsitepackages()[0])')"
cp -a "${NAVILA_SOURCE}/llava/train/transformers_replace/." "${SITE_PACKAGES}/transformers/"

verify
