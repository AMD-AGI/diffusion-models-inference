#!/bin/bash

scripts=(
    "/app/.ci/run.hunyuanvideo.sh"
    "/app/.ci/run.wan2.1.sh"
    "/app/.ci/run.wan2.2.sh"
    "/app/.ci/run.flux.sh"
    "/app/.ci/run.flux.kontext.sh"
    "/app/.ci/run.flux2.sh"
    "/app/.ci/run.stablediffusion_3_5.sh"
    "/app/.ci/run.z_image_turbo.sh"
    "/app/.ci/run.hunyuanvideo_1_5.sh"
)

if [ -n "${BENCHMARK_LIST}" ]; then # Check if BENCHMARK_LIST is used to override default benchmarks
    IFS=',' read -ra benchmark_scripts <<< "${BENCHMARK_LIST//[^-[:alnum:]_.,]/}"
    scripts=()
    for benchmark_script in "${benchmark_scripts[@]}"; do
        scripts+=("/app/.ci/run.${benchmark_script}.sh")
    done
fi

if [ ${#scripts[@]} -eq 0 ]; then
    echo "No valid benchmark scripts given."
    exit 1
fi

for script in "${scripts[@]}"; do
    if [ ! -e "${script}" ]; then
        echo "Path '${script}' does not exist"
        exit 1
    fi
done

for script in "${scripts[@]}"; do
    echo "Running '${script}'"
    bash $script

    if [ $? -ne 0 ]; then
        echo "Failed to run '${script}'." >&2
        exit 1
    fi
done

if [ -f "/app/.ci/quality_check.sh" ]; then
    echo "Running quality checks"
    bash /app/.ci/quality_check.sh
fi
