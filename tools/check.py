#!/usr/bin/env python3
"""Does Saluki on llama-server behave? Checks the model listing, the thinking switch, a tool call,
streaming and an image (skipped with MMPROJ=none) against a running server, optionally plus
needle-in-a-haystack retrieval at long context.

  tools/check.py                         # functional checks
  tools/check.py --needle 32000 131072   # also hide a key in N tokens of filler and ask for it back
  tools/check.py --url http://10.0.0.158:8888

Exit code 1 if anything fails. Standard library only.
"""
import argparse
import base64
import json
import os
import random
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib


class Server:
    def __init__(self, url: str, model: str):
        self.base, self.model = url.rstrip("/"), model

    def _open(self, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data, {"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req, timeout=3600)
        except urllib.error.HTTPError as err:
            sys.exit(f"{self.base}{path}: HTTP {err.code} {err.read().decode(errors='replace')[:400]}")
        except urllib.error.URLError as err:
            sys.exit(f"{self.base}{path}: {err.reason} (is the server up? docker compose ps)")

    def models(self) -> list[str]:
        payload = json.load(self._open("/v1/models"))
        return [item.get("id", "") for item in payload.get("data", [])]

    def props(self) -> dict:
        return json.load(self._open("/props"))

    def ask(self, prompt: str | list, think: bool | None = None, **body) -> dict:
        """One non-streamed turn; returns the message plus usage."""
        if think is not None:
            body["chat_template_kwargs"] = {"enable_thinking": think}
        reply = json.load(self._open("/v1/chat/completions",
                                      {"model": self.model, "messages": [{"role": "user", "content": prompt}], **body}))
        return {**reply["choices"][0]["message"], "usage": reply.get("usage", {})}

    def stream(self, prompt: str, think: bool) -> tuple[str, str]:
        """One streamed turn; returns (reasoning, content) as they arrived in the deltas."""
        parts = {"reasoning": [], "content": []}
        body = {"model": self.model, "stream": True, "messages": [{"role": "user", "content": prompt}],
                "chat_template_kwargs": {"enable_thinking": think}}
        for raw in self._open("/v1/chat/completions", body):
            event = raw.decode().removeprefix("data:").strip()
            if not event or event == "[DONE]":
                continue
            for choice in json.loads(event).get("choices", []):
                delta = choice.get("delta", {})
                parts["reasoning"].append(delta.get("reasoning") or delta.get("reasoning_content") or "")
                parts["content"].append(delta.get("content") or "")
        return "".join(parts["reasoning"]), "".join(parts["content"])


def reasoning(msg: dict) -> str:
    return (msg.get("reasoning") or msg.get("reasoning_content") or "").strip()


def clean(text: str | None) -> str:
    return (text or "").strip()


def model_listed(srv: Server):
    names = srv.models()
    return srv.model in names, f"ids {names}"


def thinking_off(srv: Server):
    msg = srv.ask("What is 17 + 25? Digits only.", think=False, max_tokens=32, temperature=0)
    ok = not reasoning(msg) and "42" in clean(msg["content"]) and "<think>" not in clean(msg["content"])
    return ok, f"answer {clean(msg['content'])!r}, reasoning {len(reasoning(msg))} chars"


def thinking_on(srv: Server):
    msg = srv.ask("A notebook costs 4 euros and a pen costs 1.50 euros. "
                  "How much do 3 notebooks and 2 pens cost, in euros? Reply with the number only.",
                  think=True, max_tokens=4096, temperature=0.6)
    answer = clean(msg["content"])
    ok = bool(reasoning(msg)) and "15" in answer and "<think>" not in answer
    return ok, f"answer {answer!r}, reasoning {len(reasoning(msg))} chars"


def tool_call(srv: Server):
    tool = {"type": "function", "function": {
        "name": "create_event",
        "description": "Create a calendar event.",
        "parameters": {"type": "object", "required": ["title", "date"], "properties": {
            "title": {"type": "string"},
            "date": {"type": "string", "description": "ISO date, YYYY-MM-DD"}}}}}
    msg = srv.ask("Add a calendar event titled Sprint Review on 12 March 2027.",
                  think=False, tools=[tool], tool_choice="auto", max_tokens=512, temperature=0)
    calls = msg.get("tool_calls") or []
    if not calls:
        return False, f"no tool call; content {clean(msg.get('content'))[:160]!r}"
    fn = calls[0].get("function", {})
    raw = fn.get("arguments", {})
    args = json.loads(raw) if isinstance(raw, str) else raw
    title = str(args.get("title", ""))
    ok = fn.get("name") == "create_event" and args.get("date") == "2027-03-12" and "Sprint Review" in title
    return ok, f"{fn.get('name')}({json.dumps(args, ensure_ascii=False)})"


def streaming(srv: Server):
    think, answer = srv.stream("What is the capital of Belgium? One word.", think=False)
    text = clean(answer)
    ok = "Brussels" in text and "<think>" not in text + think and not think
    return ok, f"reasoning {len(think)} chars, answer {text[:70]!r}"


def stripes_png(colours: list[tuple[int, int, int]], width: int = 96, height: int = 64) -> bytes:
    """A PNG of vertical stripes, one per colour, left to right."""
    row = b"\0" + b"".join(bytes(colours[x * len(colours) // width]) for x in range(width))
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))


def vision(srv: Server):
    """Three stripes in an order a text-only guess would not produce."""
    if not srv.props().get("modalities", {}).get("vision"):
        return None, "server has no projector (MMPROJ=none)"
    png = stripes_png([(0, 0, 255), (255, 220, 0), (255, 0, 0)])
    msg = srv.ask([{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
                   {"type": "text", "text": "Name the colours of the three vertical stripes, left to right. "
                                            "Comma-separated colour names only."}],
                  think=False, max_tokens=32, temperature=0)
    answer = clean(msg["content"])
    found = [answer.lower().find(word) for word in ("blue", "yellow", "red")]
    return -1 not in found and found == sorted(found), f"answer {answer!r}"


def needle(srv: Server, tokens: int):
    """Hide a key among ~`tokens` tokens of synthetic service logs, at a random depth, and ask for it."""
    rng = random.Random(tokens)
    key = f"{rng.randrange(10**7, 10**8)}"
    lines = [f"2026-10-08T{rng.randrange(24):02}:{rng.randrange(60):02}:{rng.randrange(60):02}Z "
             f"worker-{rng.randrange(64):02} {rng.choice(('GET', 'POST', 'PUT'))} /api/v2/"
             f"{rng.choice(('orders', 'users', 'stock', 'invoices'))}/{rng.randrange(10**6)} -> "
             f"{rng.choice((200, 200, 200, 201, 404, 503))} in {rng.randrange(2, 900)} ms"
             for _ in range(tokens // 40)]
    depth = rng.uniform(0.1, 0.9)
    lines.insert(int(len(lines) * depth), f"NOTICE maintenance key for tonight is {key}")
    start = time.time()
    msg = srv.ask("\n".join(lines) + "\n\nWhat is tonight's maintenance key? Reply with the key only.",
                  think=False, max_tokens=32, temperature=0)
    got, n = clean(msg["content"]), msg["usage"].get("prompt_tokens", 0)
    return key in got, f"{n:,} tokens, depth {depth:.0%}, {time.time() - start:.0f} s: expected {key}, got {got!r}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("SALUKI_URL", "http://127.0.0.1:8888"))
    ap.add_argument("--model", default=os.environ.get("SALUKI_MODEL", "Underdog-Saluki-27B"))
    ap.add_argument("--needle", type=int, nargs="*", default=[], metavar="TOKENS")
    args = ap.parse_args()

    srv = Server(args.url, args.model)
    checks = [(fn.__name__.replace("_", " "), fn) for fn in
              (model_listed, thinking_off, thinking_on, tool_call, streaming, vision)]
    checks += [(f"needle {n:,}", lambda s, n=n: needle(s, n)) for n in args.needle]
    failures = 0
    for name, check in checks:
        ok, detail = check(srv)
        failures += ok is False
        print(f"{'skip' if ok is None else 'ok  ' if ok else 'FAIL'}  {name:20} {detail}", flush=True)
    sys.exit(failures > 0)


if __name__ == "__main__":
    main()
