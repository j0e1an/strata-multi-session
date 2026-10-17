#!/usr/bin/env bash
# Watches the Strata conversation-cache budget, host RAM, and the crash signature.
# One compact heartbeat per poll, plus any new danger lines from the engine log.
# stderr is deliberately NOT suppressed: a broken line must surface, not go silent.
# `--once` prints a single heartbeat and exits, for testing.
LOG="${STRATA_LOG:-$HOME/Strata/strata-iq3_s-abliterated.log}"
off=$(stat -c %s "$LOG" 2>/dev/null || echo 0)

heartbeat() {
  curl -s -m 5 ${STRATA_METRICS:-http://127.0.0.1:18080/metrics} 2>/dev/null | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception as e:
    print("METRICS-UNREACHABLE:", type(e).__name__)
    raise SystemExit
c = d.get("conversation_cache")
if c is None:
    print("METRICS-NO-CACHE-SECTION keys=" + ",".join(sorted(d)[:6]))
    raise SystemExit
free = avail = 0.0
for line in open("/proc/meminfo"):
    k = line.split(":")[0]
    if k == "MemFree":
        free = int(line.split()[1]) / 1048576
    elif k == "MemAvailable":
        avail = int(line.split()[1]) / 1048576
b = c["bytes"] / 2**20
bmib = c["budget_mib"]
pct = b / (bmib or 1) * 100
parked = c["parked"]
slots = c["slots"]
evict = c["evictions"]
parks = c["parks"]
rest = c["restores"]
rr = c["requests_reused"]
rq = c["requests"]
ru = c["reused_tokens"] / 1e6
print(f"parked {parked}/{slots}  budget {b:.0f}/{bmib} MiB ({pct:.0f}%)  evict {evict}  "
      f"parks {parks}  restore {rest}  reuse {rr}/{rq} ({ru:.1f}M tok)  "
      f"MemFree {free:.1f} GiB  MemAvail {avail:.1f} GiB")
'
}

if [ "${1:-}" = "--once" ]; then heartbeat; exit 0; fi

while true; do
  if [ -r "$LOG" ]; then
    new=$(tail -c +"$((off + 1))" "$LOG" 2>/dev/null)
    off=$(stat -c %s "$LOG")
    if [ -n "$new" ]; then
      printf '%s\n' "$new" \
        | grep -E "out of memory|saving a checkpoint part failed|stopped unexpectedly|engine started|exceeds the context" \
        | sed 's/^/DANGER: /'
    fi
  fi
  heartbeat
  sleep 45
done
