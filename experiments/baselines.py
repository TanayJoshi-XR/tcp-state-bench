"""Baseline 1: estimate the congestion window from a trace, and score it against the truth.

The ESTIMATORS only see the observer view: for each data packet the time and sequence number
at which it was sent, and for each ACK its time and cumulative ack number (what a capture next
to the sender shows). They never read cwnd, ssthresh, state, flight, rto or the link files.
The truth (cwnd at each transmission) is used only by `score`.

    python -m experiments.baselines data/raw/test --out data/raw/test/baseline1.csv

Estimators (all in packets):
  const    the median cwnd of all OTHER runs (a do-nothing reference, leave-one-run-out)
  wsflight Wireshark-style bytes in flight, right after this packet is sent:
           (highest sequence sent + 1) - (highest cumulative ack seen)
  maxflight the largest wsflight over the last RTT (the observer's own RTT estimate, Karn's
           rule: only packets sent once give RTT samples). When a sender is window-limited it
           refills the window every RTT, so the peak flight approximates cwnd.
"""
import argparse
import collections
import csv
import glob
import json
import os
import statistics


def observer_view(run_dir, alg, seed, flow=0):
    pre = os.path.join(run_dir, f"{alg}_s{seed}_f{flow}")
    sends, acks = [], []
    with open(pre + "_tx.csv") as f:
        for r in csv.DictReader(f):
            sends.append((float(r["t"]), int(r["seq"])))
    with open(pre + "_ack.csv") as f:
        for r in csv.DictReader(f):
            acks.append((float(r["t"]), int(r["ack"])))
    return sends, acks


def estimate(sends, acks):
    """One estimate per data packet, in the order of `sends`. Observer information only."""
    events = [(t, 1, i, s) for i, (t, s) in enumerate(sends)] + [(t, 0, -1, a) for t, a in acks]
    events.sort()                                   # at equal times an ACK comes first
    hs = ma = 0                                     # highest seq sent + 1, highest ack seen
    first_sent, count = {}, collections.Counter()
    srtt, have_rtt, recent = 0.2, False, collections.deque()
    ws, mx = [None] * len(sends), [None] * len(sends)
    for t, kind, i, v in events:
        if kind == 1:
            count[v] += 1
            first_sent.setdefault(v, t)
            hs = max(hs, v + 1)
            w = hs - ma
            recent.append((t, w))
            while recent and recent[0][0] < t - srtt:
                recent.popleft()
            ws[i], mx[i] = w, max(x for _, x in recent)
        elif v > ma:                                # an ACK that advances
            last = v - 1
            if count[last] == 1 and last in first_sent:       # Karn: never-retransmitted packets
                s = t - first_sent[last]
                srtt = 0.875 * srtt + 0.125 * s if have_rtt else s
                have_rtt = True
            ma = v
    return ws, mx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("batch_dir")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    runs = []
    with open(os.path.join(a.batch_dir, "manifest.csv")) as f:
        for m in csv.DictReader(f):
            if m["status"] == "ok":
                runs.append(m)
    # truth per run, estimates per run
    per_run = []
    for m in runs:
        d = os.path.join(a.batch_dir, m["run_id"])
        truth = list(csv.DictReader(open(os.path.join(d, f"{m['alg']}_s{m['seed']}_f0_tx.csv"))))
        sends, acks = observer_view(d, m["alg"], m["seed"])
        ws, mx = estimate(sends, acks)
        per_run.append((m, truth, ws, mx))
    all_cwnd = [(i, float(r["cwnd"])) for i, (_, tr, _, _) in enumerate(per_run) for r in tr]
    rows = []
    for i, (m, truth, ws, mx) in enumerate(per_run):
        const = statistics.median([c for j, c in all_cwnd if j != i])     # leave-one-run-out
        for r, a_, b_ in zip(truth, ws, mx):
            c = float(r["cwnd"])
            rows.append({"run_id": m["run_id"], "alg": m["alg"], "rtt_ms": m["rtt_ms"],
                         "loss": m["loss"], "cross": m["cross"], "state": r["state"],
                         "limiter": r["limiter"], "cwnd": c,
                         "err_const": abs(const - c), "err_wsflight": abs(a_ - c),
                         "err_maxflight": abs(b_ - c)})
    if a.out:
        with open(a.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    def table(title, keyf):
        groups = collections.defaultdict(list)
        for r in rows:
            groups[keyf(r)].append(r)
        print(f"\n{title}")
        print(f"{'group':<16}{'n':>7}{'median cwnd':>13}{'MAE const':>11}{'MAE wsflight':>14}{'MAE maxflight':>15}")
        for k, g in sorted(groups.items(), key=lambda kv: str(kv[0])):
            print(f"{str(k):<16}{len(g):>7}{statistics.median(x['cwnd'] for x in g):>13.1f}"
                  f"{statistics.mean(x['err_const'] for x in g):>11.2f}"
                  f"{statistics.mean(x['err_wsflight'] for x in g):>14.2f}"
                  f"{statistics.mean(x['err_maxflight'] for x in g):>15.2f}")

    print(f"{len(runs)} runs, {len(rows)} transmitted packets. MAE = mean absolute error in packets.")
    table("All packets", lambda r: "all")
    table("By sender phase (hidden label)", lambda r: r["state"])
    table("By limiting window (hidden label)", lambda r: r["limiter"])
    table("By algorithm", lambda r: r["alg"])
    table("By RTT (ms)", lambda r: int(r["rtt_ms"]))
    table("By cross-traffic", lambda r: r["cross"])


if __name__ == "__main__":
    main()
