#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/rdna4/tune.sh [--skip-completed] [BASE_IMAGE]

Run the RDNA4 tuning matrix on local gfx1201 GPUs.

Options:
  --skip-completed  Skip workloads whose output directory contains timings.json
  -h, --help        Show this help

Environment overrides:
  CONFIG                    Host config file or directory (default: <repo>/benchmark_configs/xdit;
                            the image path /app/.ci/benchmark_configs also selects that directory)
  WORKLOAD_NAMES            Optional comma-separated workload names to run
  STATE_DIR                 Persistent host output/cache directory (default: $HOME/rdna4-finalise)
  HF_CACHE                  Hugging Face cache directory (default: $HOME/huggingface)
  INDUCTOR_CACHE            Versioned torch.compile cache (default: STATE_DIR/inductor-cache)
  AITER_JIT_CACHE           Optional shared AITER JIT directory (default: STATE_DIR/aiter-jit)
  AITER_TUNE_CACHE          Optional shared AITER tuning directory (default: STATE_DIR/aiter-tune)
  AITER_ONLINE_TUNE         Enable AITER runtime GEMM tuning (default: 1)
  PYTORCH_TUNABLEOP_ENABLED Enable PyTorch TunableOp (default: 1)
  PYTORCH_TUNABLEOP_TUNING  Enable PyTorch TunableOp runtime tuning (default: 1)
  SHARE_TUNABLEOP_RESULTS   Seed every identical GPU from merged prior results (default: 1)
  XDIT_PIPELINE_TIMEOUT_MINUTES  PipeFusion subgroup timeout (default: xDiT default)
  XDIT_SRC                  Optional host xDiT tree bind-mounted at /app/xDiT
  GPU_DEVICES               Four space-separated render nodes (default: auto-detect AMD render nodes)
  HIP_DEVICE_ORDER          Logical ROCm device order (default: 0,1,2,3)
  CPUSET_CPUS               Host CPU list for the container (default: all physical cores local to the GPUs)
  MEMORY_NODES              NUMA nodes allowed for container memory (default: all host NUMA nodes)
  NUMA_PREFERRED            Soft NUMA memory preference inside the container (default: auto).
                            Auto picks the allowed node with the most RAM, or the GPU node when
                            memory is within 10%. Empty string disables the preference.
                            Allocations spill to the other allowed nodes, so 512g jobs still fit.
                            If vm.zone_reclaim_mode is 0, a cache-full preferred node spills even
                            when the workload fits. For a sweep, set vm.zone_reclaim_mode=1 and
                            drop the page cache; restore mode 0 afterwards.
  CONTAINER_MEMORY          Container memory limit and swap limit (default: 512g)
  OMP_NUM_THREADS           OpenMP threads per process (default: 32)
  AITER_BUILD_JOBS          Maximum parallel jobs for AITER extension builds (default: 32)
  MIN_FREE_DISK_GB          Warn when / or STATE_DIR has less free space (default: 50)
  WORKLOAD_TIMEOUT_SECONDS  Per-workload timeout for each tuning pass (default: 10800)
  NOFILE_LIMIT              Container open-file limit (default: 65536; required by ROCm expandable segments)
  RCCL_ALGO                 Optional RCCL collective algorithm override (for example: Tree)
  RCCL_PROTO                Optional RCCL protocol override (for example: Simple)
EOF
}

die() {
    printf '%s\n' "$*" >&2
    exit 1
}

usage_error() {
    printf '%s\n' "$*" >&2
    usage >&2
    exit 2
}

trim() {
    local s=$1
    s=${s#"${s%%[![:space:]]*}"}
    s=${s%"${s##*[![:space:]]}"}
    printf '%s' "${s}"
}

require_command() {
    command -v "$1" >/dev/null || die "$1 is required"
}

require_exists() {
    local path=$1
    local message=$2
    [[ -e "${path}" ]] || die "${message}"
}

discover_gpu_devices() {
    local device_path sysfs_path
    for device_path in /dev/dri/renderD*; do
        [[ -e ${device_path} ]] || continue
        sysfs_path=/sys/class/drm/$(basename "${device_path}")/device
        [[ -r ${sysfs_path}/vendor ]] || continue
        [[ $(<"${sysfs_path}/vendor") == 0x1002 ]] || continue
        printf '%s\n' "${device_path}"
    done
}

gpu_count_from_name() {
    local name=$1
    if [[ ${name} =~ (^|[._])([1-4])gpu([._]|$) ]]; then
        printf '%s\n' "${BASH_REMATCH[2]}"
        return 0
    fi
    return 1
}

physical_cpus_on_node() {
    local numa_node=$1
    local cpu_path cpu siblings first_sibling
    for cpu_path in /sys/devices/system/node/node"${numa_node}"/cpu[0-9]*; do
        cpu=${cpu_path##*cpu}
        siblings=$(<"${cpu_path}/topology/thread_siblings_list")
        first_sibling=${siblings%%,*}
        first_sibling=${first_sibling%%-*}
        [[ ${cpu} == "${first_sibling}" ]] && echo "${cpu}"
    done | sort -n
}

comma_join() {
    local IFS=,
    printf '%s\n' "$*"
}

# Bounded so a wedged GPU process cannot stall the rest of the matrix.
remove_container() {
    local name=$1
    [[ -n ${name} ]] || return 0
    timeout 300 docker rm -f "${name}" >/dev/null 2>&1 || true
}

node_memtotal_kb() {
    awk '/MemTotal/ { print $4; exit }' "/sys/devices/system/node/node${1}/meminfo"
}

# Prefer the widest memory controller without binding, so large jobs can spill.
# When NUMA is balanced, keep allocations next to the GPUs.
select_preferred_memory_node() {
    local gpu_node=$1
    local allowed=$2
    local node kb gpu_kb=0 best_node="" best_kb=0
    local -a nodes=()
    IFS=, read -r -a nodes <<< "${allowed}"
    (( ${#nodes[@]} > 0 )) || return 0
    if (( ${#nodes[@]} == 1 )); then
        printf '%s' "${nodes[0]}"
        return 0
    fi
    for node in "${nodes[@]}"; do
        kb=$(node_memtotal_kb "${node}")
        [[ ${node} == "${gpu_node}" ]] && gpu_kb=${kb}
        if (( kb > best_kb )); then
            best_kb=${kb}
            best_node=${node}
        fi
    done
    if (( gpu_kb * 10 >= best_kb * 9 )); then
        printf '%s' "${gpu_node}"
    else
        printf '%s' "${best_node}"
    fi
}

# Print "file<TAB>name" for each experiment tagged rdna4. Callers sort by name
# so the run order is stable regardless of which YAML declares a workload.
list_rdna4_workloads() {
    awk '
        function trim(s) { gsub(/^[[:space:]]+|[[:space:]]+$/, "", s); return s }
        /^[[:space:]]*-[[:space:]]*name:[[:space:]]*/ {
            name = $0
            sub(/^[[:space:]]*-[[:space:]]*name:[[:space:]]*/, "", name)
            name = trim(name)
            next
        }
        name != "" && $1 == "tags:" && $0 ~ /(^|[^[:alnum:]_-])rdna4([^[:alnum:]_-]|$)/ {
            print FILENAME "\t" name
            name = ""
        }
    ' "$@"
}

# Copy one experiment from a YAML file and set num_gpus for run.py.
write_runtime_yaml() {
    local source=$1
    local target=$2
    local gpus=$3
    local dest=$4
    awk -v target="${target}" -v gpus="${gpus}" '
        function trim(s) { gsub(/^[[:space:]]+|[[:space:]]+$/, "", s); return s }
        /^[[:space:]]*-[[:space:]]*name:[[:space:]]*/ {
            if (in_block) {
                if (found) exit
                in_block = 0
            }
            line = $0
            sub(/^[[:space:]]*-[[:space:]]*name:[[:space:]]*/, "", line)
            if (trim(line) == target) {
                found = 1
                in_block = 1
                print
                print "  num_gpus: " gpus
                next
            }
        }
        in_block { print }
        END { if (!found) exit 1 }
    ' "${source}" > "${dest}"
}

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
BASE_IMAGE=diffusion-models-inference:rdna4-candidate
base_image_set=0
skip_completed=0
while (( $# > 0 )); do
    case $1 in
        --skip-completed)
            skip_completed=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            usage_error "Unknown option: $1"
            ;;
        *)
            (( base_image_set == 0 )) || usage_error "Only one BASE_IMAGE may be specified"
            BASE_IMAGE=$1
            base_image_set=1
            ;;
    esac
    shift
done

CONFIG=${CONFIG:-/app/.ci/benchmark_configs}
WORKLOAD_NAMES=${WORKLOAD_NAMES:-}
STATE_DIR=${STATE_DIR:-${HOME}/rdna4-finalise}
HF_CACHE=${HF_CACHE:-${HOME}/huggingface}
INDUCTOR_CACHE=${INDUCTOR_CACHE:-${STATE_DIR}/inductor-cache}
AITER_JIT_CACHE=${AITER_JIT_CACHE:-${STATE_DIR}/aiter-jit}
AITER_TUNE_CACHE=${AITER_TUNE_CACHE:-${STATE_DIR}/aiter-tune}
AITER_ONLINE_TUNE=${AITER_ONLINE_TUNE:-1}
PYTORCH_TUNABLEOP_ENABLED=${PYTORCH_TUNABLEOP_ENABLED:-1}
PYTORCH_TUNABLEOP_TUNING=${PYTORCH_TUNABLEOP_TUNING:-1}
SHARE_TUNABLEOP_RESULTS=${SHARE_TUNABLEOP_RESULTS:-1}
if [[ -n ${GPU_DEVICES:-} ]]; then
    read -r -a devices <<< "${GPU_DEVICES}"
else
    mapfile -t devices < <(discover_gpu_devices)
fi
HIP_DEVICE_ORDER=${HIP_DEVICE_ORDER:-0,1,2,3}
OMP_NUM_THREADS=${OMP_NUM_THREADS:-32}
AITER_BUILD_JOBS=${AITER_BUILD_JOBS:-32}
CONTAINER_MEMORY=${CONTAINER_MEMORY:-512g}
MIN_FREE_DISK_GB=${MIN_FREE_DISK_GB:-50}
WORKLOAD_TIMEOUT_SECONDS=${WORKLOAD_TIMEOUT_SECONDS:-10800}
NOFILE_LIMIT=${NOFILE_LIMIT:-65536}

(( ${#devices[@]} == 4 )) \
    || die "Expected exactly four AMD render nodes, got ${#devices[@]}. Set GPU_DEVICES explicitly."

IFS=, read -r -a hip_devices <<< "${HIP_DEVICE_ORDER}"
(( ${#hip_devices[@]} == ${#devices[@]} )) || {
    die "HIP_DEVICE_ORDER must contain ${#devices[@]} comma-separated device indices"
}

require_command docker
require_command timeout
require_exists /dev/kfd "ROCm device /dev/kfd is required"

gpu_numa_nodes=()
declare -A seen_gpu_numa_nodes=()
for device in "${devices[@]}"; do
    require_exists "${device}" "ROCm device ${device} is required"
    device_numa=$(<"/sys/class/drm/$(basename "${device}")/device/numa_node")
    [[ ${device_numa} != -1 ]] || die "Cannot determine NUMA node for ${device}"
    if [[ -z ${seen_gpu_numa_nodes[${device_numa}]+x} ]]; then
        gpu_numa_nodes+=("${device_numa}")
        seen_gpu_numa_nodes["${device_numa}"]=1
    fi
done
gpu_numa_list=$(comma_join "${gpu_numa_nodes[@]}")

if [[ -n ${CPUSET_CPUS:-} ]]; then
    cpu_list=${CPUSET_CPUS}
else
    physical_cpus=()
    for node in "${gpu_numa_nodes[@]}"; do
        mapfile -t node_cpus < <(physical_cpus_on_node "${node}")
        physical_cpus+=("${node_cpus[@]}")
    done
    (( ${#physical_cpus[@]} > 0 )) || die "Could not find physical cores on GPU NUMA nodes ${gpu_numa_list}"
    cpu_list=$(comma_join "${physical_cpus[@]}")
fi

memory_nodes_default=
for node_path in /sys/devices/system/node/node[0-9]*; do
    node=${node_path##*node}
    memory_nodes_default+="${memory_nodes_default:+,}${node}"
done
MEMORY_NODES=${MEMORY_NODES:-${memory_nodes_default}}
if [[ ${NUMA_PREFERRED+x} ]]; then
    numa_preferred=${NUMA_PREFERRED}
else
    numa_preferred=$(select_preferred_memory_node "${gpu_numa_nodes[0]}" "${MEMORY_NODES}")
fi

echo "Using GPUs: ${devices[*]} (NUMA nodes ${gpu_numa_list}, CPUs ${cpu_list})"
echo "Allowing container memory on NUMA nodes: ${MEMORY_NODES}"
if [[ -n ${numa_preferred} ]]; then
    echo "Preferring host allocations on NUMA node ${numa_preferred} (spills to other allowed nodes)"
else
    echo "NUMA memory preference disabled"
fi
for node_path in /sys/devices/system/node/node[0-9]*; do
    awk '/MemTotal|MemFree/ { printf "node %s %s: %.1f GiB\n", node, $3, $4 / 1024 / 1024 }' \
        node="${node_path##*node}" "${node_path}/meminfo"
done
if [[ -r /proc/sys/kernel/numa_balancing ]] && [[ $(< /proc/sys/kernel/numa_balancing) != 0 ]]; then
    echo "Warning: automatic NUMA balancing is enabled and may reduce multi-GPU performance. Run sysctl -w kernel.numa_balancing=0" >&2
fi
zone_reclaim_mode=unknown
if [[ -r /proc/sys/vm/zone_reclaim_mode ]]; then
    zone_reclaim_mode=$(< /proc/sys/vm/zone_reclaim_mode)
fi
if [[ -n ${numa_preferred} && ${MEMORY_NODES} == *,* && ${zone_reclaim_mode} == 0 ]]; then
    echo "Warning: vm.zone_reclaim_mode=0. A cache-full preferred node will allocate on another node instead of reclaiming local page cache. CPU-offload workloads can still spill after local memory is exhausted. For this sweep, run:" >&2
    echo "  sudo sysctl -w vm.zone_reclaim_mode=1" >&2
    echo "  sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'" >&2
    echo "Restore afterwards with: sudo sysctl -w vm.zone_reclaim_mode=0" >&2
fi

mkdir -p "${STATE_DIR}"/{home,outputs,miopen-db/cache,tunableop} \
    "${HF_CACHE}" "${INDUCTOR_CACHE}/triton" "${AITER_JIT_CACHE}" "${AITER_TUNE_CACHE}"
topology_file="${STATE_DIR}/topology.txt"
topology=$(
    printf 'gpu_devices=%s\n' "${devices[*]}"
    printf 'gpu_numa_nodes=%s\n' "${gpu_numa_list}"
    printf 'hip_device_order=%s\n' "${HIP_DEVICE_ORDER}"
    printf 'cpuset_cpus=%s\n' "${cpu_list}"
    printf 'memory_nodes=%s\n' "${MEMORY_NODES}"
    printf 'numa_preferred=%s\n' "${numa_preferred}"
    printf 'zone_reclaim_mode=%s\n' "${zone_reclaim_mode}"
    printf 'omp_num_threads=%s\n' "${OMP_NUM_THREADS}"
    printf 'rccl_algo=%s\n' "${RCCL_ALGO:-}"
    printf 'rccl_proto=%s\n' "${RCCL_PROTO:-}"
)
if [[ -f ${topology_file} ]]; then
    # zone_reclaim_mode is a transient sysctl that resets on reboot, so it is
    # recorded for provenance but never blocks a resume.
    stored_topology=$(grep -v '^zone_reclaim_mode=' "${topology_file}" || true)
    current_topology=$(printf '%s\n' "${topology}" | grep -v '^zone_reclaim_mode=' || true)
    if [[ ${stored_topology} != "${current_topology}" ]]; then
        die "GPU/NUMA execution topology changed for ${STATE_DIR}. Use a new STATE_DIR or remove its stale benchmark state."
    fi
    stored_reclaim=$(awk -F= '$1 == "zone_reclaim_mode" { print $2 }' "${topology_file}")
    if [[ -n ${stored_reclaim} && ${stored_reclaim} != "${zone_reclaim_mode}" ]]; then
        echo "Warning: vm.zone_reclaim_mode is ${zone_reclaim_mode} but this STATE_DIR was created with ${stored_reclaim}; timings may not be comparable." >&2
    fi
fi
printf '%s\n' "${topology}" > "${topology_file}"
for path in / "${STATE_DIR}"; do
    free_kb=$(df --output=avail "${path}" | tail -n 1)
    if (( free_kb < MIN_FREE_DISK_GB * 1024 * 1024 )); then
        echo "Warning: less than ${MIN_FREE_DISK_GB} GiB free on filesystem containing ${path}." >&2
    fi
done

if [[ -e ${CONFIG} ]]; then
    host_config=${CONFIG}
else
    host_config=${ROOT}/benchmark_configs/xdit
fi

shopt -s nullglob
if [[ -d ${host_config} ]]; then
    mapfile -t config_files < <(printf '%s\n' "${host_config}"/*.yaml | sort)
elif [[ -f ${host_config} ]]; then
    config_files=( "${host_config}" )
else
    die "No benchmark config found at ${host_config}"
fi
shopt -u nullglob
(( ${#config_files[@]} > 0 )) || die "No YAML files in ${host_config}"

declare -A requested_names=()
if [[ -n ${WORKLOAD_NAMES} ]]; then
    IFS=, read -r -a requested_list <<< "${WORKLOAD_NAMES}"
    for name in "${requested_list[@]}"; do
        name=$(trim "${name}")
        [[ -n ${name} ]] && requested_names["${name}"]=1
    done
fi

workload_files=()
workloads=()
workload_gpu_counts=()
declare -A selected_names=()
while IFS=$'\t' read -r file name; do
    [[ -n ${name} ]] || continue
    if (( ${#requested_names[@]} > 0 )) && [[ -z ${requested_names[${name}]+x} ]]; then
        continue
    fi
    gpu_count=$(gpu_count_from_name "${name}") || die "cannot determine GPU count from ${name}"
    workload_files+=("${file}")
    workloads+=("${name}")
    workload_gpu_counts+=("${gpu_count}")
    selected_names["${name}"]=1
done < <(list_rdna4_workloads "${config_files[@]}" | LC_ALL=C sort -t$'\t' -k2,2)

if (( ${#requested_names[@]} > 0 )); then
    missing_names=()
    for name in "${!requested_names[@]}"; do
        [[ -n ${selected_names[${name}]+x} ]] || missing_names+=("${name}")
    done
    if (( ${#missing_names[@]} > 0 )); then
        mapfile -t missing_names < <(printf '%s\n' "${missing_names[@]}" | sort)
        die "requested workloads not found among rdna4 workloads: $(IFS=', '; echo "${missing_names[*]}")"
    fi
fi

(( ${#workloads[@]} > 0 )) || die "No RDNA4 workloads selected"

docker_args=(
    --rm
    --device=/dev/kfd
    --cpuset-cpus="${cpu_list}"
    --cpuset-mems="${MEMORY_NODES}"
    --memory="${CONTAINER_MEMORY}"
    --memory-swap="${CONTAINER_MEMORY}"
    --ulimit "nofile=${NOFILE_LIMIT}:${NOFILE_LIMIT}"
    --ulimit "memlock=-1:-1"
    --ulimit "core=0:0"
    --ipc=host
    --security-opt=seccomp=unconfined
)
for device in "${devices[@]}"; do
    docker_args+=(--device="${device}")
done
for device in /dev/kfd "${devices[@]}"; do
    docker_args+=(--group-add "$(stat -c '%g' "${device}")")
done
docker_args+=(
    --user "$(id -u):$(id -g)"
    -e HOME=/tmp/rdna4-home
    -e "USER=$(id -un)"
    -e "LOGNAME=$(id -un)"
    -e HF_TOKEN
    -e "HIP_VISIBLE_DEVICES=${HIP_DEVICE_ORDER}"
    -e "OMP_NUM_THREADS=${OMP_NUM_THREADS}"
    -e "MAX_JOBS=${AITER_BUILD_JOBS}"
    -e "CMAKE_BUILD_PARALLEL_LEVEL=${AITER_BUILD_JOBS}"
    -e HF_HOME=/hf-cache
    -e MIOPEN_USER_DB_PATH=/miopen-db
    -e MIOPEN_CUSTOM_CACHE_DIR=/miopen-db/cache
    -e MIOPEN_FIND_MODE=NORMAL
    -e MIOPEN_FIND_ENFORCE=SEARCH
    -e AITER_JIT_DIR=/aiter-jit
    -e "AITER_ONLINE_TUNE=${AITER_ONLINE_TUNE}"
    -e AITER_TUNE_DIR=/aiter-tune
    -e TORCHINDUCTOR_CACHE_DIR=/inductor-cache
    -e TRITON_CACHE_DIR=/inductor-cache/triton
    -e TORCHINDUCTOR_FX_GRAPH_CACHE=1
    -e TORCHINDUCTOR_AUTOGRAD_CACHE=1
    -e "PYTORCH_TUNABLEOP_ENABLED=${PYTORCH_TUNABLEOP_ENABLED}"
    -e "PYTORCH_TUNABLEOP_TUNING=${PYTORCH_TUNABLEOP_TUNING}"
    -e PYTORCH_TUNABLEOP_FILENAME=/tunableop/tunableop_results.csv
    -v "${STATE_DIR}/home:/tmp/rdna4-home"
    -v "${HF_CACHE}:/hf-cache"
    -v "${STATE_DIR}/outputs:/outputs"
    -v "${STATE_DIR}/miopen-db:/miopen-db"
    -v "${AITER_JIT_CACHE}:/aiter-jit"
    -v "${AITER_TUNE_CACHE}:/aiter-tune"
    -v "${INDUCTOR_CACHE}:/inductor-cache"
    -v "${STATE_DIR}/tunableop:/tunableop"
    -v "${ROOT}/scripts/rdna4/numa_preferred_exec.py:/tmp/numa_preferred_exec.py:ro"
)
if [[ -n ${XDIT_SRC:-} ]]; then
    docker_args+=(
        -v "${XDIT_SRC}:/app/xDiT"
        -e PYTHONPATH=/app/xDiT
    )
    echo "Bind-mounting xDiT from ${XDIT_SRC}"
fi
if [[ -n ${numa_preferred} ]]; then
    docker_args+=(-e "NUMA_PREFERRED=${numa_preferred}")
fi

[[ -n ${RCCL_ALGO:-} ]] && docker_args+=(-e "NCCL_ALGO=${RCCL_ALGO}")
[[ -n ${RCCL_PROTO:-} ]] && docker_args+=(-e "NCCL_PROTO=${RCCL_PROTO}")
[[ -n ${XDIT_PIPELINE_TIMEOUT_MINUTES:-} ]] && \
    docker_args+=(-e "XDIT_PIPELINE_TIMEOUT_MINUTES=${XDIT_PIPELINE_TIMEOUT_MINUTES}")
[[ -n ${XDIT_LOG_PIPEFUSION_CACHE_DECISIONS:-} ]] \
    && docker_args+=(-e "XDIT_LOG_PIPEFUSION_CACHE_DECISIONS=${XDIT_LOG_PIPEFUSION_CACHE_DECISIONS}")

docker run "${docker_args[@]}" "${BASE_IMAGE}" python3 /tmp/numa_preferred_exec.py python3 -c \
    "import torch; n=torch.cuda.device_count(); arches={torch.cuda.get_device_properties(i).gcnArchName.split(':')[0] for i in range(n)}; print(f'GPUs: {n}, arches: {sorted(arches)}'); assert n >= 4, 'the RDNA4 matrix requires at least four GPUs'; assert arches == {'gfx1201'}, f'expected only gfx1201 GPUs, got {arches}'"

if [[ ${SHARE_TUNABLEOP_RESULTS} == 1 ]]; then
    shopt -s nullglob
    tunable_files=( "${STATE_DIR}/tunableop/"*.csv )
    shopt -u nullglob
    if (( ${#tunable_files[@]} > 0 )); then
        runtime_merge_dir=${STATE_DIR}/merged-runtime
        python3 "${ROOT}/scripts/rdna4/merge_rdna4_tuning.py" \
            --tunable-dir "${STATE_DIR}/tunableop" \
            --aiter-dir "${AITER_TUNE_CACHE}" \
            --output-dir "${runtime_merge_dir}"
        merged_tunable=${runtime_merge_dir}/tunableop_results_merged.csv
        if [[ -s ${merged_tunable} ]]; then
            for ordinal in "${!devices[@]}"; do
                cp "${merged_tunable}" "${STATE_DIR}/tunableop/tunableop_results${ordinal}.csv"
            done
            echo "Seeded ${#devices[@]} TunableOp rank files from merged identical-GPU results"
        fi
    fi
fi

# Ctrl-C (or SIGTERM) stops the whole matrix, not just the running workload.
# The container is daemon-managed and outlives its docker CLI, so kill it by name.
current_container=
stop_matrix() {
    local status=$1
    trap - INT TERM HUP
    if [[ -n ${current_container} ]]; then
        echo >&2
        echo "Interrupted; killing container ${current_container}" >&2
        docker kill "${current_container}" >/dev/null 2>&1 || true
    fi
    exit "${status}"
}
trap 'stop_matrix 130' INT
trap 'stop_matrix 143' TERM
# A dropped terminal must not leave a container holding every GPU.
trap 'stop_matrix 129' HUP

failed_workloads=()
for i in "${!workloads[@]}"; do
    workload=${workloads[$i]}
    source_yaml=${workload_files[$i]}
    workload_gpu_count=${workload_gpu_counts[$i]}
    workload_hip_devices=$(comma_join "${hip_devices[@]:0:workload_gpu_count}")
    workload_output_dir=${STATE_DIR}/outputs/${workload}
    timings_path=${workload_output_dir}/timings.json

    if (( skip_completed )) && [[ -f "${timings_path}" ]]; then
        echo "Skipping completed workload $((i + 1))/${#workloads[@]}: ${workload} (${timings_path})"
        continue
    fi

    mkdir -p "${workload_output_dir}"
    if ! write_runtime_yaml "${source_yaml}" "${workload}" "${workload_gpu_count}" "${workload_output_dir}/config.yaml"; then
        failed_workloads+=("${workload}")
        echo "Failed to extract ${workload} from ${source_yaml}; continuing with the next workload" >&2
        continue
    fi

    echo
    echo "Running workload $((i + 1))/${#workloads[@]} on HIP devices ${workload_hip_devices} in a fresh container: ${workload}"
    current_container=rdna4-tune-${workload}
    # A container left behind by an earlier interrupted run would make
    # docker run fail with a name conflict.
    remove_container "${current_container}"
    # --foreground keeps timeout in this process group so terminal signals reach it;
    # waiting on a background job lets bash run the signal traps immediately.
    timeout --foreground --signal=TERM --kill-after=15 "${WORKLOAD_TIMEOUT_SECONDS}" \
        docker run "${docker_args[@]}" \
        --name "${current_container}" \
        -e "HIP_VISIBLE_DEVICES=${workload_hip_devices}" \
        "${BASE_IMAGE}" \
        python3 /tmp/numa_preferred_exec.py python3 /app/.ci/run.py --no-clear-model-cache --tag rdna4 \
        --name "${workload}" \
        --results-directory /outputs --csv-output-path /outputs/results.csv \
        --print-timing-summary \
        --collect-memory-stats \
        "/outputs/${workload}/config.yaml" &
    status=0
    wait $! || status=$?
    # timeout(1) terminates the foreground docker CLI, but the daemon-managed
    # named container can survive it and retain all GPU memory. Always remove
    # a surviving workload container before starting the next supposedly
    # fresh cell.
    remove_container "${current_container}"
    current_container=
    if (( status != 0 )) \
        && [[ "${workload_output_dir}/profile_trace_rank_0.json.gz" \
            -nt "${workload_output_dir}/config.yaml" ]]; then
        echo "Profiler trace completed for ${workload}; no timings.json is expected in profile mode."
        status=0
    fi

    case ${status} in
        0)
            ;;
        124)
            failed_workloads+=("${workload}")
            echo "Workload timed out after ${WORKLOAD_TIMEOUT_SECONDS}s; continuing with a fresh container: ${workload}" >&2
            ;;
        130|143)
            current_container=rdna4-tune-${workload}
            stop_matrix "${status}"
            ;;
        *)
            failed_workloads+=("${workload}")
            echo "Workload failed; continuing with a fresh container: ${workload}" >&2
            ;;
    esac
done

if (( ${#failed_workloads[@]} > 0 )); then
    echo "Failed workloads:" >&2
    printf '  %s\n' "${failed_workloads[@]}" >&2
    exit 1
fi
