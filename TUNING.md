# Multi-session tuning for the 2x RTX 5090 Strata box

Runbook for the failure mode where a Qwen Code session dies with

> `API Error: the engine stopped unexpectedly (exit code 1); the next request restarts it`

Written 2026-10-17 after two live crashes. Config file: `strata-iq3_s-abliterated.json`
(backup of the pre-change version: `strata-iq3_s-abliterated.json.bak-promptcache`).

## 1. What the error actually is

The string comes from Strata's own `serve/server.py` (`EngineDied`). llama-swap relays it and
appends the real cause:

```
the engine stopped unexpectedly (exit code 1). The engine exited (code 1) after it reported:
saving a checkpoint part failed - please report it at github.com/Niko1221/Strata/issues ...
```

The engine log (`strata-iq3_s-abliterated.log`) line immediately before it names the failed
allocation:

```
strata serve: checkpoint save: conversation snapshot running-state copy: out of memory
strata serve: saving a checkpoint part failed
```

So the engine died inside the **conversation-checkpoint save**, on a host-RAM allocation. Not
while generating, and not because the prompt was too long.

## 2. Differential diagnosis - three failures, three signatures

| failure | signature | exit code |
|---|---|---|
| Context overflow | `the prompt exceeds the context` | n/a, request rejected |
| Kernel OOM kill | `Killed process` in `dmesg` / `journalctl -k` | **-9** |
| **This one** | `running-state copy: out of memory`, no kernel message | **1** |

Check all three before acting. The README's advice for this banner ("your PC is out of RAM,
Linux stops the engine") maps to the **kernel OOM** row, which is *not* what happens here -
nothing was killed, and `dmesg` stays clean. Exit code 1 means the engine reported the failure
itself and exited cleanly.

Both crashes on 2026-10-17 were this third kind: 03:09:20 at a 100,769-token prompt, and
05:07:49 at 179,375 tokens against `--max-context 262144` - only 68% of the window.

## 3. Root cause

Two independent things had to be true.

**(a) The guard's view of free RAM was wrong.** Strata's admission guard measures against
`MemAvailable`. On this box, with the engine loaded, `MemAvailable` read **41.35 GiB** while
`MemFree` was **3.97 GiB** - a **37.4 GiB gap** of reclaimable page cache from the mmap'd
expert files. The guard saw plenty of room, admitted a multi-gigabyte snapshot copy, and the
allocation then failed.

**(b) Concurrent long sessions multiply the demand.** The engine runs `parallel: 1` with one
262k context. Two sessions at 140-180k tokens cannot both be resident, so each arriving turn
evicts the other's live session and re-prefills its whole prompt. The log shows it directly -
every turn of both large sessions read **0 tokens reused**:

```
prompt 174284 tokens = 0 reused      prompt 123352 tokens = 0 reused
prompt 175555 tokens = 0 reused      prompt 134477 tokens = 0 reused
prompt 176522 tokens = 0 reused      prompt 140606 tokens = 0 reused
prompt 178019 tokens = 0 reused      prompt 142396 tokens = 0 reused   <- died next
```

Each cold prefill takes a fresh set of checkpoints, and that copy is what fails. **The
multiplier is the number of concurrent long sessions, not one session's length** - which is
why a 100k-token prompt crashed too.

## 4. The fix

Added to `args` in `strata-iq3_s-abliterated.json`:

```
--prompt-cache 3
--prompt-cache-every 65536
--memory-limit-mib 54000
--conversation-cache-mib 30720
--conversation-cache-slots 7
--conversation-cache-min-tokens 50000
```

| flag | what it does |
|---|---|
| `--prompt-cache 3` | 3 resident checkpoints instead of 6 (verified: the log's per-request count went 6 -> 3) |
| `--prompt-cache-every 65536` | checkpoint every 64k tokens instead of every 16k |
| `--memory-limit-mib 54000` | **the actual crash fix.** Replaces `MemAvailable` in the guard's math with a real cap, so an unbackable allocation is refused instead of attempted |
| `--conversation-cache-mib` | parks conversation state in host RAM so an interleaved session restores instead of re-prefilling |
| `--conversation-cache-slots` | how many conversations may be parked at once |
| `--conversation-cache-min-tokens` | refuses to park short side requests, so they cannot take the slots long sessions need |

After the cap, the guard line changed from
`limit 59.1 GiB (source: meminfo) ... available 52.9 GiB (MemAvailable 52.9 GiB)` to
`limit 52.7 GiB (source: flag) ... available 45.9 GiB (MemAvailable 52.1 GiB, --memory-limit-mib 54000)`.

**Nothing else moved.** Expert cache 13312 slots / 24.19 GiB, CUDA1 11264 slots / 22.64 GiB,
resident experts 6.81 GiB page-locked, VRAM free 673/442 MiB - all identical before and after.
The cap corrects the admission math only; it does not shrink the model's own allocations.

## 5. Measured effect

| | before | after |
|---|---|---|
| prompt tokens reused | **0** of 1,484,812 (19 requests) | **42%** of prompt tokens, ~60% of requests |
| large-session reuse | 0% | up to **97.5%** |
| cost of returning to a parked session | ~2.5 s prefill | **29-140 ms** restore (~70x) |
| re-prefill wasted | ~148 s per 20 min | largely eliminated |

Cross-check the log against `/metrics` **in one sample** - two separate reads disagree because
the log keeps growing while you read it.

### Did trimming the prompt cache cost speed? No.

`--prompt-cache 3 --prompt-cache-every 65536` reduces how often a checkpoint is written, so the
obvious worry is that mid-size sessions lost their prefix reuse and now pay full prefill. Bucketing
every `strata serve: prompt N tokens = R reused` line in the log by the engine generation's flags
(defaults until 05:33:46, the tuned pair from then on) and keeping only prompts under 65k:

| regime | mid-size requests | reused | avg `prompt_ms` |
|---|---|---|---|
| tuned (`3` / `65536`) | 209 | 91 (**43.5%**) | **2419 ms** |
| defaults (`6` / `16384`) | 640 | 290 (**45.3%**) | 2591 ms |

Reuse is within noise of the defaults and prefill is marginally *faster* after the change, so the
trimming is latency-neutral. Two caveats: this is an observational before/after across generations,
not a controlled A/B (the traffic differs between regimes), and the reuse that does appear below
65k is the **parking** path, not the prompt cache - its `reused` values repeat as constants
(`15270` eleven times, `17370` twice) where a prompt-cache hit would track the prompt's growth.

A zero-reuse run on a 38k-token session is therefore expected under `parallel: 1` with several
sessions interleaving, not a regression: it happened at the same rate under the old defaults.

## 6. Sizing the parking budget

Measured cost of a parked conversation: **17,406 bytes/token**
(`snapshot_bytes=3,892,476,616` for 223,627 tokens).

| tokens/session | snapshot each | warm sessions in 33.6 GiB |
|---|---|---|
| 100,000 | 1.62 GiB | 20 |
| 150,000 | 2.43 GiB | 13 |
| 200,000 | 3.24 GiB | 10 |
| 262,144 (full ctx) | 4.25 GiB | 7 |

Size it as **sessions x ~3.5 GiB**. Two mistakes already made here, do not repeat them:

- **Do not read the cgroup's lifetime `MemoryPeak` as current headroom.** It is a high-water
  mark. Steady state is ~17 GiB used with ~37 GiB genuinely free, so parking is affordable.
- **Slots and budget both bind, and short requests bind slots first.** At 2 slots, 19 of 31
  parks were conversations under 30k tokens, which evicted the 200k sessions (10 evictions).
  At a 12 GiB budget with 4 sessions the budget hit 100% and churned: 187 parks against only
  28 restores and 156 evictions. Raising both fixed it (58% used, 2 evictions, 14 of 20 parks
  restored).
- **Size `--conversation-cache-slots` from the REAL snapshot size, not the worst case.** On
  2026-10-17 the cache sat at 7/7 slots while the budget used only 33% of 30 GiB, and evicted
  15 conversations in four minutes. Live snapshots averaged **1.60 GiB (~98k tokens)**, not the
  ~3.5 GiB that "sessions x ~3.5 GiB" assumes - so the budget could have held ~18 of them but
  only 7 slots existed. Two thirds of the budget was dead capacity and every park past the
  seventh evicted something. The tell: during the spike the budget *fell* (45% -> 27%) because
  the evicted snapshots were larger than the incoming ones, and `MemFree` rose 9.7 -> 14.6 GiB
  - an eviction storm can make RAM look healthier while reuse gets worse. Raised to 12 slots
  (12 x 1.60 GiB = 19.2 GiB, still under the budget, so the MiB ceiling binds first).
  The signature to watch is `parked == slots` together with budget under half used.

## 7. The two ceilings

**VRAM caps concurrency at 1 decoder.** 2x5090 = 63.6 GiB, 62.0 GiB used, **0.96 GiB free**
(expert cache alone is 46.83 GiB). `--batch N` needs "VRAM like the main one" *per slot*, so a
second batch slot does not fit. Raising concurrency means lowering `--max-context` to free KV.

**Host RAM caps how many sessions stay warm** - the table above. This is the resource with
headroom, and it buys *no re-prefill*, not *faster generation*.

## 8. Monitoring

**`~/Strata/park-watch.sh`** polls `:18080/metrics` every 45 s and prints one line of
parked / budget% / evictions / parks / restores / reuse / MemFree, plus a `DANGER:` line for
`out of memory`, `saving a checkpoint part failed`, `stopped unexpectedly`,
`exceeds the context`, or `engine started`. Run `park-watch.sh --once` for a single heartbeat.

Note that `engine started` fires on *any* restart and a crash-restart carries identical flags,
so that line alone cannot distinguish them - treat it as a crash only if nobody restarted
anything. The other four patterns are unambiguous.

**Two bugs this monitor shipped with, both silent - read before writing another one.**

1. `str.format()` field names cannot contain arithmetic, so `{rt/1e6:.1f}` raised `KeyError`
   on every poll. The script had `2>/dev/null` on the python, so the traceback vanished and
   the loop produced **zero output for its whole life**. The supervisor saw silence for 10
   minutes and killed it as an "idle timeout" - a symptom, not the cause.
2. The first fix used `\"` inside f-string braces, which Python 3.14 rejects outright
   (`unexpected character after line continuation character`). Caught only because the
   replacement was run once by hand before being trusted.

**How to avoid both:** never suppress stderr in a monitor (a broken line must surface, not go
quiet), precompute every value into a plain variable and keep f-string braces free of nested
quotes and arithmetic, and always test with `--once` before launching a long-lived watch.

## 9. Still unverified

- **Crash-fix evidence is now substantial but not conclusive.** As of 14:10 on 2026-10-17:
  **6 h 37 m uptime with 0 crash signatures** across 4 concurrent sessions, budget 70-83%,
  108+ parks. That is far stronger than the ~1 h first measured here, though every sample so
  far is under active use rather than a deliberate soak test. The owner separately reports
  **5** sessions with no recurrence; every measurement in this box's log saw **4** live
  sessions, so the 5-session figure stays **observed, not measured here.**
- **How much VRAM `--max-context 131072` actually frees** is unmeasured, so whether
  `parallel: 2` fits is unknown.
- **Parking does not appear to slow prefill - measured 2026-10-17 at 06:54 uptime.** With the
  cache at 6-7/7 slots and 71-84% budget, uncached prefill over the last 10 requests ran
  **min 4,005 / median 7,870 / max 8,775 tok/s**, which sits inside the documented
  2.6k-10.4k band and *above* its historical ~5.6k median. So the worry that parking evicts
  expert page cache and costs prefill speed has **no supporting evidence** here.
  Two limits on that: n=10, and with ~86% reuse the requests that still read uncached tokens
  are a selected minority, so this compares medians across different conditions rather than a
  controlled A/B. `MemFree` fell from ~27 GiB to ~10 GiB as the cache filled and then
  **plateaued** (9.7-10.3 GiB across eight consecutive polls) rather than declining further -
  roughly 7.4 GiB above the 2560 MiB guard floor. Evictions ran ~2 per 46 new parks (~4%), so
  there is no churn.

## 10. Likely upstream bug

`docs/DETAILS.md:1027` promises "Oversized snapshots or host allocation failures fall back to
ordinary prompt processing." The **parking** path honours that - it logs
`skip parking (...)` and serves the request normally. The **`--prompt-cache` checkpoint** path
printed no guard or skip line in either crash and died instead. Strata 0.1.44.1. The server
itself asks for a report at github.com/Niko1221/Strata/issues.

## 11. Four sessions at 250k context - measured on the 2x5090 box, 2026-10-19

Goal: 4 concurrent sessions, each at 200 tok/s and 200k+ context. The context half is met; the
speed half is not reachable on this engine.

### Speed: a shared ceiling, not a per-session rate (2x5090, 2026-10-19, short prompts)

| setup (`--batch 4`) | 1 session | 2 | 3 | 4 (each / total) |
|---|---|---|---|---|
| `--batch-groups 2`, no `--batch-mtp` (shipped) | 251-280 | 134 each | ~100 each | **84-96 / ~335-385** |
| `--batch-groups 1 --batch-mtp` | 272-277 | 136 each | 96 each | 75 / 300 |
| `--batch-groups 4` (`--batch-mtp` is switched off by the engine) | 270 | 131 each | - | 67 / 268 |

Total throughput barely grows with sessions: two sessions together equal one. The engine log
(`strata batch:`) shows why - `avg 7.30 rows, 24.41 ms/window`. A batch window holds at most 8
rows (docs/BATCHING.md), and window time barely grows with rows (about 16 ms solo, 22 ms for 7
rows), so four sessions each get about 2 rows per window instead of the ~4.4 tokens a lone
session verifies. There is no flag for the 8-row limit. vLLM does not escape it for this model:
Flash-Next is 125B total / 6B active with a 51B embedding table that must sit in host RAM, the
FP8 checkpoint is 172 GB, QSA attention rejects an FP8 KV cache, and the published RTX PRO 6000
numbers are 74 tok/s solo, 142 with MTP, 484 at 32 clients - below what Strata already does here.

### Context: 4 x 253k holds, with every session in a slot (2x5090, 2026-10-19)

`bench/stress_append.py 55000 5 2500 4`: four append-only conversations grown from ~214k to ~253k
tokens, 5 turns. **0 crash signatures**; after the cold read each turn read ~9.7k new tokens and
reused the rest, a whole round taking 14-16 s (one session re-read once on the last turn, 36 s).
Cold prefill of all four took 90 s. Host MemAvailable minimum 10.2 GiB, engine RSS 24.8-43.8 GiB.
Per-slot cost is small: 0.62 GiB VRAM and 1.2 GiB pinned RAM at the full 262144 context.

### Beyond four sessions: safe, but not fast (2x5090, 2026-10-19)

Six sessions of 175k-224k tokens on four slots: **0 crash signatures** in two runs, but roughly half
the turns re-read ~200k tokens (40-100 s each) because the round-robin over 6 conversations and 4
slots defeats LRU. Parked snapshots are ~3.0-3.8 GiB at these sizes. Why restores miss when
parked < budget is not diagnosed.

### Mistakes made while measuring - do not repeat

- **A test that rewrites the prompt tail shows `reused=0` forever.** Recurrent (GDN) state cannot
  roll back, so only append-only prompts reuse. Use `bench/stress_append.py`.
- **`--memory-limit-mib` can be the binding cap even when RAM is free.** At 50000 the guard showed
  `source flag` with ~3.5 GiB available while the host had ~17 GiB free, and evicted 30 snapshots.
  57344 with `--conversation-cache-min-free-mib 4096` let the physical check bind instead.
- **`/tmp` is a RAM-backed tmpfs here.** Three stale `strata-fetch-*` directories held 22 GiB of
  host RAM for a day, and `MemAvailable` looked 15 GiB worse than the engine deserved. Check
  `df /tmp` before sizing the parking budget.
- **A backup taken after an edit is not a backup.** One interrupted command edited the config before
  it was rejected, so a file named `...-groups2` actually held `groups 1 + --batch-mtp`, and every
  long-context run in the first pass used the slower variant. Confirm the engine's `engine started`
  line, not the filename, before trusting a result.

### Settings in `reference/strata-iq3_s-abliterated.json`

`parallel 4`, `--batch-groups 2`, `--kv k8v4 --kv-resident 65536`, `--conversation-cache-mib 20480`,
`--conversation-cache-slots 8`, `--memory-limit-mib 57344`, `--conversation-cache-min-free-mib 4096`.
Soak time on these exact settings is about 40 minutes, not hours; section 9's 6.5 h figure was
taken on the older 30720 MiB / 12 slot settings.
