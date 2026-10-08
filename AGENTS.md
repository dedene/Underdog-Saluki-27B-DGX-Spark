# AGENTS.md

Serve Underdog Saluki 27B 1.0 (IQ2-mix GGUF, 7.89 GB, Qwen3.8-27B) on one DGX Spark with stock llama.cpp via Docker Compose.

## Layout
- `compose.yaml`: `download` (one-shot curl of the pinned revision, sha256 checked) -> `saluki` (llama-server, healthcheck).
  Knobs are `${VAR:-default}` plus `tools/serve.sh`. `.env.example` documents them.
- `tools/download.sh`, `tools/serve.sh`: fetch and launch. The image is the official arm64 CUDA 13 build, so there is no Dockerfile.
- `tools/check.py`: functional checks (+ `--needle N`). `tools/bench.py`: `/completion` timings, no chat template.

## Spark
- Same box as `~/Development/Kolibri-1-DGX-Spark` (`ssh zenjoy@10.0.0.158`). Checkout at `~/Development/Underdog-Saluki-27B-DGX-Spark`.
- GB10, sm_121, aarch64, 128 GB unified, driver 580, CUDA 13, Compose v5.
- Other model repos in `~/Development` default to port 8888 too: stop them first.

## Verify
- `docker compose config --quiet`, `shellcheck tools/*.sh`, `python3 -m py_compile tools/*.py`
- `docker compose up -d --wait`, then `tools/check.py` (all ok) and `tools/bench.py`.

## Image
- `ghcr.io/ggml-org/llama.cpp:server-cuda13` at digest `sha256:695214dcb4d4062014335702caee98836bbf92c5974b78331c70e152176cbf08`.
- llama.cpp b11459, commit `f498f864f`, built 2026-10-07. `--list-devices` reports `CUDA0: NVIDIA GB10` (124546 MiB).
- Weights revision `1336c0b5d74dfe6ad7f793577092f5effd9f97c6`. Main file sha256 `4a673518b11b1c4445f9b9a9d3c40356f5ba39f6a6260dcb25f3fbfd475d9efb`.

## Findings (2026-10-08)
- `tools/check.py` passed: model id, thinking off (`42`, no reasoning), thinking on (`15.00`, 978 chars of reasoning), one tool call, streaming.
- `tools/bench.py`: decode 22.6 tok/s (256 out), prefill 756 tok/s at 7,363 tokens and 706 tok/s at 29,485. CUDA graphs reused. `nvidia-smi` utilization stays N/A on this UMA box.
- Listening about 3 s after start once the file is local. `n_slots = 1`, `n_ctx_slot = 262144`. About 87 GiB still available with the other containers up.
- `download.sh` must end with `exit 0`. A trailing `[[ -n $MMPROJ ]] && fetch` returns 1 when vision is off, and Compose then refuses to start the server.
- Disable the image healthcheck on `download`. It probes port 8080, which this service never opens.
- The image sets `LLAMA_ARG_HOST`. `serve.sh` unsets it because the script passes `--host`.

## Constraints
- Saluki is a standard GGUF. Do not wrap it in vLLM or a custom llama.cpp fork; the model card's runtime is stock llama.cpp.
- Leave `REASONING_EFFORT` unset unless a server-wide default is the goal. The template default is `xhigh`. `enable_thinking: false` is how a request turns thinking off.
- Vision is opt-in via `MMPROJ`. The text server does not load a projector.
- Credits: write our own scripts. Kolibri-1-DGX-Spark is the pattern; do not copy its vLLM flags onto this server.
