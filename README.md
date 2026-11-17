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
- `experiments/label_run.py`: one labelled transfer, joined table, and consistency checks.

## Quick start

    python -m tests.test_labels
    python -m experiments.label_run reno --loss 0.01 --seed 1 --out data/pilot

## Validation so far

- Logging does not change sender behaviour (same retransmit counts per seed, Reno and CUBIC).
- Every packet the sender sent has exactly one record at the link; receiver arrivals match.
- Against a tcpdump capture on loopback, all 293 data packets of one run matched by sequence
  number, with timestamps within 0.24 ms.

## Limits

One flow, loopback, one Python process. Runs are not bit-for-bit repeatable (timing varies).
"Limiter" means the smaller of the two windows, not proof the sender was blocked.

## Licence

Code: MIT. A dataset licence (planned CC BY 4.0) will be added with the first release.
