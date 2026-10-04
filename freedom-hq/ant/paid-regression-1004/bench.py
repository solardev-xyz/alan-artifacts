#!/usr/bin/env python3
# Paid/unpaid cold-burst bench, adapted from #139's harness (ANSI-safe ready detection, FIRE mode).
# usage: bench.py <label> <antd-binary> <n> <port>   env: SWAP=true|false FIRE=paying|t120
import json, os, re, shutil, signal, subprocess, sys, threading, time, urllib.request
S = "/tmp/rg"
CB = "0x370e6965a8c169dbf3456edf852f5a7ffa2f1e81"
LEDGER = f"{S}/ledger-current.json"; REFS = f"{S}/refs-pool.txt"; USED = f"{S}/refs-used.txt"; RESULTS = f"{S}/results.jsonl"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
label, binary, n, port = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
swap = os.environ.get("SWAP", "true"); fire = os.environ.get("FIRE", "paying")
used = set(open(USED).read().split()) if os.path.exists(USED) else set()
refs = [r for r in open(REFS).read().split() if r not in used][:n]
assert len(refs) == n
with open(USED, "a") as f: f.write("\n".join(refs) + "\n")
dd = f"{S}/runs/{label}"; shutil.rmtree(dd, ignore_errors=True); os.makedirs(dd)
before = json.load(open(LEDGER)); shutil.copy(LEDGER, f"{dd}/pushsync_outbound.json")
logp = f"{S}/logs/{label}.log"
env = dict(os.environ)
env["SWAP_OWNER_KEY"] = open(os.path.expanduser("~/alan/secrets/gnosis-test.key")).read().strip()
args = [binary, "--data-dir", dd, "--api-addr", f"127.0.0.1:{port}", "--no-control-socket",
        "--no-disk-cache", "--gnosis-logs-rpc-url", "", "--gnosis-rpc-url", "https://rpc.gnosischain.com",
        "--chequebook", CB, "--no-auto-chequebook", "--swap-enable", swap,
        "--log-level", "info,ant_retrieval::accounting=debug,ant_retrieval::fetcher=debug,ant_p2p::pushsync_swap=info,ant_p2p::pseudosettle=trace"]
logf = open(logp, "w"); t_start = time.time()
proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT, env=env)
env.pop("SWAP_OWNER_KEY", None); del env
print(f"{label}: antd pid {proc.pid} swap={swap} fire={fire}", flush=True)
def ready():
    deadline = time.time() + 120
    while time.time() < deadline:
        txt = ANSI.sub("", open(logp).read())
        if fire == "t120":
            if time.time() - t_start >= 120: return True
        elif swap == "true":
            if "paying=true" in txt: return True
        else:
            if "SWAP settlement:" in txt: return True
        time.sleep(0.05)
    return False
ok = ready(); t_fire = time.time(); peers = 0
try: peers = len(json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/peers", timeout=5))["peers"])
except Exception: pass
print(f"{label}: ready={ok} after {t_fire - t_start:.2f}s, {peers} peers", flush=True)
res = []
def get(i, ref):
    path = f"/bzz/{ref}/" if i % 2 == 0 else f"/bytes/{ref}"
    t0 = time.time(); ttfb = None; nbytes = 0; status = None; clen = None; err = None
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=180)
        status = r.status; clen = int(r.headers.get("content-length") or -1)
        while True:
            b = r.read(65536)
            if ttfb is None: ttfb = time.time() - t0
            if not b: break
            nbytes += len(b)
    except urllib.error.HTTPError as e:
        status = e.code; err = e.read()[:300].decode(errors="replace")
    except Exception as e:
        err = repr(e)[:300]
    res.append(dict(path=path[:6], ref=ref, status=status, bytes=nbytes, clen=clen,
                    ttfb=round(ttfb or 0, 3), total=round(time.time() - t0, 3), err=err))
ths = [threading.Thread(target=get, args=(i, r)) for i, r in enumerate(refs)]
for t in ths: t.start()
for t in ths: t.join()
t_done = time.time(); wall = max(r["total"] for r in res)
time.sleep(7)
proc.send_signal(signal.SIGINT)
try: proc.wait(20)
except subprocess.TimeoutExpired: proc.kill(); proc.wait()
logf.close()
log = ANSI.sub("", open(logp).read()); open(logp, "w").write(log)
cheques = []
for line in log.splitlines():
    if "emitted SWAP cheque" not in line: continue
    g = lambda k: (re.search(rf"\b{k}=(\S+)", line) or [None, None])[1]
    cheques.append(dict(peer=g("peer"), delivered=g("delivered") == "true", units=int(g("units")), plur=int(g("plur"))))
cnt = lambda pat: len(re.findall(pat, log))
ref_ok = [int(x) for x in re.findall(r"refresh ok.*?accepted=(\d+)", log)]
after_path = f"{dd}/pushsync_outbound.json"; after = json.load(open(after_path))
shrank = [k for k, v in before.items() if int(after.get(k, 0)) < int(v)]
lost = [f for f in os.listdir(dd) if "lost" in f or "corrupt" in f or f.startswith("pushsync_outbound.json.")]
issued = sum(int(v) for v in after.values()) - sum(int(v) for v in before.values())
if not shrank and not lost:
    shutil.copy(after_path, LEDGER); ledger_note = "copied back"
else:
    ledger_note = f"NOT copied back: shrank={len(shrank)} lost={lost}"
complete = sum(1 for r in res if r["status"] == 200 and r["bytes"] == r["clen"])
total_bytes = sum(r["bytes"] for r in res); units = sorted(c["units"] for c in cheques)
per_peer = {}
for c in cheques: per_peer[c["peer"]] = per_peer.get(c["peer"], 0) + 1
med = lambda xs: sorted(xs)[len(xs)//2] if xs else 0
out = dict(label=label, binary=os.path.basename(binary), n=n, swap=swap, fire=fire, peers_at_fire=peers,
           ready_s=round(t_fire - t_start, 2), wall=wall, complete=complete,
           chunks_per_s=round(total_bytes / 4096 / wall, 1), mbit_s=round(total_bytes * 8 / wall / 1e6, 2),
           ttfb_med=med([r["ttfb"] for r in res]), ttfb_max=max(r["ttfb"] for r in res),
           total_med=med([r["total"] for r in res]),
           ttfb=sorted(r["ttfb"] for r in res), totals=sorted(r["total"] for r in res),
           cheques=len(cheques), delivered=sum(c["delivered"] for c in cheques),
           undelivered_timeout=cnt(r"not counted as delivered: timed out"), undelivered_conn=cnt(r"not counted as delivered: a connection"),
           payment_failures=cnt(r"SWAP payment failed"), peers_paid=len(per_peer),
           units_median=med(units), units_sum=sum(units),
           refresh_ok=len(ref_ok), refresh_ok_zero=sum(1 for x in ref_ok if x == 0), refresh_units=sum(ref_ok),
           refresh_failed=cnt(r"refresh failed"), refresh_timeout=cnt(r"refresh timed out"),
           credit_waits=cnt(r"every candidate peer overdraft-skipped; waiting for credit"),
           credit_freed=cnt(r"credit came free; dispatching"), credit_giveup=cnt(r"credit wait budget exhausted"),
           plur_logged=sum(c["plur"] for c in cheques), plur_ledger=issued, ledger=ledger_note,
           errors=[r for r in res if r["err"] or r["status"] != 200])
with open(RESULTS, "a") as f: f.write(json.dumps(out) + "\n")
shutil.rmtree(dd, ignore_errors=True)
print(json.dumps({k: v for k, v in out.items() if k not in ("ttfb", "totals")}), flush=True)
