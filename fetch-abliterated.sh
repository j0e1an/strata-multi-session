#!/bin/sh
#
# Fetch and verify one of the four abliterated tiers — Q2_0, IQ2_XS, IQ3_XXS, IQ3_S — from
# SC117's abliterated GSQ-RCO repo on Hugging Face, and mark each shard done in the same
# convention setup.py uses.
#
#   ./fetch-abliterated.sh --tier IQ3_S --dir ~/Strata-data/models/IQ3_S-abliterated
#   ./fetch-abliterated.sh --tier Q2_0  --dir ... --print        # the plan; writes nothing
#   ./fetch-abliterated.sh --tier Q2_0  --dir ... --link-shard2 ~/Strata-data/models/IQ3_S/<file>
#   ./fetch-abliterated.sh --tier Q2_0  --dir ... --refresh      # re-read the oids from the Hub
#
# Every file name, byte count and SHA-256 comes out of lib/catalog.py, so the tier table has one
# home and this script only does the things a shell is for: link, download, verify, mark done.
# Nothing here is a literal copied from a table.
#
# Shard 2 is the n-gram/PLE table — the same bytes in all four abliterated tiers and in the stock
# files — so it is hard-linked from whatever is already on the box instead of being pulled 28.8 GB
# again. Downloads resume (curl -C -, retried). Each shard is checked by size first, which is cheap
# and catches a truncated resume, and only then by SHA-256, which reads the whole file.
#
set -e

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

usage() {
  cat <<'EOF'
usage: fetch-abliterated.sh --tier TIER --dir DIR [--link-shard2 PATH] [--refresh] [--print]

  --tier TIER        Q2_0, IQ2_XS, IQ3_XXS or IQ3_S — the four tiers that exist uncensored
  --dir DIR          where the shards go (default $STRATA_DATA/models/<TIER>-abliterated)
  --link-shard2 PATH hard-link shard 2 from PATH instead of searching for a copy
  --refresh          re-read the shard sizes and oids from Hugging Face; offline, warn and use
                     the values pinned in lib/catalog.py rather than failing
  --print            print the plan — every URL, byte count and SHA-256 — and stop there: no
                     directory is created, no link source is needed, no network is touched

A shard already marked <file>.done is skipped, and a partial download resumes. Exit 0 only on a
tree whose shards are the expected size and hash.
EOF
}

TIER="" DIR="" LINK2="" REFRESH=0 PRINT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --tier|--dir|--link-shard2)
      [ $# -ge 2 ] || { echo "error: $1 needs a value" >&2; usage >&2; exit 2; }
      case "$1" in
        --tier)        TIER="$2" ;;
        --dir)         DIR="$2" ;;
        --link-shard2) LINK2="$2" ;;
      esac
      shift 2 ;;
    --refresh) REFRESH=1; shift ;;
    --print)   PRINT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)         echo "error: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[ -n "$TIER" ] || { echo "error: --tier is required" >&2; usage >&2; exit 2; }
DATA="${STRATA_DATA:-$HOME/Strata-data}"
[ -n "$DIR" ] || DIR="$DATA/models/$TIER-abliterated"
command -v python3 >/dev/null 2>&1 || { echo "error: python3 is required to read lib/catalog.py" >&2; exit 1; }

# The plan, read from lib/catalog.py. FA_MODE=print is for a human to read before committing to a
# ~64 GB download; FA_MODE=tsv feeds the two loops below. Neither creates anything, and neither
# reaches the network unless --refresh asked for it — and --print never does.
emit_plan() {
  FA_MODE="$1" FA_TIER="$TIER" FA_DIR="$DIR" FA_LINK2="$LINK2" FA_REFRESH="$REFRESH" \
  FA_DATA="$DATA" FA_REPO_DIR="$SCRIPT_DIR" python3 <<'PY'
import glob, json, os, sys, urllib.request
sys.path.insert(0, os.environ["FA_REPO_DIR"])
from lib import catalog

tier, mode = os.environ["FA_TIER"], os.environ["FA_MODE"]
dest, link2 = os.environ["FA_DIR"], os.environ.get("FA_LINK2") or ""
data_root = os.environ.get("FA_DATA") or os.path.expanduser("~/Strata-data")
refresh = os.environ.get("FA_REFRESH") == "1"


def die(msg, code=1):
    sys.stderr.write("error: %s\n" % msg)
    sys.exit(code)


valid = catalog.abliterated_tiers()
if tier not in valid:
    die("unknown tier %r. This script fetches the abliterated tiers only: %s"
        % (tier, ", ".join(valid)), 2)

plan = catalog.shard_plan(tier)
ple_bytes, ple_oid = catalog.shared_shard2()

# --refresh re-reads the Hub's LFS pointers, for when a tier changes under us. A jammed network is
# not a reason an install cannot proceed: warn, and keep the values pinned in the catalog.
if refresh and mode != "print":
    api = ("https://huggingface.co/api/models/%s/tree/main/%s"
           % (catalog.ABLITERATED_REPO, tier))
    try:
        with urllib.request.urlopen(api, timeout=20) as fh:
            listing = json.load(fh)
        live = {}
        for entry in listing:
            if entry.get("type") != "file":
                continue
            lfs = entry.get("lfs") or {}
            live[entry["path"].rsplit("/", 1)[-1]] = (
                lfs.get("size") or entry.get("size"), lfs.get("oid") or entry.get("oid"))
    except Exception as exc:
        sys.stderr.write("warning: --refresh could not reach Hugging Face (%s); using the values "
                         "pinned in lib/catalog.py\n" % exc)
        live = None
    if live is not None:
        for shard in plan:
            got = live.get(shard["file"])
            if got is None:
                sys.stderr.write("warning: the Hub lists no %s, so %s keeps its catalog values\n"
                                 % (shard["file"], tier))
                continue
            size, oid = got
            if size and size != shard["bytes"]:
                sys.stderr.write("note: the Hub lists %s bytes for %s, the catalog pins %s; using "
                                 "the Hub\n" % (size, shard["file"], shard["bytes"]))
                shard["bytes"] = size
            if oid and oid != shard["oid"]:
                sys.stderr.write("note: the Hub lists sha256 %s for %s, the catalog pins %s; using "
                                 "the Hub\n" % (oid, shard["file"], shard["oid"]))
                shard["oid"] = oid

# The shard that can be hard-linked is the one carrying the shared oid, wherever it sits in the
# plan — what makes it linkable is that it is the same bytes in every tier, not its index.
shared = next((s for s in plan if s["oid"] == ple_oid), None)

if mode == "print":
    total = sum(s["bytes"] for s in plan)
    print("tier        %s (abliterated)" % tier)
    print("repo        %s" % catalog.ABLITERATED_REPO)
    print("destination %s" % dest)
    print()
    for i, s in enumerate(plan, 1):
        if s is shared:
            print("shard %d     hard-link rather than download: %s" % (i, s["file"]))
            print("            the same %s bytes as every other abliterated tier's shard 2, so a"
                  % s["bytes"])
            print("            tier you already have can supply it (--link-shard2 PATH).")
        else:
            print("shard %d     download: %s" % (i, s["file"]))
        print("            url     %s" % s["url"])
        print("            bytes   %s" % s["bytes"])
        print("            sha256  %s" % s["oid"])
        print()
    if shared is not None:
        left = total - shared["bytes"]
        print("downloaded  %s bytes (%.2f GiB) of %s total -- linking shard 2 saves %s bytes"
              % (left, left / 2**30, total, shared["bytes"]))
        print("            If no shard 2 is on the box to link from, this script stops instead of")
        print("            pulling it: fetching the stock tier, or passing --link-shard2, is your")
        print("            call, not a 28.8 GB surprise.")
    if refresh:
        print()
        print("note        --print ignores --refresh: this is the plan from the values pinned in")
        print("            lib/catalog.py, and --print never touches the network.")
    sys.exit(0)

# Where shard 2 comes from. Only files that exist and are exactly the shared size are offered --
# linking something else would put a plausible-looking 28.8 GB file in the tree that the engine
# then reads as the PLE table.
source = ""
if shared is not None:
    target = os.path.join(dest, shared["file"])
    if not os.path.isfile(target) and not os.path.isfile(target + ".done"):
        if link2:
            if not os.path.isfile(link2):
                die("--link-shard2 points at nothing readable: %s" % link2)
            size = os.path.getsize(link2)
            if size != ple_bytes:
                die("--link-shard2 %s is %s bytes; the shared shard 2 is exactly %s bytes. Point "
                    "it at a real shard 2 (any abliterated tier's, or a stock tier's)."
                    % (link2, size, ple_bytes))
            source = link2
        else:
            def shared_name(other):
                return next(e["file"] for e in catalog.shard_plan(other)
                            if e["oid"] == ple_oid)
            models = os.path.join(data_root, "models")
            candidates = sorted(glob.glob(os.path.join(models, tier, "*-00002-of-00002.gguf")))
            candidates += [os.path.join(models, other + "-abliterated", shared_name(other))
                           for other in valid if other != tier]
            candidates += sorted(glob.glob(os.path.join(models, "*", "*-00002-of-00002.gguf")))
            for path in candidates:
                try:
                    if os.path.getsize(path) == ple_bytes:
                        source = path
                        break
                except OSError:
                    continue

for s in plan:
    role = "link" if s is shared else "get"
    extra = source if role == "link" else ""
    print("|".join([role, s["file"], str(s["bytes"]), s["oid"], s["url"], extra]))
PY
}

# --print is a pure plan: it is what the orchestrator shows before a ~64 GB download, so it must
# not create the directory, insist the link source already exists, or need the network.
if [ "$PRINT" = 1 ]; then
  emit_plan print
  exit 0
fi

plan=$(emit_plan tsv)          # validates the tier and the link source before anything is written
mkdir -p "$DIR"

# Pass 1: the shared shard 2, linked before anything is downloaded. A missing source should fail
# while the 51 GiB download is still ahead of us, not an hour behind it.
while IFS='|' read -r role file bytes oid url source; do
  [ "$role" = link ] || continue
  target="$DIR/$file"
  if [ -f "$target.done" ]; then
    echo "$file already marked done; nothing to link"
    continue
  fi
  if [ -f "$target" ]; then
    actual=$(wc -c < "$target" | tr -d '[:space:]')
    [ "$actual" = "$bytes" ] || {
      echo "error: $target is $actual bytes, expected $bytes" >&2; exit 1; }
    printf '%s' "$(date '+%Y-%m-%d %H:%M')" > "$target.done"
    echo "$file already on disk at the expected size; marked done"
    continue
  fi
  if [ -z "$source" ]; then
    {
      echo "error: no shard 2 to hard-link into $DIR."
      echo "       Shard 2 is the same $bytes bytes in all four abliterated tiers and in the"
      echo "       stock files, so a copy is usually already on this box. Either fetch the stock"
      echo "       tier first (setup.py puts it under \$STRATA_DATA/models/<TIER>/), or point at"
      echo "       one you already have:"
      echo "         --link-shard2 /path/to/any-tier-00002-of-00002.gguf"
      echo "       This script will not download $bytes bytes as a silent fallback. That is"
      echo "       your decision, not a surprise at the end of a long run."
    } >&2
    exit 1
  fi
  if ! link_err=$(ln "$source" "$target" 2>&1); then
    {
      echo "error: cannot hard-link $source into $target: $link_err"
      echo "       A hard link needs both on one filesystem. Either fetch into a directory on the"
      echo "       filesystem that already holds the copy, or copy it yourself -- $bytes bytes of"
      echo "       disk and time, which this script will not spend on your behalf:"
      echo "         cp \"$source\" \"$target\""
    } >&2
    exit 1
  fi
  printf '%s' "$(date '+%Y-%m-%d %H:%M')" > "$target.done"
  echo "$file hard-linked from $source ($bytes bytes, identical LFS oid $oid)"
done <<EOF
$plan
EOF

# Pass 2: the shards that have to come off the Hub.
while IFS='|' read -r role file bytes oid url source; do
  [ "$role" = get ] || continue
  target="$DIR/$file"
  if [ -f "$target.done" ]; then
    echo "$file already marked done; nothing to fetch"
    continue
  fi
  if [ -f "$target" ] && [ "$(wc -c < "$target" | tr -d '[:space:]')" = "$bytes" ]; then
    echo "$file is already on disk at the expected size; verifying it"
  else
    echo "downloading $file ($bytes bytes, $(expr "$bytes" / 1073741824) GiB) ..."
    curl -fL --retry 8 --retry-delay 5 -C - -o "$target" "$url"
  fi

  actual=$(wc -c < "$target" | tr -d '[:space:]')
  if [ "$actual" != "$bytes" ]; then
    echo "SIZE MISMATCH: got $actual, expected $bytes (re-run this script to resume)" >&2
    exit 1
  fi

  echo "verifying SHA-256 (reads the whole file, a few minutes) ..."
  got=$(sha256sum "$target" | cut -d" " -f1)
  if [ "$got" != "$oid" ]; then
    echo "SHA-256 MISMATCH: got $got, expected $oid" >&2
    exit 1
  fi

  printf '%s' "$(date '+%Y-%m-%d %H:%M')" > "$target.done"
  echo "$file downloaded and verified: $got"
done <<EOF
$plan
EOF

echo "verified tree: $DIR"
