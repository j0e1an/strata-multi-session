#!/bin/sh
# Fetch SC117's abliterated GSQ-RCO IQ3_S shard 1, verify it against Hugging Face's LFS SHA-256,
# and mark it done in the same convention setup.py uses. Shard 2 (the 28.8 GB PLE table) is
# byte-identical to the upstream file (same LFS oid), so it is hard-linked instead of downloaded.
set -e

REPO="SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF"
DIR="${STRATA_DATA:-$HOME/Strata-data}/models/IQ3_S-abliterated"
S1="Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ3_S-00001-of-00002.gguf"
S2="Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ3_S-00002-of-00002.gguf"
SHA1="40f369eca301ec6ea318dbb671b3132c26a2f0b89aa5458a7051b33215ee296c"
SHA2="316b46f3a2dbd68c900f43136ab9449f9dcc3725dfd8c794847c204bc161e113"
SIZE1=55063448064

mkdir -p "$DIR"

if [ ! -f "$DIR/$S2" ]; then
  ln "${STRATA_DATA:-$HOME/Strata-data}/models/IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00002-of-00002.gguf" \
     "$DIR/$S2"
  printf '%s' "$(date '+%Y-%m-%d %H:%M')" > "$DIR/$S2.done"
  echo "shard 2 hard-linked from the upstream IQ3_S folder (identical LFS oid $SHA2)"
fi

if [ -f "$DIR/$S1.done" ]; then
  echo "shard 1 already marked done; nothing to fetch"
  exit 0
fi

echo "downloading $S1 (51.28 GiB) ..."
curl -fL --retry 8 --retry-delay 5 -C - \
  -o "$DIR/$S1" \
  "https://huggingface.co/$REPO/resolve/main/IQ3_S/$S1"

ACTUAL_SIZE=$(stat -c %s "$DIR/$S1")
if [ "$ACTUAL_SIZE" != "$SIZE1" ]; then
  echo "SIZE MISMATCH: got $ACTUAL_SIZE, expected $SIZE1 (re-run this script to resume)"
  exit 1
fi

echo "verifying SHA-256 (reads 51 GiB, a few minutes) ..."
GOT=$(sha256sum "$DIR/$S1" | cut -d" " -f1)
if [ "$GOT" != "$SHA1" ]; then
  echo "SHA-256 MISMATCH: got $GOT, expected $SHA1"
  exit 1
fi

printf '%s' "$(date '+%Y-%m-%d %H:%M')" > "$DIR/$S1.done"
echo "shard 1 downloaded and verified: $GOT"
