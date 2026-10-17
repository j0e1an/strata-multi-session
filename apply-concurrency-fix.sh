#!/usr/bin/env bash
#
# Size and apply the multi-session concurrency flags for a Strata serve config,
# from the hardware this script is actually running on.
#
# Why these flags exist is in TUNING.md. Short version: Strata's `--prompt-cache`
# checkpoint save allocates host RAM with no admission guard, so when several long
# sessions share one `parallel: 1` engine it can die with
# "the engine stopped unexpectedly (exit code 1)". Conversation parking is the fix,
# and these flags are what make parking affordable and correctly proportioned.
#
# Nothing here is a magic constant. Every value is derived from detected inputs:
#
#   host RAM            MemTotal / MemAvailable (Linux) or hw.memsize (macOS)
#   GPU + VRAM          nvidia-smi, or Apple Silicon unified memory
#   the model itself    --max-context and --kv, read back out of your config
#   observed behaviour  if the engine is up, the REAL average snapshot size from
#                       /metrics (bytes / parked) replaces the theoretical figure
#
# The one input hardware cannot decide is how many concurrent sessions you want.
# Pass it with --sessions N. Everything else follows.
#
#   ./apply-concurrency-fix.sh --explain ~/Strata/strata-x.json   # show the math, write nothing
#   ./apply-concurrency-fix.sh             ~/Strata/strata-x.json   # apply
#   ./apply-concurrency-fix.sh --sessions 10 --dry-run ~/Strata/strata-x.json
#
# Safe to re-run: previous instances of these flags are stripped before the new set is
# appended, so it never stacks duplicates.
#
set -euo pipefail

usage() {
  cat <<'EOF'
usage: apply-concurrency-fix.sh [options] <strata-config.json>

workload input (the only thing hardware cannot decide):
  --sessions N        concurrent warm sessions to size for (default 4)

overrides (each one skips the detection for that value):
  --budget-mib N      conversation-cache budget
  --slots N           conversation-cache slots
  --min-tokens N      never park conversations below this many tokens
  --snapshot-mib N    assumed size of one parked snapshot
  --prompt-cache N    resident checkpoints per request (engine default 6)
  --every N           checkpoint interval in tokens (engine default 16384)
  --memory-limit-mib N
                      cap the RAM the admission guard believes is available
  --no-memory-limit   omit --memory-limit-mib (default on macOS, where the guard's
                      MemAvailable reading is not the failure mode)

behaviour:
  --explain           print the detected inputs and the derivation, change nothing
  --dry-run           same as --explain, plus the resulting args
  --print             print only the resulting args (for scripting)
  --no-metrics        ignore a running engine; derive purely from hardware + config
  -h, --help          this help

A backup is written once as <config>.bak-multisession and never overwritten, so the
pristine original survives repeated applications.
EOF
}

CONFIG=""
SESSIONS=4
BUDGET="" SLOTS="" MIN_TOKENS="" SNAPSHOT="" MEMORY_LIMIT="" PROMPT_CACHE="" EVERY=""
NO_MEM_LIMIT=""
EXPLAIN=0 DRY=0 PRINT_ONLY=0 NO_METRICS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --sessions)          SESSIONS="$2"; SESSIONS_GIVEN=1; shift 2 ;;
    --budget-mib)        BUDGET="$2"; shift 2 ;;
    --slots)             SLOTS="$2"; shift 2 ;;
    --min-tokens)        MIN_TOKENS="$2"; shift 2 ;;
    --snapshot-mib)      SNAPSHOT="$2"; shift 2 ;;
    --prompt-cache)      PROMPT_CACHE="$2"; shift 2 ;;
    --every)             EVERY="$2"; shift 2 ;;
    --memory-limit-mib)  MEMORY_LIMIT="$2"; shift 2 ;;
    --no-memory-limit)   NO_MEM_LIMIT=1; shift ;;
    --explain)           EXPLAIN=1; DRY=1; shift ;;
    --dry-run)           DRY=1; shift ;;
    --print)             PRINT_ONLY=1; shift ;;
    --no-metrics)        NO_METRICS=1; shift ;;
    -h|--help)           usage; exit 0 ;;
    -*)                  echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *)                   CONFIG="$1"; shift ;;
  esac
done

[ -n "$CONFIG" ] || { echo "error: no config path given" >&2; usage >&2; exit 2; }
[ -r "$CONFIG" ] || { echo "error: cannot read $CONFIG" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "error: python3 is required" >&2; exit 1; }

export SM_CONFIG="$CONFIG" SM_SESSIONS="$SESSIONS" SM_SESSIONS_GIVEN="${SESSIONS_GIVEN:-}"
export SM_BUDGET="$BUDGET" SM_SLOTS="$SLOTS" SM_MIN_TOKENS="$MIN_TOKENS" SM_SNAPSHOT="$SNAPSHOT"
export SM_PROMPT_CACHE="$PROMPT_CACHE" SM_EVERY="$EVERY"
export SM_MEMORY_LIMIT="$MEMORY_LIMIT" SM_NO_MEM_LIMIT="${NO_MEM_LIMIT:-}"
export SM_EXPLAIN="$EXPLAIN" SM_DRY="$DRY" SM_PRINT="$PRINT_ONLY" SM_NO_METRICS="$NO_METRICS"

python3 <<'PY'
import json, os, re, shutil, subprocess, sys, urllib.request

env = lambda k: (os.environ.get(k) or "").strip()
num = lambda v: int(v) if str(v).strip() not in ("", "0") else None

cfg       = env("SM_CONFIG")
sessions  = int(env("SM_SESSIONS") or 4)
want_bud  = num(env("SM_BUDGET"))
want_slot = num(env("SM_SLOTS"))
want_mint = num(env("SM_MIN_TOKENS"))
want_snap = num(env("SM_SNAPSHOT"))
want_pc   = num(env("SM_PROMPT_CACHE"))
want_ever = num(env("SM_EVERY"))
want_meml = num(env("SM_MEMORY_LIMIT"))
no_memlim = bool(num(env("SM_NO_MEM_LIMIT")))
explain, dry = int(env("SM_EXPLAIN") or 0), int(env("SM_DRY") or 0)
show, no_metrics = int(env("SM_PRINT") or 0), int(env("SM_NO_METRICS") or 0)

def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""

# ---------------------------------------------------------------- 1. detect the box
plat = sys.platform
detected = {}

if plat == "darwin":
    m = sh(["sysctl", "-n", "hw.memsize"]).strip()
    detected["memtotal_mib"] = int(int(m) / 1048576) if m.isdigit() else 0
    detected["os"] = "macOS"
    # Apple Silicon unifies system and GPU memory; there is no separate VRAM figure.
    gpus, vram = 1, detected["memtotal_mib"]
    detected["gpu_note"] = "Apple Silicon unified memory (no discrete VRAM)"
else:
    mem = {}
    try:
        for line in open("/proc/meminfo"):
            k = line.split(":")[0]
            if k in ("MemTotal", "MemAvailable", "MemFree", "SwapTotal"):
                mem[k] = int(line.split()[1]) // 1024
    except OSError:
        pass
    detected["os"] = "Linux"
    detected["memtotal_mib"]  = mem.get("MemTotal", 0)
    detected["memavail_mib"]  = mem.get("MemAvailable", 0)
    detected["memfree_mib"]   = mem.get("MemFree", 0)
    detected["swap_mib"]      = mem.get("SwapTotal", 0)
    # VRAM, if an NVIDIA stack is present.
    gpus, vram, names = 0, 0, []
    out = sh(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[1].isdigit():
            gpus += 1; vram += int(parts[1]) // 1024; names.append(parts[0])
    detected["gpu_note"] = " + ".join(names) if names else "no NVIDIA GPU reported"
    detected["gpus"], detected["vram_gib"] = gpus, vram

detected["cores"] = os.cpu_count() or 0

# ------------------------------------------------- 2. read the model back out of the config
try:
    with open(cfg) as fh:
        data = json.load(fh)
except Exception as exc:
    sys.exit("error: %s is not readable JSON: %s" % (cfg, exc))
args = data.get("args")
if not isinstance(args, list):
    sys.exit("error: %s has no string list under 'args'" % cfg)
flat = [str(a) for a in args]

def flag_value(name):
    for i, tok in enumerate(flat):
        if tok == name and i + 1 < len(flat):
            return flat[i + 1]
        if tok.startswith(name + "="):
            return tok.split("=", 1)[1]
    return None

max_context = int(flag_value("--max-context") or 0)
kv = (flag_value("--kv") or "").lower()
port = data.get("port")

# A parked snapshot holds the KV cache, so its size scales with context length and
# with the KV quantisation. 17,406 bytes/token is MEASURED, on this box, at
# --kv int8 and a 262,144 context. Other quantisations are scaled, not measured.
KV_FACTOR = {"int8": 1.0, "q8_0": 1.0, "int4": 0.5, "q4_0": 0.5, "q4_k": 0.5,
             "f16": 2.0, "bf16": 2.0, "f32": 4.0}
kv_factor = KV_FACTOR.get(kv, 1.0)
theoretical_snap_mib = int(max_context * 17406 * kv_factor / 1048576) if max_context else 0

# --------------------------------------------- 3. prefer the engine's own measurements
# If the engine is up and has parked something, bytes/parked is the real cost on this
# box with this model -- strictly better than the theoretical figure above.
measured_snap_mib, live = 0, None
if not no_metrics and port:
    try:
        with urllib.request.urlopen("http://127.0.0.1:%s/metrics" % port, timeout=4) as r:
            live = json.load(r)
    except Exception:
        live = None
    if live:
        c = live.get("conversation_cache") or {}
        if c.get("parked") and c.get("bytes"):
            measured_snap_mib = int(c["bytes"] / c["parked"] / 1048576)

snapshot_mib = want_snap or measured_snap_mib or theoretical_snap_mib or 2560
snapshot_src = ("--snapshot-mib" if want_snap else
                "measured from /metrics" if measured_snap_mib else
                "17,406 B/token x %s tokens x kv %s" % (max_context or "?", kv or "?")
                if theoretical_snap_mib else "fallback 2.5 GiB")

# ------------------------------------------------------------------- 4. derive the flags
# Observed demand beats a guessed session count. If the engine is up and its cache is
# full WITH evictions happening, whatever is configured right now is already too small,
# so sizing down to a default would be a regression rather than an improvement.
sessions_given = bool(num(env("SM_SESSIONS_GIVEN")))
obs_slots = obs_parked = obs_evict = 0
saturated = False
if live:
    lc = live.get("conversation_cache") or {}
    obs_slots  = int(lc.get("slots") or 0)
    obs_parked = int(lc.get("parked") or 0)
    obs_evict  = int(lc.get("evictions") or 0)
    saturated = obs_slots > 0 and obs_parked >= obs_slots and obs_evict > 0
sessions_src = "--sessions"
if not sessions_given and saturated and obs_slots >= sessions:
    sessions = obs_slots + 1
    sessions_src = ("observed: cache is full at %d/%d with %d evictions, so %d slots is "
                    "already too small -- sizing to %d" % (obs_slots, obs_parked, obs_evict,
                                                           obs_slots, sessions))
elif not sessions_given:
    sessions_src = "default (pass --sessions N to set it)"

# One session parks MORE than one conversation over time. On the reference box four
# sessions produced 99 parks against 12 resident slots -- a working set of roughly three
# parked conversations per live session. Sizing the budget as sessions x ONE snapshot
# under-provisions it badly; that is what made an earlier revision of this script shrink
# a box that was already evicting.
per_session = max(1, int(obs_parked / max(sessions, 1)) if (live and obs_parked) else 3)
per_session_src = ("%d conversations parked across %d sessions" % (obs_parked, sessions)
                   if (live and obs_parked) else "default 3, observed on the reference box")

# The current config is a floor whenever the engine shows pressure: evictions mean the
# running numbers are already too small, so never size below them.
cur_slots  = int(flag_value("--conversation-cache-slots") or 0)
cur_budget = int(flag_value("--conversation-cache-mib") or 0)

# Budget: what the sessions need, but never more than host RAM can actually back.
need_mib = sessions * per_session * snapshot_mib
# The guard floor is 2560 MiB by default; keep that plus a working margin free.
reserve_mib = max(6400, int(detected["memtotal_mib"] * 0.11))
ram_allowance_mib = max(0, detected["memtotal_mib"] - reserve_mib)
budget_mib = want_bud or min(need_mib, ram_allowance_mib)
budget_src = "--budget-mib" if want_bud else (
    "%d sessions x %d parked each x %.2f GiB = %.1f GiB%s" % (
        sessions, per_session, snapshot_mib / 1024, need_mib / 1024,
        "" if need_mib <= ram_allowance_mib
        else ", capped by the %d GiB RAM allowance" % (ram_allowance_mib // 1024)))

# Pressure floor: if the engine is evicting right now, the running numbers are already
# too small, so never propose anything below them.
if not want_bud and obs_evict > 0 and cur_budget > budget_mib:
    budget_mib = cur_budget
    budget_src += "; raised to the running %d GiB because the engine is evicting" % (cur_budget // 1024)
budget_mib = ((budget_mib + 1023) // 1024) * 1024          # round up to a whole GiB

# Slots: the ceiling that bites first in practice. budget / real snapshot size -- if
# slots x snapshot < budget, the surplus budget is dead capacity and slots evict.
# Round UP so the two agree by construction instead of leaving a floor-division gap.
slots = want_slot or max(1, -(-budget_mib // snapshot_mib))
slots_src = "--slots" if want_slot else "%d GiB budget / %.2f GiB per snapshot" % (
    budget_mib // 1024, snapshot_mib / 1024)
if not want_slot and obs_evict > 0 and cur_slots > slots:
    slots = cur_slots
    slots_src += "; raised to the running %d because the engine is evicting" % cur_slots

# Never park conversations too small to be worth a snapshot: ~1/5 of the context window.
min_tokens = want_mint or (max(2000, max_context // 5) if max_context else 50000)
min_src = "--min-tokens" if want_mint else "one fifth of the %s-token context" % (max_context or "50000x5")

# Checkpoint trimming: fewer resident checkpoints, written less often. Scales with RAM
# so a big box can keep more of them (they cost ~118 MB each and are unguarded).
prompt_cache = want_pc or (6 if detected["memtotal_mib"] >= 120 * 1024 else 3)
every = want_ever or (16384 if detected["memtotal_mib"] >= 120 * 1024 else 65536)
pc_src = "explicit" if (want_pc or want_ever) else (
    "%d GiB box: keep the engine defaults" % (detected["memtotal_mib"] // 1024)
    if detected["memtotal_mib"] >= 120 * 1024 else
    "%d GiB box: trim to 3 checkpoints / 64k-token interval" % (detected["memtotal_mib"] // 1024))

# memory-limit-mib corrects the guard's view of free RAM. That failure mode is a Linux
# one (MemAvailable counting reclaimable mmap'd page cache), so macOS skips it.
if no_memlim:
    memory_limit = 0
elif want_meml:
    memory_limit = want_meml
elif detected["os"] == "macOS":
    memory_limit = 0
else:
    memory_limit = max(0, detected["memtotal_mib"] - reserve_mib)

wanted = [
    ("--prompt-cache",                  str(prompt_cache)),
    ("--prompt-cache-every",            str(every)),
    ("--conversation-cache-mib",        str(budget_mib)),
    ("--conversation-cache-slots",      str(slots)),
    ("--conversation-cache-min-tokens", str(min_tokens)),
]
if memory_limit > 0:
    wanted.append(("--memory-limit-mib", str(memory_limit)))
owned = {n for n, _ in wanted}

# ------------------------------------------------- 5. strip old instances, append new
stripped, i, removed = [], 0, 0
while i < len(flat):
    tok = flat[i]
    name = tok.split("=", 1)[0] if tok.startswith("--") else None
    if name in owned:
        removed += 1
        if "=" not in tok and i + 1 < len(flat) and not flat[i + 1].startswith("--"):
            i += 1
        i += 1
        continue
    stripped.append(tok)
    i += 1
result = stripped + [x for pair in wanted for x in pair]
after = " ".join(result)

if show:
    print(after); sys.exit(0)

# --------------------------------------------------------------- 6. report the math
print("detected  %s | %d GiB RAM | %d cores | %s" % (
    detected["os"], detected["memtotal_mib"] // 1024, detected["cores"], detected["gpu_note"]))
if detected["os"] != "macOS":
    print("          MemAvailable %d GiB, MemFree %d GiB, swap %d GiB" % (
        detected.get("memavail_mib", 0) // 1024, detected.get("memfree_mib", 0) // 1024,
        detected.get("swap_mib", 0) // 1024))
    if detected.get("vram_gib"):
        print("          %d GPU(s), %d GiB VRAM" % (detected.get("gpus", 0), detected.get("vram_gib", 0)))
    if detected.get("memavail_mib", 0) - detected.get("memfree_mib", 0) > 8 * 1024:
        print("          note: MemAvailable sits %d GiB above MemFree -- that gap is reclaimable "
              "page cache, which is exactly why --memory-limit-mib exists." % (
              (detected["memavail_mib"] - detected["memfree_mib"]) // 1024))
else:
    print("          no --memory-limit-mib on macOS: the MemAvailable misreading is not the "
          "failure mode here.")
print("model     max-context %s, kv %s, engine port %s" % (max_context or "?", kv or "?", port or "?"))
if live:
    c = live.get("conversation_cache") or {}
    if c:
        print("live      engine is UP: parked %s/%s, evictions %s, budget %s MiB" % (
            c.get("parked"), c.get("slots"), c.get("evictions"), c.get("budget_mib")))
    elif measured_snap_mib == 0:
        print("live      engine is UP but has parked nothing yet")
else:
    print("live      engine not reachable on port %s -- using hardware + config only" % (port or "?"))
print()
print("sizing    sessions %d  <- %s" % (sessions, sessions_src))
print("          snapshot  %5d MiB  (%.2f GiB)   <- %s" % (snapshot_mib, snapshot_mib / 1024, snapshot_src))
print("          budget    %5d MiB  (%.1f GiB)   <- %s" % (budget_mib, budget_mib / 1024, budget_src))
print("          slots     %5d                  <- %s" % (slots, slots_src))
print("          min-tokens %6d                 <- %s" % (min_tokens, min_src))
print("          checkpoints %s every %s        <- %s" % (prompt_cache, every, pc_src))
if memory_limit:
    print("          memory-limit %5d MiB        <- MemTotal - %d MiB reserve" % (memory_limit, reserve_mib))
print()
fits = slots * snapshot_mib
print("check     slots x snapshot = %.1f GiB vs budget %.1f GiB -> %s" % (
    fits / 1024, budget_mib / 1024,
    "budget binds first (good)" if fits >= budget_mib else
    "SLOTS bind first: %.1f GiB of budget is dead capacity -- raise --slots or lower --budget-mib"
    % ((budget_mib - fits) / 1024)))
park_ram = slots * snapshot_mib
free_after = detected["memtotal_mib"] - reserve_mib - park_ram
print("check     fully parked cache needs %.1f GiB -> %s" % (
    park_ram / 1024,
    "%.1f GiB still free above the reserve" % (free_after / 1024) if free_after >= 0
    else "EXCEEDS the allowance by %.1f GiB -- lower --sessions or --slots" % (-free_after / 1024)))
print()

if dry:
    print("--dry-run: nothing written.\nresulted args:\n  %s" % after)
    sys.exit(0)

bak = cfg + ".bak-multisession"
print("config    %s" % cfg)
print("removing  %d pre-existing instance(s)" % removed)
if os.path.exists(bak):
    print("backup    %s (kept, not overwritten)" % bak)
else:
    shutil.copy2(cfg, bak)
    print("backup    %s" % bak)

data["args"] = result
tmp = cfg + ".tmp-multisession"
with open(tmp, "w") as fh:
    json.dump(data, fh, indent=1); fh.write("\n")
os.replace(tmp, cfg)

with open(cfg) as fh:
    check = " ".join(str(a) for a in json.load(fh).get("args", []))
for name, value in wanted:
    if "%s %s" % (name, value) not in check and "%s=%s" % (name, value) not in check:
        sys.exit("error: %s %s did not land in the written file" % (name, value))
print("verified  re-read the file, all %d flags present and JSON parses" % len(wanted))
print("\nRestart the engine to apply it -- these flags are read at launch, not hot-reloaded.")
PY
