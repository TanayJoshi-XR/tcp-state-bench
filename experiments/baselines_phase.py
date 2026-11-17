"""Baseline 2: classify the sender's phase (slow_start / avoidance / recovery) from a trace.

Like baselines.py, the classifiers see only (send time, seq) and (ACK time, ack number). The
true phase (the sender's `state` at each transmission) is used only for scoring.

    python -m experiments.baselines_phase data/raw/test

Classifiers:
  majority   always 'avoidance' (the most common phase)
  firstloss  'recovery' from the third duplicate ACK until an ACK passes the highest packet
             sent at that moment; before the first loss signal 'slow_start', after it
             'avoidance'
  growth     same recovery rule; outside recovery 'slow_start' if the sender released at least
             1.5 new packets per ACK over the last 6 ACKs, else 'avoidance' (in slow start the
             window grows by one per ACK, so two packets leave per ACK; in avoidance about one)
"""
import argparse
import collections
import csv
import os

from experiments.baselines import observer_view

PHASES = ["slow_start", "avoidance", "recovery"]


def classify(sends, acks, ratio=1.5, k=6):
    """Returns {name: [phase per send]} using only observer information."""
    events = [(t, 1, i, s) for i, (t, s) in enumerate(sends)] + [(t, 0, -1, a) for t, a in acks]
    events.sort()
    hs = ma = dup = 0
    in_rec, recover, loss_seen = False, -1, False
    recent_acks = collections.deque(maxlen=k)             # times of the last k advancing ACKs
    new_sends = []                                        # times of first-time sends
    out = {"majority": [None] * len(sends), "firstloss": [None] * len(sends),
           "growth": [None] * len(sends)}
    for t, kind, i, v in events:
        if kind == 1:
            if v < hs:
                loss_seen = True                          # a seq below the highest sent: resend
            else:
                new_sends.append(t)
            hs = max(hs, v + 1)
            if in_rec:
                g = f = "recovery"
            else:
                f = "avoidance" if loss_seen else "slow_start"
                if len(recent_acks) < 3:
                    g = "slow_start"                       # too little history: connection start
                else:
                    lo = recent_acks[0]
                    n_new = sum(1 for x in reversed(new_sends) if x >= lo) if new_sends else 0
                    g = "slow_start" if n_new / len(recent_acks) >= ratio else "avoidance"
            out["majority"][i], out["firstloss"][i], out["growth"][i] = "avoidance", f, g
        else:
            if v > ma:                                    # new data acknowledged
                ma, dup = v, 0
                recent_acks.append(t)
                if in_rec and ma > recover:
                    in_rec = False
                    recent_acks.clear()
            elif v == ma and ma < hs:                     # duplicate ACK
                dup += 1
                if dup == 3 and not in_rec and ma > recover:
                    in_rec, recover, loss_seen = True, hs - 1, True
                    recent_acks.clear()
    return out


def prf(pairs, cls):
    tp = sum(1 for t, p in pairs if t == cls and p == cls)
    fp = sum(1 for t, p in pairs if t != cls and p == cls)
    fn = sum(1 for t, p in pairs if t == cls and p != cls)
    pr = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    return pr, rc, (2 * pr * rc / (pr + rc) if pr + rc else 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("batch_dir")
    a = ap.parse_args()
    runs = [m for m in csv.DictReader(open(os.path.join(a.batch_dir, "manifest.csv")))
            if m["status"] == "ok"]
    pairs = {k: [] for k in ("majority", "firstloss", "growth")}
    by_cross = {k: collections.defaultdict(list) for k in pairs}
    for m in runs:
        d = os.path.join(a.batch_dir, m["run_id"])
        truth = [r["state"] for r in csv.DictReader(
            open(os.path.join(d, f"{m['alg']}_s{m['seed']}_f0_tx.csv")))]
        sends, acks = observer_view(d, m["alg"], m["seed"])
        pred = classify(sends, acks)
        for k in pairs:
            for t, p in zip(truth, pred[k]):
                pairs[k].append((t, p))
                by_cross[k][m["cross"]].append((t, p))
    n = len(pairs["majority"])
    dist = collections.Counter(t for t, _ in pairs["majority"])
    print(f"{len(runs)} runs, {n} transmitted packets. True phase mix:",
          {c: f"{100 * dist[c] / n:.1f}%" for c in PHASES})
    for k, pr in pairs.items():
        acc = sum(1 for t, p in pr if t == p) / len(pr)
        f1s = {c: prf(pr, c) for c in PHASES}
        macro = sum(v[2] for v in f1s.values()) / 3
        print(f"\n== {k}: accuracy {acc:.3f}, macro-F1 {macro:.3f}")
        for c in PHASES:
            p_, r_, f_ = f1s[c]
            print(f"   {c:<11} precision {p_:.3f}  recall {r_:.3f}  F1 {f_:.3f}")
        cm = collections.Counter(pr)
        print("   confusion (rows = truth, columns = predicted):")
        print("   " + " " * 12 + "".join(f"{c:>12}" for c in PHASES))
        for t in PHASES:
            print("   " + f"{t:<12}" + "".join(f"{cm[(t, p)]:>12}" for p in PHASES))
        if k == "growth":
            for cr, g in sorted(by_cross[k].items()):
                accx = sum(1 for t, p in g if t == p) / len(g)
                mf = sum(prf(g, c)[2] for c in PHASES) / 3
                print(f"   cross={cr}: accuracy {accx:.3f}, macro-F1 {mf:.3f} ({len(g)} packets)")


if __name__ == "__main__":
    main()
