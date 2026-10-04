#!/bin/bash
# Interleaved download cells. Paid runs hold the wallet lock and stop on low spendable.
cd /tmp/m557
PY=/home/ubuntu/alan/.venv/bin/python
port=32020
nextport() { port=$((port+1)); [ $port -gt 32035 ] && port=32020; }
paid() {
  sp=$($PY chain.py ledger-current.json | grep -o 'spendable [0-9.]*' | cut -d' ' -f2)
  echo "spendable before $1: $sp"
  awk "BEGIN{exit !($sp < 0.02)}" && { echo "STOP: spendable low"; exit 1; }
  flock -w 900 ~/alan/state/gnosis-test.lock python3 dl.py $1 57 $2 $port paid 0
  grep -q "\"label\": \"$1\".*NOT copied" results.jsonl && { echo "STOP: ledger problem in $1"; exit 2; }
}
for r in 1 2 3 4; do
  if [ $((r % 2)) = 1 ]; then A=56; B=57; else A=57; B=56; fi
  python3 dl.py F8-$A-$r $A 8 $port free 0; nextport
  python3 dl.py F8-$B-$r $B 8 $port free 0; nextport
  [ $r -le 3 ] && { paid P8-57-$r 8; nextport; }
  python3 dl.py F12-$B-$r $B 12 $port free 0; nextport
  python3 dl.py F12-$A-$r $A 12 $port free 0; nextport
  [ $r -le 3 ] && { paid P12-57-$r 12; nextport; }
  python3 dl.py W-$A-$r $A 8 $port free 1; nextport
  python3 dl.py W-$B-$r $B 8 $port free 1; nextport
done
python3 dl.py W-56-5 56 8 $port free 1; nextport
python3 dl.py W-57-5 57 8 $port free 1; nextport
echo ALL_DONE
