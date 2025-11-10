
#!/bin/bash

OPTIONS=$(getopt -o w:v --long workload:,verbose -- "$@")

if [ $? -ne 0 ]; then
  echo "Failed to parse options." >&2
  exit 1
fi

eval set -- "$OPTIONS"

# Parse options
while true; do
  case "$1" in
    -w|--workload)
      WORKLOAD="$2"
      shift 2
      ;;
    -v|--verbose)
      VERBOSE=true
      shift
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

SCRIPT="/app/.ci/run.${WORKLOAD}.sh"

if [ ! -e $SCRIPT ]; then
    echo "'$SCRIPT' not found" >&2
    exit 1
fi

# set HF_TOKEN
export HF_TOKEN=$MAD_SECRETS_HFTOKEN

# temporary fix to handle host ROCm version <6.4.2
export HSA_NO_SCRATCH_RECLAIM=1

# run workload
echo "Run instructions:"
bash $SCRIPT --help
echo "Run configurations:"
bash $SCRIPT --mad --dry-run
RECORDS=$(bash $SCRIPT --mad)

if [ $? -ne 0 ]; then
  echo "Failed to run workload" >&2
  exit 1
fi

if [ $VERBOSE ]; then
    echo "$RECORDS"
fi

# save results
echo -e "model,performance,metric" > ../results.csv
echo -e "$RECORDS"  >> ../results.csv
