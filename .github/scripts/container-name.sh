#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

# Name GPU workload containers after whoever is accountable for the run.
# A container that outlives its job keeps holding GPUs until a human steps in,
# and `docker ps` is the first place anyone looks, so the name itself should
# say who to ask. Keep this helper dependency-free so the actions that start
# containers and the cleanup action that removes them derive the same name.

# Returns a label identifying who to contact about a running workload.
workload_owner() {
  # Scheduled runs have no human initiator. GitHub still fills in an actor —
  # whoever last touched the workflow — which would point at someone who has
  # no idea the run exists, so name the trigger instead of blaming them.
  if [ "${GITHUB_EVENT_NAME:-}" = "schedule" ]; then
    printf 'scheduled'
    return
  fi

  # triggering_actor is who started *this* run; actor still names the original
  # author after a re-run, who is not the person watching it now.
  local actor="${GITHUB_TRIGGERING_ACTOR:-${GITHUB_ACTOR:-}}"

  # Docker names must match [a-zA-Z0-9][a-zA-Z0-9_.-]*. GitHub usernames
  # already fit, but bot accounts are spelled "github-actions[bot]", whose
  # brackets docker rejects outright.
  local slug
  slug=$(printf '%s' "$actor" |
    tr '[:upper:]' '[:lower:]' |
    sed 's#\[bot\]#-bot#g' |
    sed 's#[^a-z0-9._-]#-#g' |
    cut -c1-40 |
    sed 's#^[-._]*##; s#[-._]*$##')

  # An empty actor means the run is not telling us; "unknown" at least keeps
  # the container name legal and obviously unattributed.
  printf '%s' "${slug:-unknown}"
}

# Returns the container name for a workload, e.g. "jdoe-xdit-bench".
container_name() {
  printf '%s-%s' "$(workload_owner)" "${1:?container base name is required}"
}
