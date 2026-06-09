#!/usr/bin/env bash
# Reference deployment script for a single Linux host.
# Adjust paths and environment for your setup before running.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/network-llm-agent}"
IMAGE_NAME="${IMAGE_NAME:-network-llm-agent}"
CONTAINER_NAME="${CONTAINER_NAME:-network-llm-agent}"
PORT="${PORT:-5000}"
ENV_FILE="${ENV_FILE:-${REPO_DIR}/.env}"

echo "[1/4] Pulling latest source in ${REPO_DIR}"
if [[ ! -d "${REPO_DIR}/.git" ]]; then
    echo "  -> ${REPO_DIR} is not a git checkout; clone it first."
    exit 1
fi
git -C "${REPO_DIR}" pull --ff-only

echo "[2/4] Building image ${IMAGE_NAME}"
docker build -t "${IMAGE_NAME}" "${REPO_DIR}"

echo "[3/4] Stopping existing container (if any)"
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

echo "[4/4] Starting container ${CONTAINER_NAME} on port ${PORT}"
docker run -d \
    --name "${CONTAINER_NAME}" \
    --restart unless-stopped \
    -p "${PORT}:5000" \
    --env-file "${ENV_FILE}" \
    -v "${REPO_DIR}/config:/app/config:ro" \
    "${IMAGE_NAME}"

echo "Done. Container logs:"
docker logs --tail 20 "${CONTAINER_NAME}"
