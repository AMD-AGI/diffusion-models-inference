#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Resolve registry layer-cache refs for a docker buildx build.
# Expects env vars: CACHE_IMAGE_NAME, CHANNEL, REF, CACHE_SCOPE,
# EXTRA_CACHE_FROM, EXPORT, DISABLE_CACHE
# Outputs: scope, cache_from_flags, cache_to_flag, export_ref,
# export_ref_digest to GITHUB_OUTPUT

# Docker tags accept [a-zA-Z0-9._-] and may not lead with a separator, while
# branch names routinely carry slashes, so everything else collapses to a dash.
# Truncation happens before trimming so that cutting mid-name cannot leave a
# trailing separator behind.
# Callers pass github.ref_name, which is already bare, but a miswired caller
# passing github.ref would otherwise get a silently different scope.
slug() {
  printf '%s' "${1#refs/heads/}" \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's#[^a-z0-9._-]#-#g' \
    | cut -c1-40 \
    | sed 's#^[-._]*##; s#[-._]*$##'
}

# Empty output means the ref does not exist. The raw manifest is hashed rather
# than read through --format, because a cache-only manifest is not an image and
# imagetools cannot parse it as one.
manifest_digest() {
  local raw
  if ! raw=$(docker buildx imagetools inspect --raw "$1" 2>/dev/null); then
    return 0
  fi
  printf '%s' "$raw" | sha256sum | cut -d' ' -f1
}

SCOPE=$(slug "${CACHE_SCOPE:-$REF}")
if [ -z "$SCOPE" ]; then
  echo "::error::Could not derive a cache scope from ref '${REF}'"
  exit 1
fi

OWN_REF="${CACHE_IMAGE_NAME}:${CHANNEL}-${SCOPE}"
MAIN_REF="${CACHE_IMAGE_NAME}:${CHANNEL}-main"

echo "Cache scope: ${SCOPE}"

CACHE_FROM_FLAGS=""
if [ "${DISABLE_CACHE}" = "true" ]; then
  echo "Cache disabled, importing nothing"
else
  EXTRA_REFS=()
  read -r -a EXTRA_REFS <<< "${EXTRA_CACHE_FROM}" || true

  SEEN=""
  for CANDIDATE in "${OWN_REF}" "${MAIN_REF}" "${EXTRA_REFS[@]}"; do
    [ -n "${CANDIDATE}" ] || continue
    case " ${SEEN} " in *" ${CANDIDATE} "*) continue ;; esac
    SEEN="${SEEN} ${CANDIDATE}"

    # A missing ref must not fail the build: the builder imports cache straight
    # from the registry, and the first build of a scope has nothing to read.
    if [ -n "$(manifest_digest "${CANDIDATE}")" ]; then
      echo "  hit  ${CANDIDATE}"
      CACHE_FROM_FLAGS="${CACHE_FROM_FLAGS} --cache-from type=registry,ref=${CANDIDATE}"
    else
      echo "  miss ${CANDIDATE}"
    fi
  done
fi

CACHE_TO_FLAG=""
EXPORT_REF=""
EXPORT_REF_DIGEST=""
if [ "${EXPORT}" = "true" ]; then
  EXPORT_REF="${OWN_REF}"
  # mode=max is load-bearing: the expensive work lives in builder stages that
  # never reach the final image, and mode=min cannot describe them.
  # ignore-error keeps a failed cache push from failing the image push, so the
  # caller compares export_ref_digest afterwards to notice that happening.
  CACHE_TO_FLAG="--cache-to type=registry,ref=${OWN_REF},mode=max,image-manifest=true,oci-mediatypes=true,ignore-error=true"
  EXPORT_REF_DIGEST=$(manifest_digest "${OWN_REF}")
  echo "Exporting cache to ${OWN_REF}"
fi

{
  echo "scope=${SCOPE}"
  echo "cache_from_flags=${CACHE_FROM_FLAGS# }"
  echo "cache_to_flag=${CACHE_TO_FLAG}"
  echo "export_ref=${EXPORT_REF}"
  echo "export_ref_digest=${EXPORT_REF_DIGEST}"
} >> "$GITHUB_OUTPUT"
