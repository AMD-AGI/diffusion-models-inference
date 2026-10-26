#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

# Normalize the benchmark selection requested by xDiT into run.py filter flags.
# Keep this helper dependency-free so the poller and the hardware workflow accept
# exactly the same syntax: the poller decides whether a request is dispatchable
# at all, and the workflow rebuilds the flags it passes to the benchmark runner.

# Tags that can actually match on the ROCm runners this handoff targets. The
# config set also carries NVIDIA (hopper/blackwell) and RDNA (rdna4) tags, which
# would always select zero configs here and waste a full build and tuning cycle.
BENCHMARK_TAG_ALLOWLIST=(release lucid mad instinct nightly gfx942 gfx950)
BENCHMARK_ARCH_TAGS=(gfx942 gfx950)

# Config names may contain '-', but never leading: a value that starts with one
# could otherwise reach a command line as an option.
BENCHMARK_VALUE_PATTERN='^[A-Za-z0-9][A-Za-z0-9._-]*$'

# Usage: normalize_benchmark_selection RAW [ARCHITECTURES_CSV]
# Prints the canonical flag string, e.g. "--name flux.default" or "--tag release".
# An empty result is valid and means "every config for the architecture under
# test", because the benchmark runner appends the runner's own --tag <gfx arch>.
normalize_benchmark_selection() {
    local raw="$1" architectures="${2:-}"
    local -a tokens=() names=() tags=() kept=() flags=()
    local token pending='' mode=name

    # Commas and whitespace are interchangeable separators, so "a,b", "a, b",
    # "--tag a,b" and "--tag a --tag b" all reduce to the same token list.
    read -r -a tokens <<< "${raw//,/ }"

    for token in "${tokens[@]}"; do
        case "$token" in
            --name | --tag)
                [[ -z "$pending" ]] || {
                    echo "::error::${pending} requires a value" >&2
                    return 1
                }
                # A flag stays in effect for every value that follows it.
                mode=${token#--}
                pending=$token
                continue
                ;;
            -*)
                echo "::error::Only --name and --tag are supported, got: '${token}'" >&2
                return 1
                ;;
        esac

        if [[ ! "$token" =~ $BENCHMARK_VALUE_PATTERN ]]; then
            echo "::error::Invalid benchmark ${mode}: '${token}'" >&2
            return 1
        fi
        if [[ "$mode" == name ]]; then
            names+=("$token")
        else
            tags+=("$token")
        fi
        pending=''
    done

    [[ -z "$pending" ]] || {
        echo "::error::${pending} requires a value" >&2
        return 1
    }
    if [[ "${#names[@]}" -eq 0 && "${#tags[@]}" -eq 0 ]]; then
        echo "::error::No benchmarks requested; expected benchmark names, --name, or --tag." >&2
        return 1
    fi
    # The benchmark runner treats --name and --tag as mutually exclusive, so stop
    # here instead of failing several hours into the build.
    if [[ "${#names[@]}" -gt 0 && "${#tags[@]}" -gt 0 ]]; then
        echo "::error::--name and --tag cannot be combined in one request." >&2
        return 1
    fi

    for token in "${tags[@]}"; do
        if [[ " ${BENCHMARK_TAG_ALLOWLIST[*]} " != *" ${token} "* ]]; then
            echo "::error::Unsupported benchmark tag: '${token}'" >&2
            echo "::error::Allowed tags: ${BENCHMARK_TAG_ALLOWLIST[*]}" >&2
            return 1
        fi
    done

    # Architecture tags are AND-ed with the tag the runner adds for the
    # architecture it is running on, so anything but an exact match would
    # silently select nothing.
    if [[ -n "$architectures" && "${#tags[@]}" -gt 0 ]]; then
        for token in "${tags[@]}"; do
            if [[ " ${BENCHMARK_ARCH_TAGS[*]} " != *" ${token} "* ]]; then
                kept+=("$token")
            elif [[ " ${architectures//,/ } " != *" ${token} "* ]]; then
                echo "::error::Benchmark tag '${token}' is not in the requested architectures (${architectures})." >&2
                return 1
            elif [[ "$architectures" == *,* ]]; then
                echo "::error::Benchmark tag '${token}' is ambiguous with multiple architectures (${architectures})." >&2
                echo "::error::Select the architecture through the architectures field instead." >&2
                return 1
            else
                echo "Dropping redundant architecture tag '${token}'; the runner applies it automatically." >&2
            fi
        done
        tags=("${kept[@]}")
    fi

    for token in "${names[@]}"; do
        flags+=(--name "$token")
    done
    for token in "${tags[@]}"; do
        flags+=(--tag "$token")
    done

    [[ "${#flags[@]}" -eq 0 ]] || printf '%s' "${flags[*]}"
}
