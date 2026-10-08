#!/usr/bin/env python3
"""Throughput of a running Saluki server, from llama-server's own timings.

Uses /completion with ignore_eos so the measurement is the raw model, with no chat template and no
thinking. One stream matches the default PARALLEL=1 slot. Raise --streams only after raising PARALLEL,
or the extra requests queue behind the single slot and the per-request rate is the queued rate.

  tools/bench.py                  # decode 256 tokens, prefill at 4k and 16k
  tools/bench.py decode           # decode only
  tools/bench.py prefill          # prefill only
  tools/bench.py --streams 4      # decode at 4 concurrent requests

Standard library only.
"""
import argparse
import json
import os
import sys
import threading
import urllib.error
import urllib.request


def complete(url: str, prompt: str, n_predict: int) -> dict:
    body = {"prompt": prompt, "n_predict": n_predict, "ignore_eos": True, "temperature": 0,
            "cache_prompt": False, "samplers": ["temperature"]}
    req = urllib.request.Request(url.rstrip("/") + "/completion", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3600) as reply:
            return json.load(reply)
    except urllib.error.HTTPError as err:
        sys.exit(f"HTTP {err.code} {err.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as err:
        sys.exit(f"{url}: {err.reason} (is the server up? docker compose ps)")


def rates(payload: dict) -> tuple[float, float, int, int]:
    timings = payload.get("timings") or {}
    prompt_n = int(timings.get("prompt_n") or 0)
    predicted_n = int(timings.get("predicted_n") or 0)
    prefill = float(timings.get("prompt_per_second") or 0)
    decode = float(timings.get("predicted_per_second") or 0)
    return prefill, decode, prompt_n, predicted_n


def run_one(url: str, prompt: str, n_predict: int, out: list[dict]):
    out.append(complete(url, prompt, n_predict))


def report(label: str, payloads: list[dict]) -> None:
    prefill = decode = prompt_n = predicted_n = 0.0
    for payload in payloads:
        p, d, pn, dn = rates(payload)
        prefill += p
        decode += d
        prompt_n += pn
        predicted_n += dn
    n = len(payloads)
    print(f"{label:42} prefill {prefill / n:8.0f} tok/s ({prompt_n / n:.0f} tok)   "
          f"decode {decode / n:6.1f} tok/s x{n} ({predicted_n / n:.0f} tok)", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("which", nargs="?", default="all", choices=("all", "decode", "prefill"))
    ap.add_argument("--url", default=os.environ.get("SALUKI_URL", "http://127.0.0.1:8888"))
    ap.add_argument("--streams", type=int, default=1)
    args = ap.parse_args()

    sentence = "The saluki crossed the open court at dusk and kept its eyes on the far gate. "
    if args.which != "prefill":
        payloads: list[dict] = []
        threads = [threading.Thread(target=run_one, args=(args.url, sentence * 8, 256, payloads))
                   for _ in range(args.streams)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        report(f"decode, {args.streams} stream(s), 256 out", payloads)
    if args.which != "decode":
        for tokens in (4096, 16384):
            # ~4 characters per token is close enough; timings report the real count.
            payload = complete(args.url, sentence * (tokens // 10), 1)
            report(f"prefill, target {tokens}", [payload])


if __name__ == "__main__":
    main()
