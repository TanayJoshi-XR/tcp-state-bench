# tcp-state-bench

**Status: pilot. Nothing here is a result yet.**

Goal: an open benchmark for recovering the *hidden state* of a transport-protocol sender
(congestion window, slow-start threshold, RTO, recovery phase, which window is limiting,
queue depth, loss versus reordering) from packet traces alone, with **exact per-packet
ground-truth labels** taken from the sender and the emulated network.

We searched for an existing dataset or tool with these labels and found none, but a search
is not proof. See `docs/plan.md` for the plan and what is still unchecked.

## What exists now

- A mini-TCP sender/receiver over UDP (`protocol/`) and a bottleneck-link emulator
  (`experiments/netsim.py`).
- A label recorder (`experiments/labels.py`): for every data packet it logs cwnd, ssthresh,
  state, packets in flight, RTO, the limiting window and why it was sent, plus the packet's
  fate at the bottleneck (queue depth seen, delivered / queue drop / random drop) and the
  receiver arrival time.
- `experiments/label_run.py`: labelled transfers with one or more flows and optional
  unresponsive cross-traffic (constant rate or on/off bursts); one joined table per flow and
  consistency checks.
- `experiments/validate_pcap.py`: compares a tcpdump capture with the link records.

## Quick start

    python -m tests.test_labels
    python -m experiments.label_run reno --loss 0.01 --seed 1 --out data/pilot
    python -m experiments.label_run cubic --flows 2 --stagger 0.2 \
        --cross-rate 1.5e6 --cross-on 0.4 --cross-off 0.4 --out data/pilot

## Validation so far

- Logging does not change sender behaviour (same retransmit counts per seed, Reno and CUBIC).
- Every packet the sender sent has exactly one record at the link; receiver arrivals match.
- Against tcpdump captures on loopback (single flow; and 2 flows plus bursty cross-traffic),
  every delivered data packet matched by sequence number (unmatched: 0), with timestamps within
  0.26 ms, and the cross-traffic packet count in the capture equalled the link records (264 = 264).
- Every queue drop (data, control or cross) has a row, so the rows add up to the link's own
  drop counter. This check found and fixed two gaps: dropped SYN/FIN packets were not recorded,
  and a few cross-traffic packets were marked delivered after the experiment had ended
  (now `lost_at_teardown`).

## Limits

Loopback, one Python process, one bottleneck. Cross-traffic is unresponsive (does not slow
down), so it is not a model of real competing flows. Control packets (SYN/ACK/FIN) are recorded
in a separate file and are not part of the per-packet sender labels. Runs are not bit-for-bit repeatable (timing varies).
"Limiter" means the smaller of the two windows, not proof the sender was blocked.

## Licence

Code: MIT. A dataset licence (planned CC BY 4.0) will be added with the first release.
