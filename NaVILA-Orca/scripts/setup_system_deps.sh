#!/usr/bin/env bash
set -euo pipefail

# Qt's xcb platform plugin uses host libraries that are not shipped inside the
# Python wheel. Install the same package set on Ubuntu 22.04 and 24.04.

DPKG_QUERY_BIN="${DPKG_QUERY_BIN:-dpkg-query}"
VERIFY_ONLY=0
PACKAGES=(
  libgl1
  libegl1
  libopengl0
  libxkbcommon-x11-0
  libxcb-cursor0
  libxcb-icccm4
  libxcb-image0
  libxcb-keysyms1
  libxcb-render-util0
  libxcb-util1
  libxcb-xinerama0
)

case "${1:-}" in
  "") ;;
  --verify) VERIFY_ONLY=1 ;;
  -h|--help)
    echo "Usage: ./scripts/setup_system_deps.sh [--verify]"
    exit 0
    ;;
  *)
    echo "Usage: ./scripts/setup_system_deps.sh [--verify]" >&2
    exit 2
    ;;
esac

if ! command -v "${DPKG_QUERY_BIN}" >/dev/null 2>&1; then
  echo "Ubuntu/Debian dpkg-query is required to check OrcaLab GUI libraries." >&2
  exit 2
fi

MISSING=()
for package in "${PACKAGES[@]}"; do
  if ! "${DPKG_QUERY_BIN}" -W -f='${Status}' "${package}" 2>/dev/null |
    grep -qx 'install ok installed'; then
    MISSING+=("${package}")
  fi
done

if [[ "${#MISSING[@]}" -eq 0 ]]; then
  echo "OrcaLab GUI system libraries verified"
  exit 0
fi

echo "Missing OrcaLab GUI system packages: ${MISSING[*]}" >&2
if [[ "${VERIFY_ONLY}" -eq 1 ]]; then
  echo "Run ./NaVILA-Orca/scripts/setup_system_deps.sh to install them." >&2
  exit 2
fi

if [[ "${EUID}" -eq 0 ]]; then
  APT_PREFIX=()
elif command -v sudo >/dev/null 2>&1; then
  APT_PREFIX=(sudo)
else
  echo "sudo is required to install the missing Ubuntu packages." >&2
  exit 2
fi

"${APT_PREFIX[@]}" apt-get update
"${APT_PREFIX[@]}" apt-get install --yes "${MISSING[@]}"

exec "$0" --verify
