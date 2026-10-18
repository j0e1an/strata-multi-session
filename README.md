# Strata + the uncensored Qwen3.8 build, tuned for many concurrent sessions

A setup guide for running [Strata](https://github.com/Niko1221/Strata) with the
**abliterated (uncensored) Qwen3.8-Flash-Next GGUF**, plus a script that sizes the
engine's caching flags for the hardware it runs on so that **several long-running agent
sessions can share one engine without it dying.**

Nothing here is theoretical. Every number was measured on the two boxes below.

| | reference box | second box |
|---|---|---|
| CPU | AMD Ryzen 7 9800X3D (8c/16t) | Apple M5 Max |
| GPU | 2x RTX 5090, 62 GiB VRAM | 128 GiB unified memory |
| Host RAM | 59 GiB | 128 GiB |
| OS | Linux | macOS |

## The problem, in one paragraph

Run four or five Qwen Code (or any agent) sessions at once against one local Strata
engine and a session dies with:

```
API Error: the engine stopped unexpectedly (exit code 1); the next request restarts it
```

This is **not** a context-window overflow. It is a host-RAM allocation failure inside
Strata's `--prompt-cache` checkpoint save, which has no admission guard. The engine's
own log says so:

```
checkpoint save: conversation snapshot running-state copy: out of memory
```

`apply-concurrency-fix.sh` sizes and applies the flags that stop it.

## Quick start

```bash
git clone https://github.com/j0e1an/strata-multi-session
cd strata-multi-session
./apply-concurrency-fix.sh --explain ~/Strata/strata-<yourprofile>.json   # see the math
./apply-concurrency-fix.sh            ~/Strata/strata-<yourprofile>.json   # apply
```

Then restart the engine. The flags are read at launch, not hot-reloaded.

---

## Part 1 - install Strata

```bash
git clone https://github.com/Niko1221/Strata ~/Strata
cd ~/Strata
python3 -m venv .venv && .venv/bin/pip install -e .
```

On macOS the Metal path builds its own binary at `~/Strata/build-metal/strata` — note
that it is **not** at `~/Strata/engine/strata`, which is the Linux location. Checking
`--help` against the wrong path returns an empty flag list and looks like the flags are
unsupported when they are not.

## Part 2 - get the uncensored model

The build is **SC117's abliterated GSQ-RCO IQ3_S** on Hugging Face:
`SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF`. It arrives as two shards:

| shard | what it is | size |
|---|---|---|
| `...-00001-of-00002.gguf` | the abliterated weights | 51.28 GiB |
| `...-00002-of-00002.gguf` | the 28.8 GB PLE expert table | byte-identical to the stock IQ3_S file |

`fetch-abliterated-iq3s.sh` downloads shard 1, verifies it against Hugging Face's LFS
SHA-256, and **hard-links** shard 2 from a stock IQ3_S download instead of pulling it
twice. It is resumable (`-C -` with retries) and marks completion with a `.done` file.

```bash
./fetch-abliterated-iq3s.sh          # ~51 GiB, verify reads it all back
```

### A fresh pack is mandatory

This trips people up. The abliterated build is a **tensor transplant**, which moves
expert offsets. The stock pack's `native_experts.txt` no longer matches these files, and
the engine refuses it outright:

```
ArenaExpertSource: the pack's native_experts.txt does not match the model
```

`prepare-abliterated-pack.sh` builds the pack with two `iq_pack.py` passes — index,
tokenizer and dense first, then the 47 GB `experts.bin` for low-RAM resident mode. It
waits for the download's `.done` marker, so you can start both in sequence.

```bash
./prepare-abliterated-pack.sh        # the experts pass is one-time and slow
```

## Part 3 - run it

Serve the model with a Strata config (`reference/strata-iq3_s-abliterated.json` is the
one these notes were written against). Its paths are `/home/YOU/...` placeholders —
edit them, or generate your own with Strata's setup flow and just run Part 4 on it.

```bash
.venv/bin/python serve/server.py --engine strata \
  --config ~/Strata/strata-iq3_s-abliterated.json --port 18080
```

## Part 4 - apply the concurrency fix

This is the part that is not in Strata's own docs.

```bash
./apply-concurrency-fix.sh --explain ~/Strata/strata-<profile>.json
```

```
detected  Linux | 59 GiB RAM | 16 cores | NVIDIA GeForce RTX 5090 + NVIDIA GeForce RTX 5090
          MemAvailable 17 GiB, MemFree 11 GiB, swap 7 GiB
          2 GPU(s), 62 GiB VRAM
          note: MemAvailable sits 6 GiB above MemFree -- that gap is reclaimable page
          cache, which is exactly why --memory-limit-mib exists.
model     max-context 262144, kv int8, engine port 18080
live      engine is UP: parked 11/12, evictions 11, budget 30720 MiB

sizing    sessions 4  <- default (pass --sessions N to set it)
          snapshot   1907 MiB  (1.86 GiB)   <- measured from /metrics
          budget    30720 MiB  (30.0 GiB)   <- 4 sessions x 2 parked each x 1.86 GiB =
                                               14.9 GiB; raised to the running 30 GiB
                                               because the engine is evicting
          slots        17                  <- 30 GiB budget / 1.86 GiB per snapshot
          min-tokens  52428                 <- one fifth of the 262144-token context
          checkpoints 3 every 65536         <- 59 GiB box: trim to 3 / 64k-token interval
          memory-limit 53831 MiB            <- MemTotal - 6653 MiB reserve

check     slots x snapshot = 31.7 GiB vs budget 30.0 GiB -> budget binds first (good)
check     fully parked cache needs 31.7 GiB -> 20.9 GiB still free above the reserve
```

It is idempotent — previous instances of its flags are stripped before the new set is
appended — and it writes a one-time backup as `<config>.bak-multisession` that later
runs never overwrite.

### What each flag does

| flag | why |
|---|---|
| `--conversation-cache-mib` | budget for parked conversation snapshots |
| `--conversation-cache-slots` | how many conversations can be parked at once |
| `--conversation-cache-min-tokens` | never park conversations too small to be worth it |
| `--prompt-cache` | resident checkpoints per request (engine default 6) |
| `--prompt-cache-every` | checkpoint interval in tokens (engine default 16384) |
| `--memory-limit-mib` | replaces `MemAvailable` in the guard's admission math |

### How the values are derived

Nothing is a magic constant. The script detects:

- **host RAM** — `MemTotal` / `MemAvailable` on Linux, `hw.memsize` on macOS
- **GPU and VRAM** — `nvidia-smi`, or Apple Silicon unified memory
- **the model itself** — `--max-context` and `--kv`, read back out of your config
- **observed behaviour** — if the engine is up, the **real** average snapshot size from
  `/metrics` (`bytes / parked`) replaces the theoretical figure

and derives:

- **snapshot size** — measured if the engine is up, otherwise
  `17,406 bytes/token x max-context x kv factor`. The 17,406 B/token figure is measured
  (`snapshot_bytes=3,892,476,616` for 223,627 tokens), at `--kv int8`.
- **budget** — `sessions x parked-per-session x snapshot`, capped by what host RAM can
  back. Parked-per-session is read from the live engine when possible: one session parks
  **more than one** conversation over time (four sessions produced 99 parks against 12
  resident slots), and sizing as `sessions x one snapshot` badly under-provisions it.
- **slots** — `budget / snapshot`, rounded **up**. If `slots x snapshot < budget`, the
  surplus budget is dead capacity and slots evict instead.
- **min-tokens** — one fifth of the context window.
- **memory-limit** — `MemTotal - reserve`, Linux only.

Two guards make it safe to run on a box that is already working:

1. **Pressure floor.** If the engine is evicting right now, the running numbers are
   already too small, so the script never proposes anything below them.
2. **Observed demand.** If the cache is full with evictions, the slot count is already
   insufficient, so it sizes up rather than down to a default.

The one thing hardware cannot decide is how many concurrent sessions you want. That is
`--sessions N`.

## Part 5 - watch it

```bash
./park-watch.sh            # one heartbeat per 45s, plus any crash signature
./park-watch.sh --once     # single heartbeat, for testing
```

```
parked 7/12  budget 13770/30720 MiB (45%)  evict 24  parks 204  restore 173
reuse 198/255 (22.4M tok)  MemFree 15.1 GiB  MemAvail 21.2 GiB
```

It prints `DANGER:` for the crash signatures and for engine restarts. Its stderr is
deliberately not suppressed — a broken heartbeat must surface rather than go silent.
That is a lesson learned the hard way: an early version had a `str.format()` bug that
raised on every poll, `2>/dev/null` swallowed the traceback, and the monitor printed
nothing for its entire life.

---

## Reading the numbers

**The three failures look alike and are not.** Tell them apart before changing anything:

| failure | signature | exit code |
|---|---|---|
| context overflow | `the prompt exceeds the context` | n/a |
| kernel OOM kill | `Killed process` in `dmesg` | **-9** |
| this one | `running-state copy: out of memory` | **1** |

**Two ceilings, and they are different.** VRAM caps concurrency at one decoder
(`parallel: 1`) — a second batch slot needs "VRAM like the main one" per slot, and there
was 0.96 GiB free. Host RAM caps how many sessions stay warm. Concurrency and warmth are
separate problems with separate fixes.

**An eviction storm makes RAM look healthier while reuse gets worse.** During one storm
the budget *fell* 45% → 27% while `MemFree` *rose* 9.7 → 14.6 GiB, because the evicted
snapshots were larger than the incoming ones. Judge the cache by `parked` against
`slots`, not by free RAM.

**Do not read a cgroup's lifetime `MemoryPeak` as current headroom.** It is a high-water
mark. On this box it read 52.94 GiB while steady state was ~17 GiB used with ~37 GiB
genuinely free — and that misreading is what made an earlier pass of this work conclude
that parking was unaffordable when it was not.

## What is still unverified

- The five-session figure is owner-reported. Every measurement here saw four live
  sessions.
- How much VRAM `--max-context 131072` frees, which is the only route to `parallel: 2`.
- Whether 12 slots is enough under sustained load. It held at zero evictions across 52
  parks, then the cache filled and evictions resumed as sessions grew past 2 GiB each.

## Files

| file | what |
|---|---|
| `apply-concurrency-fix.sh` | detect hardware, size and apply the flags |
| `park-watch.sh` | the monitor |
| `fetch-abliterated.sh` | download + verify the model |
| `prepare-abliterated-pack.sh` | build the Strata pack it requires |
| `reference/strata-iq3_s-abliterated.json` | the shipping config |
| `TUNING.md` | the full diagnosis, root cause and measurements |

## Credits and licence

Strata is by [Niko1221](https://github.com/Niko1221/Strata), MIT-licensed. The model is
SC117's abliterated GSQ-RCO build — check the licence on the Hugging Face repo before
redistributing weights. The scripts and notes here are MIT.
