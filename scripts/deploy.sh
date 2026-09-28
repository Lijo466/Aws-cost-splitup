#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

NEW_SHA="${1:?Usage: deploy.sh <NEW_SHA>}"
PREV_TAG="$(cat .last_deployed_sha 2>/dev/null || true)"
COMPOSE=(docker compose -f docker-compose.prod.yml)

if [ -z "${GHCR_TOKEN:-}" ]; then
  echo "ERROR: GHCR_TOKEN not set" >&2
  exit 1
fi

printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "${GHCR_USER:-ci}" --password-stdin

echo ">> Pulling image for ${NEW_SHA}"
IMAGE_TAG="$NEW_SHA" "${COMPOSE[@]}" pull

echo ">> Recreating containers with ${NEW_SHA}"
IMAGE_TAG="$NEW_SHA" "${COMPOSE[@]}" up -d --remove-orphans

echo ">> Waiting for /health ..."
ok=0
for _ in $(seq 1 30); do
  if curl -sf "http://localhost:${FLASK_PORT:-5000}/health" > /dev/null; then ok=1; break; fi
  sleep 3
done

if [ "$ok" -ne 1 ]; then
  echo ">> Health check FAILED for ${NEW_SHA}"
  "${COMPOSE[@]}" logs --tail=50 aws-dashboard || true
  if [ -n "$PREV_TAG" ] && [ "$PREV_TAG" != "$NEW_SHA" ]; then
    echo ">> Rolling back to ${PREV_TAG}"
    IMAGE_TAG="$PREV_TAG" "${COMPOSE[@]}" up -d --remove-orphans
    for _ in $(seq 1 20); do
      curl -sf "http://localhost:${FLASK_PORT:-5000}/health" > /dev/null && break
      sleep 3
    done
  else
    echo ">> No previous image recorded - manual intervention required."
  fi
  exit 1
fi

echo "$NEW_SHA" > .last_deployed_sha
docker logout ghcr.io > /dev/null 2>&1 || true
docker image prune -f > /dev/null
echo ">> Deploy OK: ${NEW_SHA}"
