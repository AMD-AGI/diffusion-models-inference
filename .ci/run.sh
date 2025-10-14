#!/bin/bash

scripts=(
    "/app/.ci/run.hunyuanvideo.sh"
    "/app/.ci/run.wan_2_1.sh"
    "/app/.ci/run.wan_2_2.sh"
    "/app/.ci/run.flux.sh"
)

for script in "${scripts[@]}"; do
    if [ ! -e "${script}" ]; then
        echo "Path '${script}' does not exist"
        exit 1
    fi
    echo "Running '${script}'"
    bash $script
done
