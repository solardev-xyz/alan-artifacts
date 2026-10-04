#!/usr/bin/env python3
import json, statistics, collections
S = "/tmp/m557"
EXCL = set(__import__("sys").argv[1:])
rows = [json.loads(l) for l in open(f"{S}/results.jsonl") if not json.loads(l)["label"].startswith("smoke") and json.loads(l)["label"] not in EXCL]
def pct(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return None
    k = (len(xs) - 1) * p; f = int(k); c = min(f + 1, len(xs) - 1)
    return round(xs[f] + (xs[c] - xs[f]) * (k - f), 2)
med = lambda xs: pct(xs, 0.5)
cells = collections.defaultdict(list)
for r in rows:
    kind = "W" if r["probes"] else ("P" if r["mode"] == "paid" else "F")
    cells[(kind, r["n"], r["build"])].append(r)
print("cell | runs | segs | complete | trunc | 502 | 404 | 256KiB p50/p90/max | TTFB p50/p90 | seg total p50/p90 | last done p50 (min-max) | chunks/s p50 (min-max) | peers@fire")
for key in sorted(cells):
    rs = cells[key]; segs = [q for r in rs for q in r["req"] if q["kind"] == "seg"]
    walls = [r["wall"] for r in rs]; cps = [r["chunks_per_s"] for r in rs]
    print(f"{key} | {len(rs)} | {len(segs)} | {sum(r['complete'] for r in rs)} | {sum(r['truncated'] for r in rs)} | "
          f"{sum(1 for q in segs if q['status']==502)} | {sum(1 for q in segs if q['status']==404)} | "
          f"{med([q['t256'] for q in segs])}/{pct([q['t256'] for q in segs],.9)}/{max((q['t256'] or 0) for q in segs)} | "
          f"{med([q['ttfb'] for q in segs])}/{pct([q['ttfb'] for q in segs],.9)} | "
          f"{med([q['total'] for q in segs if q['status']==200])}/{pct([q['total'] for q in segs if q['status']==200],.9)} | "
          f"{med(walls)} ({min(walls)}-{max(walls)}) | {med(cps)} ({min(cps)}-{max(cps)}) | {[r['peers_at_fire'] for r in rs]}")
    if key[0] == "P":
        plur = sum(r["plur_ledger"] for r in rs); logged = sum(r["plur_logged"] for r in rs)
        byt = sum(r["seg_bytes"] for r in rs); ch = sum(r["cheques"] for r in rs)
        print(f"   paid: cheques={ch} delivered={sum(r['cheques_delivered'] for r in rs)} payfail={sum(r['payment_failures'] for r in rs)} "
              f"plur_ledger={plur} plur_logged={logged} MB={byt/1e6:.1f} cheques/MB={ch/(byt/1e6):.1f} xBZZ/GB={plur/1e16/(byt/1e9):.4f} "
              f"per-run xBZZ={[round(r['plur_ledger']/1e16,5) for r in rs]} journal_left={[r['journal_left'] for r in rs]} ledger={[r['ledger'] for r in rs]}")
    if key[0] == "W":
        for kind in ("site-index", "path"):
            ps = [q for r in rs for q in r["req"] if q["kind"] == kind]
            ok = [q["total"] for q in ps if q["status"] == 200]
            print(f"   {kind}: n={len(ps)} ok={len(ok)} non200={[q['status'] for q in ps if q['status']!=200]} median/p90/max={med(ok)}/{pct(ok,.9)}/{max(ok) if ok else None} >5s={sum(1 for x in ok if x>5)}")
        ok = [q["total"] for r in rs for q in r["req"] if q["kind"] != "seg" and q["status"] == 200]
        print(f"   all probes: n={len(ok)} median/p90/max={med(ok)}/{pct(ok,.9)}/{max(ok) if ok else None}")
