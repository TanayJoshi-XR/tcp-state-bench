"""Checks for the observer-only phase classifiers. Run:  python -m tests.test_baselines_phase"""
from experiments.baselines_phase import classify


def build_slow_then_avoid():
    sends, acks, t, nxt = [(0.0, 0), (0.0, 1)], [], 0.0, 2
    for ack in range(1, 9):                       # slow start: every ACK releases 2 packets
        t += 0.1
        acks.append((t, ack))
        for _ in range(2):
            sends.append((t + 0.001, nxt)); nxt += 1
    for ack in range(9, 20):                      # avoidance: every ACK releases 1 packet
        t += 0.1
        acks.append((t, ack))
        sends.append((t + 0.001, nxt)); nxt += 1
    return sends, acks


def test_growth_rule_sees_slow_start_then_avoidance():
    sends, acks = build_slow_then_avoid()
    g = classify(sends, acks)["growth"]
    assert g[0] == "slow_start" and g[10] == "slow_start", g[:12]
    assert g[-1] == "avoidance", g[-4:]
    print("ok  growth rule: 2 packets per ACK -> slow_start, then 1 per ACK -> avoidance")


def test_three_dup_acks_start_recovery_until_ack_passes_recover_point():
    sends = [(0.00, i) for i in range(10)] + [(0.30, 5), (0.35, 10), (0.50, 11)]
    acks = [(0.10, 5), (0.20, 5), (0.21, 5), (0.22, 5), (0.40, 11), (0.45, 11)]
    # 3 dup ACKs of 5 at 0.21/0.22 after the first at 0.20: dup count reaches 3 at 0.22
    out = classify(sends, acks)
    f = out["firstloss"]
    assert f[10] == "recovery" and f[11] == "recovery", f      # resend of 5, new 10: inside recovery
    assert f[12] == "avoidance", f                               # after ACK 11 > recover(=9)
    print("ok  3 duplicate ACKs -> recovery, ends when an ACK passes the highest packet sent then")


if __name__ == "__main__":
    test_growth_rule_sees_slow_start_then_avoidance()
    test_three_dup_acks_start_recovery_until_ack_passes_recover_point()
    print("\nALL PHASE-BASELINE CHECKS PASSED")
