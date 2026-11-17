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
        c, g = m["flows"][0]["checks"], m["global_checks"]
        assert c["tx_link_exact_match"], c
        assert c["delivered_without_rx"] == 0, c
        assert c["sends_violating_window_label"] == 0, c
        assert g["queue_drops_account_for_link_stats"], g
        assert g["link_stats_dropped_random"] == c["drop_random"], (g, c)
        assert c["queue_seen_within_buffer"], c
        rows = list(csv.DictReader(open(os.path.join(d, "t_f0_joined.csv"))))
        firsts = [r for r in rows if r["nth"] == "1"]
        assert sorted(int(r["seq"]) for r in firsts) == list(range(m["flows"][0]["sender_stats"]["packets"]))
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


def test_two_flows_with_cross_traffic_reconcile():
    with tempfile.TemporaryDirectory() as d:
        cross = {"rate_bps": 1.5e6, "on_s": 0.3, "off_s": 0.3, "seed": 4}
        m = run("cubic", 150_000, 4e6, 0.02, 30, 0.005, 4, d, prefix="m", n_flows=2,
                stagger=0.2, cross=cross)
        g = m["global_checks"]
        assert g["queue_drops_account_for_link_stats"], g      # every queue drop has a row
        assert g["cross_every_packet_recorded"] and g["cross_packets_sent"] > 0, g
        for f in m["flows"]:
            c = f["checks"]
            assert c["tx_link_exact_match"] and c["delivered_without_rx"] == 0, c
            assert c["sends_violating_window_label"] == 0, c
    print("ok  2 flows + bursty cross-traffic: every packet (data, control, cross) has a fate row")


if __name__ == "__main__":
    test_labelled_run_is_consistent()
    test_fixed_window_run_is_labelled()
    test_labels_do_not_change_behaviour_counts()
    test_two_flows_with_cross_traffic_reconcile()
    print("\nALL LABEL CHECKS PASSED")
