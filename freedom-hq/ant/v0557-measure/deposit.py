import json, os, secrets, shutil, signal, subprocess, sys, time, urllib.request
S="/tmp/m557"; amount=int(sys.argv[1]); port=32048
CB="0x370e6965a8c169dbf3456edf852f5a7ffa2f1e81"
dd=f"{S}/deposit-dd"; shutil.rmtree(dd, ignore_errors=True); os.makedirs(dd, mode=0o700)
key=open(os.path.expanduser("~/alan/secrets/gnosis-test.key")).read().strip()
key=key[2:] if key.startswith("0x") else key
fd=os.open(f"{dd}/identity.json", os.O_WRONLY|os.O_CREAT, 0o600)
os.write(fd, json.dumps({"signing_key":key,"overlay_nonce":secrets.token_hex(32)}).encode()); os.close(fd)
shutil.copy(f"{S}/ledger-current.json", f"{dd}/pushsync_outbound.json")
env=dict(os.environ); env["SWAP_OWNER_KEY"]=key; del key
logf=open(f"{S}/logs/deposit-{int(time.time())}.log","w")
p=subprocess.Popen([f"{S}/bin/antd-0.5.57","--data-dir",dd,"--api-addr",f"127.0.0.1:{port}","--no-control-socket","--gnosis-logs-rpc-url","","--gnosis-rpc-url","https://rpc.gnosischain.com","--chequebook",CB,"--no-auto-chequebook","--swap-enable","false"],stdout=logf,stderr=subprocess.STDOUT,env=env)
del env
try:
    for i in range(120):
        time.sleep(1)
        try:
            b=urllib.request.urlopen(f"http://127.0.0.1:{port}/chequebook/balance",timeout=5).read().decode()
            print("balance before:",b); break
        except Exception: pass
    req=urllib.request.Request(f"http://127.0.0.1:{port}/chequebook/deposit?amount={amount}",method="POST")
    try:
        r=urllib.request.urlopen(req,timeout=180); print("deposit:",r.status,r.read().decode())
    except urllib.error.HTTPError as e: print("deposit failed:",e.code,e.read().decode())
    time.sleep(10)
    print("balance after:",urllib.request.urlopen(f"http://127.0.0.1:{port}/chequebook/balance",timeout=10).read().decode())
finally:
    p.send_signal(signal.SIGINT)
    try: p.wait(20)
    except subprocess.TimeoutExpired: p.kill(); p.wait()
    shutil.rmtree(dd, ignore_errors=True)
    print("data dir removed:", not os.path.exists(dd))
