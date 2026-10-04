#!/usr/bin/env python3
# #136's readiness harness: from spawn poll /readiness every 10 ms; at the first 200,
# GET /bzz/<fresh seg>/ (30 s cap); on a non-200 retry the same ref every 0.2 s.
# usage: rd.py <label> <56|57> <port>
import http.client, json, os, shutil, signal, subprocess, sys, time
S = "/tmp/m557"; BIN = {"56": f"{S}/bin/antd-0.5.56", "57": f"{S}/bin/antd-0.5.57"}
label, build, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
assert 32020 <= port <= 32049
used = set(open(f"{S}/used.txt").read().split())
ref = next(r for r in open(f"{S}/pool.txt").read().split() if r not in used)
open(f"{S}/used.txt", "a").write(ref + "\n")
dd = f"{S}/runs/{label}"; shutil.rmtree(dd, ignore_errors=True); os.makedirs(dd)
args = [BIN[build], "--data-dir", dd, "--api-addr", f"127.0.0.1:{port}", "--no-control-socket", "--no-disk-cache",
        "--gnosis-logs-rpc-url", "", "--no-auto-chequebook", "--log-level", "info"]
if build == "57": args += ["--swap-enable", "false"]
logf = open(f"{S}/logs/{label}.log", "w")
t0 = time.time(); p = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT, env=dict(os.environ, NO_COLOR="1"))
def req(path, method="GET", timeout=2.0):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout); c.request(method, path)
    r = c.getresponse(); body = r.read(); return r.status, body
t_ready = None; t_peer = None
while time.time() - t0 < 120:
    try:
        st, _ = req("/readiness", timeout=1)
        if t_peer is None:
            try:
                if json.loads(req("/peers")[1])["peers"]: t_peer = time.time() - t0
            except Exception: pass
        if st == 200: t_ready = time.time() - t0; break
    except Exception: pass
    time.sleep(0.01)
attempts = []; t_ok = None; t_ok_hdr = None
while t_ready is not None and time.time() - t0 < t_ready + 90:
    a0 = time.time()
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=30); c.request("GET", f"/bzz/{ref}/")
        r = c.getresponse(); th = time.time() - t0; body = r.read(); st = r.status
        clen = int(r.headers.get("content-length") or -1)
        attempts.append([round(a0 - t0, 3), st, len(body)])
        if st == 200 and len(body) == clen: t_ok = time.time() - t0; t_ok_hdr = th; break
    except Exception as e:
        attempts.append([round(a0 - t0, 3), repr(e)[:60], 0])
    time.sleep(0.2)
p.send_signal(signal.SIGINT)
try: p.wait(20)
except subprocess.TimeoutExpired: p.kill(); p.wait()
logf.close(); shutil.rmtree(dd, ignore_errors=True)
out = dict(label=label, build=build, ref=ref[:8], ready_s=None if t_ready is None else round(t_ready, 3),
           first_peer_s=None if t_peer is None else round(t_peer, 3),
           first200_hdr_s=None if t_ok_hdr is None else round(t_ok_hdr, 3), first200_done_s=None if t_ok is None else round(t_ok, 3),
           first_after_ready_status=attempts[0][1] if attempts else None, non200=sum(1 for a in attempts if a[1] != 200), attempts=attempts[:20])
open(f"{S}/rd-results.jsonl", "a").write(json.dumps(out) + "\n"); print(json.dumps(out), flush=True)
