#!/usr/bin/env python3
"""Lookup helper: python3 -I verify_lookup.py questions.json REGEX [formset-substr]
Prints each matching ONE_OF/NUMERIC/CHECKBOX with the raw value read from the efivar file."""
import json, re, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_hii_settings import efivar
q = json.load(open(sys.argv[1]))
rx = re.compile(sys.argv[2], re.I)
fsf = sys.argv[3] if len(sys.argv) > 3 else ""
cache = {}
for x in q:
    if not rx.search(x["prompt"] or "") or fsf not in (x["formset"] or ""):
        continue
    st = x["store"]
    val = None
    if st:
        k = (st[0], st[1])
        if k not in cache:
            cache[k] = efivar(st[0], st[1])
        d = cache[k]
        if d is not None and x["voff"] + x["width"] <= len(d):
            val = int.from_bytes(d[x["voff"]:x["voff"] + x["width"]], "little")
    opt = {o[0]: o[1] for o in x["options"]}
    dflt = [o[0] for o in x["options"] if o[2] & 0x10]
    print("%-6s %-42s %-14s @%-4d w%d qid=%-5d val=%-6s %-28s dflt=%s mms=%s form=%s" % (
        x["op"], x["prompt"][:42], st[0] if st else x["vsid"], x["voff"], x["width"], x["qid"],
        val, repr(opt.get(val, ""))[:28], dflt or x["defaults"], x["minmaxstep"], (x["form"] or "")[:30]))
