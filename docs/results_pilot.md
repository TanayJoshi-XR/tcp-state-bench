# Pilot result: estimating cwnd from a trace (30 runs, 37,487 packets)

**This is a pilot on a 30-run sample, not a result.** One bottleneck, loopback, our own
mini-TCP (Reno with NewReno recovery, and CUBIC), sender-side capture point only.

Estimators see only (send time, sequence number) and (ACK time, ack number).
Error = mean absolute error in packets against the true cwnd at each transmission.

| group | packets | median cwnd | const | Wireshark-style flight | peak flight over 1 RTT |
|---|---:|---:|---:|---:|---:|
| all | 37487 | 24.2 | 22.86 | 0.96 | 3.01 |
| avoidance | 32333 | 23.4 | 21.47 | 0.65 | 2.09 |
| recovery | 3277 | 45.7 | 40.91 | 1.78 | 8.65 |
| slow start | 1877 | 18.0 | 15.24 | 4.93 | 9.01 |
| limited by cwnd | 37344 | 24.1 | 22.44 | 0.90 | 2.99 |
| limited by receiver window | 132 | 154.0 | 131.60 | 20.50 | 10.38 |

## What this says

- **When the sender is limited by its own window, bytes in flight already gives cwnd to within
  about one packet.** A bulk sender refills its window on every ACK, so the number of unacked
  packets sits at the window. The plain cwnd task is therefore easy in that regime, and a
  benchmark made only of this would not discriminate between methods.
- **Where it is not easy:** slow start (error 4.9), loss recovery (1.8, the window is inflated
  by duplicate ACKs), bursty cross-traffic (1.6 vs 0.7), and receiver-limited periods (20.5).
  When the receiver's window is the limit, cwnd does not shape the traffic at all, so it cannot be
  recovered from the trace. Those rows should be marked unidentifiable, not scored as failures.
- **ssthresh, RTO and phase have no direct observer counterpart,** so they are the tasks that
  should carry the benchmark. Not yet attempted.
- The "peak flight over 1 RTT" estimator is worse than the simple one here. It lags after the
  window shrinks. It is kept as an honest second baseline.

## Limits

The sender only ever sends bulk data and is nearly always window-limited. Other capture points
(after the bottleneck, at the receiver) will be harder and are not tested. Thirty runs cannot
support claims about how this changes across the grid.

---

# Pilot result 2: classifying the sender's phase from a trace (same 30 runs)

True phase mix over 37,487 packets: slow start 5.0%, avoidance 86.3%, recovery 8.7%.
Because avoidance dominates, **macro-F1** (average of the three phases' F1) is the honest score;
plain accuracy rewards guessing the majority.

| classifier | accuracy | macro-F1 | slow start F1 | avoidance F1 | recovery F1 |
|---|---:|---:|---:|---:|---:|
| always "avoidance" | 0.863 | 0.309 | 0.000 | 0.926 | 0.000 |
| first loss rule | 0.881 | 0.783 | 0.434 | 0.927 | 0.988 |
| growth rule | 0.975 | 0.923 | 0.793 | 0.987 | 0.988 |

Recovery rule (both non-trivial classifiers): enter at the third duplicate ACK, leave when an ACK
passes the highest packet sent at that moment. Growth rule: outside recovery, "slow start" if at
least 1.5 new packets left per ACK over the last 6 ACKs.

## Where the growth rule is wrong

- 797 of its 832 "avoidance called slow start" errors are within 30 packets of the sender
  entering avoidance: the observer needs a few ACKs to notice the growth has slowed.
- All 79 "slow start called recovery" errors are packets resent after a timeout. The observer
  sees no third duplicate ACK, so its recovery state machine and the sender's timeout path
  disagree. A timeout detector would fix this.

## Read this with care

- These rules copy how this sender behaves (one packet per ACK, three duplicate ACKs, a known
  recovery rule). Near-perfect recovery recall shows the labels are consistent and the task is
  learnable on this sender. It does **not** show the method works on Linux TCP, which uses SACK,
  different recovery and pacing. A second sender is needed before any claim beyond this one.
- Single capture point (next to the sender), 30 runs, a hand-written rule, no learning.
