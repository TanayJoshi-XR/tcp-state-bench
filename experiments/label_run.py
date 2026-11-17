"""Run one labelled transfer and write ground-truth files + a joined per-packet table.

    python -m experiments.label_run reno --loss 0.0 --seed 1 --out results/labels

Outputs (prefix = <alg>_<params>):
    *_tx.csv     sender-side truth per transmitted data packet (cwnd, ssthresh, ...)
    *_ack.csv    sender-side truth per processed ACK
    *_evt.csv    loss-recovery events
    *_link.csv   fate of each data packet at the bottleneck (queue depth, drop reason)
    *_rx.csv     receiver arrivals
    *_joined.csv one row per transmission = tx + link fate + receiver time + derived labels
    *_meta.json  parameters + the result of every consistency check
"""
import argparse
import csv
import json
import os
import subprocess
import sys

from experiments import netsim
from experiments.labels import write_rx
from protocol.congestion import Reno, Cubic

ALGS = {"reno": lambda: Reno(), "cubic": lambda: Cubic()}
PKT = 1041


def _read(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def join(tx, link, rx):
    """Join tx rows, link rows and rx rows. Keys: (seq, nth) for tx<->link, and
    (seq, k-th delivered copy) for link<->rx (the link is FIFO: no reordering)."""
    link_by = {(int(r["seq"]), int(r["nth"])): r for r in link}
    rx_by = {(int(r["seq"]), int(r["k"])): r for r in rx}
    delivered_k = {}
    out, missing = [], 0
    fate_by_seq = {}
    for r in sorted(link, key=lambda r: (int(r["seq"]), int(r["nth"]))):
        fate_by_seq.setdefault(int(r["seq"]), []).append(r["fate"])
    for r in tx:
        key = (int(r["seq"]), int(r["nth"]))
        lk = link_by.get(key)
        if lk is None:
            missing += 1
            continue
        row = dict(r)
        row.update({"fate": lk["fate"], "queue_seen": lk["queue_seen"], "t_enter": lk["t_enter"],
                    "t_deliver_actual": lk["t_deliver_actual"]})
        rxt = ""
        if lk["fate"] == "delivered":
            k = delivered_k[int(r["seq"])] = delivered_k.get(int(r["seq"]), 0) + 1
            rr = rx_by.get((int(r["seq"]), k))
            rxt = rr["t_rx"] if rr else ""
        row["t_rx"] = rxt
        nth = int(r["nth"])
        earlier = fate_by_seq[int(r["seq"])][:nth - 1]
        # a retransmission is spurious if an earlier copy of the same packet was delivered
        row["spurious_retx"] = int(nth > 1 and "delivered" in earlier)
        out.append(row)
    return out, missing


def check(joined, tx, link, rx, res, queue_pkts, n_cc):
    c = {}
    c["tx_rows"] = len(tx)
    c["link_rows"] = len(link)
    c["tx_link_exact_match"] = (len(tx) == len(link) and len(joined) == len(tx))
    c["max_abs_tx_vs_link_enter_ms"] = round(1000 * max(
        (abs(float(j["t"]) - float(j["t_enter"])) for j in joined), default=0), 3)
    delivered = [j for j in joined if j["fate"] == "delivered"]
    c["delivered"] = len(delivered)
    c["rx_rows"] = len(rx)
    c["delivered_without_rx"] = sum(1 for j in delivered if j["t_rx"] == "")
    c["drop_queue"] = sum(1 for j in joined if j["fate"] == "drop_queue")
    c["drop_random"] = sum(1 for j in joined if j["fate"] == "drop_random")
    c["link_stats_dropped_queue"] = res["link"]["dropped_queue"]
    c["link_stats_dropped_random"] = res["link"]["dropped_random"]
    c["queue_seen_max"] = max((int(j["queue_seen"]) for j in joined), default=0)
    c["queue_seen_within_buffer"] = c["queue_seen_max"] <= queue_pkts
    # label sanity: a send must fit inside the window the label claims
    bad = 0
    for j in joined:
        if j["reason"] in ("new", "timeout_goback"):
            usable = max(1, min(int(float(j["cwnd"])), int(j["peer_window"])))
            if int(j["flight"]) >= usable:
                bad += 1
    c["sends_violating_window_label"] = bad
    c["spurious_retx"] = sum(int(j["spurious_retx"]) for j in joined)
    c["retx_total"] = sum(1 for j in joined if int(j["nth"]) > 1)
    return c


def run(alg, size, rate_bps, delay, queue_pkts, loss, seed, outdir, prefix=None, rcv_buf=256):
    os.makedirs(outdir, exist_ok=True)
    cc = ALGS[alg]
    res = netsim.run_flows(1, size, rate_bps, delay, queue_pkts, make_cc=cc, loss=loss,
                           label=True, seed=seed, recv_kw={"rcv_buf": rcv_buf})
    prefix = prefix or f"{alg}_s{seed}"
    lab = res["label"]
    f0 = lab["flows"][0]
    f0["trace"].write(outdir, prefix)
    lab["tap"].write(f0["addr"], outdir, prefix)
    write_rx(f0["rx_log"], outdir, prefix)
    tx = _read(os.path.join(outdir, f"{prefix}_tx.csv"))
    link = _read(os.path.join(outdir, f"{prefix}_link.csv"))
    rx = _read(os.path.join(outdir, f"{prefix}_rx.csv"))
    joined, missing = join(tx, link, rx)
    if joined:
        with open(os.path.join(outdir, f"{prefix}_joined.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(joined[0].keys()))
            w.writeheader()
            w.writerows(joined)
    checks = check(joined, tx, link, rx, res, queue_pkts, None)
    checks["tx_without_link_row"] = missing
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    except Exception:
        commit, dirty = None, None
    meta = {"alg": alg, "size": size, "rate_bps": rate_bps, "delay_s": delay,
            "queue_pkts": queue_pkts, "rcv_buf": rcv_buf, "loss": loss, "seed": seed, "packet_bytes": PKT,
            "git_commit": commit, "git_dirty": dirty,
            "transfer_time_s": res["flows"][0]["transfer_time"],
            "sender_stats": {k: res["flows"][0][k] for k in
                             ("timeouts", "fast_retransmits", "partial_retransmits",
                              "sent_total", "packets")},
            "checks": checks}
    with open(os.path.join(outdir, f"{prefix}_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("alg", choices=ALGS)
    ap.add_argument("--size", type=int, default=300_000)
    ap.add_argument("--rate", type=float, default=4e6)
    ap.add_argument("--delay", type=float, default=0.02)
    ap.add_argument("--queue", type=int, default=30)
    ap.add_argument("--loss", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--rcvbuf", type=int, default=256)
    ap.add_argument("--out", default="results/labels")
    a = ap.parse_args()
    m = run(a.alg, a.size, a.rate, a.delay, a.queue, a.loss, a.seed, a.out, rcv_buf=a.rcvbuf)
    print(json.dumps(m, indent=2))
