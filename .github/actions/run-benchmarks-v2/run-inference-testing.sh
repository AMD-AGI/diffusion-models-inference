#!/usr/bin/env bash
# Submit a benchmark job to the infrastructure-managed inference-testing driver.
#
# The runner has no access to the driver container: it hands over work by
# writing a job directory into the spool shared through the runner-work volume,
# then waits for the driver to mark it DONE. Nothing here talks to Docker.
#
# Inputs (env):
#   CONVERTED_DIR        — path to converted config directory
#   ARCH                 — architecture name
#   RESULT_DIR           — benchmark output directory, collected as an artifact
#   HF_TOKEN             — HuggingFace token
#   JOB_TIMEOUT_MINUTES  — driver-side timeout
#   ITT_SPOOL_ROOT       — spool root, exported by the ITT compose override
#   GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT
set -euo pipefail

SPOOL_ROOT="${ITT_SPOOL_ROOT:-}"
if [ -z "$SPOOL_ROOT" ]; then
  echo "::error::ITT_SPOOL_ROOT is not set. This runner is not deployed with the" \
       "inference-testing compose override; route this job to an ITT runner."
  exit 1
fi

if [ ! -d "$SPOOL_ROOT" ]; then
  echo "::error::Spool root $SPOOL_ROOT does not exist. The inference-testing" \
       "driver container is not running on this node."
  exit 1
fi

JOB_ID="${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${ARCH//[^A-Za-z0-9_.-]/_}"
JOB_DIR="$SPOOL_ROOT/$JOB_ID"

rm -rf "$JOB_DIR"
mkdir -p "$JOB_DIR"
cp -R "$CONVERTED_DIR" "$JOB_DIR/configs"

# The request carries the HF token, so keep it unreadable to other users and
# remove it as soon as the driver is finished with it.
(
  umask 077
  {
    echo "OWNER_UID=$(id -u)"
    echo "OWNER_GID=$(id -g)"
    echo "TIMEOUT_SECONDS=$((JOB_TIMEOUT_MINUTES * 60))"
    echo "HF_TOKEN=${HF_TOKEN}"
    echo "CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-0,1,2,3,4,5,6,7}"
    # MLflow reads these directly; only present when reporting to Databricks.
    if [ -n "${DATABRICKS_HOST:-}" ]; then
      echo "DATABRICKS_HOST=${DATABRICKS_HOST}"
      echo "DATABRICKS_TOKEN=${DATABRICKS_TOKEN}"
    fi
  } > "$JOB_DIR/request.env"
)

# shellcheck disable=SC2317  # invoked indirectly via trap
on_exit() {
  rm -f "$JOB_DIR/request.env"
  # Driver and per-container logs live in the spool, which is never uploaded.
  if [ -n "${RESULT_DIR:-}" ] && [ -d "$RESULT_DIR" ]; then
    for log in "$JOB_DIR"/*.log; do
      if [ -f "$log" ]; then
        cp "$log" "$RESULT_DIR/" || true
      fi
    done
  fi
}
trap on_exit EXIT
# Let the driver abort the run if the workflow is cancelled.
trap 'echo "Cancelling driver job $JOB_ID"; touch "$JOB_DIR/CANCEL"' INT TERM

echo "Submitting job $JOB_ID to $SPOOL_ROOT"
touch "$JOB_DIR/READY"

tail -F "$JOB_DIR/driver.log" 2>/dev/null &
TAIL_PID=$!

# Client-side guard in case the driver container is not running at all.
WAIT_LIMIT=$(( (JOB_TIMEOUT_MINUTES + 15) * 60 ))
WAITED=0
while [ ! -e "$JOB_DIR/DONE" ]; do
  if [ "$WAITED" -ge "$WAIT_LIMIT" ]; then
    kill "$TAIL_PID" 2>/dev/null || true
    touch "$JOB_DIR/CANCEL"
    echo "::error::Timed out after ${WAIT_LIMIT}s waiting for the driver to finish $JOB_ID"
    exit 1
  fi
  sleep 5
  WAITED=$((WAITED + 5))
done

sleep 2
kill "$TAIL_PID" 2>/dev/null || true

STATUS=$(cat "$JOB_DIR/exit_code" 2>/dev/null || echo 1)
echo "Driver job $JOB_ID exited with status $STATUS"
exit "$STATUS"
