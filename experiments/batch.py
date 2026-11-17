"""Run a batch of labelled transfers from a parameter grid, one subprocess per run.

    python -m experiments.batch --out data/raw/test --sample 30 --seeds 1

Each run goes to <out>/<run_id>/ (label files + meta.json). <out>/manifest.csv has one row per
run: parameters, status ('ok' / 'check_failed' / 'error' / 'timeout'), wall time, and the
checks that failed. A crashing or hanging run is recorded and the batch carries on.
Re-running the same command skips runs that already have status ok (resume).

Grid (what one run varies):
    alg      reno (NewReno-style) | cubic
    rtt_ms   propagation round trip (one-way delay = rtt/2)
    rate_mbps  bottleneck rate
    buffer   bottleneck queue in packets
    loss     extra random loss probability
    cross    none | bursty (50% of the bottleneck rate, 0.4 s on / 0.4 s off)
Transfer size = about DURATION seconds at the expected rate (at least 300 kB), so slow
start is not the whole run and no run takes minutes.
"""
import argparse
import csv
import itertools
import json
import os
import random
import subprocess
import sys
import time

GRID = {
    "alg": ["reno", "cubic"],
    "rtt_ms": [20, 40, 80, 160],
    "rate_mbps": [2, 4, 8],
    "buffer": [10, 30, 100],
    "loss": [0.0, 0.005, 0.02],
    "cross": ["none", "bursty"],
}
DURATION = 3.0
MIN_SIZE = 300_000
RUN_TIMEOUT = 120
FIELDS = ["run_id", "alg", "rtt_ms", "rate_mbps", "buffer", "loss", "cross", "seed", "size",
          "status", "wall_s", "transfer_s", "tx_rows", "retx", "timeouts", "fast_retx",
          "failed_checks"]


def configs():
    keys = list(GRID)
    return [dict(zip(keys, v)) for v in itertools.product(*GRID.values())]


def run_id(c, seed):
    return (f"{c['alg']}_rtt{c['rtt_ms']}_bw{c['rate_mbps']}_q{c['buffer']}_"
            f"loss{c['loss']}_x{c['cross']}_s{seed}")


def size_for(c):
    """About DURATION seconds of transfer at the rate we expect to achieve: the bottleneck rate,
    or the Mathis estimate 1.22*MSS/(RTT*sqrt(loss)) when random loss is what limits TCP.
    (Sizing by link rate alone made lossy long-RTT runs take 20-30 s.)"""
    rate = c["rate_mbps"] * 1e6 / 8
    if c["loss"] > 0:
        rate = min(rate, 1.22 * 1024 / (c["rtt_ms"] / 1000.0 * c["loss"] ** 0.5))
    return max(MIN_SIZE, int(rate * DURATION))


def failed_checks(meta):
    bad = []
    g = meta["global_checks"]
    if not g["queue_drops_account_for_link_stats"]:
        bad.append("queue_drops_do_not_add_up")
    if not g["cross_every_packet_recorded"]:
        bad.append("cross_packets_missing")
    for f in meta["flows"]:
        c = f["checks"]
        if not c["tx_link_exact_match"]:
            bad.append("tx_link_mismatch")
        if c["delivered_without_rx"]:
            bad.append("delivered_without_rx")
        if c["sends_violating_window_label"]:
            bad.append("window_label_violation")
        if not c["queue_seen_within_buffer"]:
            bad.append("queue_over_buffer")
    return sorted(set(bad))


def run_one(c, seed, out):
    rid = run_id(c, seed)
    rdir = os.path.join(out, rid)
    cmd = [sys.executable, "-m", "experiments.label_run", c["alg"],
           "--size", str(size_for(c)), "--rate", str(c["rate_mbps"] * 1e6),
           "--delay", str(c["rtt_ms"] / 2000.0), "--queue", str(c["buffer"]),
           "--loss", str(c["loss"]), "--seed", str(seed), "--out", rdir]
    if c["cross"] == "bursty":
        cmd += ["--cross-rate", str(c["rate_mbps"] * 1e6 * 0.5), "--cross-on", "0.4",
                "--cross-off", "0.4"]
    row = {"run_id": rid, "seed": seed, "size": size_for(c), **c}
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=RUN_TIMEOUT)
        row["wall_s"] = round(time.time() - t0, 2)
        if p.returncode != 0:
            row["status"] = "error"
            row["failed_checks"] = (p.stderr.strip().splitlines() or ["?"])[-1][:200]
            return row
        with open(os.path.join(rdir, f"{c['alg']}_s{seed}_meta.json")) as f:
            meta = json.load(f)
        bad = failed_checks(meta)
        fl = meta["flows"][0]
        row.update(status="check_failed" if bad else "ok", failed_checks=";".join(bad),
                   transfer_s=round(fl["transfer_time_s"], 2), tx_rows=fl["sender_stats"]["sent_total"],
                   retx=fl["sender_stats"]["sent_total"] - fl["sender_stats"]["packets"],
                   timeouts=fl["sender_stats"]["timeouts"], fast_retx=fl["sender_stats"]["fast_retransmits"])
    except subprocess.TimeoutExpired:
        row["status"], row["wall_s"] = "timeout", round(time.time() - t0, 2)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--sample", type=int, default=0, help="random sample of this many configs (0 = all)")
    ap.add_argument("--sample-seed", type=int, default=0)
    ap.add_argument("--seeds", type=int, default=1, help="repeat each config with seeds 1..N")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cfgs = configs()
    if a.sample:
        cfgs = random.Random(a.sample_seed).sample(cfgs, a.sample)
    jobs = [(c, s) for c in cfgs for s in range(1, a.seeds + 1)]
    mpath = os.path.join(a.out, "manifest.csv")
    done = {}
    if os.path.exists(mpath):
        with open(mpath) as f:
            done = {r["run_id"]: r for r in csv.DictReader(f)}
    rows = dict(done)
    t_start = time.time()
    for i, (c, s) in enumerate(jobs, 1):
        rid = run_id(c, s)
        if done.get(rid, {}).get("status") == "ok":
            continue
        row = run_one(c, s, a.out)
        rows[rid] = row
        with open(mpath, "w", newline="") as f:           # rewrite after every run: crash-safe
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows.values())
        print(f"[{i}/{len(jobs)}] {rid}: {row['status']} {row.get('wall_s')}s {row.get('failed_checks', '')}",
              flush=True)
    st = {}
    for r in rows.values():
        st[r["status"]] = st.get(r["status"], 0) + 1
    print("DONE", st, f"total wall {time.time() - t_start:.0f}s")


if __name__ == "__main__":
    main()
