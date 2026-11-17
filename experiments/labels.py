"""Ground-truth label recorder for the hidden-state benchmark pilot.

The sender (protocol.mini_tcp.send_file) calls these hooks when `trace=` is given.
The hooks ONLY append to lists, so they never change what the sender does.

Every row is stamped with time.time() (one clock for sender, emulator and receiver,
because the whole experiment is one Python process).

tx rows   one per DATA packet the sender hands to the network (new or retransmitted)
ack rows  one per ACK the sender processes
evt rows  loss-recovery events (timeout, fast_retransmit, partial_ack, recovery_exit)

Definitions (so the labels are unambiguous):
  nth        1 for the first transmission of a sequence number, 2 for the second, ...
  flight     packets sent but not yet cumulatively acked (nxt - base) BEFORE this send
  limiter    which window is the smaller one at this moment:
               'cwnd'  congestion window < receiver window
               'rwnd'  receiver window < congestion window
               'equal' both the same
               'fixed' no congestion control (cc=None), a fixed window is used
               'probe' the usable window is 0 and the sender sends 1 packet anyway
  reason     why this packet was sent: 'new', 'fast_retransmit', 'partial_ack',
             'timeout' (single resend, cc=None) or 'timeout_goback' (cc given: after a
             timeout the sender goes back and resends everything from the hole)
"""
import csv
import json
import os
import struct

TX_COLS = ["tx_id", "seq", "nth", "reason", "t", "cwnd", "ssthresh", "state",
           "flight", "base", "peer_window", "limiter", "rto", "srtt"]
ACK_COLS = ["t", "ack", "window", "kind", "dup", "base", "nxt",
            "cwnd", "ssthresh", "state", "rto"]
EVT_COLS = ["t", "kind", "cwnd", "ssthresh", "flight", "rto"]


class LabelTrace:
    def __init__(self):
        self.tx, self.acks, self.events = [], [], []
        self._nth = {}
        self._tx_id = 0

    def on_tx(self, t, seq, reason, cwnd, ssthresh, state, flight, base,
              peer_window, limiter, rto, srtt):
        n = self._nth.get(seq, 0) + 1
        self._nth[seq] = n
        self.tx.append((self._tx_id, seq, n, reason, t, cwnd, ssthresh, state,
                        flight, base, peer_window, limiter, rto, srtt))
        self._tx_id += 1

    def on_ack(self, t, ack, window, kind, dup, base, nxt, cwnd, ssthresh, state, rto):
        self.acks.append((t, ack, window, kind, dup, base, nxt, cwnd, ssthresh, state, rto))

    def on_event(self, t, kind, cwnd, ssthresh, flight, rto):
        self.events.append((t, kind, cwnd, ssthresh, flight, rto))

    def write(self, outdir, prefix):
        os.makedirs(outdir, exist_ok=True)
        for name, cols, rows in (("tx", TX_COLS, self.tx), ("ack", ACK_COLS, self.acks),
                                 ("evt", EVT_COLS, self.events)):
            with open(os.path.join(outdir, f"{prefix}_{name}.csv"), "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(cols)
                w.writerows(rows)


LINK_COLS = ["seq", "nth", "t_enter", "queue_seen", "fate", "t_depart_model",
             "t_deliver_model", "t_deliver_actual"]
RX_COLS = ["t_rx", "seq", "k"]


class PacketTap:
    """Records the fate of every DATA packet at the bottleneck link (called by Link).

    queue_seen = number of packets in the system (queued or being sent) when this
    packet arrived, BEFORE it was added.  fate is 'delivered', 'drop_queue' (buffer
    full) or 'drop_random' (the extra random-loss model); 'lost_at_teardown' marks a packet the
    link could not hand to the OS because the experiment had already ended.
    t_depart_model   = when the last bit leaves the bottleneck (model time)
    t_deliver_model  = t_depart + propagation delay (when the link should deliver it)
    t_deliver_actual = when the link thread really handed it to the OS
    """

    def __init__(self):
        self.rows = {}            # addr -> list of row lists
        self._nth = {}

    def record(self, now, data, addr, qlen, fate, t_depart, t_deliver):
        if len(data) < 5:
            return None
        flags, seq = struct.unpack_from("!BI", data)
        if flags == 0x80:         # background cross-traffic packet (see netsim.CrossTraffic)
            addr, n = "cross", 1
        elif flags != 0:          # SYN / ACK / FIN: control packet; the row stores the flags byte
            addr, seq = "control", flags
            n = self._nth.get(("control", None), 0) + 1
            self._nth[("control", None)] = n
        else:
            n = self._nth.get((addr, seq), 0) + 1
            self._nth[(addr, seq)] = n
        row = [seq, n, now, qlen, fate, t_depart, t_deliver, None]
        self.rows.setdefault(addr, []).append(row)
        return row

    def delivered(self, row, t):
        row[7] = t

    def undelivered(self, row):
        row[4] = "lost_at_teardown"     # the link tried to deliver it after its socket was closed

    def write(self, addr, outdir, prefix):
        with open(os.path.join(outdir, f"{prefix}_link.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(LINK_COLS)
            w.writerows(self.rows.get(addr, []))


def write_rx(rx_log, outdir, prefix):
    """rx_log: list of (t, seq) for every data packet the receiver pulled off its socket."""
    k = {}
    with open(os.path.join(outdir, f"{prefix}_rx.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(RX_COLS)
        for t, seq in rx_log:
            k[seq] = k.get(seq, 0) + 1
            w.writerow([t, seq, k[seq]])
