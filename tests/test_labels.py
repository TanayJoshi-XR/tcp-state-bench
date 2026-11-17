"""Checks for the ground-truth label recorder. Run:  python -m tests.test_labels"""
import csv
import os
import tempfile

from experiments import netsim
from experiments.label_run import run
from protocol.congestion import Reno


def test_labelled_run_is_consistent():
    with tempfile.TemporaryDirectory() as d:
        m = run("reno", 200_000, 4e6, 0.02, 30, 0.02, 3, d, prefix="t")
        c = m["checks"]
        assert c["tx_link_exact_match"], c
        assert c["delivered_without_rx"] == 0, c
        assert c["sends_violating_window_label"] == 0, c
        assert c["drop_queue"] == c["link_stats_dropped_queue"], c
        assert c["drop_random"] == c["link_stats_dropped_random"], c
        assert c["queue_seen_within_buffer"], c
        rows = list(csv.DictReader(open(os.path.join(d, "t_joined.csv"))))
        firsts = [r for r in rows if r["nth"] == "1"]
        assert sorted(int(r["seq"]) for r in firsts) == list(range(m["sender_stats"]["packets"]))
    print("ok  labelled Reno run: every packet accounted for, labels consistent with the window rule")


def test_fixed_window_run_is_labelled():
    r = netsim.run_flows(1, 100_000, 4e6, 0.02, 30, make_cc=None, label=True, seed=5,
                         send_kw={"window": 8})
    tx = r["label"]["flows"][0]["trace"].tx
    assert tx and all(row[7] == "fixed" and row[5] == 8.0 for row in tx)
    assert all(row[8] < 8 for row in tx if row[3] == "new")      # flight < window before a send
    print("ok  fixed-window (no congestion control) run is labelled: state 'fixed', flight < window")


def test_labels_do_not_change_behaviour_counts():
    a = netsim.run_flows(1, 200_000, 4e6, 0.02, 30, make_cc=Reno, seed=9)["flows"][0]
    b = netsim.run_flows(1, 200_000, 4e6, 0.02, 30, make_cc=Reno, seed=9, label=True)["flows"][0]
    for k in ("fast_retransmits", "partial_retransmits", "timeouts", "sent_total"):
        assert a[k] == b[k], (k, a[k], b[k])
    print("ok  logging on/off gives the same retransmit counts for the same seed")


if __name__ == "__main__":
    test_labelled_run_is_consistent()
    test_fixed_window_run_is_labelled()
    test_labels_do_not_change_behaviour_counts()
    print("\nALL LABEL CHECKS PASSED")
