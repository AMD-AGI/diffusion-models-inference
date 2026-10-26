#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# The helper path is resolved relative to this action at runtime.
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../../../scripts/benchmark-selection.sh"

started=$(date +%s)

AUTH_FAILURE_STATUS=77

is_auth_failure() {
    printf '%s' "$1" \
        | jq -e '(.status? // "") == "401" or (.message? // "") == "Bad credentials"' \
            >/dev/null 2>&1
}

# GITHUB_TOKEN is an installation token that cannot be refreshed beyond 24 hours. Once it
# expires every call fails, so stop loudly instead of silently ignoring xDiT requests.
fail_auth() {
    echo "::error::GitHub rejected the token (HTTP 401); it expired while the poller was running."
    echo "::error::GITHUB_TOKEN lives at most 24 hours, so loiter_seconds must stay below that."
    exit 1
}

# gh prints HTTP error bodies to stdout, which would otherwise be parsed as a response.
# Capture the body and the exit status separately so failures never reach jq.
api() {
    local response status=0
    response=$(gh api "$@" 2>/dev/null) || status=$?
    if [[ "$status" -ne 0 ]]; then
        if is_auth_failure "$response"; then
            return "$AUTH_FAILURE_STATUS"
        fi
        return "$status"
    fi
    printf '%s' "$response"
}

# Hand the watch over only on a clean exit; cancellation and timeout kill the shell first.
relaunch() {
    local elapsed attempt delay=15 response status auth_failed=false
    elapsed=$(( $(date +%s) - started ))
    if [[ "$elapsed" -lt 60 ]]; then
        sleep $(( 60 - elapsed ))
    fi
    for attempt in 1 2 3 4 5; do
        status=0
        response=$(gh api "repos/${GITHUB_REPOSITORY}/actions/workflows/${SELF_WORKFLOW}/dispatches" \
            -f ref="${GITHUB_REF_NAME}" 2>/dev/null) || status=$?
        if [[ "$status" -eq 0 ]]; then
            return 0
        fi
        printf '%s\n' "$response"
        if is_auth_failure "$response"; then
            auth_failed=true
            break
        fi
        if [[ "$attempt" -lt 5 ]]; then
            echo "handover dispatch attempt ${attempt} failed; retrying in ${delay}s"
            sleep "$delay"
            delay=$(( delay * 2 ))
        fi
    done
    if [[ "$auth_failed" == true ]]; then
        echo "::error::GitHub rejected the token (HTTP 401) during handover."
        echo "::error::GITHUB_TOKEN lives at most 24 hours, so loiter_seconds must stay below that."
    fi
    echo "::error::Handover failed; no poller is watching xDiT any more."
    echo "::error::Dispatch this workflow manually to resume."
    exit 1
}

allowed=$(printf '%s' "$ALLOWED_ACTORS" | tr -d '[:space:]' | tr ',' '\n' | grep -v '^$' || true)
if [[ -z "$allowed" ]]; then
    echo "::error::Repository variable XDIT_CI_ALLOWED_ACTORS is empty."
    exit 1
fi

for tool in gh jq unzip git; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "::error::${tool} is required on the xDiT poller runner."
        exit 1
    fi
done

# Let a heavy workflow dispatched by the previous poller become visible to the runs API.
sleep 20

deadline=$(( $(date +%s) + LOITER_SECONDS ))

while [[ "$(date +%s)" -lt "$deadline" ]]; do
    api_status=0
    candidates=$(api \
        "repos/${XDIT_REPO}/actions/workflows/${XDIT_WORKFLOW}/runs?status=in_progress&per_page=50") \
        || api_status=$?
    [[ "$api_status" -ne "$AUTH_FAILURE_STATUS" ]] || fail_auth
    if [[ "$api_status" -ne 0 ]]; then
        echo "xDiT run list could not be fetched; retrying after the poll interval"
        sleep "$POLL_INTERVAL_SECONDS"
        continue
    fi

    if ! rows=$(printf '%s' "$candidates" | jq -c \
        '[(.workflow_runs | select(type == "array"))[] | {id, attempt: .run_attempt, workflow_id, sha: .head_sha, actor: (.triggering_actor.login // .actor.login // ""), title: (.display_title // "")}]' \
        2>/dev/null); then
        echo "xDiT run list had an unexpected shape; retrying after the poll interval"
        sleep "$POLL_INTERVAL_SECONDS"
        continue
    fi

    for i in $(seq 0 $(( $(echo "$rows" | jq 'length') - 1 ))); do
        xrun="$(echo "$rows" | jq -r ".[$i].id").$(echo "$rows" | jq -r ".[$i].attempt")"
        title=$(echo "$rows" | jq -r ".[$i].title")
        actor=$(echo "$rows" | jq -r ".[$i].actor")
        current_workflow_id=$(echo "$rows" | jq -r ".[$i].workflow_id")
        sha=$(echo "$rows" | jq -r ".[$i].sha")

        # A restarted xDiT wait job has a new run ID but carries the initial human-triggered run ID
        # in its title. Deduplicating on that stable ID prevents a second heavy build.
        original_run_id=$(printf '%s' "$title" | sed -n 's/.*\[origin:\([0-9][0-9]*\)\].*/\1/p')
        current_run_id=${xrun%%.*}
        if [[ "$actor" != "github-actions[bot]" ]]; then
            original_run_id=$current_run_id
        else
            original_run_id=${original_run_id:-$current_run_id}
        fi
        if [[ "$original_run_id" != "$current_run_id" ]]; then
            api_status=0
            origin=$(api "repos/${XDIT_REPO}/actions/runs/${original_run_id}") || api_status=$?
            [[ "$api_status" -ne "$AUTH_FAILURE_STATUS" ]] || fail_auth
            if [[ "$api_status" -ne 0 ]]; then
                echo "xDiT run ${xrun}: origin run could not be fetched, skipping"
                continue
            fi
            origin_workflow_id=$(printf '%s' "$origin" | jq -r '.workflow_id // ""')
            if [[ "$origin_workflow_id" != "$current_workflow_id" ]]; then
                echo "xDiT run ${xrun}: origin run is not from the same workflow, skipping"
                continue
            fi
            actor=$(printf '%s' "$origin" | jq -r '.triggering_actor.login // .actor.login // ""')
            sha=$(printf '%s' "$origin" | jq -r '.head_sha // ""')
        fi

        if ! printf '%s\n' "$allowed" | grep -Fxqi "$actor"; then
            echo "xDiT run ${xrun}: actor not in allow-list, skipping"
            continue
        fi

        # Request metadata is immutable across continuations: only the original human run uploads it.
        api_status=0
        artifacts=$(api "repos/${XDIT_REPO}/actions/runs/${original_run_id}/artifacts") || api_status=$?
        [[ "$api_status" -ne "$AUTH_FAILURE_STATUS" ]] || fail_auth
        if [[ "$api_status" -ne 0 ]]; then
            echo "xDiT run ${xrun}: artifact list could not be fetched, skipping"
            continue
        fi
        artifact_id=$(printf '%s' "$artifacts" \
            | jq -r '[.artifacts[]? | select(.name == "rocm-hardware-request") | .id] | first // ""' \
            2>/dev/null || true)
        if [[ -z "$artifact_id" ]]; then
            echo "xDiT run ${xrun}: request metadata artifact is not available yet"
            continue
        fi

        metadata_dir=$(mktemp -d)
        if ! gh api "repos/${XDIT_REPO}/actions/artifacts/${artifact_id}/zip" \
            > "$metadata_dir/request.zip" 2>/dev/null; then
            if is_auth_failure "$(head -c 1024 "$metadata_dir/request.zip")"; then
                rm -rf "$metadata_dir"
                fail_auth
            fi
            rm -rf "$metadata_dir"
            echo "xDiT run ${xrun}: request metadata artifact could not be downloaded"
            continue
        fi
        if ! unzip -p "$metadata_dir/request.zip" request.json \
            | head -c 65537 > "$metadata_dir/request.json"; then
            rm -rf "$metadata_dir"
            echo "xDiT run ${xrun}: invalid request metadata artifact"
            continue
        fi
        if [[ $(wc -c < "$metadata_dir/request.json") -gt 65536 ]]; then
            rm -rf "$metadata_dir"
            echo "xDiT run ${xrun}: request.json exceeds 64 KiB"
            continue
        fi
        if ! requested_benchmarks=$(jq -er '.benchmarks | select(type == "string")' "$metadata_dir/request.json") ||
              ! requested_architectures=$(jq -er '.architectures | select(type == "string")' "$metadata_dir/request.json") ||
              ! amd_ref=$(jq -er '.amd_ref | select(type == "string")' "$metadata_dir/request.json"); then
            rm -rf "$metadata_dir"
            echo "xDiT run ${xrun}: request metadata fields are missing or invalid"
            continue
        fi
        rm -rf "$metadata_dir"

        if [[ ! "$sha" =~ ^[0-9a-fA-F]{40}$ ]]; then
            echo "xDiT run ${original_run_id}: invalid requested SHA '${sha}', skipping"
            continue
        fi

        # Architectures stay out of this check: the hardware workflow resolves
        # architecture tags once it knows which runners the request landed on.
        if ! sanitized_benchmarks=$(normalize_benchmark_selection "$requested_benchmarks"); then
            echo "xDiT run ${xrun}: invalid benchmark selection, skipping"
            continue
        fi

        architectures=()
        IFS=',' read -r -a requested <<< "$requested_architectures"
        for architecture in "${requested[@]}"; do
            architecture=${architecture#"${architecture%%[![:space:]]*}"}
            architecture=${architecture%"${architecture##*[![:space:]]}"}
            if [[ ! "$architecture" =~ ^(gfx942|gfx950)$ ]]; then
                echo "xDiT run ${xrun}: invalid architecture '${architecture}', skipping"
                architectures=()
                break
            fi
            architectures+=("$architecture")
        done
        if [[ "${#architectures[@]}" -eq 0 ]]; then
            echo "xDiT run ${xrun}: no valid architectures requested, skipping"
            continue
        fi
        sanitized_architectures=$(IFS=,; echo "${architectures[*]}")

        if [[ ! "$amd_ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] ||
           ! git check-ref-format "refs/heads/${amd_ref}" >/dev/null 2>&1; then
            echo "xDiT run ${xrun}: invalid AMD ref '${amd_ref}', skipping"
            continue
        fi

        # A failed lookup must not be read as "nothing dispatched yet", or the same xDiT run
        # would be built twice.
        api_status=0
        heavy_runs=$(api "repos/${GITHUB_REPOSITORY}/actions/workflows/${HEAVY_WORKFLOW}/runs?per_page=100") \
            || api_status=$?
        [[ "$api_status" -ne "$AUTH_FAILURE_STATUS" ]] || fail_auth
        if [[ "$api_status" -ne 0 ]]; then
            echo "xDiT run ${xrun}: existing hardware runs could not be listed, skipping"
            continue
        fi
        if ! seen=$(printf '%s' "$heavy_runs" | jq -r --arg m "[xdit-run:${original_run_id}]" \
            '[(.workflow_runs | select(type == "array"))[] | select((.display_title // "") | contains($m))] | length' \
            2>/dev/null); then
            echo "xDiT run ${xrun}: hardware run list had an unexpected shape, skipping"
            continue
        fi
        if [[ "$seen" -gt 0 ]]; then
            continue
        fi

        api_status=0
        api "repos/${GITHUB_REPOSITORY}/actions/workflows/${HEAVY_WORKFLOW}/dispatches" \
            -f ref="${GITHUB_REF_NAME}" \
            -f inputs[xdit_sha]="$sha" \
            -f 'inputs[benchmarks]='"$sanitized_benchmarks" \
            -f 'inputs[architectures]='"$sanitized_architectures" \
            -f 'inputs[xdit_run_id]='"$original_run_id" \
            -f 'inputs[amd_ref]='"$amd_ref" || api_status=$?
        [[ "$api_status" -ne "$AUTH_FAILURE_STATUS" ]] || fail_auth
        if [[ "$api_status" -ne 0 ]]; then
            echo "xDiT run ${xrun}: hardware dispatch failed, retrying on the next poll"
            continue
        fi

        echo "dispatched hardware run for original xDiT run ${original_run_id} (sha ${sha})"
        relaunch
        exit 0
    done

    sleep "$POLL_INTERVAL_SECONDS"
done

echo "loiter window elapsed with nothing to dispatch"
relaunch
