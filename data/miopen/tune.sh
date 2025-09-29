#!/bin/bash

# glob, concatenate and retain unique MIOpen driver commands
echo "Extracting workload MIOpenDriver calls"
cat /app/diffusion-models-inference/src/*/miopen/drivercmd/*.txt | sort | uniq > drivercmd.txt


# find MIOpenDriver executable
echo "Searching for MIOpenDriver executable"
miopendriver_path=$(find / -type f -name MIOpenDriver -executable 2> /dev/null | head -n 1)

if [ -z "$miopendriver_path" ]; then
    echo "Executable MIOpenDriver not found."
    exit 1
fi

# create a symbolic link to /bin
echo "Creating a symbolic link to MIOpenDriver"
ln -s "$miopendriver_path" /bin/MIOpenDriver

# run MIOpen tuning
echo "Executing MIOpen tuning"
bash drivercmd.txt

# change permissions to host user
chown -R $HOST_UID:$HOST_UID /app/diffusion-models-inference/data/miopen/userdb