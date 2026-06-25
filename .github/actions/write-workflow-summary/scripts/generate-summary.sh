#!/usr/bin/env bash
set -euo pipefail

# Generate a GitHub Actions step summary with tables and code blocks.
# Expects env vars: INPUT_TITLE, INPUT_TEXT, INPUT_TABLES (JSON), INPUT_CODE (JSON)

echo "## ${INPUT_TITLE}" >> "$GITHUB_STEP_SUMMARY"
echo "" >> "$GITHUB_STEP_SUMMARY"

# Process text content
if [ -n "${INPUT_TEXT}" ]; then
  echo "${INPUT_TEXT}" >> "$GITHUB_STEP_SUMMARY"
  echo "" >> "$GITHUB_STEP_SUMMARY"
fi

# Process table sections
echo "${INPUT_TABLES}" | jq -c 'to_entries[]' | while IFS= read -r section; do
  TITLE=$(echo "$section" | jq -r '.key')
  DATA=$(echo "$section" | jq -c '.value')

  echo "### $TITLE" >> "$GITHUB_STEP_SUMMARY"
  echo "| Key | Value |" >> "$GITHUB_STEP_SUMMARY"
  echo "|-----|-------|" >> "$GITHUB_STEP_SUMMARY"
  echo "$DATA" | jq -r 'to_entries | .[] | "| \(.key) | \(if .value then "`\(.value | tostring)`" else "" end) |"' >> "$GITHUB_STEP_SUMMARY"
  echo "" >> "$GITHUB_STEP_SUMMARY"
done

# Process code sections
echo "${INPUT_CODE}" | jq -c 'to_entries[]' | while IFS= read -r section; do
  TITLE=$(echo "$section" | jq -r '.key')
  DATA=$(echo "$section" | jq -c '.value')

  echo "### $TITLE" >> "$GITHUB_STEP_SUMMARY"
  echo '```json' >> "$GITHUB_STEP_SUMMARY"
  echo "$DATA" | jq . >> "$GITHUB_STEP_SUMMARY"
  echo '```' >> "$GITHUB_STEP_SUMMARY"
  echo "" >> "$GITHUB_STEP_SUMMARY"
done
