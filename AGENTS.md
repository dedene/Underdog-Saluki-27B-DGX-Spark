# AGENTS.md

Serve Underdog Saluki 27B 1.0 (IQ2-mix GGUF, 7.89 GB, Qwen3.8-27B) on one DGX Spark with stock llama.cpp via Docker Compose.

## Layout
- `compose.yaml`: `download` (one-shot curl of the pinned revision, sha256 checked) -> `saluki` (llama-server, healthcheck).
  Knobs are `${VAR:-default}` plus `tools/serve.sh`. `.env.example` documents them.
- `tools/download.sh`, `tools/serve.sh`: fetch and launch. The image is the official arm64 CUDA 13 build, so there is no Dockerfile.
- `tools/check.py`: functional checks (+ `--needle N`). `tools/bench.py`: `/completion` timings, no chat template.
  `tools/chatbench.py`: chat-template decode per workload with draft acceptance. Use it to compare `SPEC` settings.
- `SPEC` (dflash default, mtp, none) picks the drafter in both `download.sh` and `serve.sh`; keep the file names in step.

## Spark
- Same box as `~/Development/Kolibri-1-DGX-Spark` (`ssh zenjoy@10.0.0.158`). Checkout at `~/Development/Underdog-Saluki-27B-DGX-Spark`.
- GB10, sm_121, aarch64, 128 GB unified, driver 580, CUDA 13, Compose v5.
- Other model repos in `~/Development` default to port 8888 too: stop them first.

## Verify
- `docker compose config --quiet`, `shellcheck tools/*.sh`, `python3 -m py_compile tools/*.py`
- `docker compose up -d --wait`, then `tools/check.py` (all ok) and `tools/chatbench.py`.

## Image
- `ghcr.io/ggml-org/llama.cpp:server-cuda13` at index digest `sha256:6869e4456217802e5372774be236a9aa93666cfb7f033b7d39cd58b178af6489`
  (arm64 `sha256:0e1f80e7…`). llama.cpp b11515, commit `3d65c90d0`, tag `server-cuda13-b11515`.
- Before that: b11459 (`f498f864f`, digest `sha256:695214dc…`). It has `draft-dflash` but predates the DFlash head fix (#30111).
- Weights revision `1336c0b5d74dfe6ad7f793577092f5effd9f97c6`. Main file sha256 `4a673518b11b1c4445f9b9a9d3c40356f5ba39f6a6260dcb25f3fbfd475d9efb`.
- Drafters: `z-lab/Qwen3.8-27B-DFlash2-GGUF@2d9571f8` Q4_K_M; `Kujira/Underdog-Saluki-27B-1.0-MTP-GGUF@5d92db9b` (MTP graft).

## Findings (2026-10-08)
- `tools/check.py` passed: model id, thinking off (`42`, no reasoning), thinking on (`15.00`, 978 chars of reasoning), one tool call, streaming.
- `tools/bench.py`: decode 22.6 tok/s (256 out), prefill 756 tok/s at 7,363 tokens and 706 tok/s at 29,485. CUDA graphs reused. `nvidia-smi` utilization stays N/A on this UMA box.
- Listening about 3 s after start once the file is local. `n_slots = 1`, `n_ctx_slot = 262144`. About 87 GiB still available with the other containers up.
- `download.sh` must end with `exit 0`. A trailing `[[ -n $MMPROJ ]] && fetch` returns 1 when vision is off, and Compose then refuses to start the server.
- Disable the image healthcheck on `download`. It probes port 8080, which this service never opens.
- The image sets `LLAMA_ARG_HOST`. `serve.sh` unsets it because the script passes `--host`.

## Findings (2026-10-10, speculative decoding)
- Weighted chat decode: none 23.9, mtp+ngram-mod 45.9, dflash+ngram-mod n4 52.7 tok/s. Full table in README.
- DFlash acceptance is 16-19% on Dutch (vs 36-40% for MTP), so Dutch barely beats plain decode. Everything else favours DFlash.
- Losers: n-max 5-10, `--spec-draft-p-min`, Q8_0 drafter, q8_0 KV (slower at depth), `-ub` > 512. `-bs` +1-2%.
- `draft-mtp,draft-dflash` together fails at load ("context type MTP requested but model doesn't contain MTP layers").
- `ngram-mod` pools across requests: repeated bench prompts inflate it. Don't benchmark with `tools/bench.py`'s repeated sentence.
- Greedy text differs between builds and drafters at near-ties, even b11459 vs b11515 with no drafter. Not a bug.
- Saluki's GGUF is about 20% IQ1_S/IQ1_M and 52% IQ2. TensorFold reads no GGUF, and NVFP4/MLX can't hold these grids exactly.

## Findings (2026-10-10, Dutch slips)
- "Bedeckt"/"sonnig" in Dutch are tail samples. Saluki: P(Bedeckt)=0.0155, p/pmax 0.036 in a table cell; Q4_K_M 0.0015, p/pmax 0.019.
  Default `MIN_P` is 0.05 for that reason (card: 0). Hermes sends no sampling params, so server defaults apply.

## Constraints
- Saluki is a standard GGUF. Do not wrap it in vLLM or a custom llama.cpp fork; the model card's runtime is stock llama.cpp.
- Leave `REASONING_EFFORT` unset unless a server-wide default is the goal. The template default is `xhigh`. `enable_thinking: false` is how a request turns thinking off.
- Vision is opt-in via `MMPROJ`. The text server does not load a projector.
- Credits: write our own scripts. Kolibri-1-DGX-Spark is the pattern; do not copy its vLLM flags onto this server.
