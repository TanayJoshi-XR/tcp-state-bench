# Plan (short version)

Full plan with diagrams: https://claude.ai/artifact/NgsT79KfYHmu716k3q59Ar

**Claim we want to be able to make:** a benchmark with exact per-packet labels of hidden sender
state, six inference tasks, three capture points (before the queue, after the bottleneck, at the
receiver), an opaque (encrypted-style) mode, and a small real-stack check (Linux TCP via
TCP_INFO, QUIC via qlog). Wording: "we found no equivalent", never "first".

**Not yet checked:** Kaur et al. (CCR, passive out-of-sequence state machine), IEEE 8361713
(ML TCP state monitoring), the IMC 2025 program, and whether the Abagnale and Nebby authors have
unreleased labelled traces (plan: email them before writing).

**Baseline to beat:** Wireshark already computes `tcp.analysis.bytes_in_flight` for real TCP.

**Next:** batches of the run grid; first baseline task (estimate cwnd from the trace, score against these labels).
