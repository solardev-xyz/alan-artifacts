#!/bin/bash
cd /tmp/m557
flock -w 900 ~/alan/state/gnosis-test.lock python3 up.py 56 U-56-free false > up-56-free.out 2>&1
grep -q '"ledger_problems": {}' up/U-56-free/result.json || { echo "STOP ledger problem 56"; exit 2; }
flock -w 900 ~/alan/state/gnosis-test.lock python3 up.py 57 U-57-free false > up-57-free.out 2>&1
grep -q '"ledger_problems": {}' up/U-57-free/result.json || { echo "STOP ledger problem 57"; exit 2; }
echo FREE_DONE
