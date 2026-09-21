#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Open a pull request for an already-pushed MIOpen database branch.
# Expects env vars: GH_TOKEN, BRANCH_NAME, BASE_BRANCH, RUN_NUMBER, RUN_URL,
#   API_URL, GITHUB_OUTPUT

TITLE="Update MIOpen database (run #${RUN_NUMBER})"
BODY="Tuned MIOpen databases produced by [run #${RUN_NUMBER}](${RUN_URL})."

PAYLOAD=$(jq -n \
  --arg title "$TITLE" \
  --arg head "$BRANCH_NAME" \
  --arg base "$BASE_BRANCH" \
  --arg body "$BODY" \
  '{title: $title, head: $head, base: $base, body: $body}')

HTTP_CODE=$(curl -sS -o response.json -w '%{http_code}' \
  -X POST "$API_URL" \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -d "$PAYLOAD") || HTTP_CODE="000"

PR_URL=$(jq -r '.html_url // empty' response.json 2>/dev/null || true)

if [ "$HTTP_CODE" = "201" ] && [ -n "$PR_URL" ]; then
  echo "url=${PR_URL}" >> "$GITHUB_OUTPUT"
  echo "Opened ${PR_URL}"
else
  # The branch is already pushed, so a failed PR call is recoverable by hand and
  # must not fail the job. 403 usually means Actions may not create pull requests.
  MESSAGE=$(jq -r '.message // empty' response.json 2>/dev/null || true)
  echo "::warning::Could not open a pull request (HTTP ${HTTP_CODE}${MESSAGE:+: ${MESSAGE}}). Branch ${BRANCH_NAME} is pushed; open the pull request manually."
  echo "url=" >> "$GITHUB_OUTPUT"
fi

rm -f response.json
