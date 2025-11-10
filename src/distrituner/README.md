# Distributed tuning tools (MIOpen, hipblaslt-bench)

These tools help distribute work across multiple GPUs in parallel.


## MIOpen tuning
Set `HIP_VISIBLE_DEVICES` to control which devices are used for tuning. Additionally, you need a file containing MIOpenDriver commands for the tuning you want to execute.

Example:
```
HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 python miopen_tuner.py drivercmds.txt
```

The MIOpen database files are written to `./tuning/device{i}/`, where `{i}` indicates device index. The target directory can be controlled by setting `--tuning-output-path [dir]`.

The tuner writes log files for each tuning task to folder `./logs`, this can be controlled by the argument `--log-dir [dir]`.

The arguments `--miopen-find-mode [int]` and `--miopen-find-enforce [int]` control each GPU worker's `MIOPEN_FIND_MODE` and `MIOPEN_FIND_ENFORCE` environment variables, respectively.
 