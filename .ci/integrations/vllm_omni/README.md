# vLLM Omni Integration

The `online_runner.py` in this folder benchmarks vLLM Omni in online serving mode — it starts the vllm-omni server, waits for readiness, and sends requests to the appropriate endpoint (`/v1/images/generations`, `/v1/images/edits`, or `/v1/videos/sync`) depending on the benchmark config. It uses the same CLI args as xDiT and writes per-iteration wall times to `timings.json`.

`runner.py` is an alternative that runs vLLM Omni in-process via the Python API instead of through the server.
