# Hardware-aware setup for Strata + the uncensored model

Design for extending `strata-multi-session` from "patch a config you already have"
into "set up the whole thing on a machine you have never touched".

Written 2026-10-17. Revised the same day after a builder review; the revisions are
marked. Every number here names its source — upstream's `setup.py`, a file in this
repo, or a measurement on the reference box. A number with no source is a bug in
this document, not a detail.

## 1. Intent

The repo today documents one fix (multi-session parking, so the engine stops dying
with `exit code 1`) and ships one script that sizes that fix for whatever box runs
it. It assumes the reader is already running Strata, already running the IQ3_S
abliterated build, and already has ~60 GiB of RAM.

The reader is not that person. The reader has some other box and wants the same
thing: an uncensored Qwen3.8-Flash-Next endpoint that survives several concurrent
sessions. So the tool has to answer the question *before* the concurrency question:
**can this machine run this model, and which build of it?**

Partner's framing, verbatim: "not all machine have the same hardware we have so
this script is to help others to do the same but cater to their hardware."

## 2. Goals

- Detect the box — RAM, VRAM, GPU count and names, OS, cores, free disk — and
  recommend a specific tier from it, with the arithmetic shown.
- Reach the **whole upstream catalog**, not just the four abliterated quants
  (partner's decision).
- Download the model to a **location the user chooses**, after checking free disk.
- Install Strata itself when it is missing, by delegating to upstream's installer
  (partner's decision).
- Apply the concurrency flags — the existing script, unchanged in purpose.
- Offer **per-user auto-start** that needs no sudo, on Linux and macOS (partner's
  decision).
- Offer **CPU vision** as the default vision mode on Linux, at a 1024 image-token
  cap (partner's decision; see §10 for why this is Linux-only).

## 3. Non-goals

- **Not a new installer.** Upstream's `setup.py` already does venv, engine
  download-or-compile, resumable GGUF fetch, pack build, MTP fetch, and writing the
  run script. Reimplementing that duplicates logic that moves every release.
- **Not a general model server.** One model family: Qwen3.8-Flash-Next and its
  quantized releases.
- **Not a benchmark suite.** It recommends; it does not measure the user's tokens/s.
- **Not a replacement for `TUNING.md`**, which stays the explanation of *why*.
- **Not AMD or Intel support** (partner's decision, §12).

## 4. Anti-goals

- **A number nobody can trace.** This repo's credibility is that its figures are
  measured. A recommendation built on an invented threshold is worse than no
  recommendation.
- **Overselling what is new.** Upstream already pre-selects a size from this PC's
  RAM (`setup.py:2241`). Claiming hardware-awareness as if it were novel is false.
  See §6.
- **A silent "no".** Telling someone their 24 GB card cannot run this model is a
  correct, useful answer. Saying nothing — or letting a 62 GiB download start onto
  a 40 GB disk, or running a sizing pass on a GPU the detector did not understand —
  is not.

## 5. Constraints

- POSIX `sh` entry points with embedded `python3`, matching the existing scripts.
  No new dependencies beyond `curl`, `python3`, and what Strata itself needs.
- Runs on Linux and macOS. macOS has no `nvidia-smi` and no separate VRAM figure;
  Apple Silicon unifies memory (`sysctl hw.memsize`).
- No sudo for anything **the tool itself** does. Upstream's installer is the one
  documented exception — see §13.
- Idempotent: re-running must not stack duplicate flags or re-download verified
  files. The existing script already establishes this pattern.
- The concurrency math in `apply-concurrency-fix.sh` stays as it is — measured and
  working on the reference box. The new tool calls it; it does not fork it.

## 6. What upstream already does, and what this repo adds

The line the README must draw honestly.

Upstream `setup.py` already: checks GPU/driver/RAM/CPU/free disk; pre-selects a
quant from RAM; downloads resumably with SHA-256 checks; builds the expert pack;
fetches the MTP draft layer; writes the run script; and takes every answer as a
flag (`--family`, `--model`, `--context`, `--kv`, `--vision yes|no|gpu|cpu`,
`--vision-tokens`, `--data-dir`, `--models-dir`, `--gguf-dir`, `--gpus`,
`--layer-split`, `--resident-budget-gib`, `--kv-streaming`, `--port`, `--host`,
`--api-key`, `--yes`, `--no-start`, `--check`).

Upstream does **not**:

1. Offer the abliterated build as a choice. SC117's
   `Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF` is not in `FAMILIES`, so
   `--model IQ3_S` means the *censored* ISTA-DASLab repo.
2. Size anything for **several concurrent sessions**. Its RAM figure is for one
   engine serving one context. The parked-conversation cache — the fix this repo
   publishes — is invisible to it.
3. Set up auto-start. It writes `run-<model>.sh` and runs it in the foreground.
4. Default vision to CPU, or know the encode cost curve.

### Correction from the builder review

An earlier draft said the uncensored build "cannot be installed by `setup.py` at
all." That is too strong, and the truth is more interesting: it *can*, and the way
it can is a trap.

`--gguf-dir` skips the download entirely (`to_fetch = 0 if a.gguf_dir`,
`setup.py:6411`), and `gguf_dir_shards()` falls back to globbing
`*-00001-of-*.gguf` and matching `model.lower() in p.name.lower()`
(`setup.py:1716`). `gguf_unsupported()` only rejects quants absent from `MODELS`
(`setup.py:1745`). So `...-abliterated-IQ3_S-00001-of-00002.gguf` parses as
`IQ3_S`, which is in `MODELS`, and upstream accepts it — **filing it under the
stock name**: `packs/iq3_s`, `strata-iq3_s.json`, `model_name:
qwen3.8-flash-next`. Worse, its pack step is conditional —
`elif not (pack / "native_experts.txt").exists()` (`setup.py:6521`) — so a box that
already holds the stock pack **silently reuses it**, which is exactly the
`ArenaExpertSource: the pack's native_experts.txt does not match the model` failure
this repo's README already warns about.

## 7. The install path for the uncensored model

Partner's decision, 2026-10-17: **split paths.**

- **Stock / censored tiers** → delegate to upstream. `setup.py` with the flags the
  recommender derived. Nothing new to trust.
- **The uncensored build** → this repo's own scripts, which are what actually runs
  on the reference box today: `fetch-abliterated-iq3s.sh` generalized over the four
  abliterated tiers, `prepare-abliterated-pack.sh`, and a config templated from
  `reference/`. The recommender picks among the four tiers; the tool never
  smuggles an abliterated shard past upstream's name collision.

The cost of this choice, stated: the tool owns config generation for the
uncensored path. That is deliberate — it is cheaper than depending on a smuggling
path upstream never designed for, and the scripts involved already exist and work.

The clean end state is an upstream PR adding SC117's repo to `FAMILIES`, at which
point the split collapses into one path. Noted, not depended on.

## 8. The catalog the recommender chooses from

Linux sizes are upstream's own, from `setup.py` `MODELS` (`download_gb` = the
download, `ram_gb` = RAM to keep every expert resident, `arena_gb` = the expert
arena written by the low-RAM mode). Abliterated file sizes are the real per-quant
totals read from the Hugging Face tree API on 2026-10-17.

| tier | family | download | RAM | abliterated? |
|---|---|---|---|---|
| IQ1_M | Coder (256 of 512 experts) | 58.4 GB | 32 GB | no |
| Q2_0 | qwen | 66.4 GB | 48 GB | yes — 62.23 GiB |
| IQ2_XS | qwen | 68.0 GB | 48 GB | yes — 63.76 GiB |
| IQ3_XXS | qwen | 75.8 GB | 60 GB | yes — 70.91 GiB |
| IQ3_S | qwen | 83.6 GB | 62 GB | yes — 78.10 GiB |
| UD-IQ4_XS | Unsloth | 93.7 GB | 48 GB | no |
| UD-Q4_K_XL | Unsloth | 111.3 GB | 48 GB | no |
| NVFP4 | NVIDIA only | 128.0 GB | 80 GB | no |
| UD-Q5_K_XL | Unsloth | 158.3 GB | 108 GB | no |
| UD-Q6_K_XL | Unsloth | 169.2 GB | 118 GB | no |
| Q8_0 | Unsloth | 188.2 GB | 136 GB | no |

Two facts the tool states out loud rather than smoothing over:

- **Abliteration exists at four tiers only** — Q2_0, IQ2_XS, IQ3_XXS, IQ3_S. A box
  that lands on IQ1_M or any Unsloth tier gets a *censored* model, and the tool
  says so instead of quietly substituting.
- **The Coder release is half the experts** (256 of 512, the ones code, tools and
  vision use — `setup.py` `FAMILIES.coder`). It is the only sub-48 GB option, it
  is not abliterated, and it needs a different expert profile
  (`expert-profile-coder.bin`).

### The Mac is a different catalog

`setup.py:5706` hands every darwin run to `metal/setup_mac.py`, which is not the
same program with fewer flags — it is a different installer with a narrower menu.
Verified 2026-10-17:

- `MAC_MODELS` (`metal/setup_mac.py:45`) offers **Q2_0, IQ3_XXS, IQ3_S,
  UD-Q4_K_XL, UD-Q5_K_XL, UD-Q6_K_XL**. No IQ2_XS, no Coder IQ1_M, no NVFP4, no
  UD-IQ4_XS, no Q8_0.
- Its parser (`metal/setup_mac.py:488-506`) accepts `--family {qwen,unsloth}`,
  `--model`, `--context`, `--port`, `--host`, `--api-key`, `--data-dir`,
  `--models-dir`, `--gguf-dir`, `--vision {yes,no}`, `--yes`, `--setup`,
  `--no-start`, `--check`, `--update`, `--build`. There is **no** `--vision cpu`,
  no `--vision-tokens`, no `--kv`, no `--low-ram`, no `--resident-budget-gib`, no
  `--gpus`, no `--layer-split`, no `--parallel`.
- It hardcodes `"gpu": True` in the vision block it writes
  (`metal/setup_mac.py:677`).

So the recommender carries **two catalogs and two vision paths**, and the README
says so. Pretending one table covers both OSes would produce a Mac recommendation
for a tier the Mac installer cannot install.

Context lengths come from upstream's `CONTEXTS`: 8192, 32768, 65536, 131072,
204800, 262144, 393216, 524288. Upstream's own note: 204800 sits inside the trained
262144 so it needs no rope scaling and costs ~2.8 GB of 8-bit KV with IQ3_S against
~3.6 GB at 256K — a middle step for PCs that cannot hold 256K.

## 9. The sizing math

Two ceilings, binding in a specific order.

**VRAM** caps how many conversations run at once. Measured on the reference box:
`parallel: 1` with 0.96 GiB free across 2x RTX 5090 (62 GiB). KV cost per token is
**17,406 bytes/token at `--kv int8`** — measured, not modelled:
`snapshot_bytes 3,892,476,616 / 223,627 tokens`. Other KV formats scale from that;
the engine's own help gives `k8v4` as 816 B/cell against int8's 1,056, which is the
check on the scaling.

**Host RAM** caps how many warm sessions survive, which is what the concurrency
flags size. Measured parked-snapshot cost on this box: **1.86 GiB average** (live
`/metrics`, 4 sessions).

The tool prints both ceilings, says which one binds, and names the flag that
relieves it — context length and KV format for the VRAM ceiling, budget and slots
for the RAM ceiling. A ceiling that drives no flag is decoration.

### First-run sizing, before any park has happened

The theoretical figure — 17,406 B/token × `--max-context` — is **4.25 GiB** at
262144/int8, against the **1.86 GiB** actually measured on the box those numbers
came from. Sizing a fresh install from the raw worst case is ~2.3x pessimistic, and
on a 60 GB box that is the difference between "four sessions fit" and "budget
exhausted at four slots".

**My call:** two phases. Phase one sizes from the measured per-token rate applied to
an *expected conversation length* rather than the whole context window, and labels
the number as an estimate. Phase two re-runs `apply-concurrency-fix.sh` once the
engine has parked something, at which point the script's existing live-`/metrics`
path replaces the estimate with a measurement. The tool tells the user to expect the
second pass instead of pretending the first one is final.

Phase two is not refinement. **The measured value itself moves.** On the reference
box the average parked snapshot was 1.86 GiB at 15:00 and **2.22 GiB at 21:00 the
same day** — 20% in six hours, as the live conversations grew longer. At 2.22 GiB
the same 30 GiB budget holds 13 slots rather than 16, and a 17-slot plan made that
afternoon was stale by evening. Any sizing figure in this tool therefore carries an
expiry: it is a reading of a running engine, not a property of the model.

## 10. Vision

Default `cpu`, cap `max_tokens: 1024` — **on Linux**.

The CPU encoder is the reference box's choice and it costs text generation nothing
measurable: decode 251 → 242 tok/s with ranges 218-299 against 235-252, overlapping;
VRAM 32102/31604 → 31872/31596 MiB. `server.py` adds `--vision` to the engine args
when the config has a `vision` section (`serve/server.py:2582`) and adds
`--vram-reserve-mib` only when `gpu: true` — so CPU vision is just the section.

The cap is a measured tradeoff, and the tool shows this table when it asks:

| image tokens | encode, CPU, 8 threads |
|---|---|
| 300 | 1.29 s |
| 768 | 5.9 s |
| 972 | 8.7 s |
| 2028 | 31.8 s |
| 4015 (uncapped) | 148 s |

All measured 2026-10-17 on this box. Cost is superlinear — 2.56x the tokens cost
4.6x the time — so linear extrapolation from "3 s at 300 tokens" is wrong. mtmd
warns that Qwen-VL needs at least 1024 image tokens for grounding, and
`docs/DETAILS.md` #767 says a larger value changes the answers, not only the speed.

The mmproj is one file for every Flash-Next quant:
`mmproj-Qwen3.8-Flash-Next-BF16.gguf`, 907,543,008 bytes, SHA-256
`b1a82259702816a5330d7bd7607cd9676b11780e79ff7348c21103ff3ce49bd0`
(sizes and hash from `setup.py:423`). It works against the abliterated shard
because `strata-vision` opens the text model vocab-only, no weights.

**On macOS this section does not apply.** The Mac's `strata-vision` is built with
ggml's Metal backend and encodes sub-second, and `setup_mac.py` offers only
`--vision yes|no` while hardcoding `gpu: True`. The tool must not quote the CPU
encode curve on a Mac, and must not promise a CPU encoder there.

## 11. Auto-start

Per-user, no sudo, chosen by OS:

- **Linux**: a systemd *user* unit at `~/.config/systemd/user/strata.service`,
  enabled with `systemctl --user enable`, offering `loginctl enable-linger $USER`
  so it starts without anyone logging in. Logs through `journalctl --user -u strata`.
- **macOS**: a LaunchAgent plist at `~/Library/LaunchAgents/dev.strata.plist` with
  `RunAtLoad` and `KeepAlive`, logs to files under the data directory.

Both write the file, enable it, and print how to undo it. Neither touches a system
unit; neither needs a password.

**My calls here.** The unit manages Strata's own `serve/server.py`, never
llama-swap — this box routes through a router, but a fresh install should not
inherit that assumption. If a llama-swap *is* detected, the tool says so and leaves
the router alone rather than managing two things. And the unit does not exec
upstream's `run-<model>.sh` blindly: `write_run_script()` (`setup.py:5388`) appends
`--open` unless `--no-browser` was passed, which inside a systemd unit means trying
to launch a browser on a headless box.

## 12. GPU detection scope

Partner's decision, 2026-10-17: **NVIDIA and Apple Silicon only.**

Detection is `nvidia-smi` on Linux and `sysctl hw.memsize` on macOS. Anything else —
Radeon, Intel Arc — gets an explicit "this tool detects NVIDIA and Apple Silicon
only" and stops, rather than a sizing run built on a VRAM figure of zero. Upstream
does support AMD (`--backend hip`, `build_engine_hip()`, `amd_mem_text()`) and Intel
(`sycl/setup_intel.py`), so this is a limitation of *this tool*, stated as such, not
a claim about Strata.

## 13. Where no-sudo stops

**My call.** The tool itself never needs root. Upstream's installer does when the
box has no Python 3.10+ with `ensurepip`: `setup.sh` says plainly "Python 3.10+ with
venv is needed; installing it (sudo will ask for your password)" and runs
`sudo apt-get install -y python3 python3-venv python3-pip`. The same is true of the
CUDA driver, which no installer here can provide without root.

So the tool pre-checks for a usable Python and, when it is missing, stops with the
exact package command to run — rather than silently inheriting a sudo prompt in the
middle of what was sold as a rootless install. The README lists this as the one
exception.

## 14. Which measured constants may be quoted where

**My call.** `17,406 B/token`, the `1.86 GiB` average snapshot, "three parked
conversations per session", and the CPU encode curve were all measured on
GSQ-RCO IQ3_S at 262144 with `--kv int8`. They may be quoted for the qwen-family
GSQ-RCO tiers. For IQ1_M, the Unsloth tiers and NVFP4 — different shard counts,
`budget: True` modes, a different expert profile — the tool prints "not measured on
this tier" next to any figure it carries over.

The KV rate is plausibly architecture-general, since the attention stack is the same
and only the expert count changes. Plausible is not measured, and §4 makes that
distinction an obligation, so it is stated as an assumption rather than a fact.

## 15. What is left to the builder

- Exact flag names on the new entry point, and how much is one script versus a
  small set that composes. Repo style is one script per job with a shared detection
  idiom; follow it.
- Whether the quant table is a literal in the script or a data file. It must work
  offline, with `--refresh` to re-query Hugging Face.
- How the tool detects that Strata is already installed, and where.
- The pack-build step for abliterated tiers other than IQ3_S: each needs its own
  expert pack, built by two passes over 55-78 GB. Real time cost; say how long
  before starting it.
- Order of operations when the hardware supports an abliterated tier but the user
  would rather trade censorship for experts: ask, and show the tradeoff in one line.

## 16. Verified shard table for the four abliterated tiers

Read from the Hugging Face tree API on 2026-10-17. Sizes in bytes; hashes are LFS
SHA-256 oids, first 16 hex shown, full values to ship in the table.

| tier | shard 1 bytes | shard 1 oid | shard 2 |
|---|---|---|---|
| Q2_0 | 38,021,379,872 | `1620faf21c845524…` | shared |
| IQ2_XS | 39,665,015,072 | `4acc3ebb5050d201…` | shared |
| IQ3_XXS | 47,342,144,896 | `03b11926b41a3d7f…` | shared |
| IQ3_S | 55,063,448,064 | `40f369eca301ec6e…` | shared |

**Shard 2 is byte-identical across all four tiers**: 28,800,138,432 bytes, oid
`316b46f3a2dbd68c…` — the same value `fetch-abliterated-iq3s.sh` already pins. It is
the n-gram/PLE table, which does not depend on the expert quantization. So the
hard-link trick that already saves 28 GB on IQ3_S generalizes to every tier, and the
tool should say so before starting a download.

**My call:** ship all eight shard sizes and oids hardcoded, so verification works
with no network; `--refresh` re-queries the Hub when a tier changes.
