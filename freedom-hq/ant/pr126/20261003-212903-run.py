#!/usr/bin/env python3
"""One #127 measurement run: fresh data dir seeded with the chequebook
association and the current outbound ledger, the harness binary, then
ledger checks (no entry shrank, nothing moved aside / marked lost) and
log tallies. The data dir (key-derived state) is deleted afterwards.

usage: run.py <binary> <label> <mode> [KEY=VAL ...]
"""
import json, os, re, shutil, subprocess, sys, time, collections

S = os.environ["ALAN_SCRATCH"]
CB = "370e6965a8c169dbf3456edf852f5a7ffa2f1e81"
W = "d5572300e441b77b72bdd318bbfa7b97a1f03096"
LEDGER = f"{S}/ledger/current.json"
binary, label, mode = sys.argv[1:4]
extra = dict(a.split("=", 1) for a in sys.argv[4:])
ANALYZE_ONLY = binary == "--analyze"

dd = f"{S}/runs/{label}/data"
logdir = f"{S}/runs/{label}"
if ANALYZE_ONLY:
    prev = json.load(open(f"{logdir}/result.json"))
if not ANALYZE_ONLY and os.path.exists(dd):
    shutil.rmtree(dd)
if not ANALYZE_ONLY:
    os.makedirs(dd)
os.makedirs(f"{S}/files", exist_ok=True)
before = json.load(open(LEDGER))
if not ANALYZE_ONLY:
    shutil.copy(LEDGER, f"{dd}/pushsync_outbound.json")
    json.dump({"chequebook": "0x" + CB, "issuer": "0x" + W, "salt": "", "deploy_tx": ""},
              open(f"{dd}/chequebook.json", "w"))
env = dict(os.environ)
env.update({
    "MODE": mode, "LABEL": label, "ANT_DATA_DIR": dd, "ANT_FILE_DIR": f"{S}/files",
    "ANT_KEY_FILE": os.environ["ANT_KEY_FILE"],
    "GNOSIS_RPC_URL": "https://rpc.gnosischain.com",
    "ANT_LOG": "info,ant_p2p::pushsync_swap=debug,ant_retrieval::accounting=debug",
})
env.update(extra)
env["NO_COLOR"] = "1"
if not ANALYZE_ONLY:
    t = time.time()
    with open(f"{logdir}/stdout.log", "w") as so, open(f"{logdir}/stderr.log", "w") as se:
        p = subprocess.Popen([binary], env=env, stdout=so, stderr=se)
        open(f"{logdir}/pid", "w").write(str(p.pid))
        rc = p.wait()
    wall = time.time() - t
else:
    rc, wall = prev["rc"], prev["wall_s"]

# --- ledger checks -------------------------------------------------------
problems = {}
names = sorted(os.listdir(dd)) if not ANALYZE_ONLY else []
odd = [n for n in names if n.startswith("pushsync_outbound.json") and n != "pushsync_outbound.json"]
if odd:
    problems["lost_or_moved_files"] = odd
after = {}
try:
    after = json.load(open(f"{dd}/pushsync_outbound.json")) if not ANALYZE_ONLY else None
except Exception as e:
    problems["ledger_unreadable"] = str(e)
if ANALYZE_ONLY:
    before = json.load(open(f"{S}/ledger/before-{label}.json")) if os.path.exists(f"{S}/ledger/before-{label}.json") else before
    after = json.load(open(LEDGER)) if os.path.exists(f"{S}/ledger/before-{label}.json") else before
shrunk = {k: (v, after.get(k)) for k, v in before.items() if int(after.get(k, "0")) < int(v)}
if shrunk:
    problems["shrunk"] = shrunk
mine = lambda d: {k: int(v) for k, v in d.items() if k.startswith(CB + ":")}
tb, ta = sum(mine(before).values()), sum(mine(after).values())
if after and not problems and not ANALYZE_ONLY:
    shutil.copy(LEDGER, f"{S}/ledger/before-{label}.json")
    json.dump(after, open(LEDGER, "w"))
new_benef = len(set(mine(after)) - set(mine(before)))

# --- log tallies ---------------------------------------------------------
ANSI = re.compile(r"\x1b\[[0-9;]*m")
log = ANSI.sub("", open(f"{logdir}/stderr.log", errors="replace").read()).splitlines()
def field(l, k):
    m = re.search(rf"\b{k}=(\S+)", l)
    return m.group(1) if m else None
c = collections.Counter()
plur = collections.Counter()
peers_paid = set()
for l in log:
    if "emitted pushsync cheque" in l:
        c["pushsync_cheques_raw"] += 1
        plur["pushsync_raw"] += int(field(l, "amount") or 0)
        peers_paid.add(field(l, "peer"))
    elif "emitted retrieval cheque" in l or "emitted SWAP cheque" in l:
        ok = (field(l, "delivered") or field(l, "processed")) == "true"
        c["swap_cheques_delivered" if ok else "swap_cheques_undelivered"] += 1
        plur["swap"] += int(field(l, "plur") or 0)
        plur["swap_units"] += int(field(l, "units") or 0)
        peers_paid.add(field(l, "peer"))
    elif "cheque emit failed" in l:
        c["pushsync_emit_failed"] += 1
    elif "peer disconnected" in l:
        c["disconnects"] += 1
    if re.search(r"blocklist|blacklist", l, re.I):
        c["blocklist_mentions"] += 1
    if "payment failed" in l or "SWAP payment" in l and "fail" in l:
        c["payment_failures"] += 1
    if re.search(r"overdraft", l, re.I) and "pushsync" in l.lower():
        c["pushsync_overdraft"] += 1
    if " WARN " in l and "pushsync" in l.lower():
        msg = re.sub(r"\S+=\S+", "", l.split("ant_retrieval::fetcher:")[-1])[:90].strip()
        c["pushsync_warn"] += 1
        c["warn: " + msg] += 1
    if re.search(r"reset|RST", l) and "push" in l.lower():
        c["pushsync_reset_lines"] += 1
top_warn = dict(sorted(((k, v) for k, v in c.items() if k.startswith("warn: ")), key=lambda x: -x[1])[:6])
for k in list(c):
    if k.startswith("warn: "):
        del c[k]
res = {"label": label, "mode": mode, "rc": rc, "wall_s": round(wall, 1), **dict(c),
       "plur": dict(plur), "peers_paid": len(peers_paid - {None}),
       "ledger_total_before": tb, "ledger_total_after": ta, "ledger_delta_plur": ta - tb,
       "new_beneficiaries": new_benef, "ledger_problems": problems, "top_pushsync_warns": top_warn}
out = open(f"{logdir}/stdout.log").read().splitlines()
res["harness"] = [l for l in out if re.search(r"PEERS|SWAP_ENABLE|UPLOAD_START|UPLOAD_END|STREAM_START|STREAM_CAPTURE_END|HARNESS_FAIL", l)]
rep = [l for l in out if " REPORT " in l]
if rep:
    try:
        r = json.loads(rep[-1].split(" REPORT ", 1)[1].rsplit("}", 1)[0] + "}")
        res["publisher"] = {k: r.get(k) for k in ("segments_pushed", "segments_published", "segments_listed",
            "segments_failed", "segments_dropped", "bytes_published", "chunks_published", "sustained_mbit_s",
            "publish_ms_p50", "publish_ms_p95", "publish_ms_max", "lag_ms_p50", "lag_ms_p95", "lag_ms_max",
            "lag_ms_final", "kept_up", "error_count")}
        res["publisher_errors"] = collections.Counter(re.sub(r"\d+", "N", e)[:100] for e in r.get("errors", [])).most_common(5)
    except Exception as e:
        res["publisher_parse_error"] = str(e)
print("RESULT " + json.dumps(res))
open(f"{logdir}/result.json", "w").write(json.dumps(res))
# key-derived state: the data dir goes
if not ANALYZE_ONLY:
    shutil.rmtree(dd, ignore_errors=True)
