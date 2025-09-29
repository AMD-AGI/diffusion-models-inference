#!/bin/bash

scripts=(
    "/ci/run.hunyuanvideo.sh"
    "/ci/run.wan_2_1.sh"
    "/ci/run.wan_2_2.sh"
)

for script in "${scripts[@]}"; do
    if [ ! -e "${script}" ]; then
        echo "Path '${script}' does not exist"
        exit 1
    fi
    echo "Running '${script}'"
    bash $script
done
