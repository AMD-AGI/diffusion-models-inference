#!/usr/bin/env bash

log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"
}


check_config_subdirectory() {
    log "Checking config subdirectory $1 with reference path $2"
    VIDEO_REFERENCE_FILE=$(find "${2}" -type f -name "reference.mp4" | head -1)
    IMAGE_REFERENCE_FILE=$(find "${2}" -type f -name "reference.png" | head -1)

    if [[ -z "${VIDEO_REFERENCE_FILE}" && -z "${IMAGE_REFERENCE_FILE}" ]]; then
        log "No reference file found in ${2}, skipping."
        return
    fi

    if [[ -f "${IMAGE_REFERENCE_FILE}" &&  -f "${VIDEO_REFERENCE_FILE}" ]]; then
        log "Image reference file ${IMAGE_REFERENCE_FILE} and video reference file ${VIDEO_REFERENCE_FILE} both exist, skipping."
        return
    fi

    if [[ -f "${VIDEO_REFERENCE_FILE}" ]]; then
        # Video reference case
        GENERATED_VIDEO_FILE=$(find "${1}" -mindepth 1 -type f -name "*.mp4" | head -1)
        if [ -z "${GENERATED_VIDEO_FILE}" ]; then
            log "No generated video file found in ${1}, skipping."
            return
        fi
        ARBITER_OUTPUT_FILE="$(dirname "${GENERATED_VIDEO_FILE}")/arbiter.json"
        arbiter measure vmaf "${VIDEO_REFERENCE_FILE}" "${GENERATED_VIDEO_FILE}" --output-format json 2> "${ARBITER_OUTPUT_FILE}.stderr" > "${ARBITER_OUTPUT_FILE}" 
        arbiter measure video_lpips "${VIDEO_REFERENCE_FILE}" "${GENERATED_VIDEO_FILE}" --output-format json 2>> "${ARBITER_OUTPUT_FILE}.stderr" >> "${ARBITER_OUTPUT_FILE}"
        arbiter measure video_mse "${VIDEO_REFERENCE_FILE}" "${GENERATED_VIDEO_FILE}" --output-format json 2>> "${ARBITER_OUTPUT_FILE}.stderr" >> "${ARBITER_OUTPUT_FILE}"
        arbiter measure video_ssim "${VIDEO_REFERENCE_FILE}" "${GENERATED_VIDEO_FILE}" --output-format json 2>> "${ARBITER_OUTPUT_FILE}.stderr" >> "${ARBITER_OUTPUT_FILE}"
        log "arbiter measurement successful for ${GENERATED_VIDEO_FILE}, wrote result to ${ARBITER_OUTPUT_FILE}"
        return
    elif [[ -f "${IMAGE_REFERENCE_FILE}" ]]; then
        # Image reference case
        GENERATED_IMAGE_FILE=$(find "${1}" -mindepth 1 -type f -name "*.png" | head -1)
        if [ -z "${GENERATED_IMAGE_FILE}" ]; then
            log "No generated image file found in ${1}, skipping."
            return
        fi
        ARBITER_OUTPUT_FILE="$(dirname "${GENERATED_IMAGE_FILE}")/arbiter.json"
        arbiter measure lpips "${IMAGE_REFERENCE_FILE}" "${GENERATED_IMAGE_FILE}" --output-format json 2> "${ARBITER_OUTPUT_FILE}.stderr" > "${ARBITER_OUTPUT_FILE}" 
        arbiter measure mse "${IMAGE_REFERENCE_FILE}" "${GENERATED_IMAGE_FILE}" --output-format json 2>> "${ARBITER_OUTPUT_FILE}.stderr" >> "${ARBITER_OUTPUT_FILE}"
        arbiter measure ssim "${IMAGE_REFERENCE_FILE}" "${GENERATED_IMAGE_FILE}" --output-format json 2>> "${ARBITER_OUTPUT_FILE}.stderr" >> "${ARBITER_OUTPUT_FILE}"
        log "arbiter measurement completed for ${GENERATED_IMAGE_FILE}, wrote result to ${ARBITER_OUTPUT_FILE}"
        return
    else
        log "No reference file found in ${2}, skipping."
        return
    fi
}


check_workload_subdirectory() {
    log "Checking workload path $1"
    WORKLOAD_NAME=$(basename $1)

    find "${1}" -mindepth 1 -maxdepth 1 -type d | while IFS= read -r CONFIG_SUBDIRECTORY; do
        CONFIG_NAME=$(basename ${CONFIG_SUBDIRECTORY})
        REFERENCE_PATH="${REFERENCE_PATH_ROOT}/${WORKLOAD_NAME}/${CONFIG_NAME}"
        if [ ! -d "${REFERENCE_PATH}" ]; then
            log "Reference path ${REFERENCE_PATH} does not exist for workload ${WORKLOAD_NAME}, skipping."
            continue
        fi

        check_config_subdirectory "${CONFIG_SUBDIRECTORY}" "${REFERENCE_PATH}"
    done
}


# A few checks to determine if we can run quality checks
if ! command -v arbiter &> /dev/null; then
    log "Warning: `arbiter` executable not available, skipping quality checks."
    exit 0
fi

# Infer platform architecture we are on to determine where to look up reference videos and images
if ! command -v amd-smi &> /dev/null; then
    log "Error: amd-smi not found, cannot determine platform architecture"
    exit 1
fi

PLATFORM_FULL_NAME=$(amd-smi static -g 0 -B --csv 2>/dev/null | cut -d',' -f 5 | head -2 | tail -1) || {
    log "Error: Failed to query AMD GPU information"
    exit 1
}

if [[ -z "${PLATFORM_FULL_NAME}" ]]; then
    log "Error: Could not determine platform name from amd-smi"
    exit 1
fi
PLATFORM_ABBREVIATED_NAME=$(grep -o "MI[0-9]\{3\}" <<< ${PLATFORM_FULL_NAME} | tr '[:upper:]' '[:lower:]') # e.g., "mi300"

if [[ "${PLATFORM_ABBREVIATED_NAME}" == "mi300" || "${PLATFORM_ABBREVIATED_NAME}" == "mi355" ]]; then
    REFERENCE_PATH_ROOT="/app/references/${PLATFORM_ABBREVIATED_NAME}"
    if [[ ! -d "${REFERENCE_PATH_ROOT}" ]]; then
        log "Warning: Reference path ${REFERENCE_PATH_ROOT} does not exist"
        exit 0
    fi
else
    log "Warning: Unsupported platform architecture for quality checks: ${PLATFORM_ABBREVIATED_NAME}"
    exit 0
fi

BENCHMARK_OUTPUT_DIR="/outputs"
if [[ ! -d "${BENCHMARK_OUTPUT_DIR}" ]]; then
    log "Benchmark output directory ${BENCHMARK_OUTPUT_DIR} does not exist, nothing to check"
    exit 0  # Not an error if nothing to check
fi

find "${BENCHMARK_OUTPUT_DIR}" -mindepth 1 -maxdepth 1 -type d | while IFS= read -r WORKLOAD_SUBDIRECTORY; do
    check_workload_subdirectory "${WORKLOAD_SUBDIRECTORY}"
done

log "Quality checks completed for platform ${PLATFORM_ABBREVIATED_NAME}"
