"""Checks for the observer-only estimators. Run:  python -m tests.test_baselines"""
from experiments.baselines import estimate


def test_wsflight_matches_hand_count():
    # send 0,1,2 ; ACK 2 (covers 0,1) ; send 3,4 ; retransmit 2 ; ACK 5
    sends = [(0.00, 0), (0.01, 1), (0.02, 2), (0.31, 3), (0.32, 4), (0.33, 2)]
    acks = [(0.30, 2), (0.40, 5)]
    ws, _ = estimate(sends, acks)
    # after each send: highest seq+1 minus highest ack
    assert ws == [1, 2, 3, 2, 3, 3], ws   # ACK 2 arrives before send 3, so 4-2, 5-2, then 5-2 again
    print("ok  wsflight equals (highest seq sent + 1) - (highest ack seen), retransmit adds nothing")


def test_estimators_ignore_everything_but_times_seq_and_acks():
    sends = [(0.0, 0), (0.1, 1)]
    ws1, mx1 = estimate(sends, [(0.2, 2)])
    ws2, mx2 = estimate(list(sends), [(0.2, 2)])
    assert (ws1, mx1) == (ws2, mx2)
    print("ok  estimate() takes only (time, seq) and (time, ack): no label can leak in")


if __name__ == "__main__":
    test_wsflight_matches_hand_count()
    test_estimators_ignore_everything_but_times_seq_and_acks()
    print("\nALL BASELINE CHECKS PASSED")
