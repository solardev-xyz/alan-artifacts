#!/usr/bin/env python3
# v0.5.56 vs v0.5.57 cold-burst bench. Merges #139/#141's bench.py (paid ledger carry,
# journal merge, cheque tally) with #135's per-read timing (time to 256 KiB) and
# #132's site probes under load.
# usage: dl.py <label> <build 56|57> <n> <port> <free|paid> [probes 0|1]
import json, os, re, shutil, signal, subprocess, sys, threading, time, urllib.request, http.client
S = "/tmp/m557"
CB = "0x370e6965a8c169dbf3456edf852f5a7ffa2f1e81"
BIN = {"56": f"{S}/bin/antd-0.5.56", "57": f"{S}/bin/antd-0.5.57"}
LEDGER = f"{S}/ledger-current.json"; POOL = f"{S}/pool.txt"; USED = f"{S}/used.txt"; RESULTS = f"{S}/results.jsonl"
SITE1 = "c4f8a45301b57d0e36f0f5348ed371aee42ea0b9fe9b3caaf26015d652eedc40"
SITE2 = "ab77201f6541a9ceafb98a46c643273cfa397a87798273dd17feb2aa366ce2e6"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
label, build, n, port, mode = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
probes = len(sys.argv) > 6 and sys.argv[6] == "1"
assert 32020 <= port <= 32049
paid = mode == "paid"
assert not (paid and build != "57")
used = set(open(USED).read().split())
refs = [r for r in open(POOL).read().split() if r not in used][:n]
assert len(refs) == n
with open(USED, "a") as f: f.write("\n".join(refs) + "\n")
dd = f"{S}/runs/{label}"; shutil.rmtree(dd, ignore_errors=True); os.makedirs(dd, mode=0o700)
before = {}
if paid:
    before = json.load(open(LEDGER)); shutil.copy(LEDGER, f"{dd}/pushsync_outbound.json")
logp = f"{S}/logs/{label}.log"
env = dict(os.environ); env["NO_COLOR"] = "1"
args = [BIN[build], "--data-dir", dd, "--api-addr", f"127.0.0.1:{port}", "--no-control-socket",
        "--no-disk-cache", "--gnosis-logs-rpc-url", "", "--no-auto-chequebook",
        "--log-level", "info,ant_retrieval::accounting=debug,ant_p2p::pushsync_swap=info"]
if build == "57":
    args += ["--swap-enable", "true" if paid else "false"]
if paid:
    args += ["--gnosis-rpc-url", "https://rpc.gnosischain.com", "--chequebook", CB]
    env["SWAP_OWNER_KEY"] = open(os.path.expanduser("~/alan/secrets/gnosis-test.key")).read().strip()
logf = open(logp, "w"); t_start = time.time()
proc = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT, env=env)
env.pop("SWAP_OWNER_KEY", None); del env
print(f"{label}: pid {proc.pid} build={build} mode={mode} n={n} probes={probes}", flush=True)

def peers():
    try: return len(json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/peers", timeout=2))["peers"])
    except Exception: return 0
t_peer = None; ok = False; deadline = time.time() + 120
while time.time() < deadline:
    if t_peer is None and peers() > 0: t_peer = time.time()
    if t_peer is not None:
        if not paid: ok = True; break
        if "paying=true" in ANSI.sub("", open(logp).read()): ok = True; break
    time.sleep(0.05)
t_fire = time.time(); p_fire = peers()
print(f"{label}: ready={ok} first_peer={None if t_peer is None else round(t_peer - t_start, 2)}s fire={t_fire - t_start:.2f}s peers={p_fire}", flush=True)

res = []
def get(kind, path, method="GET"):
    t0 = time.time(); ttfb = t256 = None; nbytes = 0; status = clen = err = None; trunc = False
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=180)
        c.request(method, path); r = c.getresponse(); status = r.status
        t_hdr = time.time() - t0
        clen = int(r.headers.get("content-length") or -1)
        if method == "HEAD":
            ttfb = t_hdr; r.read()
        else:
            while True:
                try: b = r.read1(16384)
                except http.client.IncompleteRead as e: trunc = True; err = "IncompleteRead"; break
                if not b: break
                if ttfb is None: ttfb = time.time() - t0
                nbytes += len(b)
                if t256 is None and nbytes >= 262144: t256 = time.time() - t0
            if status == 200 and clen >= 0 and nbytes != clen: trunc = True
    except Exception as e:
        err = repr(e)[:200]; trunc = nbytes > 0
    res.append(dict(kind=kind, path=path[:20], method=method, status=status, bytes=nbytes, clen=clen,
                    ttfb=None if ttfb is None else round(ttfb, 3), t256=None if t256 is None else round(t256, 3),
                    total=round(time.time() - t0, 3), trunc=trunc, err=err))
jobs = [("seg", f"/bzz/{r}/", "GET") for r in refs]
if probes:
    jobs += [("site-index", f"/bzz/{SITE1}/", "GET"),
             ("path", f"/bzz/{SITE1}/tracks/01%20arrival.wav", "HEAD"),
             ("site-index", f"/bzz/{SITE2}/", "GET")]
ths = [threading.Thread(target=get, args=j) for j in jobs]
for t in ths: t.start()
for t in ths: t.join()
time.sleep(7)
proc.send_signal(signal.SIGINT)
try: proc.wait(30)
except subprocess.TimeoutExpired: proc.kill(); proc.wait()
logf.close()
log = ANSI.sub("", open(logp).read()); open(logp, "w").write(log)
cheques = []
for line in log.splitlines():
    if "emitted SWAP cheque" not in line: continue
    g = lambda k: (re.search(rf"\b{k}=(\S+)", line) or [None, None])[1]
    cheques.append(dict(peer=g("peer"), delivered=g("delivered") == "true", units=int(g("units")), plur=int(g("plur"))))
cnt = lambda pat: len(re.findall(pat, log))
out = {}
if paid:
    after_path = f"{dd}/pushsync_outbound.json"
    after = json.load(open(after_path)) if os.path.exists(after_path) else {}
    leftover = []
    for part in (f"{after_path}.journal.old", f"{after_path}.journal"):
        if not os.path.exists(part): continue
        leftover.append(os.path.basename(part))
        for line in open(part):
            line = line.strip()
            if not line or line.startswith("#"): continue
            for k, v in json.loads(line).items():
                if int(v) > int(after.get(k, 0)): after[k] = v
    shrank = [k for k, v in before.items() if int(after.get(k, 0)) < int(v)]
    lost = [f for f in os.listdir(dd) if "lost" in f or "corrupt" in f or "torn" in f]
    issued = sum(int(v) for v in after.values()) - sum(int(v) for v in before.values())
    if not shrank and not lost and after:
        json.dump(after, open(LEDGER, "w"), indent=2); note = "copied back"
    else:
        note = f"NOT copied back: shrank={len(shrank)} lost={lost}"
    out.update(plur_ledger=issued, ledger=note, ledger_entries=len(after), journal_left=leftover)
segs = [r for r in res if r["kind"] == "seg"]
seg_bytes = sum(r["bytes"] for r in segs); wall = max(r["total"] for r in segs)
out = dict(label=label, build=build, mode=mode, n=n, probes=probes, peers_at_fire=p_fire,
           first_peer_s=None if t_peer is None else round(t_peer - t_start, 2), fire_s=round(t_fire - t_start, 2),
           wall=wall, seg_bytes=seg_bytes, chunks_per_s=round(seg_bytes / 4096 / wall, 1),
           complete=sum(1 for r in segs if r["status"] == 200 and not r["trunc"]),
           truncated=sum(1 for r in segs if r["trunc"]), s502=sum(1 for r in res if r["status"] == 502),
           s404=sum(1 for r in res if r["status"] == 404), other_err=sum(1 for r in res if r["status"] not in (200, 404, 502)),
           cheques=len(cheques), cheques_delivered=sum(c["delivered"] for c in cheques),
           plur_logged=sum(c["plur"] for c in cheques), peers_paid=len({c["peer"] for c in cheques}),
           payment_failures=cnt(r"SWAP payment failed"), disconnects=cnt(r"peer disconnected"),
           bzz_retries=cnt(r"manifest lookup failed, retrying"), **out, req=res)
with open(RESULTS, "a") as f: f.write(json.dumps(out) + "\n")
shutil.rmtree(dd, ignore_errors=True)
print(json.dumps({k: v for k, v in out.items() if k != "req"}), flush=True)
