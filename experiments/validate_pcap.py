"""Compare a tcpdump capture (loopback, taken during a labelled run) with the link records.

    tcpdump -i lo -U -w run.pcap udp &        # start BEFORE the run, stop after it
    python -m experiments.label_run cubic --flows 2 --out data/x
    python -m experiments.validate_pcap run.pcap data/x cubic_s1 --flows 2

For every flow, the k-th delivered copy of each sequence number in the link records must be
the k-th copy of that sequence number the capture saw. Reports unmatched packets and the
time offset (capture time minus the link's recorded hand-over time). Needs tshark.
"""
import argparse
import collections
import csv
import json
import os
import statistics
import subprocess


def read_pcap(path):
    out = subprocess.check_output(
        ["tshark", "-r", path, "-T", "fields", "-e", "frame.time_epoch", "-e", "udp.dstport",
         "-e", "udp.length", "-e", "data.data"], text=True, stderr=subprocess.DEVNULL)
    data, port_len = collections.defaultdict(list), collections.Counter()
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) < 4:
            continue
        port_len[(int(p[1]), int(p[2]))] += 1
        if not p[3]:
            continue
        b = bytes.fromhex(p[3].replace(":", ""))
        if b[0] == 0:                                   # data packet: flags=0
            data[int(p[1])].append((float(p[0]), int.from_bytes(b[1:5], "big")))
    return data, port_len


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pcap")
    ap.add_argument("outdir")
    ap.add_argument("prefix")
    ap.add_argument("--flows", type=int, default=1)
    a = ap.parse_args()
    data, _ = read_pcap(a.pcap)
    ok_all = True
    used = set()
    for i in range(a.flows):
        with open(os.path.join(a.outdir, f"{a.prefix}_f{i}_link.csv")) as f:
            link = list(csv.DictReader(f))
        dl = [r for r in link if r["fate"] == "delivered"]
        cands = [p for p, pk in data.items() if len(pk) == len(dl) and p not in used]
        if not cands:
            print(f"flow {i}: {len(dl)} delivered in link records; no capture port has that many data packets")
            ok_all = False
            continue
        port = cands[0]
        used.add(port)
        per_seq = collections.defaultdict(list)
        for t, sq in data[port]:
            per_seq[sq].append(t)
        k, off, bad = collections.defaultdict(int), [], 0
        for r in dl:
            s = int(r["seq"])
            j = k[s]
            k[s] += 1
            if j >= len(per_seq[s]):
                bad += 1
                continue
            off.append((per_seq[s][j] - float(r["t_deliver_actual"])) * 1000)
        print(f"flow {i} (capture port {port}): {len(dl)} delivered, unmatched {bad}, "
              f"offset ms min {min(off):.3f} median {statistics.median(off):.3f} max {max(off):.3f}")
        ok_all = ok_all and bad == 0
    cross_csv = os.path.join(a.outdir, f"{a.prefix}_cross_link.csv")
    if os.path.exists(cross_csv):
        with open(cross_csv) as f:
            n_cross = sum(1 for r in csv.DictReader(f) if r["fate"] == "delivered")
        _, port_len = read_pcap(a.pcap)
        # the cross-traffic sink is the only other port that receives full-size packets
        others = [(n, p) for (p, ln), n in port_len.items() if ln == 1049 and p not in used]
        n_cap = max(others)[0] if others else 0
        print(f"cross-traffic: {n_cross} delivered in link records, {n_cap} full-size packets "
              f"on the sink port in the capture")
        ok_all = ok_all and n_cap == n_cross
    print("PCAP VALIDATION", "OK" if ok_all else "FAILED")


if __name__ == "__main__":
    main()
