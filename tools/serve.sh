#!/usr/bin/env bash
# llama-server arguments for Saluki. Defaults match the model card's general-use preset
# (thinking on, temperature 0.6, top_p 0.95, top_k 20). Extra arguments from compose are appended.
set -euo pipefail

file=${MODEL_FILE:-Underdog-Saluki-27B-1.0-IQ2-mix.gguf}
args=(
  --model "/models/${file}"
  --alias "${SERVED_NAME:-Underdog-Saluki-27B}"
  --host "${HOST:-0.0.0.0}"
  --port "${PORT:-8888}"
  --jinja
  --reasoning-format deepseek
  --n-gpu-layers "${NGL:-all}"
  --flash-attn on
  --ctx-size "${CONTEXT:-262144}"
  --parallel "${PARALLEL:-1}"
  --temp "${TEMP:-0.6}"
  --top-p "${TOP_P:-0.95}"
  --top-k "${TOP_K:-20}"
  --min-p "${MIN_P:-0}"
)
[[ -n ${MMPROJ:-} ]] && args+=(--mmproj "/models/${MMPROJ}")
[[ -n ${REASONING_EFFORT:-} ]] && args+=(--reasoning-effort "${REASONING_EFFORT}")
[[ -n ${API_KEY:-} ]] && args+=(--api-key "${API_KEY}")

# The image sets LLAMA_ARG_HOST, and we pass --host ourselves.
unset LLAMA_ARG_HOST
exec /app/llama-server "${args[@]}" "$@"
