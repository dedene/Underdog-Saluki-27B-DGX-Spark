# Underdog Saluki 27B on a single DGX Spark (llama.cpp, Docker Compose)

Serve [Underdog Saluki 27B 1.0](https://huggingface.co/ConwayResearch/Underdog-Saluki-27B-1.0) on one NVIDIA DGX Spark with stock llama.cpp. One command downloads the pinned GGUF and a speculative drafter, and starts an OpenAI-compatible API at about 53 tok/s on mixed chat work (24 without the drafter).

Saluki is a 2-bit GGUF of Qwen3.8-27B, 7.89 GB, built by [Underdog](https://underdog.ai/saluki) on top of the ISTA-DASLab quant. The model card's runtime is stock llama.cpp: the chat template handles tool calls and thinking, and the server speaks the OpenAI chat API. On a Spark the weights are a small slice of the 128 GB unified memory, so the native 262,144-token context fits with the whole model on the GPU.

**Hardware:** DGX Spark (GB10, sm_121, aarch64), driver 580, CUDA 13, Docker with the NVIDIA Container Toolkit and Compose v2. About 10 GB free disk for the weights and drafter, plus the llama.cpp image.

## Quickstart

```bash
git clone https://github.com/dedene/Underdog-Saluki-27B-DGX-Spark.git && cd Underdog-Saluki-27B-DGX-Spark
docker compose up -d --wait     # download 9 GB (first run only), load, wait until healthy
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

- **Image:** `ghcr.io/ggml-org/llama.cpp:server-cuda13`, pinned to llama.cpp b11515 (`3d65c90d0`). That build is the official arm64 CUDA 13 image. It has DFlash and MTP drafting built in, and the DFlash output-head fix (#30111) that b11459 lacks. On this Spark, `llama-server --list-devices` reports `CUDA0: NVIDIA GB10` with the full unified memory, so the published image is the runtime.
- **`tools/download.sh`:** curl the pinned revision, and the drafter `SPEC` needs, into `~/.cache/underdog-saluki`. Resume if interrupted, and check the Hugging Face LFS sha256. A stamp file makes the next start a no-op.
- **`tools/serve.sh`:** `llama-server` with `--jinja`, `--reasoning-format deepseek`, flash attention on, every layer on the GPU, the model card's sampling preset, and the `SPEC` drafter.
- **Speculative decoding:** a small drafter proposes a few tokens and Saluki checks them in one pass, so one weight read yields several tokens. The default pairs z-lab's [DFlash2 drafter](https://huggingface.co/z-lab/Qwen3.8-27B-DFlash2-GGUF) for Qwen3.8-27B (Q4_K_M, 1.14 GB) with llama.cpp's `ngram-mod`, which drafts text the reply repeats from the prompt. Saluki is a quant of Qwen3.8-27B, so the base model's drafter fits.
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
| `SPEC` | dflash | `dflash`, `mtp`, or `none`. See [Numbers](#numbers). `mtp` uses [Kujira's Saluki file](https://huggingface.co/Kujira/Underdog-Saluki-27B-1.0-MTP-GGUF) with Qwen3.8's MTP head grafted on (8.35 GB, main tensors byte-identical). |
| `SPEC_DRAFT_MAX` | 4 (dflash), 3 (mtp) | Tokens drafted per step. Longer drafts help code and hurt prose. |
| `MMPROJ` | (empty, text only) | `mmproj-Underdog-Saluki-27B-1.0-Q8_0.gguf` (629 MB) or `-F16.gguf` (928 MB). |
| `PORT`, `HOST`, `SERVED_NAME` | 8888, 0.0.0.0, Underdog-Saluki-27B | |
| `API_KEY` | (empty) | Bearer token, when set. |
| `MODEL_DIR` | `~/.cache/underdog-saluki` | Weights. |
| `LLAMA_CPP_IMAGE` | b11515 digest | Another llama.cpp image, when you bump it. Re-run `tools/check.py` after a bump. |

For any other llama-server flag, append it to the `saluki` service `command:` list. `tools/serve.sh` forwards those arguments.

## Numbers

One DGX Spark, llama.cpp b11515, IQ2-mix weights, f16 KV, flash attention on, context 262144, one slot. Measured 10 October 2026 with `tools/chatbench.py`: six chat requests through the template, greedy plus three sampled seeds each (the thinking-on cell sampled twice), decode rate from llama-server's own timings. *Weighted* is total tokens over total decode time.

| Decode, tok/s | `SPEC=none` | `SPEC=mtp` | **`SPEC=dflash`** |
|---|---:|---:|---:|
| Code, greedy / sampled | 24.0 / 23.9 | 46.1 / 44.3 | **60.4 / 55.0** |
| Code edit (returns the input file), greedy / sampled | 23.7 / 23.7 | 210 / 234 | **232 / 239** |
| English prose, greedy / sampled | 24.0 / 24.0 | 34.7 / 31.9 | **41.9 / 37.5** |
| Dutch prose, greedy / sampled | 23.9 / 23.9 | **31.4 / 30.5** | 27.7 / 25.9 |
| Tool call, greedy | 24.0 | 54.3 | **72.7** |
| Thinking on, sampled | 23.9 | 36.7 | **45.9** |
| **Weighted** | **23.9** | **45.9** | **52.7** |
| Decode after an 11k-token prompt | 21.9 | 33.7 | **34.5** |
| Decode after a 41k-token prompt | 18.4 | 32.2 | **36.0** |
| Prefill, 11k / 41k tokens | 786 / 720 | 773 / 710 | 769 / 708 |

The `SPEC=none` column ran on b11459 (b11515 measured the same, 23.8). The tool-call sampled cell is left out of the table: `ngram-mod` shares its pool across requests, so a bench that repeats a prompt flatters it.

What the sweep showed:

- **DFlash is the fastest overall.** It drafts a block of tokens per pass. Draft acceptance is 74% on code, 96% on tool calls, and 90% on code edits.
- **DFlash is weak on Dutch.** Acceptance falls to 16-19% there, which leaves it barely above plain decode. The drafter was trained mostly on English. MTP is Qwen3.8's own head and keeps 36-40% acceptance on Dutch. Use `SPEC=mtp` if most traffic isn't English.
- **`ngram-mod` is close to free and wins big on edits.** Code edits went from 74 to 232 tok/s with no loss elsewhere.
- **No gain from these:** a draft length of 5-10 (better on code, worse on prose), `--spec-draft-p-min`, the Q8_0 drafter, q8_0 KV (slower at depth: 28.8 vs 35.2 tok/s at 41k), and `-ub` above the default (prefill is compute-bound at about 770 tok/s). `--backend-sampling` adds 1-2%. MTP and DFlash cannot run together.
- **Greedy text is not bit-identical across settings.** Batch verification changes floating-point order, so a near-tied token can flip. b11459 and b11515 without any drafter already differ the same way on 3 of 5 prompts.

The file is 7.89 GB, so a full read of the weights once per token at the Spark's 273 GB/s caps plain decode at about 35 tok/s. Plain decode measures 24, and only drafting gets past the cap.

Once the files are on disk, the server is listening about 3 seconds after start. With `SPEC=dflash` at the full context, llama-server holds 26.7 GB, and about 90 GiB of unified memory stays available. `tools/check.py`, including `--needle 32000`, passes in all three `SPEC` modes. `tools/bench.py` is still the raw-model check (`/completion`, no template). Its repeated sentence is near-perfect for a drafter, so use `tools/chatbench.py` to compare `SPEC` settings.

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
