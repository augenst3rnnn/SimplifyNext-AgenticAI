#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="$(basename "${PROJECT_ROOT}")"
ARCHIVE_DIR="${PROJECT_ROOT}/dist"
ARCHIVE_PATH="${ARCHIVE_DIR}/navvlm-orcalab-kit.tar.gz"

mkdir -p "${ARCHIVE_DIR}"

# This is a clean reproduction archive, not a snapshot of a developer's
# machine.  In particular, exclude linked legacy repositories, build output,
# caches and any locally downloaded foundation-model checkpoints.
tar \
  --exclude="${PROJECT_NAME}/.git" \
  --exclude="${PROJECT_NAME}/components" \
  --exclude="${PROJECT_NAME}/dist" \
  --exclude="${PROJECT_NAME}/build" \
  --exclude="${PROJECT_NAME}/outputs" \
  --exclude="${PROJECT_NAME}/logs" \
  --exclude="*/__pycache__" \
  --exclude="*/.pytest_cache" \
  --exclude="*.egg-info" \
  -czf "${ARCHIVE_PATH}" \
  -C "$(dirname "${PROJECT_ROOT}")" "${PROJECT_NAME}"

printf 'kit: %s\n' "${ARCHIVE_PATH}"
