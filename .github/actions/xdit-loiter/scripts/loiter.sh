#!/usr/bin/env bash
set -euo pipefail

started=$(date +%s)

# Hand the watch over only on a clean exit; cancellation and timeout kill the shell first.
relaunch() {
    local elapsed
    elapsed=$(( $(date +%s) - started ))
    if [[ "$elapsed" -lt 60 ]]; then
        sleep $(( 60 - elapsed ))
    fi
    if ! gh api "repos/${GITHUB_REPOSITORY}/actions/workflows/${SELF_WORKFLOW}/dispatches" \
        -f ref="${GITHUB_REF_NAME}"; then
        echo "::error::Handover failed; no poller is watching xDiT any more."
        echo "::error::Dispatch this workflow manually to resume."
        exit 1
    fi
}

allowed=$(printf '%s' "$ALLOWED_ACTORS" | tr -d '[:space:]' | tr ',' '\n' | grep -v '^$' || true)
if [[ -z "$allowed" ]]; then
    echo "::error::Repository variable XDIT_CI_ALLOWED_ACTORS is empty."
    exit 1
fi

if ! command -v unzip >/dev/null 2>&1; then
    echo "::error::unzip is required on the xDiT poller runner."
    exit 1
fi

# Let a heavy workflow dispatched by the previous poller become visible to the runs API.
sleep 20

deadline=$(( $(date +%s) + LOITER_SECONDS ))

while [[ "$(date +%s)" -lt "$deadline" ]]; do
    candidates=$(gh api \
        "repos/${XDIT_REPO}/actions/workflows/${XDIT_WORKFLOW}/runs?status=in_progress&per_page=50" \
        2>/dev/null || echo '{"workflow_runs":[]}')

    rows=$(echo "$candidates" | jq -c \
        '[.workflow_runs[] | {id, attempt: .run_attempt, workflow_id, sha: .head_sha, actor: (.triggering_actor.login // .actor.login // ""), title: (.display_title // "")}]')

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
            origin=$(gh api "repos/${XDIT_REPO}/actions/runs/${original_run_id}" 2>/dev/null || true)
            origin_workflow_id=$(printf '%s' "$origin" | jq -r '.workflow_id // ""')
            if [[ "$origin_workflow_id" != "$current_workflow_id" ]]; then
                echo "xDiT run ${xrun}: origin run is not from the same workflow, skipping"
                continue
            fi
            actor=$(printf '%s' "$origin" | jq -r '.triggering_actor.login // .actor.login // ""')
            sha=$(printf '%s' "$origin" | jq -r '.head_sha // ""')
        fi

        if ! printf '%s\n' "$allowed" | grep -Fxqi "$actor"; then
            echo "xDiT run ${xrun}: actor '${actor}' not in allow-list, skipping"
            continue
        fi

        # Request metadata is immutable across continuations: only the original human run uploads it.
        artifact_id=$(gh api "repos/${XDIT_REPO}/actions/runs/${original_run_id}/artifacts" \
            --jq '.artifacts[] | select(.name == "rocm-hardware-request") | .id' 2>/dev/null \
            | head -1 || true)
        if [[ -z "$artifact_id" ]]; then
            echo "xDiT run ${xrun}: request metadata artifact is not available yet"
            continue
        fi

        metadata_dir=$(mktemp -d)
        if ! gh api "repos/${XDIT_REPO}/actions/artifacts/${artifact_id}/zip" > "$metadata_dir/request.zip"; then
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

        benchmarks=()
        IFS=',' read -r -a requested <<< "$requested_benchmarks"
        for name in "${requested[@]}"; do
            name=${name#"${name%%[![:space:]]*}"}
            name=${name%"${name##*[![:space:]]}"}
            if [[ ! "$name" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
                echo "xDiT run ${xrun}: invalid benchmark name '${name}', skipping"
                benchmarks=()
                break
            fi
            benchmarks+=("$name")
        done
        if [[ "${#benchmarks[@]}" -eq 0 ]]; then
            echo "xDiT run ${xrun}: no valid benchmarks requested, skipping"
            continue
        fi
        sanitized_benchmarks=$(IFS=,; echo "${benchmarks[*]}")

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

        seen=$(gh api \
            "repos/${GITHUB_REPOSITORY}/actions/workflows/${HEAVY_WORKFLOW}/runs?per_page=100" \
            2>/dev/null \
            | jq --arg m "[xdit-run:${original_run_id}]" \
                '[.workflow_runs[] | select((.display_title // "") | contains($m))] | length')
        if [[ "${seen:-0}" -gt 0 ]]; then
            continue
        fi

        gh api \
            "repos/${GITHUB_REPOSITORY}/actions/workflows/${HEAVY_WORKFLOW}/dispatches" \
            -f ref="${GITHUB_REF_NAME}" \
            -f inputs[xdit_sha]="$sha" \
            -f 'inputs[benchmarks]='"$sanitized_benchmarks" \
            -f 'inputs[architectures]='"$sanitized_architectures" \
            -f 'inputs[xdit_run_id]='"$original_run_id" \
            -f 'inputs[amd_ref]='"$amd_ref"

        echo "dispatched hardware run for original xDiT run ${original_run_id} (sha ${sha}, actor ${actor})"
        relaunch
        exit 0
    done

    sleep "$POLL_INTERVAL_SECONDS"
done

echo "loiter window elapsed with nothing to dispatch"
relaunch
