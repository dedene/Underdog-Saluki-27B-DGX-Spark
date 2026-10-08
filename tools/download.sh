#!/usr/bin/env bash
# Fetch the pinned Saluki GGUF (and the optional vision projector) into /models.
# Resumable. A later start is a no-op once the revision stamp matches.
set -euo pipefail

dest=/models
model_id=${MODEL_ID:-ConwayResearch/Underdog-Saluki-27B-1.0}
revision=${MODEL_REVISION:-1336c0b5d74dfe6ad7f793577092f5effd9f97c6}
file=${MODEL_FILE:-Underdog-Saluki-27B-1.0-IQ2-mix.gguf}
mmproj=${MMPROJ:-}

# sha256 of each file at the pinned revision (Hugging Face LFS oid).
declare -A SHA=(
  [Underdog-Saluki-27B-1.0-IQ2-mix.gguf]=4a673518b11b1c4445f9b9a9d3c40356f5ba39f6a6260dcb25f3fbfd475d9efb
  [mmproj-Underdog-Saluki-27B-1.0-F16.gguf]=f3aa57813a513420c2ef765503b057c1946bf4c8cdf3679d266a6644fc6cfd50
  [mmproj-Underdog-Saluki-27B-1.0-Q8_0.gguf]=2e0b88b680a45a58eea666b9776a89601b16f4ef575b871a686d82ee65938756
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
  local name=$1
  local expect=${SHA[$name]:-}
  if [[ -z $expect ]]; then
    echo "no checksum for ${name}; known files: ${!SHA[*]}" >&2
    exit 1
  fi
  local path=$dest/$name stamp=$dest/.$name.sha256 url="https://huggingface.co/${model_id}/resolve/${revision}/${name}"
  if [[ -f $stamp && $(<"$stamp") != "$revision $expect" ]]; then
    echo "revision changed, re-downloading $name"
    rm -f "$path" "$stamp"
  fi
  if [[ -f $path && -f $stamp && $(<"$stamp") == "$revision $expect" ]]; then
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
  printf '%s %s\n' "$revision" "$expect" >"$stamp"
  echo "verified $name"
}

fetch "$file"
if [[ -n $mmproj ]]; then
  fetch "$mmproj"
fi
