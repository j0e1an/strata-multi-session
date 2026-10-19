#!/usr/bin/env python3
"""Append-only long-context stress test: N sessions, each a growing conversation.

    python3 bench/stress_append.py BASE_WORDS TURNS GROW_WORDS N
    python3 bench/stress_append.py 55000 5 2500 4        # ~214k -> ~253k tokens, 4 sessions

Every turn replays the whole conversation plus the model's own earlier reply and a new user message,
the way an agent does, so `reused` shows whether the slot or the parked snapshot was found. A
prompt whose tail is rewritten instead of appended shows reused=0 every turn and proves nothing.
Roughly 3.9 tokens per generated word. Watch the engine log for the four DANGER patterns in
park-watch.sh while it runs. Standard library only.
"""
import json, os, sys, time, threading, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conc import words, URL, MODEL  # noqa: E402

BASE, TURNS, GROW, N = (int(a) for a in sys.argv[1:5])
msgs = [[{"role": "user", "content": words(BASE, 7000 + i) +
          "\n\nTurn 0: reply with one short sentence about the first line."}] for i in range(N)]


def go(i, turn, res):
    body = json.dumps({"model": MODEL, "messages": msgs[i], "max_tokens": 150, "temperature": 0.3,
                       "chat_template_kwargs": {"enable_thinking": False}}).encode()
    t = time.time()
    try:
        r = json.load(urllib.request.urlopen(
            urllib.request.Request(URL, body, {"Content-Type": "application/json"}), timeout=3000))
        tm = r.get("timings", {})
        msgs[i].append({"role": "assistant", "content": r["choices"][0]["message"].get("content") or "ok"})
        msgs[i].append({"role": "user", "content": f"Turn {turn + 1}: " + words(GROW, 9500 + i * 50 + turn) +
                        "\nOne short sentence please."})
        res[i] = (time.time() - t, r["usage"]["prompt_tokens"], tm.get("cache_n"), tm.get("prompt_n"))
    except Exception as e:
        res[i] = (time.time() - t, "ERR", str(e)[:200], None)


for turn in range(TURNS):
    res = {}
    th = [threading.Thread(target=go, args=(i, turn, res)) for i in range(N)]
    t = time.time()
    [x.start() for x in th]
    [x.join() for x in th]
    print(f"--- turn {turn} wall={time.time() - t:.0f}s", flush=True)
    for i, v in sorted(res.items()):
        print(f"  s{i}: lat={v[0]:.0f}s prompt={v[1]} reused={v[2]} read={v[3]}", flush=True)
