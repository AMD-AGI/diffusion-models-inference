# vLLM Omni Integration

`online_runner.py` benchmarks vLLM Omni in online serving mode — it starts the
vllm-omni server, waits for readiness, and sends requests to the appropriate
endpoint (`/v1/images/generations`, `/v1/images/edits`, or `/v1/videos/sync`)
depending on the benchmark config. It writes per-iteration wall times to
`timings.json`.
