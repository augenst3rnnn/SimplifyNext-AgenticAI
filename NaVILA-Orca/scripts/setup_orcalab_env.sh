#!/usr/bin/env bash
set -euo pipefail

# Create the OrcaLab runtime used by this project. It is intentionally a
# prefix environment so it can coexist with the dedicated NaVILA environment.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="$(cd "${PROJECT_ROOT}/.." && pwd)"
ENV_PREFIX="${NAVILA_ORCALAB_ENV_PREFIX:-${WORKSPACE_ROOT}/.conda/envs/orcalab}"
PYTHON="${ENV_PREFIX}/bin/python"
CONSTRAINTS="${PROJECT_ROOT}/constraints/orcalab-26.7.1.txt"
TORCH_VERSION="2.11.0"
TORCHVISION_VERSION="0.26.0"
PYTORCH_INDEX="https://download.pytorch.org/whl/cu128"
export PATH="${ENV_PREFIX}/bin:${PATH}"
export SKIP_PATCHELF=1

usage() {
  cat <<'EOF'
Usage: ./scripts/setup_orcalab_env.sh [--verify]

Creates the tested OrcaLab 26.7.1 / MJLab 1.2.0 / PyTorch CUDA 12.8
environment. Override the environment location with
NAVILA_ORCALAB_ENV_PREFIX if required.
EOF
}

verify() {
  if [[ ! -x "${PYTHON}" ]]; then
    echo "OrcaLab Python does not exist: ${PYTHON}" >&2
    return 2
  fi

  env -u LD_LIBRARY_PATH "${PYTHON}" - "${PROJECT_ROOT}" <<'PY'
import sys
import subprocess
from importlib import metadata
from pathlib import Path

def require_equal(name, actual, expected):
    if actual != expected:
        raise SystemExit(
            f"{name} version mismatch: found {actual}, expected {expected}. "
            "Run ./NaVILA-Orca/scripts/setup_orcalab_env.sh to repair it."
        )

project = Path(sys.argv[1]).resolve()
require_equal("Python", sys.version_info[:2], (3, 12))

expected = {
    "setuptools": "81.0.0",
    "orca-lab": "26.7.1",
    "orca-gym": "26.7.1",
    "orcalab-pyside": "26.7.1",
    "patchelf": "0.17.2.4",
    "mjlab": "1.2.0",
    "mujoco-warp": "3.5.0",
    "rsl-rl-lib": "5.0.1",
    "torch": "2.11.0",
    "torchvision": "0.26.0",
}
installed = {name: metadata.version(name) for name in expected}
for name, version in expected.items():
    require_equal(name, installed[name].split("+", 1)[0], version)

import torch
import torchvision
require_equal("torch CUDA build", torch.version.cuda, "12.8")
if not torch.__version__.endswith("+cu128"):
    raise SystemExit(
        f"PyTorch build mismatch: found {torch.__version__}, expected the cu128 wheel. "
        "Run ./NaVILA-Orca/scripts/setup_orcalab_env.sh to repair it."
    )
if not torchvision.__version__.endswith("+cu128"):
    raise SystemExit(
        f"torchvision build mismatch: found {torchvision.__version__}, expected the cu128 wheel. "
        "Run ./NaVILA-Orca/scripts/setup_orcalab_env.sh to repair it."
    )

import navila_orca
if not Path(navila_orca.__file__).resolve().is_relative_to(project / "src"):
    raise SystemExit(
        f"navila_orca resolves outside this checkout: {navila_orca.__file__}. "
        "Run ./NaVILA-Orca/scripts/setup_orcalab_env.sh to repair it."
    )

import shutil
if shutil.which("patchelf") is None:
    raise SystemExit(
        "patchelf is installed in the OrcaLab environment but is not on PATH. "
        "Run ./NaVILA-Orca/scripts/setup_orcalab_env.sh to repair it."
    )
from importlib.util import find_spec
import PySide6
import shiboken6
orcalab_pyside_spec = find_spec("orcalab_pyside")
if orcalab_pyside_spec is None or orcalab_pyside_spec.origin is None:
    raise SystemExit("orcalab-pyside package is missing")
native_library = (
    Path(orcalab_pyside_spec.origin).resolve().parent / "dist" / "OrcaPySide.so"
)
glvnd_sonames = ("libGL.so.1", "libOpenGL.so.0")
opengl_consumers = [
    native_library,
    native_library.parent / "libPySideGameLauncher.so",
]
for consumer in opengl_consumers:
    if not consumer.is_file():
        raise SystemExit(f"OrcaLab OpenGL consumer is missing: {consumer}")
    needed = subprocess.check_output(
        [shutil.which("patchelf"), "--print-needed", str(consumer)], text=True
    ).splitlines()
    glvnd = [item for item in needed if Path(item).name in glvnd_sonames]
    if not glvnd:
        raise SystemExit(
            f"{consumer.name} declares no supported OpenGL dependency. "
            "Run ./NaVILA-Orca/scripts/setup_orcalab_env.sh to repair it."
        )
    unbound = [
        item
        for item in glvnd
        if not Path(item).is_absolute() or not Path(item).is_file()
    ]
    if unbound:
        raise SystemExit(
            f"{consumer.name} is not bound to host GLVND: {', '.join(unbound)}. "
            "Run ./NaVILA-Orca/scripts/setup_orcalab_env.sh to repair it."
        )
native_rpath = subprocess.check_output(
    [shutil.which("patchelf"), "--print-rpath", str(native_library)], text=True
).strip()
expected_pyside = Path(PySide6.__file__).resolve().parent
expected_rpath_entries = {
    str(expected_pyside),
    str(expected_pyside / "Qt" / "lib"),
    str(Path(shiboken6.__file__).resolve().parent),
}
missing_rpath_entries = sorted(
    expected_rpath_entries.difference(native_rpath.split(":"))
)
if missing_rpath_entries:
    raise SystemExit(
        "OrcaLab native viewport RPATH is incomplete or points at a different "
        f"Python environment; missing {', '.join(missing_rpath_entries)}. "
        "Run ./NaVILA-Orca/scripts/setup_orcalab_env.sh to repair it."
    )

import orcalab_pyside

qt_root = expected_pyside
xcb_plugin = next(qt_root.rglob("libqxcb.so"), None)
if xcb_plugin is None:
    raise SystemExit("PySide6 xcb platform plugin is missing")
linked = subprocess.run(
    ["ldd", str(xcb_plugin)], check=True, capture_output=True, text=True
).stdout
missing = [line.strip() for line in linked.splitlines() if "not found" in line]
if missing:
    raise SystemExit(
        "OrcaLab GUI system libraries are missing:\n"
        + "\n".join(missing)
        + "\nRun ./NaVILA-Orca/scripts/setup_system_deps.sh to repair them."
    )
print("OrcaLab runtime verified")
print(f"python={sys.executable}")
print("; ".join(f"{name}={installed[name]}" for name in expected))
PY
  "${PYTHON}" -m pip check
}

case "${1:-}" in
  "") ;;
  --verify) verify; exit $? ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if ! command -v conda >/dev/null 2>&1; then
  echo "Conda is required. Install Miniconda or Anaconda, reopen the terminal, then rerun this command." >&2
  exit 2
fi
if [[ ! -x "${PYTHON}" ]]; then
  conda create --yes --prefix "${ENV_PREFIX}" python=3.12 pip
fi

"${PYTHON}" -m pip install --upgrade \
  "pip==26.0.1" "setuptools==81.0.0" "wheel==0.46.3"
"${PYTHON}" -m pip install --index-url "${PYTORCH_INDEX}" \
  "torch==${TORCH_VERSION}+cu128" \
  "torchvision==${TORCHVISION_VERSION}+cu128"
"${PYTHON}" -m pip install \
  --constraint "${CONSTRAINTS}" \
  --editable "${PROJECT_ROOT}[orca,test]"
env -u LD_LIBRARY_PATH \
  "${PYTHON}" "${PROJECT_ROOT}/scripts/prepare_orcalab_runtime.py" "${CONSTRAINTS}"
verify
