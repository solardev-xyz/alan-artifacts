#!/bin/bash
cd /tmp/m557
python3 dl.py F8-57-5 57 8 32036 free 0
python3 dl.py F8-56-5 56 8 32037 free 0
for i in $(seq 1 12); do
  if [ $((i % 2)) = 1 ]; then A=56; B=57; else A=57; B=56; fi
  python3 rd.py R-$A-$i $A 32038
  python3 rd.py R-$B-$i $B 32039
done
echo ALL_DONE
