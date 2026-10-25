#!/usr/bin/env python3
"""Dump raw IFR ops of the form package that contains file offset OFF, in window [OFF-before, OFF+after].
usage: python3 -I verify_dumpops.py OFF before after"""
import sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_hii_settings as v
data = open(v.HII, "rb").read()
off = int(sys.argv[1], 0); before = int(sys.argv[2]); after = int(sys.argv[3])
for pl in v.walk_db(data):
    for foff, pkg in pl["forms"]:
        if foff <= off < foff + len(pkg):
            ops = v.parse_forms(pkg, pl["strings"], foff)
            idx = [i for i, o in enumerate(ops) if o[0] == off][0]
            for o in ops[max(0, idx - before): idx + after]:
                o_off, op, sc, depth, raw = o
                extra = ""
                if op in (0x05, 0x06, 0x07, 0x01, 0x03, 0x02, 0x0F, 0x1C, 0x23):
                    extra = repr(pl["strings"].get(struct.unpack_from("<H", raw, 2)[0], ""))[:40]
                    if op in (0x05, 0x06, 0x07, 0x0F, 0x1C, 0x23):
                        extra += " qid=%d vs=%d off=%d" % struct.unpack_from("<HHH", raw, 6)
                if op == 0x12:
                    extra = "qid=%d == %d" % struct.unpack_from("<HH", raw, 2)
                if op == 0x14:
                    q, n = struct.unpack_from("<HH", raw, 2)
                    extra = "qid=%d in %s" % (q, list(struct.unpack_from("<%dH" % n, raw, 6)))
                if op == 0x09:
                    extra = "opt %r val=%d fl=%x" % (pl["strings"].get(struct.unpack_from("<H", raw, 2)[0]), int.from_bytes(raw[6:], "little"), raw[4])
                print("0x%07x d%-2d %s%-14s sc=%d %-40s %s" % (o_off, depth, "  " * depth, v.OPN.get(op, "0x%02x" % op), sc, raw.hex()[:40], extra))
