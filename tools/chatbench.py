#!/usr/bin/env python3
"""Decode speed on real chat work, for comparing SPEC settings.

tools/bench.py repeats one sentence under ignore_eos, which a drafter predicts almost perfectly. This
script sends six chat requests through the template instead (code, a code edit, English prose, Dutch
prose, a tool call, thinking on), greedy and at the model card's sampling, and reports llama-server's
own decode rate and draft acceptance per cell. The weighted figure is total tokens over total decode time.

  tools/chatbench.py                    # greedy + 3 sampled seeds per cell, thinking cell sampled twice
  tools/chatbench.py --quick            # greedy + 1 sampled seed, no thinking cell
  tools/chatbench.py --depth            # also decode after an 11k- and a 41k-token prompt
  tools/chatbench.py --json run.json    # keep every reply, to diff greedy text between settings

The code-edit and long prompts read Python's own standard library, so they differ a little between
Python versions. Compare runs from the same host. Standard library only.
"""
import argparse
import json
import os
import pathlib
import statistics
import sys
import sysconfig
import time
import urllib.error
import urllib.request

STDLIB = pathlib.Path(sysconfig.get_paths()["stdlib"])

TOOLS = [
    {"type": "function", "function": {"name": "get_weather", "description": "Weather forecast for a city.",
     "parameters": {"type": "object", "required": ["city", "day"], "properties": {
         "city": {"type": "string"}, "day": {"type": "string", "enum": ["today", "tomorrow"]}}}}},
    {"type": "function", "function": {"name": "search_flights", "description": "Search flights.",
     "parameters": {"type": "object", "required": ["origin", "destination", "date"], "properties": {
         "origin": {"type": "string"}, "destination": {"type": "string"}, "date": {"type": "string"}}}}},
]

# name: (prompt, max_tokens, thinking)
CELLS = {
    "code": ("Write a Python function that parses an ISO 8601 duration such as P3DT4H12M30S into seconds. "
             "Use type hints, raise ValueError on bad input, and add five doctest examples.", 512, False),
    "edit": ("Add complete type hints to this code and return the full modified file, nothing else.\n\n```python\n"
             + "\n".join((STDLIB / "textwrap.py").read_text().splitlines()[:140]) + "\n```", 768, False),
    "prose": ("Explain to a homeowner how an air-to-water heat pump works, when it beats a gas boiler, "
              "and what to check before buying one. About 350 words.", 512, False),
    "dutch": ("Schrijf een vriendelijke maar duidelijke e-mail aan een klant waarin je uitlegt dat de oplevering "
              "van hun website twee weken later valt door een probleem met de betaalintegratie. Stel een nieuwe "
              "planning voor in drie stappen.", 512, False),
    "tool": ("What's the weather in Ghent and in Antwerp tomorrow? Also find flights from Brussels to Lisbon "
             "on 2026-11-14.", 256, False),
    "think": ("A shop sells pens in packs of 6 and 10. What is the largest number of pens you cannot buy "
              "exactly, if any? Then do the same for packs of 6, 9 and 20. Show your answer briefly.", 1536, True),
}


def chat(url: str, body: dict) -> dict:
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3600) as reply:
            return json.load(reply)
    except urllib.error.HTTPError as err:
        sys.exit(f"HTTP {err.code} {err.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as err:
        sys.exit(f"{url}: {err.reason} (is the server up? docker compose ps)")


def run(url: str, cell: str, sampled: bool, seed: int) -> dict:
    prompt, max_tokens, think = CELLS[cell]
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "seed": seed,
            "chat_template_kwargs": {"enable_thinking": think}, "cache_prompt": False,
            **({"temperature": 0.6, "top_p": 0.95, "top_k": 20} if sampled else {"temperature": 0})}
    if cell == "tool":
        body["tools"] = TOOLS
    start = time.time()
    payload = chat(url, body)
    timings, message = payload.get("timings", {}), payload["choices"][0]["message"]
    return {"cell": cell, "sampled": sampled, "seed": seed, "wall": time.time() - start,
            "n": timings.get("predicted_n", 0), "decode": timings.get("predicted_per_second", 0.0),
            "draft_n": timings.get("draft_n", 0), "draft_accepted": timings.get("draft_n_accepted", 0),
            "reply": [message.get("reasoning_content"), message.get("content"), message.get("tool_calls")]}


def depth(url: str, tokens: int) -> dict:
    words: list[str] = []
    for path in sorted(STDLIB.glob("*.py"))[:400]:
        words += path.read_text(errors="replace").split()
    body = {"messages": [{"role": "user", "content": "Summarize this code base in about 200 words:\n"
                          + " ".join(words[: int(tokens * 0.62)])}],
            "max_tokens": 256, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False},
            "cache_prompt": False}
    timings = chat(url, body)["timings"]
    return {"prompt_n": timings["prompt_n"], "prefill": timings["prompt_per_second"],
            "decode": timings["predicted_per_second"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("SALUKI_URL", "http://127.0.0.1:8888"))
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--depth", action="store_true")
    ap.add_argument("--json", metavar="FILE")
    args = ap.parse_args()

    seeds = [1] if args.quick else [1, 2, 3]
    chat(args.url, {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 8})  # warm-up
    rows = []
    for cell, (_, _, think) in CELLS.items():
        if think and args.quick:
            continue
        plan = [(True, s) for s in seeds[:2]] if think else [(False, 0)] + [(True, s) for s in seeds]
        runs = [run(args.url, cell, sampled, seed) for sampled, seed in plan]
        rows += runs
        for label, group in (("greedy", [r for r in runs if not r["sampled"]]),
                             ("sampled", [r for r in runs if r["sampled"]])):
            if not group:
                continue
            drafted = sum(r["draft_n"] for r in group)
            accepted = f"{100 * sum(r['draft_accepted'] for r in group) / drafted:3.0f}%" if drafted else "   -"
            print(f"{cell:6} {label:8} decode {statistics.mean(r['decode'] for r in group):6.1f} tok/s   "
                  f"draft accepted {accepted}   x{len(group)}", flush=True)
    weighted = sum(r["n"] for r in rows) / sum(r["n"] / r["decode"] for r in rows)
    print(f"weighted decode {weighted:.1f} tok/s over {sum(r['n'] for r in rows)} tokens")

    long = [depth(args.url, tokens) for tokens in (8192, 32768)] if args.depth else []
    for row in long:
        print(f"depth  {row['prompt_n']:6} tok prompt   prefill {row['prefill']:5.0f} tok/s   "
              f"decode {row['decode']:5.1f} tok/s")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps({"weighted": weighted, "rows": rows, "depth": long}, indent=1))


if __name__ == "__main__":
    main()
