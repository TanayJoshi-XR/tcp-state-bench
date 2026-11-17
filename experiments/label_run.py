"""Run a labelled transfer (one or more flows, optional cross-traffic) and write
ground-truth files plus a joined per-packet table for every flow.

    python -m experiments.label_run reno --flows 2 --stagger 0.3 --out data/pilot
    python -m experiments.label_run cubic --cross-rate 1e6 --cross-on 0.3 --cross-off 0.3

Files per flow i (prefix = <alg>_s<seed>_f<i>):
    *_tx.csv     sender truth per transmitted data packet (cwnd, ssthresh, ...)
    *_ack.csv    sender truth per processed ACK
    *_evt.csv    loss-recovery events
    *_link.csv   fate of each data packet at the bottleneck (queue depth, drop reason)
    *_rx.csv     receiver arrivals
    *_joined.csv one row per transmission = tx + link fate + receiver time + derived labels
Per run:
    <alg>_s<seed>_cross_link.csv   fate of every cross-traffic packet (only with cross-traffic)
    <alg>_s<seed>_control_link.csv fate of SYN/ACK/FIN packets on the data link
    <alg>_s<seed>_meta.json        parameters + the result of every consistency check
"""
import argparse
import csv
import json
import os
import subprocess

from experiments import netsim
from experiments.labels import write_rx
from protocol.congestion import Reno, Cubic

ALGS = {"reno": lambda: Reno(), "cubic": lambda: Cubic()}
PKT = 1041


def _read(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def join(tx, link, rx, flow):
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
        row = {"flow": flow}
        row.update(r)
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


def check_flow(joined, tx, link, rx, missing, queue_pkts):
    c = {}
    c["tx_rows"] = len(tx)
    c["link_rows"] = len(link)
    c["tx_link_exact_match"] = (len(tx) == len(link) and len(joined) == len(tx) and missing == 0)
    c["max_abs_tx_vs_link_enter_ms"] = round(1000 * max(
        (abs(float(j["t"]) - float(j["t_enter"])) for j in joined), default=0), 3)
    delivered = [j for j in joined if j["fate"] == "delivered"]
    c["delivered"] = len(delivered)
    c["rx_rows"] = len(rx)
    c["delivered_without_rx"] = sum(1 for j in delivered if j["t_rx"] == "")
    c["drop_queue"] = sum(1 for j in joined if j["fate"] == "drop_queue")
    c["drop_random"] = sum(1 for j in joined if j["fate"] == "drop_random")
    c["queue_seen_max"] = max((int(j["queue_seen"]) for j in joined), default=0)
    c["queue_seen_within_buffer"] = c["queue_seen_max"] <= queue_pkts
    bad = 0                    # a send must fit inside the window the label claims
    for j in joined:
        if j["reason"] in ("new", "timeout_goback"):
            usable = max(1, min(int(float(j["cwnd"])), int(j["peer_window"])))
            if int(j["flight"]) >= usable:
                bad += 1
    c["sends_violating_window_label"] = bad
    c["spurious_retx"] = sum(int(j["spurious_retx"]) for j in joined)
    c["retx_total"] = sum(1 for j in joined if int(j["nth"]) > 1)
    return c


def run(alg, size, rate_bps, delay, queue_pkts, loss, seed, outdir, prefix=None,
        rcv_buf=256, n_flows=1, stagger=0.0, cross=None):
    os.makedirs(outdir, exist_ok=True)
    cc = ALGS[alg]
    res = netsim.run_flows(n_flows, size, rate_bps, delay, queue_pkts, make_cc=cc, loss=loss,
                           label=True, seed=seed, recv_kw={"rcv_buf": rcv_buf},
                           stagger=stagger, cross=cross)
    prefix = prefix or f"{alg}_s{seed}"
    lab = res["label"]
    flows_meta, all_ok = [], True
    n_data_rows = n_drop_q = 0
    for i, fl in enumerate(lab["flows"]):
        fp = f"{prefix}_f{i}"
        fl["trace"].write(outdir, fp)
        lab["tap"].write(fl["addr"], outdir, fp)
        write_rx(fl["rx_log"], outdir, fp)
        tx = _read(os.path.join(outdir, f"{fp}_tx.csv"))
        link = _read(os.path.join(outdir, f"{fp}_link.csv"))
        rx = _read(os.path.join(outdir, f"{fp}_rx.csv"))
        joined, missing = join(tx, link, rx, i)
        if joined:
            with open(os.path.join(outdir, f"{fp}_joined.csv"), "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(joined[0].keys()))
                w.writeheader()
                w.writerows(joined)
        ch = check_flow(joined, tx, link, rx, missing, queue_pkts)
        n_data_rows += len(link)
        n_drop_q += ch["drop_queue"]
        flows_meta.append({"flow": i, "transfer_time_s": res["flows"][i]["transfer_time"],
                           "sender_stats": {k: res["flows"][i][k] for k in
                                            ("timeouts", "fast_retransmits", "partial_retransmits",
                                             "sent_total", "packets")},
                           "checks": ch})
    # cross-traffic packets: every one must have a fate row
    cross_rows = lab["tap"].rows.get("cross", [])
    cross_drop_q = sum(1 for r in cross_rows if r[4] == "drop_queue")
    if cross_rows:
        with open(os.path.join(outdir, f"{prefix}_cross_link.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["cross_idx", "nth", "t_enter", "queue_seen", "fate",
                        "t_depart_model", "t_deliver_model", "t_deliver_actual"])
            w.writerows(cross_rows)
    control_rows = lab["tap"].rows.get("control", [])
    control_drop_q = sum(1 for r in control_rows if r[4] == "drop_queue")
    if control_rows:
        with open(os.path.join(outdir, f"{prefix}_control_link.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["flags", "nth", "t_enter", "queue_seen", "fate",
                        "t_depart_model", "t_deliver_model", "t_deliver_actual"])
            w.writerows(control_rows)
    glob = {"data_rows_all_flows": n_data_rows,
            "control_packets_recorded": len(control_rows),
            "control_queue_drops": control_drop_q,
            "cross_packets_sent": res.get("cross", {}).get("sent", 0),
            "cross_rows": len(cross_rows),
            "cross_every_packet_recorded": len(cross_rows) == res.get("cross", {}).get("sent", 0),
            "drop_queue_all": n_drop_q + cross_drop_q + control_drop_q,
            "link_stats_dropped_queue": res["link"]["dropped_queue"],
            "link_stats_dropped_random": res["link"]["dropped_random"]}
    glob["queue_drops_account_for_link_stats"] = glob["drop_queue_all"] == glob["link_stats_dropped_queue"]
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
                                         stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    except Exception:
        commit, dirty = None, None
    meta = {"alg": alg, "size": size, "rate_bps": rate_bps, "delay_s": delay,
            "queue_pkts": queue_pkts, "rcv_buf": rcv_buf, "loss": loss, "seed": seed,
            "n_flows": n_flows, "stagger_s": stagger, "cross": cross, "packet_bytes": PKT,
            "git_commit": commit, "git_dirty": dirty, "flows": flows_meta, "global_checks": glob}
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
    ap.add_argument("--flows", type=int, default=1)
    ap.add_argument("--stagger", type=float, default=0.0)
    ap.add_argument("--cross-rate", type=float, default=0.0, help="cross-traffic rate while ON, bit/s")
    ap.add_argument("--cross-on", type=float, default=None)
    ap.add_argument("--cross-off", type=float, default=None)
    ap.add_argument("--out", default="data/pilot")
    a = ap.parse_args()
    cross = None
    if a.cross_rate > 0:
        cross = {"rate_bps": a.cross_rate, "on_s": a.cross_on, "off_s": a.cross_off, "seed": a.seed}
    m = run(a.alg, a.size, a.rate, a.delay, a.queue, a.loss, a.seed, a.out,
            rcv_buf=a.rcvbuf, n_flows=a.flows, stagger=a.stagger, cross=cross)
    print(json.dumps(m, indent=2))
