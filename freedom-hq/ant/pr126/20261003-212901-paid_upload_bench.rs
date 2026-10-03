//! #127 measurement harness (not part of the PR): drives an embedded
//! node through the same ant-ffi C ABI the iOS example apps call.
//!
//! * `MODE=drive`  — AntDrive-shaped: `ant_upload_start` on one file,
//!   polled with `ant_upload_status` to completion.
//! * `MODE=stream` — AntStream-shaped: `ant_start_gateway` +
//!   `ant_publisher_start` / `_push_segment` / `_progress` / `_stop`,
//!   fed by a wall-clock synthetic HLS generator.
//! * `MODE=quote` / `MODE=buy` — the product storage flow
//!   (`ant_storage_quote` / `ant_storage_buy`).
//!
//! The account key is read from `ANT_KEY_FILE` into memory, turned into
//! an identity document with `ant_identity_from_key` and handed to
//! `ant_init_with_identity`; it is never printed or written.
use std::ffi::{CStr, CString};
use std::io::Write;
use std::os::raw::c_char;
use std::ptr;
use std::time::{Duration, Instant};

use ant_ffi::*;

fn env(key: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| die(&format!("missing env {key}")))
}
fn env_or<T: std::str::FromStr>(key: &str, d: T) -> T {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(d)
}
fn die(m: &str) -> ! {
    eprintln!("HARNESS: {m}");
    println!("HARNESS_FAIL {m}");
    std::process::exit(1)
}
fn take(p: *mut c_char) -> Option<String> {
    if p.is_null() {
        return None;
    }
    let s = unsafe { CStr::from_ptr(p).to_string_lossy().into_owned() };
    unsafe { ant_free_string(p) };
    Some(s)
}
fn cs(s: &str) -> CString {
    CString::new(s).unwrap()
}
fn t0() -> &'static Instant {
    static T: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    T.get_or_init(Instant::now)
}
fn out(line: String) {
    println!("{:.2} {line}", t0().elapsed().as_secs_f64());
    let _ = std::io::stdout().flush();
}

/// xorshift64* — incompressible, label-seeded bytes, no deps.
struct Rng(u64);
impl Rng {
    fn new(seed: &str) -> Self {
        let mut h: u64 = 0xcbf2_9ce4_8422_2325;
        for b in seed.bytes() {
            h ^= u64::from(b);
            h = h.wrapping_mul(0x100_0000_01b3);
        }
        Self(h | 1)
    }
    fn fill(&mut self, buf: &mut [u8]) {
        for c in buf.chunks_mut(8) {
            self.0 ^= self.0 >> 12;
            self.0 ^= self.0 << 25;
            self.0 ^= self.0 >> 27;
            let v = self.0.wrapping_mul(0x2545_f491_4f6c_dd1d).to_le_bytes();
            c.copy_from_slice(&v[..c.len()]);
        }
    }
}

fn main() {
    t0();
    let mode = env("MODE");
    let data_dir = env("ANT_DATA_DIR");
    let rpc = env("GNOSIS_RPC_URL");
    let label = env_or("LABEL", "run".to_string());

    // Identity from the key file, in memory only.
    let identity = {
        let key = std::fs::read_to_string(env("ANT_KEY_FILE"))
            .unwrap_or_else(|_| die("cannot read key file"));
        let k = cs(key.trim());
        drop(key);
        let mut err = ptr::null_mut();
        take(unsafe { ant_identity_from_key(k.as_ptr(), &raw mut err) })
            .unwrap_or_else(|| die("ant_identity_from_key failed"))
    };
    let mut err = ptr::null_mut();
    let h = unsafe {
        ant_init_with_identity(
            cs(&data_dir).as_ptr(),
            ptr::null(),
            cs(&identity).as_ptr(),
            &raw mut err,
        )
    };
    drop(identity);
    if h.is_null() {
        die(&format!("init: {}", take(err).unwrap_or_default()));
    }
    out(format!("INIT label={label} mode={mode}"));
    if let Ok(v) = std::env::var("SWAP_ENABLE") {
        let on = v == "true";
        let mut err = ptr::null_mut();
        let ok = unsafe { ant_set_swap_enabled(h, on, &raw mut err) } == 0;
        out(format!("SWAP_ENABLE {on} ok={ok} {}", take(err).unwrap_or_default()));
    }
    let code = match mode.as_str() {
        "quote" | "buy" => storage(h, &rpc, &mode),
        "deposit" => deposit(h, &rpc),
        "drive" | "stream" => {
            wait_peers(h);
            connect(h, &rpc);
            if mode == "drive" {
                drive(h, &label)
            } else {
                stream(h, &rpc, &label)
            }
        }
        _ => die("unknown MODE"),
    };
    unsafe { ant_shutdown(h) };
    std::process::exit(code);
}

fn wait_peers(h: *mut AntHandle) {
    let want: i32 = env_or("PEERS", 80);
    let start = Instant::now();
    loop {
        let n = unsafe { ant_peer_count(h) };
        if n >= want || start.elapsed() > Duration::from_secs(240) {
            out(format!("PEERS {n}"));
            return;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
}

fn connect(h: *mut AntHandle, rpc: &str) {
    let mut err = ptr::null_mut();
    let r = unsafe {
        ant_storage_connect_batch(h, cs(rpc).as_ptr(), cs(&env("ANT_BATCH")).as_ptr(), &raw mut err)
    };
    match take(r) {
        Some(s) => out(format!("CONNECTED {s}")),
        None => die(&format!("connect: {}", take(err).unwrap_or_default())),
    }
    let mut err = ptr::null_mut();
    let s = take(unsafe { ant_storage_settlement_status(h, &raw mut err) });
    out(format!(
        "SETTLEMENT {} {}",
        s.unwrap_or_default(),
        take(err).unwrap_or_default()
    ));
    // Let the funds watch publish before the clock starts.
    std::thread::sleep(Duration::from_secs(env_or("SETTLE_S", 3)));
    {
        let mut err = ptr::null_mut();
        let s = take(unsafe { ant_swap_status(h, &raw mut err) });
        out(format!("SWAP_STATUS {}", s.unwrap_or_default()));
    }
}

fn storage(h: *mut AntHandle, rpc: &str, mode: &str) -> i32 {
    let depth: u8 = env_or("DEPTH", 21);
    let days: u64 = env_or("DAYS", 1);
    let mut err = ptr::null_mut();
    let q = take(unsafe { ant_storage_quote(h, cs(rpc).as_ptr(), depth, days, &raw mut err) });
    out(format!("QUOTE {} {}", q.unwrap_or_default(), take(err).unwrap_or_default()));
    if mode == "buy" {
        let amount = env("AMOUNT_PER_CHUNK");
        let mut err = ptr::null_mut();
        let r = take(unsafe {
            ant_storage_buy(h, cs(rpc).as_ptr(), depth, cs(&amount).as_ptr(), 0, &raw mut err)
        });
        out(format!("BUY {} {}", r.unwrap_or_default(), take(err).unwrap_or_default()));
    }
    0
}

fn deposit(h: *mut AntHandle, rpc: &str) -> i32 {
    let mut err = ptr::null_mut();
    let before = take(unsafe { ant_storage_settlement_deposit(h, cs(rpc).as_ptr(), &raw mut err) });
    out(format!("DEPOSIT_BEFORE {} {}", before.unwrap_or_default(), take(err).unwrap_or_default()));
    let amount = env("AMOUNT_PLUR");
    let mut err = ptr::null_mut();
    let r = take(unsafe {
        ant_storage_settlement_topup_amount(h, cs(rpc).as_ptr(), cs(&amount).as_ptr(), &raw mut err)
    });
    out(format!("DEPOSIT_AFTER {} {}", r.clone().unwrap_or_default(), take(err).unwrap_or_default()));
    let mut err = ptr::null_mut();
    let s = take(unsafe { ant_swap_status(h, &raw mut err) });
    out(format!("SWAP_STATUS {}", s.unwrap_or_default()));
    i32::from(r.is_none())
}

fn drive(h: *mut AntHandle, label: &str) -> i32 {
    let mb: usize = env_or("SIZE_MB", 300);
    let path = format!("{}/{label}.bin", env("ANT_FILE_DIR"));
    {
        let mut f = std::fs::File::create(&path).unwrap();
        let mut rng = Rng::new(label);
        let mut buf = vec![0u8; 1 << 20];
        for _ in 0..mb {
            rng.fill(&mut buf);
            f.write_all(&buf).unwrap();
        }
    }
    let mut err = ptr::null_mut();
    let job = take(unsafe {
        ant_upload_start(
            h,
            cs(&path).as_ptr(),
            cs(&env("ANT_BATCH")).as_ptr(),
            cs(&format!("{label}.mp4")).as_ptr(),
            cs("video/mp4").as_ptr(),
            &raw mut err,
        )
    })
    .unwrap_or_else(|| die(&format!("upload_start: {}", take(err).unwrap_or_default())));
    let start = Instant::now();
    out(format!("UPLOAD_START job={job} bytes={}", mb << 20));
    let limit = Duration::from_secs(env_or("UPLOAD_TIMEOUT_S", 7200));
    let cjob = cs(&job);
    loop {
        std::thread::sleep(Duration::from_secs(5));
        let mut err = ptr::null_mut();
        let v = take(unsafe { ant_upload_status(h, cjob.as_ptr(), &raw mut err) })
            .unwrap_or_default();
        let j: serde_json::Value = serde_json::from_str(&v).unwrap_or_default();
        let status = j["status"].as_str().unwrap_or("").to_string();
        out(format!("STATUS t={:.1} {v}", start.elapsed().as_secs_f64()));
        if status == "completed" || status == "failed" || start.elapsed() > limit {
            out(format!(
                "UPLOAD_END status={status} wall_s={:.1} bytes={}",
                start.elapsed().as_secs_f64(),
                mb << 20
            ));
            let _ = std::fs::remove_file(&path);
            return i32::from(status != "completed");
        }
    }
}

fn stream(h: *mut AntHandle, rpc: &str, label: &str) -> i32 {
    let port: u16 = env_or("GATEWAY_PORT", 31813);
    let addr = format!("127.0.0.1:{port}");
    let mut err = ptr::null_mut();
    if !unsafe { ant_start_gateway(h, cs(&addr).as_ptr(), true, cs(rpc).as_ptr(), &raw mut err) } {
        die(&format!("gateway: {}", take(err).unwrap_or_default()));
    }
    std::thread::sleep(Duration::from_secs(2));
    let seg_ms: u32 = env_or("SEGMENT_MS", 2000);
    let kbps: u32 = env_or("BITRATE_KBPS", 3000);
    let secs: u64 = env_or("DURATION_S", 600);
    let cfg = serde_json::json!({
        "channel": format!("ant127-{label}"),
        "gateway": format!("http://{addr}"),
        "batch_id": env("ANT_BATCH"),
        "segment_ms": seg_ms,
        "bitrate_kbps": kbps,
        "notes": format!("Linux x86_64 harness, synthetic {kbps} kbit/s {seg_ms} ms segments ({label})"),
    });
    let mut err = ptr::null_mut();
    if !unsafe { ant_publisher_start(h, cs(&cfg.to_string()).as_ptr(), &raw mut err) } {
        die(&format!("publisher_start: {}", take(err).unwrap_or_default()));
    }
    let mut rng = Rng::new(label);
    let mut init = vec![0u8; 1200];
    rng.fill(&mut init);
    let mut err = ptr::null_mut();
    unsafe { ant_publisher_push_segment(h, true, init.as_ptr(), init.len(), 0, false, &raw mut err) };
    take(err);
    let seg_bytes = (kbps as usize * 1000 / 8) * seg_ms as usize / 1000;
    let start = Instant::now();
    out(format!("STREAM_START seg_bytes={seg_bytes} segments={}", secs * 1000 / u64::from(seg_ms)));
    let mut n: u64 = 0;
    let mut dropped = 0u64;
    let mut buf = vec![0u8; seg_bytes];
    let mut next_progress = Duration::from_secs(10);
    while start.elapsed() < Duration::from_secs(secs) {
        let due = Duration::from_millis(u64::from(seg_ms) * (n + 1));
        while start.elapsed() < due {
            if start.elapsed() >= next_progress {
                let mut err = ptr::null_mut();
                let p = take(unsafe { ant_publisher_progress(h, &raw mut err) }).unwrap_or_default();
                out(format!("PROGRESS {p}"));
                next_progress += Duration::from_secs(10);
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        rng.fill(&mut buf);
        let mut err = ptr::null_mut();
        let r = unsafe {
            ant_publisher_push_segment(h, false, buf.as_ptr(), buf.len(), seg_ms, false, &raw mut err)
        };
        take(err);
        if r == 1 {
            dropped += 1;
        }
        n += 1;
    }
    out(format!("STREAM_CAPTURE_END pushed={n} dropped_at_push={dropped}"));
    let mut err = ptr::null_mut();
    let rep = take(unsafe { ant_publisher_stop(h, &raw mut err) }).unwrap_or_default();
    out(format!("REPORT {rep} {}", take(err).unwrap_or_default()));
    0
}
