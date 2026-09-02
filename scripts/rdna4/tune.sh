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
  GPU_DEVICES               Four space-separated render nodes (default: /dev/dri/renderD130 /dev/dri/renderD132 /dev/dri/renderD131 /dev/dri/renderD133)
  HIP_DEVICE_ORDER          Logical ROCm device order (default: 0,2,1,3)
  CPUSET_CPUS               Host CPU list for the container (default: all physical cores on GPU NUMA node)
  MEMORY_NODES              NUMA nodes allowed for container memory (default: all host NUMA nodes)
  CONTAINER_MEMORY          Container memory limit and swap limit (default: 512g)
  OMP_NUM_THREADS           OpenMP threads per process (default: 32)
  AITER_BUILD_JOBS          Maximum parallel jobs for AITER extension builds (default: 32)
  MIN_FREE_DISK_GB          Warn when / or STATE_DIR has less free space (default: 50)
  WORKLOAD_TIMEOUT_SECONDS  Per-workload timeout for each tuning pass (default: 7200)
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

# Print "file<TAB>name" for each experiment tagged rdna4.
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
GPU_DEVICES=${GPU_DEVICES:-"/dev/dri/renderD130 /dev/dri/renderD132 /dev/dri/renderD131 /dev/dri/renderD133"}
HIP_DEVICE_ORDER=${HIP_DEVICE_ORDER:-0,2,1,3}
OMP_NUM_THREADS=${OMP_NUM_THREADS:-32}
AITER_BUILD_JOBS=${AITER_BUILD_JOBS:-32}
CONTAINER_MEMORY=${CONTAINER_MEMORY:-512g}
MIN_FREE_DISK_GB=${MIN_FREE_DISK_GB:-50}
WORKLOAD_TIMEOUT_SECONDS=${WORKLOAD_TIMEOUT_SECONDS:-7200}
NOFILE_LIMIT=${NOFILE_LIMIT:-65536}

read -r -a devices <<< "${GPU_DEVICES}"
(( ${#devices[@]} == 4 )) || die "GPU_DEVICES must contain exactly four render nodes"

IFS=, read -r -a hip_devices <<< "${HIP_DEVICE_ORDER}"
(( ${#hip_devices[@]} == ${#devices[@]} )) || {
    die "HIP_DEVICE_ORDER must contain ${#devices[@]} comma-separated device indices"
}

require_command docker
require_command timeout
require_exists /dev/kfd "ROCm device /dev/kfd is required"

numa_node=
for device in "${devices[@]}"; do
    require_exists "${device}" "ROCm device ${device} is required"
    device_numa=$(<"/sys/class/drm/$(basename "${device}")/device/numa_node")
    [[ ${device_numa} != -1 ]] || die "Cannot determine NUMA node for ${device}"
    if [[ -n ${numa_node} && ${device_numa} != "${numa_node}" ]]; then
        die "All GPU_DEVICES must be on one NUMA node; ${device} is on node ${device_numa}, expected ${numa_node}"
    fi
    numa_node=${device_numa}
done

if [[ -n ${CPUSET_CPUS:-} ]]; then
    cpu_list=${CPUSET_CPUS}
else
    mapfile -t physical_cpus < <(physical_cpus_on_node "${numa_node}")
    (( ${#physical_cpus[@]} > 0 )) || die "Could not find physical cores on NUMA node ${numa_node}"
    cpu_list=$(comma_join "${physical_cpus[@]}")
fi

memory_nodes_default=
for node_path in /sys/devices/system/node/node[0-9]*; do
    node=${node_path##*node}
    memory_nodes_default+="${memory_nodes_default:+,}${node}"
done
MEMORY_NODES=${MEMORY_NODES:-${memory_nodes_default}}

echo "Using GPUs: ${devices[*]} (NUMA node ${numa_node}, CPUs ${cpu_list})"
echo "Allowing container memory on NUMA nodes: ${MEMORY_NODES}"
for node_path in /sys/devices/system/node/node[0-9]*; do
    awk '/MemTotal|MemFree/ { printf "node %s %s: %.1f GiB\n", node, $3, $4 / 1024 / 1024 }' \
        node="${node_path##*node}" "${node_path}/meminfo"
done
if [[ -r /proc/sys/kernel/numa_balancing ]] && [[ $(< /proc/sys/kernel/numa_balancing) != 0 ]]; then
    echo "Warning: automatic NUMA balancing is enabled and may reduce multi-GPU performance. Run sysctl -w kernel.numa_balancing=0" >&2
fi

mkdir -p "${STATE_DIR}"/{home,outputs,miopen-db/cache,aiter-jit,aiter-tune,inductor-cache/triton,tunableop} "${HF_CACHE}"
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
done < <(list_rdna4_workloads "${config_files[@]}")

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
    -e AITER_ONLINE_TUNE=1
    -e AITER_TUNE_DIR=/aiter-tune
    -e TORCHINDUCTOR_CACHE_DIR=/inductor-cache
    -e TRITON_CACHE_DIR=/inductor-cache/triton
    -e TORCHINDUCTOR_FX_GRAPH_CACHE=1
    -e TORCHINDUCTOR_AUTOGRAD_CACHE=1
    -e PYTORCH_TUNABLEOP_ENABLED=1
    -e PYTORCH_TUNABLEOP_TUNING=1
    -e PYTORCH_TUNABLEOP_FILENAME=/tunableop/tunableop_results.csv
    -v "${STATE_DIR}/home:/tmp/rdna4-home"
    -v "${HF_CACHE}:/hf-cache"
    -v "${STATE_DIR}/outputs:/outputs"
    -v "${STATE_DIR}/miopen-db:/miopen-db"
    -v "${STATE_DIR}/aiter-jit:/aiter-jit"
    -v "${STATE_DIR}/aiter-tune:/aiter-tune"
    -v "${STATE_DIR}/inductor-cache:/inductor-cache"
    -v "${STATE_DIR}/tunableop:/tunableop"
)

[[ -n ${RCCL_ALGO:-} ]] && docker_args+=(-e "NCCL_ALGO=${RCCL_ALGO}")
[[ -n ${RCCL_PROTO:-} ]] && docker_args+=(-e "NCCL_PROTO=${RCCL_PROTO}")

docker run "${docker_args[@]}" "${BASE_IMAGE}" python3 -c \
    "import torch; n=torch.cuda.device_count(); arches={torch.cuda.get_device_properties(i).gcnArchName.split(':')[0] for i in range(n)}; print(f'GPUs: {n}, arches: {sorted(arches)}'); assert n >= 4, 'the RDNA4 matrix requires at least four GPUs'; assert arches == {'gfx1201'}, f'expected only gfx1201 GPUs, got {arches}'"

# Ctrl-C (or SIGTERM) stops the whole matrix, not just the running workload.
# The container is daemon-managed and outlives its docker CLI, so kill it by name.
current_container=
stop_matrix() {
    local status=$1
    trap - INT TERM
    if [[ -n ${current_container} ]]; then
        echo >&2
        echo "Interrupted; killing container ${current_container}" >&2
        docker kill "${current_container}" >/dev/null 2>&1 || true
    fi
    exit "${status}"
}
trap 'stop_matrix 130' INT
trap 'stop_matrix 143' TERM

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
    write_runtime_yaml "${source_yaml}" "${workload}" "${workload_gpu_count}" "${workload_output_dir}/config.yaml" \
        || die "failed to extract ${workload} from ${source_yaml}"

    echo
    echo "Running workload $((i + 1))/${#workloads[@]} on HIP devices ${workload_hip_devices} in a fresh container: ${workload}"
    current_container=rdna4-tune-${workload}
    # --foreground keeps timeout in this process group so terminal signals reach it;
    # waiting on a background job lets bash run the signal traps immediately.
    timeout --foreground --signal=TERM --kill-after=15 "${WORKLOAD_TIMEOUT_SECONDS}" \
        docker run "${docker_args[@]}" \
        --name "${current_container}" \
        -e "HIP_VISIBLE_DEVICES=${workload_hip_devices}" \
        "${BASE_IMAGE}" \
        python3 /app/.ci/run.py --no-clear-model-cache --tag rdna4 \
        --name "${workload}" \
        --results-directory /outputs --csv-output-path /outputs/results.csv \
        --print-timing-summary \
        "/outputs/${workload}/config.yaml" &
    status=0
    wait $! || status=$?
    current_container=

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
