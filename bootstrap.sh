#!/bin/sh
#
# One entry point: an unknown box in, a plan (or, with --apply, a running endpoint) out.
#
#   ./bootstrap.sh                                  # the plan; writes and downloads nothing
#   ./bootstrap.sh --dir ~/Strata-data --apply      # do it
#   ./bootstrap.sh --tier IQ3_XXS --sessions 6 --explain
#
# The order is the order the answers arrive in:
#
#   1. is there a Python this tool can run at all        spec §13 — probed in sh, see below
#   2. what box is this                                  lib/detect.py
#   3. can this tool size it at all                      NVIDIA / Apple Silicon only (§12)
#   4. which tier, and what each ceiling costs           lib/recommend.py (§9)
#   5. will it fit on disk, before anything is fetched   §16, including the shared shard 2
#   6. the phase-one sizing, labelled an estimate        §9's two phases
#   7. the stages --apply would run: install, fetch, pack, config, concurrency, vision, autostart
#
# Every figure the plan computes comes out of lib/, so this file owns no sizing arithmetic and no
# measured constant: it sequences, it refuses, and it runs the scripts that already do the work —
# fetch-abliterated.sh and apply-concurrency-fix.sh as subprocesses, because spec §5 says call it,
# do not fork it. What it still owns, precisely, is two measured READINGS quoted in prose rather
# than computed — the dated parked-snapshot readings in the sizing section and the CPU-encoder
# decode reading beside the vision mode — each naming its box and its date, and the first carrying
# spec §14's "not measured on this tier" caveat whenever the chosen tier is outside the qwen
# family. The vision prose and the vision block are lib/vision.py's (spec §10), the CPU encode
# curve with them and the caveat that travels with it.
#
# --apply is what writes or downloads. Without it the script prints the plan and changes nothing:
# no directory is created, no file is written, no byte is fetched.
#
# No `set -e`, deliberately. lib/autostart.py's enable line is a `;`-list whose exit status is
# loginctl's (lib/autostart.py:226-232), and an errexit shell aborts before loginctl ever runs —
# measured on this box with a throwaway user unit. The stages here check their own status instead,
# and run the enable steps one at a time rather than executing the printed line and trusting rc 0.
#
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

usage() {
  cat <<'EOF'
usage: bootstrap.sh [options]

where things go:
  --dir DIR            data dir for models, packs and configs
                       (default $STRATA_DATA, else ~/Strata-data)
  --strata DIR         where Strata itself is, or goes
                       (default $STRATA_HOME, else ~/Strata)
  --upstream DIR       the upstream Strata checkout whose setup.sh installs a stock tier
                       (default $STRATA_UPSTREAM, else ~/strata)

what to install:
  --sessions N         warm conversations to keep parked at once (default 4)
  --tier TIER          take this tier instead of the recommended one
  --abliterated        prefer the uncensored build (the default)
  --stock              prefer the censored build
  --vision cpu|gpu|no  vision mode. Linux defaults to cpu at 1024 image tokens; macOS has no
                       cpu encoder to choose (setup_mac.py hardcodes gpu: true, :677)
  --port N             the port to write in the config and start the unit on (default 8095,
                       which is serve/server.py's own — server.py takes the port from --port
                       only, never from the config's "port" key)
  --host HOST          the host to write in the config (default 127.0.0.1; a non-loopback host
                       with no API key is an open LAN endpoint — see the caveat the plan prints)

what to do:
  --autostart          write and enable the per-user unit (a systemd user unit on Linux, a
                       LaunchAgent on macOS). Needs no password; nothing is enabled without --apply
  --no-autostart       don't (the default)
  --refresh            re-read the shard sizes and oids from Hugging Face (passed to the fetcher)
  --explain            print the plan and the rendered unit; change nothing
  --dry-run            print the plan; change nothing (the default)
  --apply              write and download

test hooks — each stands in for a state this box cannot produce, so the stop paths stay tested
instead of shipping unexercised:
  --force-unsupported-gpu   pretend the GPU detector found neither NVIDIA nor Apple Silicon (§12)
  --force-no-python         pretend no usable 3.10+ interpreter exists (§13)
  --force-os NAME           pretend to be linux or darwin (the §13 remedy differs by OS)
  --force-ram-mib N         pretend this much RAM (on macOS, this much unified memory)
  --free-mib N              pretend this much is free in --dir (the disk preflight)
  --router-port N           which port to probe for a router (default 8090)

Exit codes: 0 the plan is runnable; 1 a stop (no GPU this tool can size, no usable Python, no tier
fits, not enough disk, upstream missing when --apply needs it); 2 a usage error.
EOF
}

# ------------------------------------------------------------------------------ defaults
DATA_DIR="${STRATA_DATA:-$HOME/Strata-data}"
STRATA_HOME_DIR="${STRATA_HOME:-$HOME/Strata}"
UPSTREAM="${STRATA_UPSTREAM:-$HOME/strata}"
SESSIONS=4
TIER=""
WANT_ABL=1
VISION=""
AUTOSTART=0
REFRESH=0
EXPLAIN=0
APPLY=0
PORT=8095
HOST=127.0.0.1
ROUTER_PORT="${STRATA_ROUTER_PORT:-8090}"
DIR_GIVEN=0
FORCE_GPU=0
FORCE_NO_PY=0
FORCE_OS=""
FORCE_RAM=""
FREE_MIB=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dir|--strata|--upstream|--sessions|--tier|--vision|--port|--host|--router-port|\
    --force-os|--force-ram-mib|--free-mib)
      [ $# -ge 2 ] || { echo "error: $1 needs a value" >&2; usage >&2; exit 2; }
      case "$1" in
        --dir)           DATA_DIR="$2"; DIR_GIVEN=1 ;;
        --strata)        STRATA_HOME_DIR="$2" ;;
        --upstream)      UPSTREAM="$2" ;;
        --sessions)      SESSIONS="$2" ;;
        --tier)          TIER="$2" ;;
        --vision)        VISION="$2" ;;
        --port)          PORT="$2" ;;
        --host)          HOST="$2" ;;
        --router-port)   ROUTER_PORT="$2" ;;
        --force-os)      FORCE_OS="$2" ;;
        --force-ram-mib) FORCE_RAM="$2" ;;
        --free-mib)      FREE_MIB="$2" ;;
      esac
      shift 2 ;;
    --abliterated)             WANT_ABL=1; shift ;;
    --stock)                   WANT_ABL=0; shift ;;
    --autostart)               AUTOSTART=1; shift ;;
    --no-autostart)            AUTOSTART=0; shift ;;
    --refresh)                 REFRESH=1; shift ;;
    --explain)                 EXPLAIN=1; shift ;;
    --dry-run)                 APPLY=0; shift ;;
    --apply)                   APPLY=1; shift ;;
    --force-unsupported-gpu)   FORCE_GPU=1; shift ;;
    --force-no-python)         FORCE_NO_PY=1; shift ;;
    -h|--help)                 usage; exit 0 ;;
    *)                         echo "error: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

whole() { case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }
whole "$SESSIONS"     || { echo "error: --sessions wants a whole number, got '$SESSIONS'" >&2; exit 2; }
whole "$PORT"         || { echo "error: --port wants a number, got '$PORT'" >&2; exit 2; }
whole "$ROUTER_PORT"  || { echo "error: --router-port wants a number, got '$ROUTER_PORT'" >&2; exit 2; }
case "$VISION" in cpu|gpu|no|"") ;; *) echo "error: --vision takes cpu, gpu or no" >&2; exit 2 ;; esac
for numeric in "$FORCE_RAM" "$FREE_MIB"; do
  [ -z "$numeric" ] && continue
  whole "$numeric" || { echo "error: a test hook wants a whole number, got '$numeric'" >&2; exit 2; }
done
case "$FORCE_OS" in ""|linux|darwin) ;; *) echo "error: --force-os takes linux or darwin" >&2; exit 2 ;; esac

# --------------------------------------------------------------------------- 0. which OS
# The same rule lib/detect.py uses: anything that is not darwin goes down the Linux path.
# Resolved in sh because the §13 stop below has to know which remedy to print and cannot ask
# Python which OS it is running on — on that path Python is the thing that is missing.
case "$(uname -s)" in
  Darwin) OS=darwin ;;
  *)      OS=linux ;;
esac
[ -z "$FORCE_OS" ] || OS="$FORCE_OS"

# ----------------------------------------------- 1. spec §13: is there a usable Python at all?
#
# Probed here, in sh, before lib/ is imported — lib/detect.py is itself Python, so on a box with
# no usable interpreter there is nothing to ask, and the brief's "detect, then stop if the Python
# is missing" order is unreachable. Upstream's own test (setup.sh:9) is `import sys, venv,
# ensurepip` behind a 3.10 floor: venv alone is not enough, because Debian and Ubuntu ship venv
# without ensurepip and the venv it builds has no pip in it.
#
# The candidates are lib/detect.py's, in lib/detect.py's order: the .venv of an existing Strata
# install first (that is what setup.sh reuses, and on a box whose system python3 has no ensurepip
# — this one, python3 3.14.4 — it is the only one that answers), system python3 second for the
# fresh box that still has to create one.
PY_PROBE='import sys, venv, ensurepip; print("ok" if sys.version_info >= (3, 10) else "")'
VENV_PY="$STRATA_HOME_DIR/.venv/bin/python"
PY=""
if [ "$FORCE_NO_PY" != 1 ]; then
  if [ -x "$VENV_PY" ] && [ "$("$VENV_PY" -c "$PY_PROBE" 2>/dev/null)" = "ok" ]; then
    PY="$VENV_PY"
  elif command -v python3 >/dev/null 2>&1 && [ "$(python3 -c "$PY_PROBE" 2>/dev/null)" = "ok" ]; then
    PY=python3
  fi
fi

if [ -z "$PY" ]; then
  # The one step of the whole install that can need a password, said plainly and stopped at,
  # rather than inherited as a sudo prompt inside what was sold as a rootless install (spec §13).
  {
    echo "stop: no usable Python 3.10+ on this box."
    echo "      Neither $VENV_PY nor python3 passed upstream's own test (import sys, venv,"
    echo "      ensurepip, 3.10+). This tool needs one to read lib/, and upstream's installer"
    echo "      needs one that can make a venv with pip in it."
    if [ "$OS" = darwin ]; then
      # Never print the apt/dnf remedy on a Mac: that package set does not exist there, and
      # upstream resolves this rootlessly by downloading a standalone CPython (setup.sh:36-46).
      echo "      macOS: upstream's installer fetches a standalone CPython itself"
      echo "      (setup.sh:36-46) — no package manager and no password. Run upstream's setup.sh"
      echo "      once, or put a 3.10+ interpreter at \$STRATA_HOME/.venv/bin/python or on PATH,"
      echo "      and re-run this."
    else
      echo "      This is the one documented exception to the no-password rule, and this tool"
      echo "      will not run it for you:"
      echo "        Debian/Ubuntu:  sudo apt-get install -y python3 python3-venv python3-pip"
      echo "        Fedora/RHEL:    sudo dnf install python3 python3-pip python3-venv"
      echo "      Then re-run this."
    fi
    echo "      Nothing was written and nothing was downloaded."
  } >&2
  exit 1
fi

# --------------------------------------------------- 2-6. the plan, in one python pass
#
# detect -> the §12 stop -> the shared-shard-2 scan -> recommend -> the disk preflight -> the
# phase-one sizing. One pass, in this order, because the disk figure depends on the shard-2 scan,
# which depends on --dir, and the recommendation has to carry the same 28.8 GB saving the
# preflight does (spec §16) — two passes would print two different disk figures for one install.
#
# detect() never produces has_shared_shard2 and no other task owns it, so the scan that populates
# it lives here.
run_plan() {
  env BS_REPO="$SCRIPT_DIR" BS_OS="$OS" BS_DIR="$DATA_DIR" BS_STRATA="$STRATA_HOME_DIR" \
    BS_SESSIONS="$SESSIONS" BS_ABL="$WANT_ABL" BS_TIER="$TIER" BS_VISION="$VISION" \
    BS_PORT="$PORT" BS_HOST="$HOST" BS_ROUTER_PORT="$ROUTER_PORT" BS_FREE="$FREE_MIB" \
    BS_RAM="$FORCE_RAM" BS_FORCE_GPU="$FORCE_GPU" BS_EXPLAIN="$EXPLAIN" BS_AUTOSTART="$AUTOSTART" \
    BS_UPSTREAM="$UPSTREAM" BS_INSTALLED=0 \
    "$PY" - <<'PY'
import os, sys, textwrap
sys.path.insert(0, os.environ["BS_REPO"])
from lib import autostart, catalog, detect, recommend, vision

MIB = 1_048_576
GB = 1e9

# The plan's prose column: 16 spaces in, filled to 94. lib/vision.explain() fills its own block to
# the same column, so it is handed this indent rather than guessing the layout.
PROSE_INDENT = " " * 16


def col(label, value=""):
    print("%-16s%s" % (label, value))


def wrap(text, indent=PROSE_INDENT):
    return textwrap.fill(text, width=94, initial_indent=indent, subsequent_indent=indent)


def gib(mib):
    return "%.2f GiB" % (mib / 1024)


os_name = os.environ["BS_OS"]
data_dir = os.path.expanduser(os.environ["BS_DIR"])
strata = os.path.expanduser(os.environ["BS_STRATA"])
upstream = os.path.expanduser(os.environ["BS_UPSTREAM"])
sessions = int(os.environ["BS_SESSIONS"])
want_abl = os.environ["BS_ABL"] == "1"
tier_wanted = os.environ["BS_TIER"]
vision_flag = os.environ["BS_VISION"]          # the raw --vision value, "" when it was absent
port = int(os.environ["BS_PORT"])
host = os.environ["BS_HOST"]
router_port = int(os.environ["BS_ROUTER_PORT"])
free_override = int(os.environ["BS_FREE"] or 0)
force_ram = int(os.environ["BS_RAM"] or 0)
force_gpu = os.environ["BS_FORCE_GPU"] == "1"
explain = os.environ["BS_EXPLAIN"] == "1"
autostart_on = os.environ["BS_AUTOSTART"] == "1"

# ---------------------------------------------------------------------- the box (lib/detect.py)
# data_dir here is the Strata install, not the data dir: detect() looks for that install's .venv,
# and it does not read $STRATA_HOME itself, so the caller hands its --strata value over.
box = detect.detect(platform_name=os_name, data_dir=strata)
if force_ram:
    box["ram_mib"] = force_ram
    if box["os"] == "darwin":
        # Apple Silicon unifies the two: on a Mac the forced RAM figure IS the VRAM figure.
        box["vram_mib"] = force_ram
        box["gpu_supported"] = force_ram > 0
        box["gpus"] = ["Apple Silicon"] if force_ram > 0 else []
        box["gpu_note"] = detect.APPLE_NOTE
if force_gpu:
    box["gpu_supported"] = False
    box["gpus"] = []
    box["gpu_note"] = detect.NO_NVIDIA_NOTE

print("box")
col("os", box["os"])
col("ram", "%d MiB (%s)" % (box["ram_mib"], gib(box["ram_mib"])))
if box["os"] != "darwin":
    col("memavailable", "%d MiB (%s)" % (box["memavail_mib"], gib(box["memavail_mib"])))
col("vram", "%d MiB (%s)" % (box["vram_mib"], gib(box["vram_mib"])))
col("gpus", " + ".join(box["gpus"]) if box["gpus"] else box["gpu_note"])
col("cores", box["cores"])
col("python", "%s  (3.10+, venv + ensurepip)" % (box["venv_python"] or "none"))
print()

# ------------------------------------------------- spec §12: NVIDIA and Apple Silicon only
if not box["gpu_supported"]:
    print("stop")
    print(wrap("this tool sizes tiers for NVIDIA and Apple Silicon only, and this box is neither."))
    print(wrap(box["gpu_note"] or "no GPU reported"))
    print(wrap("Strata itself supports AMD (--backend hip, build_engine_hip()) and Intel "
               "(sycl/setup_intel.py); this tool cannot size them. Sizing a tier from a vram_mib "
               "of 0 would be a guess rather than a fit, so it stops here. Nothing was written "
               "and nothing was downloaded."))
    sys.exit(1)

# ------------------------------------------------------- the shared shard 2, and where it is
ple_bytes, ple_oid = catalog.shared_shard2()


def find_shared_shard2(root):
    """Files under <root>/models that ARE the shared shard 2: shard-2 name shape, exactly the
    shared byte count.

    detect() never produces has_shared_shard2 and no other task owns it, so the orchestrator
    measures it — without it the 28.8 GB saving spec §16 exists for is never claimed. The name
    test is deliberate: some other 28.8 GB file would claim the saving and then put the wrong
    bytes where the engine reads the n-gram/PLE table. When in doubt this finds nothing, which is
    the direction that asks the disk for the shard a second time rather than under-counting it.
    """
    hits = []
    models = os.path.join(root, "models")
    for dirpath, _dirs, files in os.walk(models):
        for name in sorted(files):
            if not name.endswith("-00002-of-00002.gguf"):
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(path) == ple_bytes:
                    hits.append(path)
            except OSError:
                continue
    return hits


shard2_hits = find_shared_shard2(data_dir)
box["has_shared_shard2"] = bool(shard2_hits)

# ------------------------------------------------------------------- the tier (lib/recommend.py)
plan = recommend.recommend(box, sessions=sessions, want_abliterated=want_abl)
plans = [plan] + (plan["alternatives"] if plan["tier"] else [])

# A tier --tier names that recommend() did not offer goes through recommend.forced_plan(), which
# builds it from the same _kv_plan / _disk_gb / _ceiling the recommender uses. The orchestrator
# used to rebuild those three here out of recommend's public constants, and the copy drifted
# silently whenever the library's shape moved; it is lib's answer now, and tests/test_recommend.py
# pins it without a GPU.


if plan["tier"] is None:
    # The no-tier answer, and the one thing it must not be is "this machine cannot run the model".
    print("stop")
    print(wrap(plan["reason"]))
    smallest = min(catalog.tiers_for(os_name), key=lambda n: catalog.TIERS[n]["ram_gb"])
    fits_in_ram, low_ram = recommend.fits(box, catalog.TIERS[smallest])
    if fits_in_ram and low_ram:
        print(wrap("That is this tool's answer, not upstream's. The smallest tier here, %s, still "
                   "passes upstream's low-RAM-mode test (setup.py:4039-4042) on this box: the card "
                   "holds what it can of its %s GB expert arena and the rest is read from the SSD, "
                   "so upstream's installer will install it in the low-RAM mode rather than refuse. "
                   "The honest sentence is \"not at a published floor\", not \"this machine cannot "
                   "run the model\"." % (smallest, catalog.TIERS[smallest]["arena_gb"])))
    print(wrap("Nothing was written and nothing was downloaded."))
    sys.exit(1)

chosen = plan
if tier_wanted:
    if tier_wanted not in catalog.TIERS:
        print("stop")
        col("tier", "no such tier: %s" % tier_wanted)
        print(wrap("this catalog offers: %s" % ", ".join(catalog.tiers_for(os_name))))
        sys.exit(2)
    if os_name == "darwin" and tier_wanted not in catalog.MAC_TIERS:
        print("stop")
        col("tier", "%s is not in metal/setup_mac.py's MAC_MODELS" % tier_wanted)
        print(wrap("the Mac installer's menu is %s; recommending one of the others sends you to an "
                   "installer that would refuse it (spec §8)."
                   % ", ".join(catalog.tiers_for("darwin"))))
        sys.exit(1)
    chosen = (next((p for p in plans if p["tier"] == tier_wanted), None)
              or recommend.forced_plan(box, tier_wanted, sessions=sessions,
                                       want_abliterated=want_abl))

tier = chosen["tier"]
tier_l = tier.lower()
row = catalog.TIERS[tier]
abl = bool(chosen["abliterated"])
model_dir = os.path.join(data_dir, "models", "%s-abliterated" % tier if abl else tier)
pack_dir = os.path.join(data_dir, "packs", "%s_abliterated" % tier_l if abl else tier_l)
config = os.path.join(strata, "strata-%s-abliterated.json" % tier_l if abl else "strata-%s.json" % tier_l)
stock_pack = os.path.join(data_dir, "packs", tier_l)

# spec §14: the KV rate, the parked-snapshot range, the parks-per-session count and the CPU encode
# curve were all measured on qwen-family GSQ-RCO IQ3_S at 262144 with --kv int8. lib owns the
# sentence and the test for which tiers need it; this file appends it wherever it prints one of
# those figures, which is every place below that says "measured". The rate's own wording is in
# lib/recommend._forced_reason, where the ladder that used the rate is described.
carry = recommend.carry_over_note(tier)

print("tier")
col("choice", "%s  %s%s" % (tier, "(uncensored)" if abl else "(censored)",
                            "  --tier" if chosen.get("forced") else "  (recommended)"))
print(wrap(chosen["reason"]))
if plan["alternatives"] and not chosen.get("forced"):
    col("alternatives", ", ".join(a["tier"] for a in plan["alternatives"]))
elif plan["tier"]:
    col("recommends", "%s (its arithmetic is above; --tier overrides it)" % plan["tier"])
if chosen.get("forced") and not chosen.get("fits_in_ram"):
    print(wrap("upstream's own --check verdict for %s on this box is \"does not fit\" (setup.py:"
               "6092): %s GiB of RAM against a %s GiB floor, more than the %d GiB tight band under "
               "it, and its low-RAM-mode test does not close the gap either. --apply will still try "
               "if you insist; this tool will not pretend it was recommended."
               % (tier, "%.1f" % chosen["ram_gib"], row["ram_gb"], recommend.TIGHT_BAND_GB)))
col("context", "%d  --kv %s%s" % (chosen["context"], chosen["kv"],
                                  "  (from --tier, not the recommender)" if chosen.get("forced") else ""))
if chosen["low_ram"]:
    print(wrap("low-RAM mode: its experts do not fit this RAM with the %d GB upstream keeps beside "
               "them (setup.py:4011-4014), so the installer writes a %s GB expert arena to disk — "
               "the disk figure below counts it."
               % (recommend.LOW_RAM_HEADROOM_GB, row["arena_gb"] + 1)))
if chosen["ceiling"] == "none":
    col("ceiling", "none this tool can relieve"
                   if os_name == "darwin" else "neither ceiling binds")
else:
    col("ceiling", "%s" % chosen["ceiling"])
    col("relieve with", chosen["relief"])
print()

# --------------------------------------------------------- the disk, before anything is fetched
free_mib = free_override or detect.disk_free_mib(data_dir)
free_gb = free_mib * MIB / GB

installed = os.path.isfile(os.path.join(strata, "serve", "server.py"))
upstream_here = os.path.isfile(os.path.join(upstream, "setup.sh"))


def find_stock_copy(root, name):
    """Bytes of .gguf already under <root>/models/<name>, the directory this tool treats as the
    stock (censored) tier's home.

    --apply's stage 1 runs upstream's installer whenever Strata is not already installed, and on
    the uncensored path that installer still downloads the STOCK build — the engine comes from it,
    and its shards are the copy fetch-abliterated.sh hard-links shard 2 from (the stage list below
    says the same). Whether those bytes are already on disk is a filesystem fact, so it is
    measured here and the arithmetic that turns it into a figure stays in lib/recommend.
    disk_preflight().

    Summed by size rather than matched by name on purpose: this catalog pins the abliterated
    filenames (catalog.shard_plan) and no stock ones, so a name test would be a guess. Like the
    shared-shard-2 scan above, it errs toward asking for the bytes again — a partial download
    counts as absent, which over-reserves rather than under-reserves.
    """
    total = 0
    for dirpath, _dirs, files in os.walk(os.path.join(root, "models", name)):
        for fname in files:
            if not fname.endswith(".gguf"):
                continue
            try:
                total += os.path.getsize(os.path.join(dirpath, fname))
            except OSError:
                continue
    return total


# Stage 1 puts the stock tier on disk only if it runs at all: not installed, and an installer to
# run. With no setup.sh here --apply stops at the install stage before a byte is fetched, so the
# stock bytes are not asked for either — and the disk section says so rather than going quiet.
stage_one_downloads = bool(abl and not installed and upstream_here)
stock_copy_held = find_stock_copy(data_dir, tier) >= recommend.stock_download_bytes(tier)
disk = recommend.disk_preflight(box, tier, stage_one_downloads=stage_one_downloads,
                                stock_copy_held=stock_copy_held)
need_gb = disk["need_gb"]

print("target")
col("data dir", data_dir)
col("strata", "%s  (%s)" % (strata, "installed" if installed else "not installed"))
if not installed:
    col("upstream", "%s  (%s)" % (upstream, "found" if upstream_here else "no setup.sh here"))
col("config", "%s  (%s)" % (config,
                            "exists, left alone" if os.path.isfile(config) else
                            ("would write" if abl else "upstream's installer writes it")))
col("free here", "%.1f GB" % free_gb)
print()
print("disk")
if need_gb is None:
    col("needs", "unknown for %s" % tier)
    print(wrap("this catalog pins no per-file sizes for %s (family %s), so quoting upstream's "
               "%s GB figure would be quoting the wrong file. This preflight cannot say, and does "
               "not refuse on a guess: upstream's own free-disk check (setup.py:6418-6420) is what "
               "stops the download." % (tier, row["family"], row["download_gb"])))
else:
    col("needs", "%.1f GB free in %s" % (need_gb, data_dir))
    col("free", "%.1f GB" % free_gb)
    if abl:
        col("shared shard 2", "%s bytes (%.1f GB), the same file in all four abliterated tiers"
            % (ple_bytes, ple_bytes / GB))
        if shard2_hits:
            col("found", shard2_hits[0])
            print(wrap("counted once, not twice: the fetcher hard-links it rather than pulling it "
                       "again (spec §16). %d matching shard-2 file(s) under %s/models."
                       % (len(shard2_hits), data_dir)))
        else:
            print(wrap("no copy under %s/models, so the full total is asked for. fetch-abliterated.sh "
                       "will stop rather than download it if nothing can supply it — fetching the "
                       "stock tier first, or passing --link-shard2, is your call." % data_dir))
    # Which stage-1 case this is, said out loud: the figure is the same size either way only by
    # coincidence, and a reader comparing this plan with upstream's later check has to be able to
    # tell which downloads were already on disk.
    if disk["stage_one_stock"]:
        col("plus stage 1", "%.1f GB of the stock %s shards, less the %.1f GB shard 2 they share "
                            "with this tier" % (disk["stock_gb"], tier, ple_bytes / GB))
        print(wrap("counted because Strata is not installed at %s, so --apply's stage 1 runs %s's "
                   "setup.sh, and that installer downloads the censored build even on the "
                   "uncensored path: the engine comes from it, and its shards are the copy "
                   "fetch-abliterated.sh hard-links shard 2 from. The preflight used to leave this "
                   "out and say ok at %.1f GB, leaving %.1f GB for upstream's own free-disk check "
                   "(setup.py:6418-6420) to discover."
                   % (strata, upstream, need_gb - disk["stock_gb"], disk["stock_gb"])))
    elif abl and stock_copy_held:
        print(wrap("stage 1's stock shards are NOT counted: %s already holds them, so upstream's "
                   "installer finds them there instead of fetching them."
                   % os.path.join(data_dir, "models", tier)))
    elif abl and installed:
        print(wrap("stage 1's stock shards are NOT counted: Strata is already installed at %s, so "
                   "--apply skips the install stage and nothing here fetches the censored build."
                   % strata))
    elif abl:
        print(wrap("stage 1's stock shards are NOT counted because there is no setup.sh at %s: "
                   "--apply stops at the install stage before anything is fetched. Point --upstream "
                   "at an upstream checkout and this figure grows by %.1f GB."
                   % (upstream, round((recommend.stock_download_bytes(tier) - ple_bytes) / GB, 1))))
    if chosen["low_ram"]:
        col("plus arena", "%.1f GB the low-RAM mode writes (setup.py:6418-6420)" % disk["arena_gb"])
    if need_gb > free_gb:
        col("verdict", "REFUSED")
        print()
        print("stop")
        print(wrap("not enough disk to fetch this: %.1f GB has to be free in %s and this filesystem "
                   "has %.1f GB free. The figure is the real size of the shards "
                   "(lib/catalog.download_bytes)%s%s, not upstream's published download_gb."
                   % (need_gb, data_dir, free_gb,
                      ", plus the %.1f GB expert arena the low-RAM mode writes" % disk["arena_gb"]
                      if chosen["low_ram"] else "",
                      ", plus the %.1f GB of stock shards stage 1 fetches" % disk["stock_gb"]
                      if disk["stage_one_stock"] else "")))
        print(wrap("Free the disk, or point --dir at a filesystem that can hold it. Nothing was "
                   "downloaded and nothing was written."))
        sys.exit(1)
    col("verdict", "ok")
print()

# ------------------------------------------------- the phase-one sizing (spec §9, two phases)
# The snapshot figure and the budget that follows from it are lib's, including the parks-per-
# session count and the RAM reserve the budget is capped by: recommend.phase_one_budget() is the
# same arithmetic apply-concurrency-fix.sh:241-255 does, and this file used to keep a third copy
# of it. What stays here is only the labelling spec §9 asks for.
snapshot_est = recommend.estimate_snapshot_mib(chosen["context"], kv=chosen["kv"])
sized = recommend.phase_one_budget(box["ram_mib"], sessions, snapshot_est)
snapshot_mib, budget_mib, slots = sized["snapshot_mib"], sized["budget_mib"], sized["slots"]
capped, reserve_mib, allowance_mib = sized["capped"], sized["reserve_mib"], sized["allowance_mib"]
if sized["fallback"]:
    snapshot_src = ("fallback: lib/recommend.FALLBACK_SNAPSHOT_MIB (%d MiB) — no context to size "
                    "from" % snapshot_mib)
else:
    snapshot_src = ("estimate: lib/recommend.estimate_snapshot_mib(%d tokens, --kv %s)"
                    % (chosen["context"], chosen["kv"]))

print("sizing  (phase one: an estimate, with an expiry)")
col("sessions", sessions)
col("snapshot", "%d MiB (%s)   <- %s%s" % (snapshot_mib, gib(snapshot_mib), snapshot_src, carry))
col("budget", "%d MiB (%.1f GiB)   <- %d sessions x %d parked each%s%s"
    % (budget_mib, budget_mib / 1024, sessions, recommend.PER_SESSION_PARKS,
       ", capped by the %s RAM allowance (MemTotal less the %d MiB reserve)"
       % (gib(allowance_mib), reserve_mib) if capped else "", carry))
col("slots", "%d   <- budget / snapshot, rounded up" % slots)
col("one source", "lib/recommend.estimate_snapshot_mib + lib/recommend.phase_one_budget")
print(wrap("apply-concurrency-fix.sh keeps its own KV table: it has no k8v4 key (so it defaults "
           "to 1.0 there) and uses 0.5 for q4_0 where lib/recommend uses 576/1056. On those two "
           "formats its figure is not this one; on int8 the two agree. This plan prints one "
           "figure and names its source rather than putting both on the screen."))
print(wrap("This is a reading of a running engine, not a property of the model, and it expires. "
           "The same box measured 1.86 GiB at 15:00 and 2.22 GiB at 21:00 on 2026-10-17, and "
           "1.43 GiB on 2026-10-18%s. Once the engine has parked something, re-run "
           "apply-concurrency-fix.sh: its /metrics path replaces this estimate with a measurement "
           "of your own engine, which may come back larger or smaller than the number above."
           % carry))
print()

# ------------------------------------------------------------------ the router, reported only
if autostart.detect_llama_swap(router_port):
    col("router", "something is listening on 127.0.0.1:%d" % router_port)
    print(wrap("reported, not managed. The unit owns serve/server.py, never a router (spec §11): "
               "this tool will not start, stop or reconfigure what is behind that port, and it "
               "does not claim to have identified llama-swap — the probe only says something is "
               "there."))
else:
    col("router", "nothing listening on 127.0.0.1:%d" % router_port)
print()

# ------------------------------------------------------------------- vision (spec §10, by OS)
# The mode is the flag or this OS's default; the prose and the written block are lib/vision.py's,
# so the encode curve and the cap it justifies exist in one place (spec §10, §14).
vision_mode = vision_flag or vision.default_mode(os_name)
print("vision")
if os_name == "darwin":
    col("mode", "%s (Metal encoder)" % vision_mode)
    print(vision.explain(os_name, tier, indent=PROSE_INDENT))
else:
    col("mode", "%s%s" % (vision_mode, " (default)" if not vision_flag else " (--vision)"))
    if vision_mode != "no":
        col("mmproj", "%s  %s bytes" % (catalog.MMPROJ["file"], catalog.MMPROJ["bytes"]))
        if vision_mode == "cpu":
            col("max_tokens", "%d" % vision.VISION_MAX_TOKENS)
            print(vision.explain(os_name, tier, indent=PROSE_INDENT))
        else:
            print(wrap("gpu: true makes server.py add --vram-reserve-mib to the engine args; the "
                       "CPU encoder costs text generation nothing measurable on this box "
                       "(decode 251 -> 242 tok/s, overlapping ranges)."))
print()

# ------------------------------------------------------------- autostart (spec §11), preview
unit = autostart.unit_path(os_name)
enable, undo = autostart.enable_commands(os_name)
log_path = os.path.join(strata, "strata-%s-abliterated.log" % tier_l if abl
                        else "strata-%s.log" % tier_l)
print("autostart")
col("mode", "on (--autostart)" if autostart_on else "off (pass --autostart to enable)")
col("unit", unit)
col("enable", enable)
col("undo", undo)
col("logs", "journalctl --user -u strata" if os_name != "darwin" else log_path)
if os_name == "darwin":
    print(wrap("launchctl load -w errors on an already-loaded agent, so a re-run reports that "
               "rather than failing; the log file's parent directory has to exist before the "
               "agent starts (on Linux journald owns the log and there is no file to create)."))
print(wrap("caveat: a unit does not inherit your exported STRATA_API_KEY — systemd and launchd "
           "start it with their own environment, so a key that works in your shell is simply "
           "absent there. Put it in the unit (Environment=) or the config if the endpoint needs "
           "one. server.py reads host from the config but takes the port from --port only, which "
           "is why the unit passes it."))
if host not in ("127.0.0.1", "localhost", "::1"):
    print(wrap("host: %s is not loopback. With no API key in the unit's environment that is an "
               "open endpoint for every machine on the LAN." % host))
if explain:
    body = autostart.render(os_name, os.path.join(strata, "serve", "server.py"), config, log_path,
                            port=port, python_bin=box["venv_python"] or "python3")
    print()
    print("unit body (what --apply writes to %s):" % unit)
    for line in body.splitlines():
        print("  %s" % line)
print()

# ------------------------------------------------------------------ what the stages will use
# Printed, not hidden: the shell parses these to build the stage list and to run it, so the plan
# and the commands cannot drift apart.
print("derived")
for key, value in (("tier", tier), ("family", row["family"]), ("abliterated", 1 if abl else 0),
                   ("context", chosen["context"]), ("kv", chosen["kv"]),
                   ("low_ram", 1 if chosen["low_ram"] else 0), ("sessions", sessions),
                   ("snapshot_mib", snapshot_mib), ("budget_mib", budget_mib), ("slots", slots),
                   ("port", port), ("host", host), ("vision", vision_mode),
                   ("vision_tokens", vision.VISION_MAX_TOKENS),
                   ("data_dir", data_dir), ("strata", strata), ("upstream", upstream),
                   ("model_dir", model_dir), ("pack_dir", pack_dir), ("stock_pack", stock_pack),
                   ("config", config), ("log", log_path), ("installed", 1 if installed else 0),
                   ("shared_shard2", 1 if shard2_hits else 0),
                   ("shard1_bytes", row["shard_bytes"][0] if row["shard_bytes"] else "unknown"),
                   ("unit", unit), ("undo", undo),
                   ("disk_need_gb", "%.1f" % need_gb if need_gb is not None else "unknown"),
                   ("disk_free_gb", "%.1f" % free_gb)):
    col("", "%s=%s" % (key, value))
PY
}

plan=$(run_plan)
plan_rc=$?
printf '%s\n' "$plan"
[ "$plan_rc" -eq 0 ] || exit "$plan_rc"

# Pull the derived values back out. One source of truth: python computed them once, and the
# stages below run the same numbers the plan printed.
get() { printf '%s\n' "$plan" | sed -n "s/^  *$1=//p" | sed -n '1p'; }
TIER=$(get tier)
FAMILY=$(get family)
ABL=$(get abliterated)
CONTEXT=$(get context)
KV=$(get kv)
LOW_RAM=$(get low_ram)
SESSIONS=$(get sessions)
PORT=$(get port)
HOST=$(get host)
VISION_MODE=$(get vision)
VISION_TOKENS=$(get vision_tokens)
DATA_DIR=$(get data_dir)
STRATA_HOME_DIR=$(get strata)
UPSTREAM=$(get upstream)
MODEL_DIR=$(get model_dir)
PACK_DIR=$(get pack_dir)
STOCK_PACK=$(get stock_pack)
CONFIG=$(get config)
LOG_PATH=$(get log)
INSTALLED=$(get installed)

# ------------------------------------------------------- confirm the target dir (interactive)
# Only under --apply, only on a terminal, and only when --dir was not given: a plan run and a
# scripted apply never stop for a question.
if [ "$APPLY" = 1 ] && [ "$DIR_GIVEN" != 1 ] && [ -t 0 ]; then
  printf 'install models, packs and configs under %s ? [Y/n] ' "$DATA_DIR"
  read -r reply || reply=""
  case "$reply" in
    [nN]*)
      printf 'where? '
      read -r where || where=""
      if [ -n "$where" ]; then
        DATA_DIR="$where"
        plan=$(run_plan)
        plan_rc=$?
        printf '%s\n' "$plan"
        [ "$plan_rc" -eq 0 ] || exit "$plan_rc"
        MODEL_DIR=$(get model_dir)
        PACK_DIR=$(get pack_dir)
        STOCK_PACK=$(get stock_pack)
      fi ;;
  esac
fi

# --------------------------------------------------------------------------- 7. the stages
# --dry-run prints them and stops; --apply runs them in the same order.
say_stage() { printf '  %s\n' "$1"; }

upstream_argv() {
  # The flags the installer for THIS OS accepts (spec §8): metal/setup_mac.py has no --kv, no
  # --vision-tokens and no --vision cpu, so handing it Linux's set is an "unrecognized argument".
  set -- --model "$TIER" --family "$FAMILY" --context "$CONTEXT" --data-dir "$DATA_DIR" \
         --models-dir "$DATA_DIR/models" --port "$PORT" --host "$HOST" --no-start --yes
  if [ "$OS" != darwin ]; then
    set -- "$@" --kv "$KV"
    case "$VISION_MODE" in
      no)  set -- "$@" --vision no ;;
      gpu) set -- "$@" --vision gpu --vision-tokens "$VISION_TOKENS" ;;
      *)   set -- "$@" --vision cpu --vision-tokens "$VISION_TOKENS" ;;
    esac
  else
    case "$VISION_MODE" in
      no) set -- "$@" --vision no ;;
      *)  set -- "$@" --vision yes ;;
    esac
  fi
  printf '%s\n' "$@"
}

echo "stages"
if [ "$INSTALLED" = 1 ]; then
  say_stage "1 install      Strata is already installed at $STRATA_HOME_DIR — skipped"
else
  say_stage "1 install      cd $UPSTREAM && ./setup.sh (upstream's installer, not this tool)"
  printf '%s\n' "$(upstream_argv)" | while IFS= read -r arg; do printf '                   %s\n' "$arg"; done
  if [ "$ABL" = 1 ]; then
    say_stage "               (the uncensored path still needs upstream for the engine; its stock"
    say_stage "                shards are also where the hard-linkable shard 2 comes from)"
  fi
fi
if [ "$ABL" = 1 ]; then
  say_stage "2 fetch        $SCRIPT_DIR/fetch-abliterated.sh --tier $TIER --dir $MODEL_DIR"
  [ "$REFRESH" = 1 ] && say_stage "               --refresh"
  say_stage "3 pack         $PACK_DIR"
  if [ "$TIER" = IQ3_S ]; then
    say_stage "               via $SCRIPT_DIR/prepare-abliterated-pack.sh"
  else
    say_stage "               NOT automated: prepare-abliterated-pack.sh is pinned to IQ3_S"
    say_stage "               (its shard name and pack path are literals there). For $TIER the two"
    say_stage "               iq_pack.py passes are yours to run; each is one pass over the shard."
  fi
  say_stage "               time cost: two passes over the $(get shard1_bytes)-byte shard, one"
  say_stage "               time. A stock pack already at $STOCK_PACK is"
  say_stage "               NOT reusable: SC117's tensor transplant moves expert offsets and the"
  say_stage "               engine refuses the stock pack with 'native_experts.txt does not match"
  say_stage "               the model'. The abliterated pack goes in its own directory."
else
  say_stage "2 fetch        upstream's installer already fetched it (stock path)"
  say_stage "3 pack         upstream's installer already built it (stock path)"
fi
say_stage "4 config       $CONFIG"
if [ -f "$CONFIG" ]; then
  say_stage "               (exists — left alone; re-running does not rewrite your config)"
fi
say_stage "5 concurrency  $SCRIPT_DIR/apply-concurrency-fix.sh --sessions $SESSIONS $CONFIG"
say_stage "               (called as a subprocess; its math stays its own — spec §5)"
say_stage "6 vision       $VISION_MODE"
if [ "$OS" != darwin ] && [ "$VISION_MODE" = cpu ]; then
  say_stage "               max_tokens $VISION_TOKENS, gpu false"
fi
if [ "$AUTOSTART" = 1 ]; then
  say_stage "7 autostart    write $(get unit)"
  say_stage "               then run the enable steps one at a time (never the printed ; line"
  say_stage "               under an errexit shell: its status would be loginctl's)"
else
  say_stage "7 autostart    skipped (pass --autostart)"
fi
echo

if [ "$APPLY" != 1 ]; then
  echo "dry run: nothing written, nothing downloaded. Re-run with --apply."
  echo
  echo "phase two: the sizing above is an estimate. Once the engine has parked at least one"
  echo "conversation, re-run:"
  echo "    $SCRIPT_DIR/apply-concurrency-fix.sh --sessions $SESSIONS $CONFIG"
  echo "and read the numbers off your own engine. Expect them to move."
  exit 0
fi

# --------------------------------------------------------------------------- apply: the stages
FETCH="$SCRIPT_DIR/fetch-abliterated.sh"
PACKER="$SCRIPT_DIR/prepare-abliterated-pack.sh"
CONCURRENCY="$SCRIPT_DIR/apply-concurrency-fix.sh"
export STRATA_DATA="$DATA_DIR"
export STRATA_HOME="$STRATA_HOME_DIR"

# 1. install — upstream's installer, or stop. This tool is not a new installer (spec §3).
if [ "$INSTALLED" != 1 ]; then
  if [ ! -f "$UPSTREAM/setup.sh" ]; then
    {
      echo "stop: Strata is not installed at $STRATA_HOME_DIR and there is no setup.sh at"
      echo "      $UPSTREAM. Clone upstream and point --upstream at it, or install Strata"
      echo "      yourself and re-run with --strata. This tool delegates the install (spec §3);"
      echo "      it does not reimplement it."
    } >&2
    exit 1
  fi
  args=""
  set --
  while IFS= read -r a; do set -- "$@" "$a"; done <<ARGV
$(upstream_argv)
ARGV
  echo "install      running upstream's setup.sh ..."
  ( cd "$UPSTREAM" && ./setup.sh "$@" ) || {
    echo "stop: upstream's setup.sh exited $?" >&2; exit 1; }
fi

# 2. fetch — the uncensored shards, from this repo's fetcher.
if [ "$ABL" = 1 ]; then
  set -- --tier "$TIER" --dir "$MODEL_DIR"
  [ "$REFRESH" = 1 ] && set -- "$@" --refresh
  echo "fetch        $FETCH $*"
  "$FETCH" "$@" || { echo "stop: fetch-abliterated.sh exited $?" >&2; exit 1; }
fi

# 3. pack — two passes over the shard, one time. Skipped when the pack is already there.
if [ "$ABL" = 1 ]; then
  if [ -f "$PACK_DIR/native_experts.txt" ]; then
    echo "pack         $PACK_DIR already has native_experts.txt — skipped"
  elif [ "$TIER" = IQ3_S ] && [ -f "$PACKER" ]; then
    echo "pack         $PACKER (two passes over the shard; tens of minutes to hours) ..."
    "$PACKER" || { echo "stop: prepare-abliterated-pack.sh exited $?" >&2; exit 1; }
  else
    echo "pack         not automated for $TIER here — see stage 3 in the plan above."
  fi
fi

# 4. config — the uncensored path's config is this tool's to write (spec §7). Stock configs come
#    from upstream and are left alone.
if [ "$ABL" = 1 ]; then
  if [ -f "$CONFIG" ]; then
    echo "config       $CONFIG exists — left alone"
  else
    env BS_MODE=config BS_REPO="$SCRIPT_DIR" BS_OS="$OS" BS_DIR="$DATA_DIR" \
        BS_STRATA="$STRATA_HOME_DIR" BS_TIER="$TIER" BS_ABL="$ABL" BS_CONTEXT="$CONTEXT" \
        BS_KV="$KV" BS_LOW_RAM="$LOW_RAM" BS_PORT="$PORT" BS_HOST="$HOST" BS_VISION="$VISION_MODE" \
        BS_MODEL_DIR="$MODEL_DIR" BS_PACK_DIR="$PACK_DIR" BS_CONFIG="$CONFIG" \
        "$PY" - <<'PY'
import json, os, sys
sys.path.insert(0, os.environ["BS_REPO"])
from lib import catalog, vision

os_name = os.environ["BS_OS"]
strata = os.environ["BS_STRATA"]
data = os.environ["BS_DIR"]
tier = os.environ["BS_TIER"]
tier_l = tier.lower()
model_dir = os.environ["BS_MODEL_DIR"]
pack = os.environ["BS_PACK_DIR"]
config = os.environ["BS_CONFIG"]
low_ram = os.environ["BS_LOW_RAM"] == "1"
vision_mode = os.environ["BS_VISION"]

ref = os.path.join(os.environ["BS_REPO"], "reference", "strata-iq3_s-abliterated.json")
with open(ref) as fh:
    cfg = json.load(fh)

plan = catalog.shard_plan(tier)
shard1 = os.path.join(model_dir, plan[0]["file"])
shard2 = os.path.join(model_dir, next(s["file"] for s in plan
                                      if s["oid"] == catalog.shared_shard2()[1]))

# The reference config is the reference box's, paths and all. Every path-bearing field is rewritten
# here; the flags that are not about paths (expert-cache, prefill, spec, mtp) stay as measured.
repl = {"--pack": pack, "--native": shard1, "--ple-gguf": shard2,
        "--max-context": os.environ["BS_CONTEXT"], "--kv": os.environ["BS_KV"],
        "--expert-profile": os.path.join(strata, "data", "expert-profile.bin"),
        "--mtp": os.path.join(data, "mtp", "rt")}
args, i, stripped = [], 0, 0
while i < len(cfg["args"]):
    tok = cfg["args"][i]
    if tok in repl:
        args += [tok, repl[tok]]
        i += 2
        continue
    if tok == "--resident-experts":
        # Upstream writes this only from low_ram_resident (setup.py:6329, :6336). Keep it when
        # this box is in that mode, drop it when it is not, and never stack a second copy.
        i += 1
        if low_ram:
            args.append(tok)
        else:
            stripped += 1
        continue
    args.append(tok)
    i += 1
if low_ram and "--resident-experts" not in args:
    args += ["--resident-experts"]

cfg["exe"] = os.path.join(strata, "engine", "strata")
cfg["args"] = args
cfg["cwd"] = strata
cfg["tokenizer"] = os.path.join(pack, "tokenizer")
cfg["model_name"] = "qwen3.8-flash-next-abliterated-%s" % tier_l
cfg["log"] = os.path.join(strata, "strata-%s-abliterated.log" % tier_l)
cfg["port"] = int(os.environ["BS_PORT"])
cfg["host"] = os.environ["BS_HOST"]
cfg["open_browser"] = False

# lib_dirs is the reference box's CUDA install. An entry that does not exist on this box is worse
# than no key at all, so the ones that are not there are dropped and said.
kept = [d for d in cfg.get("lib_dirs", []) if os.path.isdir(os.path.expanduser(d))]
dropped = [d for d in cfg.get("lib_dirs", []) if d not in kept]
if kept:
    cfg["lib_dirs"] = kept
else:
    cfg.pop("lib_dirs", None)

if vision_mode == "no":
    cfg.pop("vision", None)
else:
    # lib/vision.block() owns the shape: gpu forced on for a Mac (metal/setup_mac.py:677 hardcodes
    # it) and max_tokens written on both paths, because setup_mac.py:678 hardcodes that too
    # (spec §10). Key order is its own.
    cfg["vision"] = vision.block(exe=os.path.join(strata, "engine", "strata-vision"),
                                 mmproj=os.path.join(model_dir, catalog.MMPROJ["file"]),
                                 model=shard1, os_name=os_name,
                                 gpu=vision_mode == "gpu")

parent = os.path.dirname(config)
if parent and not os.path.isdir(parent):
    os.makedirs(parent, exist_ok=True)
tmp = config + ".tmp-bootstrap"
with open(tmp, "w") as fh:
    json.dump(cfg, fh, indent=1)
    fh.write("\n")
os.replace(tmp, config)
print("config       wrote %s" % config)
if dropped:
    print("config       dropped lib_dirs that do not exist here: %s" % ", ".join(dropped))
if stripped:
    print("config       dropped --resident-experts (this box is not in the low-RAM mode)")
PY
  fi
fi

# 5. concurrency — the existing script, as a subprocess, unchanged (spec §5).
if [ -r "$CONFIG" ]; then
  echo "concurrency  $CONCURRENCY --sessions $SESSIONS $CONFIG"
  # Two snapshot figures can appear on one screen here, so say which one the config carries. With
  # the engine down the script sizes from the whole context window (17,406 B/token x --max-context,
  # apply-concurrency-fix.sh:191), which is ~2.3x the phase-one estimate the plan printed above
  # (that one multiplies by an expected conversation length, not the window). Both are estimates
  # until a park happens; the script's is what lands in the file.
  echo "             note: its figure, not the estimate above, is what the config will carry."
  echo "             Once the engine has parked something, re-run it: the /metrics path replaces"
  echo "             both."
  if [ -x "$CONCURRENCY" ]; then
    "$CONCURRENCY" --sessions "$SESSIONS" "$CONFIG" || {
      echo "warning: apply-concurrency-fix.sh exited $? — the config is written; re-run it by hand." >&2; }
  else
    bash "$CONCURRENCY" --sessions "$SESSIONS" "$CONFIG" || {
      echo "warning: apply-concurrency-fix.sh exited $? — re-run it by hand." >&2; }
  fi
else
  echo "concurrency  skipped: no readable config at $CONFIG yet" >&2
fi

# 6. vision — written into the config above by lib/vision.block(); nothing else to do here.
echo "vision       $VISION_MODE in $CONFIG"

# 7. autostart — render, write, then run the enable steps one at a time.
if [ "$AUTOSTART" = 1 ]; then
  unit_file=$(env BS_MODE=autostart BS_REPO="$SCRIPT_DIR" BS_OS="$OS" BS_STRATA="$STRATA_HOME_DIR" \
      BS_CONFIG="$CONFIG" BS_LOG="$LOG_PATH" BS_PORT="$PORT" BS_PYTHON="$PY" \
      "$PY" - <<'PY'
import os, sys
sys.path.insert(0, os.environ["BS_REPO"])
from lib import autostart

os_name = os.environ["BS_OS"]
strata = os.environ["BS_STRATA"]
server = os.path.join(strata, "serve", "server.py")
path = os.path.expanduser(autostart.unit_path(os_name))
body = autostart.render(os_name, server, os.environ["BS_CONFIG"], os.environ["BS_LOG"],
                        port=int(os.environ["BS_PORT"]), python_bin=os.environ["BS_PYTHON"])
parent = os.path.dirname(path)
if parent and not os.path.isdir(parent):
    os.makedirs(parent, exist_ok=True)
tmp = path + ".tmp-bootstrap"
with open(tmp, "w") as fh:
    fh.write(body)
os.replace(tmp, path)
print(path)
PY
) || { echo "stop: could not write the auto-start file" >&2; exit 1; }
  echo "autostart    wrote $unit_file"

  if [ "$OS" = darwin ]; then
    if ! command -v launchctl >/dev/null 2>&1; then
      echo "autostart    no launchctl here — enable it yourself:"
      echo "             launchctl load -w $unit_file"
    else
      # launchctl load -w errors on an already-loaded agent. On a re-run that is the state we
      # wanted, not a failure, so it is reported and not treated as one.
      out=$(launchctl load -w "$unit_file" 2>&1)
      rc=$?
      if [ $rc -eq 0 ]; then
        echo "autostart    loaded"
      else
        case "$out" in
          *"already loaded"*) echo "autostart    already loaded — nothing to do" ;;
          *) echo "warning: launchctl load -w exited $rc: $out" >&2 ;;
        esac
      fi
    fi
  else
    if ! command -v systemctl >/dev/null 2>&1; then
      echo "autostart    no systemctl here — enable it yourself:"
      echo "             systemctl --user daemon-reload && systemctl --user enable --now strata.service"
    else
      systemctl --user daemon-reload || echo "warning: systemctl --user daemon-reload failed" >&2
      systemctl --user enable --now strata.service
      rc=$?
      if [ $rc -ne 0 ]; then
        # The start failing is not the enable failing: the unit is enabled and can be read with
        # journalctl. Reported per step, which is why these are not run as one printed line.
        echo "warning: systemctl --user enable --now exited $rc — the unit may be enabled but not"
        echo "         running. Read it with: journalctl --user -u strata" >&2
      fi
      loginctl enable-linger "${USER:-$(id -un)}" 2>/dev/null \
        || echo "warning: loginctl enable-linger did not take — the unit will not start before" \
                  "someone logs in (spec §11)." >&2
    fi
  fi
  # The undo is printed, not run: undoing the enable is the user's call, and the line is
  # lib/autostart.py's own (enable_commands' second half), so it is echoed verbatim.
  printf 'autostart    undo: %s\n' "$(get undo)"
fi

echo
echo "done. The sizing above is phase one; once the engine has parked something, re-run"
echo "apply-concurrency-fix.sh to replace the estimate with a measurement of your own engine."
