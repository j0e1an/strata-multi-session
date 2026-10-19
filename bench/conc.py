#!/usr/bin/env python3
"""Concurrent short-prompt decode test: N parallel requests, per-stream and total tok/s.

    python3 bench/conc.py CONTEXT_WORDS N [N ...]      e.g.  python3 bench/conc.py 0 1 2 3 4

CONTEXT_WORDS 0 sends a ~30-token prompt. Talks to the engine on 127.0.0.1:18080 directly.
Standard library only.
"""
import json, sys, time, threading, urllib.request, random

URL = "http://127.0.0.1:18080/v1/chat/completions"
MODEL = "qwen3.8-flash-next-abliterated-iq3_s"


def words(n, seed):
    r = random.Random(seed)
    w = ["alpha", "beta", "gamma", "delta", "kernel", "socket", "buffer", "thread",
         "mutex", "cache", "index", "parser", "token", "vector", "matrix", "queue"]
    return " ".join(r.choice(w) + str(r.randint(0, 999)) for _ in range(n))


def one(i, ctx_words, out, res):
    prompt = (words(ctx_words, 1000 + i) + "\n\n" if ctx_words else "") + \
        "Write a Python function implementing an LRU cache with detailed comments, then explain its complexity."
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": out, "temperature": 0.6,
                       "chat_template_kwargs": {"enable_thinking": False}}).encode()
    t = time.time()
    r = json.load(urllib.request.urlopen(
        urllib.request.Request(URL, body, {"Content-Type": "application/json"}), timeout=1800))
    res[i] = (time.time() - t, r["usage"], r.get("timings", {}))


def run(n, ctx_words, out=800):
    res = {}
    th = [threading.Thread(target=one, args=(i, ctx_words, out, res)) for i in range(n)]
    t = time.time()
    [x.start() for x in th]
    [x.join() for x in th]
    wall = time.time() - t
    total = sum(v[1]["completion_tokens"] for v in res.values())
    print(f"N={n} ctx_words={ctx_words} wall={wall:.1f}s total_gen={total}", flush=True)
    for i, v in sorted(res.items()):
        tm = v[2]
        print(f"   s{i}: prompt={v[1]['prompt_tokens']} gen={v[1]['completion_tokens']} "
              f"decode={tm.get('predicted_per_second', 0):.0f} tok/s lat={v[0]:.1f}s", flush=True)


if __name__ == "__main__":
    ctx = int(sys.argv[1])
    for n in map(int, sys.argv[2:]):
        run(n, ctx)
