#!/usr/bin/env python3
"""One AntDrive-shaped 300 MiB upload through #126's ant-ffi harness (#138's method).
Fresh data dir seeded with chequebook.json (no deploy path reachable) and the
chequebook's outbound ledger; afterwards the ledger (file + #141 journal) is
checked (no shrink, nothing moved aside / lost) and carried back; the data dir
(key-derived state) is deleted.
v0.5.56's OutboundLedger keys by bare beneficiary, so it gets the 0x370e section
with the prefix stripped, and its figures are folded back under the prefix (max).
usage: up.py <56|57> <label> <swap true|false> [SIZE_MB]"""
import collections, json, os, re, shutil, subprocess, sys, time
S = "/tmp/m557"
CB = "370e6965a8c169dbf3456edf852f5a7ffa2f1e81"
W = "d5572300e441b77b72bdd318bbfa7b97a1f03096"
LEDGER = f"{S}/ledger-current.json"
BATCH = "0xdc24a8c40d5349a305cc3588d567a0c5d2f2aeb1c561665d44c596a48d73d0dd"
build, label, swap = sys.argv[1:4]
size_mb = sys.argv[4] if len(sys.argv) > 4 else "300"
binary = f"{S}/target-{build}/release/examples/paid_upload_bench"
logdir = f"{S}/up/{label}"; dd = f"{logdir}/data"
shutil.rmtree(logdir, ignore_errors=True); os.makedirs(dd, mode=0o700); os.makedirs(f"{S}/files", exist_ok=True)
before = json.load(open(LEDGER))
if build == "56":
    seed = {k.split(":", 1)[1]: v for k, v in before.items() if k.startswith(CB + ":")}
else:
    seed = before
json.dump(seed, open(f"{dd}/pushsync_outbound.json", "w"))
json.dump({"chequebook": "0x" + CB, "issuer": "0x" + W, "salt": "", "deploy_tx": ""}, open(f"{dd}/chequebook.json", "w"))
env = dict(os.environ)
env.update({"MODE": "drive", "LABEL": label, "ANT_DATA_DIR": dd, "ANT_FILE_DIR": f"{S}/files",
            "ANT_KEY_FILE": os.path.expanduser("~/alan/secrets/gnosis-test.key"), "ANT_BATCH": BATCH,
            "GNOSIS_RPC_URL": "https://rpc.gnosischain.com", "SIZE_MB": size_mb, "UPLOAD_TIMEOUT_S": "1800",
            "ANT_LOG": "info,ant_p2p::pushsync_swap=debug,ant_retrieval::accounting=debug,ant_retrieval::fetcher=debug",
            "NO_COLOR": "1"})
if build == "57": env["SWAP_ENABLE"] = swap
t = time.time()
with open(f"{logdir}/stdout.log", "w") as so, open(f"{logdir}/stderr.log", "w") as se:
    p = subprocess.Popen([binary], env=env, stdout=so, stderr=se)
    open(f"{logdir}/pid", "w").write(str(p.pid))
    cpu = []
    while p.poll() is None:
        try:
            f = open(f"/proc/{p.pid}/stat").read().rsplit(")", 1)[1].split()
            cpu.append((int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK"))
        except Exception: pass
        time.sleep(5)
    rc = p.returncode
wall = time.time() - t

# --- ledger -----------------------------------------------------------------
problems = {}
names = sorted(os.listdir(dd))
journals = [n for n in names if n in ("pushsync_outbound.json.journal", "pushsync_outbound.json.journal.old")]
odd = [n for n in names if n.startswith("pushsync_outbound.json") and n != "pushsync_outbound.json" and n not in journals]
odd += [n for n in names if "lost" in n or "corrupt" in n]
if odd: problems["lost_or_moved_files"] = odd
after = {}
try: after = json.load(open(f"{dd}/pushsync_outbound.json"))
except Exception as e: problems["ledger_unreadable"] = str(e)
for j in journals:
    for line in open(f"{dd}/{j}"):
        line = line.strip()
        if not line or line.startswith("#"): continue
        for k, v in json.loads(line).items():
            if int(v) > int(after.get(k, 0)): after[k] = v
if build == "56":
    merged = dict(before)
    for k, v in after.items():
        key = k if ":" in k else f"{CB}:{k}"
        if int(v) > int(merged.get(key, 0)): merged[key] = v
    after = merged
shrunk = {k: (v, after.get(k)) for k, v in before.items() if int(after.get(k, "0")) < int(v)}
if shrunk: problems["shrunk"] = shrunk
tb = sum(int(v) for v in before.values()); ta = sum(int(v) for v in after.values())
if after and not problems:
    shutil.copy(LEDGER, f"{S}/ledger-before-{label}.json")
    json.dump(after, open(LEDGER, "w"), indent=2)

# --- log tallies ------------------------------------------------------------
c = collections.Counter(); plur = 0; peers_paid = set()
def field(l, k):
    m = re.search(rf"\b{k}=(\S+)", l); return m.group(1) if m else None
for l in open(f"{logdir}/stderr.log", errors="replace"):
    if "emitted pushsync cheque" in l:
        c["old_style_cheques"] += 1; plur += int(field(l, "amount") or 0); peers_paid.add(field(l, "peer"))
    elif "emitted SWAP cheque" in l:
        c["cheques_delivered" if field(l, "delivered") == "true" else "cheques_undelivered"] += 1
        plur += int(field(l, "plur") or 0); peers_paid.add(field(l, "peer"))
    elif "cheque emit failed" in l: c["cheque_emit_failed"] += 1
    if "peer disconnected" in l: c["disconnects"] += 1
    if "SWAP payment failed" in l: c["payment_failures"] += 1
    if "connection is closed" in l: c["connection_is_closed"] += 1
    if "pushsync attempt failed; hedging" in l: c["hedge_after_failure"] += 1
    if "at its credit limit; waiting for credit" in l: c["gate_waits"] += 1
    if "no pushsync peer has credit" in l: c["gate_giveups"] += 1
st = []
for l in open(f"{logdir}/stdout.log"):
    m = re.search(r"STATUS t=([\d.]+) (\{.*\})", l)
    if m: st.append((float(m.group(1)), json.loads(m.group(2))))
total = next((j.get("chunks_total") for _, j in reversed(st) if j.get("chunks_total")), None)
def first(frac):
    for tt, j in st:
        if total and j.get("chunks_pushed", 0) >= frac * total: return tt
last = st[-1][1] if st else {}
harness = [l.strip()[:300] for l in open(f"{logdir}/stdout.log") if re.search(r"PEERS|SWAP_ENABLE|CONNECTED|SETTLEMENT|SWAP_STATUS|UPLOAD_START|UPLOAD_END|HARNESS_FAIL", l)]
res = {"label": label, "build": build, "swap": swap, "rc": rc, "wall_s": round(wall, 1),
       "status": last.get("status"), "chunks_total": total, "chunks_pushed": last.get("chunks_pushed"),
       "t50": first(0.5), "t90": first(0.9), "t99": first(0.99),
       "t100": next((tt for tt, j in st if j.get("status") == "completed"), None),
       "requeued": last.get("chunks_requeued"), "last_error": (last.get("last_error") or "")[:200],
       "cpu_s": cpu[-1] if cpu else None, **dict(c), "cheque_plur_logged": plur, "peers_paid": len(peers_paid - {None}),
       "ledger_delta_plur": ta - tb, "ledger_entries": len(after), "journals_left": journals,
       "ledger_problems": problems, "harness": harness}
print("RESULT " + json.dumps(res), flush=True)
open(f"{logdir}/result.json", "w").write(json.dumps(res))
with open(f"{S}/up-results.jsonl", "a") as f: f.write(json.dumps(res) + "\n")
shutil.rmtree(dd, ignore_errors=True)
