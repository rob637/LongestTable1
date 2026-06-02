#!/usr/bin/env bash
# Manual deploy fallback. Normally `git push` triggers Cloud Build automatically.
# Usage: ./deploy.sh
set -euo pipefail

SERVICE="${SERVICE:-longest-table-vienna}"
REGION="${REGION:-us-east4}"
PROJECT="$(gcloud config get-value project 2>/dev/null)"

if [[ -z "${PROJECT}" ]]; then
  echo "No gcloud project set. Run: gcloud config set project YOUR_PROJECT" >&2
  exit 1
fi

echo "Deploying ${SERVICE} to ${REGION} in project ${PROJECT}..."
gcloud run deploy "${SERVICE}" \
  --source . \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated
