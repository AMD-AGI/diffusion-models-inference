# MIOpen user database

Public `amdsiloai/pytorch-xdit` images need buildin MIOpen user databases for reaching
maximal benchmark performance on selected example workloads. Here you find instructions
for generating these user databases as well as pre-created databases to be used with
the public images.

## Use

Point

```
MIOPEN_USER_DB_PATH
```

environment variable to the directory cotaining the user database.

## Generate

From the repository root, run

```
docker build -f docker/Dockerfile[.ci] -t pytorch_xdit_core --target core .
```

to build the base image with the default build arguments. For the repository root, run

````
docker run \
    --cap-add=SYS_PTRACE \
    --security-opt seccomp=unconfined --privileged \
    --device=/dev/kfd --device=/dev/dri \
    --group-add video \
    --ipc=host --network host \
    --user root \
    --shm-size 128G \
    --rm \
    --mount type=bind,src=.,dst=/app/diffusion-models-inference \
    -e HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
    -e OMP_NUM_THREADS=16 \
    -e MIOPEN_USER_DB_PATH=/app/diffusion-models-inference/data/miopen/userdb \
    -e MIOPEN_FIND_MODE=1 \
    -e MIOPEN_FIND_ENFORCE=3 \
    -e HOST_UID=$(id -u) \
    pytorch_xdit_core \
    bash /app/diffusion-models-inference/data/miopen/tune.sh
```

to generate the user database.