# Underdog Saluki 27B on a single DGX Spark (llama.cpp, Docker Compose)

Serve [Underdog Saluki 27B 1.0](https://huggingface.co/ConwayResearch/Underdog-Saluki-27B-1.0) on one NVIDIA DGX Spark with stock llama.cpp. One command downloads the pinned GGUF and starts an OpenAI-compatible API.

Saluki is a 2-bit GGUF of Qwen3.8-27B, 7.89 GB, built by [Underdog](https://underdog.ai/saluki) on top of the ISTA-DASLab quant. The model card's runtime is stock llama.cpp: the chat template handles tool calls and thinking, and the server speaks the OpenAI chat API. On a Spark the weights are a small slice of the 128 GB unified memory, so the native 262,144-token context fits with the whole model on the GPU.

**Hardware:** DGX Spark (GB10, sm_121, aarch64), driver 580, CUDA 13, Docker with the NVIDIA Container Toolkit and Compose v2. About 10 GB free disk for the weights, plus the llama.cpp image.

## Quickstart

```bash
git clone https://github.com/dedene/Underdog-Saluki-27B-DGX-Spark.git && cd Underdog-Saluki-27B-DGX-Spark
docker compose up -d --wait     # download 7.89 GB (first run only), load, wait until healthy
tools/check.py                  # thinking switch, tool call, streaming
docker compose down
```

`up --wait` returns once `/health` answers. `docker compose logs -f download` shows the fetch; `docker compose logs -f saluki` shows the load. A later start skips the download.

The API is at `http://<spark-ip>:8888/v1`, model `Underdog-Saluki-27B`. llama-server also serves its own chat UI at `http://<spark-ip>:8888`.

```bash
curl http://<spark-ip>:8888/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "Underdog-Saluki-27B",
  "messages": [{"role": "user", "content": "Explain in three sentences what a 2-bit quant keeps and what it spends."}],
  "chat_template_kwargs": {"reasoning_effort": "medium"}
}'
```

Thinking is on by default at effort `xhigh`, which is the Qwen3.8 template default. The trace comes back in `message.reasoning_content` and the answer in `message.content`. Sampling defaults are the model card's general-use preset: temperature 0.6, top_p 0.95, top_k 20.

For a fast tool call, turn thinking off and set temperature 0 on that request:

```bash
curl http://<spark-ip>:8888/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "Underdog-Saluki-27B",
  "temperature": 0,
  "chat_template_kwargs": {"enable_thinking": false},
  "tools": [{"type": "function", "function": {"name": "create_event", "description": "Create a calendar event.",
    "parameters": {"type": "object", "required": ["title", "date"], "properties": {
      "title": {"type": "string"}, "date": {"type": "string"}}}}}],
  "messages": [{"role": "user", "content": "Add a calendar event titled Sprint Review on 12 March 2027."}]
}'
```

## How it is put together

- **Image:** `ghcr.io/ggml-org/llama.cpp:server-cuda13`, pinned to llama.cpp b11459 (`f498f864f`). That build is the official arm64 CUDA 13 image. On this Spark, `llama-server --list-devices` reports `CUDA0: NVIDIA GB10` with the full unified memory, so the published image is the runtime.
- **`tools/download.sh`:** curl the pinned revision into `~/.cache/underdog-saluki`, resume if interrupted, and check the Hugging Face LFS sha256. A stamp file makes the next start a no-op.
- **`tools/serve.sh`:** `llama-server` with `--jinja`, `--reasoning-format deepseek`, flash attention on, every layer on the GPU, and the model card's sampling preset.
- **`compose.yaml`:** the `download` service runs to completion, then `saluki` serves offline from that directory. A healthcheck hits `/health`.
- Nothing is installed on the host.

## Configuration

Copy `.env.example` to `.env` and uncomment what you want to change, then `docker compose up -d --wait` again (Compose recreates the container when settings change).

| Variable | Default | |
|---|---|---|
| `CONTEXT` | 262144 | Tokens per sequence. The GGUF's native window. `32768` is the model card's laptop example. |
| `PARALLEL` | 1 | Server slots. |
| `NGL` | all | Layers on the GPU. |
| `TEMP`, `TOP_P`, `TOP_K`, `MIN_P` | 0.6, 0.95, 20, 0 | General-use sampling. A request overrides them. |
| `REASONING_EFFORT` | (template default: xhigh) | Server-wide `low`, `medium`, or `xhigh`. Unset lets each request pass `reasoning_effort` or `enable_thinking`. |
| `MMPROJ` | (empty, text only) | `mmproj-Underdog-Saluki-27B-1.0-Q8_0.gguf` (629 MB) or `-F16.gguf` (928 MB). |
| `PORT`, `HOST`, `SERVED_NAME` | 8888, 0.0.0.0, Underdog-Saluki-27B | |
| `API_KEY` | (empty) | Bearer token, when set. |
| `MODEL_DIR` | `~/.cache/underdog-saluki` | Weights. |
| `LLAMA_CPP_IMAGE` | b11459 digest | Another llama.cpp image, when you bump it. Re-run `tools/check.py` after a bump. |

For any other llama-server flag, append it to the `saluki` service `command:` list. `tools/serve.sh` forwards those arguments.

## Numbers

One DGX Spark, llama.cpp b11459, IQ2-mix weights, f16 KV, flash attention on, context 262144, one slot. Measured 8 October 2026 with `tools/bench.py`, which calls `/completion` with `ignore_eos`. The figure is the raw model, with no chat template and no thinking. llama-server reused CUDA graphs during the run.

| | Throughput |
|---|---|
| Decode, 1 stream, 145 in / 256 out | **22.6 tok/s** |
| Prefill, 7,363 tokens | 756 tok/s |
| Prefill, 29,485 tokens | 706 tok/s |

Once the GGUF is on disk, the server reports the model loaded and listening in about 3 seconds. On this network the 7.89 GB file took 2 minutes 7 seconds, then a sha256 check. With the server up, and the other containers already on the box, about 87 GiB of unified memory was still available.

The file is 7.89 GB, so a full read of the weights once per token at the Spark's 273 GB/s is about 35 tok/s. 22.6 tok/s is the measured decode.

## Notes

- **Thinking and tools.** The model card's fast tool-call preset is thinking off and temperature 0. With thinking on, Saluki often reasons at length before it answers. Per request, `chat_template_kwargs.reasoning_effort` is `xhigh` (default), `medium`, or `low`, and `enable_thinking: false` closes the think block immediately.
- **Parallel tool calls.** Underdog's write-up says about a fifth of multi-call replies have small formatting slips. A single function call is the case this server is aimed at. `tools/check.py` covers that case.
- **Vision.** The main file is text. Set `MMPROJ` to one of the two projector files in the same repo and bring the stack up again. The download service fetches it, and `serve.sh` passes `--mmproj`.
- **Port 8888** is a common default for Spark model servers. If another one is running, stop it or set `PORT`.
- **The API is open on the LAN.** llama-server warns when no API key is set. Set `API_KEY` to require a bearer token.
- **Memory.** The weights are 7.89 GB. Keep other GPU jobs in mind if you raise `CONTEXT` past the native window or raise `PARALLEL`, because llama-server allocates the context it is given.

## Credits

- [Underdog](https://underdog.ai/saluki) for Saluki 27B 1.0 (Apache-2.0). The weights are not in this repository.
- The Qwen team for Qwen3.8-27B, and [ISTA-DASLab](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF) for the quant Saluki is built on. Both Apache-2.0.
- The [llama.cpp](https://github.com/ggml-org/llama.cpp) project, including the official CUDA 13 image.
- The shape of this repository follows [Kolibri-1-DGX-Spark](https://github.com/dedene/Kolibri-1-DGX-Spark): one Compose project, a pinned download, and a small check.

## License

MIT, see [LICENSE](LICENSE). The model weights are licensed by their authors under Apache-2.0.
