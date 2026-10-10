#!/usr/bin/env bash
# Fetch the pinned Saluki GGUF, the drafter SPEC needs, and the vision projector (unless MMPROJ=none) into /models.
# Resumable. A later start is a no-op once the revision stamp matches.
set -euo pipefail

dest=/models
model_id=${MODEL_ID:-ConwayResearch/Underdog-Saluki-27B-1.0}
revision=${MODEL_REVISION:-1336c0b5d74dfe6ad7f793577092f5effd9f97c6}
file=${MODEL_FILE:-Underdog-Saluki-27B-1.0-IQ2-mix.gguf}
mmproj=${MMPROJ:-mmproj-Underdog-Saluki-27B-1.0-Q8_0.gguf}
spec=${SPEC:-dflash}

# sha256 of each file at the pinned revision (Hugging Face LFS oid).
declare -A SHA=(
  [Underdog-Saluki-27B-1.0-IQ2-mix.gguf]=4a673518b11b1c4445f9b9a9d3c40356f5ba39f6a6260dcb25f3fbfd475d9efb
  [mmproj-Underdog-Saluki-27B-1.0-F16.gguf]=f3aa57813a513420c2ef765503b057c1946bf4c8cdf3679d266a6644fc6cfd50
  [mmproj-Underdog-Saluki-27B-1.0-Q8_0.gguf]=2e0b88b680a45a58eea666b9776a89601b16f4ef575b871a686d82ee65938756
  [Qwen3.8-27B-DFlash2-Q4_K_M.gguf]=1a25c56858e1ebe93f2718ac1d49d1151f9323325c1bbfd6209370f4db131ebd
  [Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf]=98f6ebb527917a2541df8e667d51c3ec8a8bb135b44c606d40cc6ed924e89e52
)
# Drafters live in other repos. Pinned like the weights: repo and revision per file.
declare -A SOURCE=(
  [Qwen3.8-27B-DFlash2-Q4_K_M.gguf]="z-lab/Qwen3.8-27B-DFlash2-GGUF 2d9571f8ce46e151f61c6499c99dee6079e1d610"
  [Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf]="Kujira/Underdog-Saluki-27B-1.0-MTP-GGUF 5d92db9b7aa466a7c8f55e26dac2e366c36e8e5b"
)

curl_fetch() {
  local url=$1 path=$2
  local -a args=(-fL --retry 8 --retry-delay 2 --retry-all-errors -C - --output "$path" "$url")
  if [[ -n ${HF_TOKEN:-} ]]; then
    args=(-fL --retry 8 --retry-delay 2 --retry-all-errors -C - -H "Authorization: Bearer ${HF_TOKEN}" --output "$path" "$url")
  fi
  curl "${args[@]}"
}

fetch() {
  local name=$1 repo=$model_id rev=$revision
  [[ -n ${SOURCE[$name]:-} ]] && read -r repo rev <<<"${SOURCE[$name]}"
  local expect=${SHA[$name]:-}
  if [[ -z $expect ]]; then
    echo "no checksum for ${name}; known files: ${!SHA[*]}" >&2
    exit 1
  fi
  local path=$dest/$name stamp=$dest/.$name.sha256 url="https://huggingface.co/${repo}/resolve/${rev}/${name}"
  if [[ -f $stamp && $(<"$stamp") != "$rev $expect" ]]; then
    echo "revision changed, re-downloading $name"
    rm -f "$path" "$stamp"
  fi
  if [[ -f $path && -f $stamp && $(<"$stamp") == "$rev $expect" ]]; then
    echo "present $name"
    return
  fi
  mkdir -p "$dest"
  echo "downloading $name"
  curl_fetch "$url" "$path"
  echo "checking sha256 of $name"
  local got
  got=$(sha256sum "$path" | awk 'NR==1 { print $1 }')
  if [[ $got != "$expect" ]]; then
    echo "checksum mismatch for $name (got $got), downloading again" >&2
    rm -f "$path"
    curl_fetch "$url" "$path"
    got=$(sha256sum "$path" | awk 'NR==1 { print $1 }')
    if [[ $got != "$expect" ]]; then
      echo "checksum mismatch for $name: $got" >&2
      exit 1
    fi
  fi
  printf '%s %s\n' "$rev" "$expect" >"$stamp"
  echo "verified $name"
}

# Keep these file names in step with tools/serve.sh.
case $spec in
  dflash) fetch "$file"; fetch Qwen3.8-27B-DFlash2-Q4_K_M.gguf ;;
  mtp) fetch Underdog-Saluki-27B-1.0-IQ2-mix-MTP.gguf ;;
  none) fetch "$file" ;;
  *) echo "SPEC must be dflash, mtp or none (got $spec)" >&2; exit 1 ;;
esac
if [[ $mmproj != none ]]; then
  fetch "$mmproj"
fi
