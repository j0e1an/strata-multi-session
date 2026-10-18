#!/bin/sh
# Wait for the verified abliterated download, then build its Strata native pack.
# Mirrors exactly what setup.py did for the stock IQ3_S model (two iq_pack.py passes:
# index/tokenizer/dense first, then the 47 GB experts.bin for the low-RAM resident mode).
# A fresh pack is required: SC117's tensor transplant moves expert offsets, so the stock
# pack's native_experts.txt no longer matches these files (the engine refuses it with
# "ArenaExpertSource: the pack's native_experts.txt does not match the model").
set -e

DIR="${STRATA_DATA:-$HOME/Strata-data}/models/IQ3_S-abliterated"
S1="Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ3_S-00001-of-00002.gguf"
PACK="${STRATA_DATA:-$HOME/Strata-data}/packs/iq3_s_abliterated"
PY="${STRATA_HOME:-$HOME/Strata}/.venv/bin/python"
TOOL="${STRATA_HOME:-$HOME/Strata}/tools/iq_pack.py"

echo "waiting for the download to finish and verify ..."
i=0
while [ ! -f "$DIR/$S1.done" ]; do
  i=$((i + 1))
  if [ "$i" -gt 180 ]; then
    echo "TIMED OUT after 30 min waiting for $S1.done"
    # The fetcher writes no log of its own: this one is an operator redirect (e.g.
    #   ./fetch-abliterated.sh --tier IQ3_S 2>&1 | tee ~/Strata/fetch-abliterated.log)
    # and may not exist. || true keeps a missing log from becoming this script's exit
    # status — the timeout's own message and exit 1 below are the report.
    tail -5 "${STRATA_HOME:-$HOME/Strata}/fetch-abliterated.log" 2>/dev/null || true
    exit 1
  fi
  sleep 10
done
echo "download verified at $(cat "$DIR/$S1.done")"

echo "pass 1: index.txt + tokenizer + dense.bin ..."
"$PY" "$TOOL" --gguf "$DIR/$S1" --out "$PACK"

echo "pass 2: experts.bin (47 GB, one time) ..."
"$PY" "$TOOL" --gguf "$DIR/$S1" --out "$PACK" --experts-bin

if [ ! -f "$PACK/native_experts.txt" ]; then
  echo "pack is NOT finished: native_experts.txt missing"
  exit 1
fi
echo "PACK READY: $PACK"
