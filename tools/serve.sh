#!/usr/bin/env bash
# llama-server arguments for Saluki. Defaults match the model card's general-use preset
# (thinking on, temperature 0.6, top_p 0.95, top_k 20), plus min_p 0.05 against tail slips such as German
# spellings in Dutch. Extra arguments from compose are appended.
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
  --min-p "${MIN_P:-0.05}"
)
# Speculative decoding. File names match tools/download.sh. ngram-mod drafts text the reply repeats
# from the prompt or earlier turns (code edits); it takes precedence over the model drafter when it hits.
# shellcheck disable=SC2054 # --spec-type takes a comma-separated list
case ${SPEC:-dflash} in
  dflash)
    args+=(--model-draft /models/Qwen3.8-27B-DFlash2-Q4_K_M.gguf --spec-type draft-dflash,ngram-mod
      --spec-draft-n-max "${SPEC_DRAFT_MAX:-4}" --backend-sampling) ;;
  mtp)
    args[1]=/models/Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf
    args+=(--spec-type draft-mtp,ngram-mod --spec-draft-n-max "${SPEC_DRAFT_MAX:-3}" --backend-sampling) ;;
  none) ;;
  *) echo "SPEC must be dflash, mtp or none (got ${SPEC})" >&2; exit 1 ;;
esac
mmproj=${MMPROJ:-mmproj-Underdog-Saluki-27B-1.0-Q8_0.gguf}
[[ $mmproj != none ]] && args+=(--mmproj "/models/${mmproj}")
[[ -n ${REASONING_EFFORT:-} ]] && args+=(--reasoning-effort "${REASONING_EFFORT}")
[[ -n ${API_KEY:-} ]] && args+=(--api-key "${API_KEY}")

# The image sets LLAMA_ARG_HOST, and we pass --host ourselves.
unset LLAMA_ARG_HOST
exec /app/llama-server "${args[@]}" "$@"
