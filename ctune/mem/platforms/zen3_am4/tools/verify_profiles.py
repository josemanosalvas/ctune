#!/usr/bin/env python3
"""Independent check of the Gigabyte ProfileN efivars (no public spec; the layout is re-derived from the bytes).
Prints header fields, record table, and diffs of the Setup, AmdSetup and AOD_SETUP records against the live variables at
memory-related offsets (the offsets and widths come from the verify_hii_settings question table JSON)."""
import sys, os, struct, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_hii_settings as v
G = "b05e6b5f-6ed8-4015-b5c5-b1049faf3e5b"
live = {"Setup": v.efivar("Setup", "ec87d643-eba4-4bb5-a1e5-3f3e36b20da9"),
        "AmdSetup": v.efivar("AmdSetup", "3a997502-647a-4c82-998e-52ef9486a247"),
        "AOD_SETUP": v.efivar("AOD_SETUP", "5ed15dc0-edef-4161-9151-6014c4cc630c"),
        "GcSensorVarName": v.efivar("GcSensorVarName", "5489a3e4-c745-11ea-87d0-0242ac130003")}
qs = json.load(open(sys.argv[1]))
loc = {}
for q in qs:
    st = q["store"]
    if st and st[0] in live and q["op"] in ("ONE_OF", "NUMERIC", "CHECKBOX"):
        loc.setdefault((st[0], q["voff"], q["width"]), q)
for n in range(9):
    raw = open(os.path.join(v.EFIV, "Profile%d-%s" % (n, G)), "rb").read()[4:]
    h = struct.unpack_from("<HHHBBI", raw, 0)
    tag = raw[12:24]
    name = raw[24:56].decode("utf-16-le", "replace").split("\0")[0]
    recs = []
    p = 0x38
    while p + 6 <= len(raw):
        idx, ln = struct.unpack_from("<IH", raw, p)
        recs.append((idx, raw[p + 6:p + 6 + ln]))
        p += 6 + ln
    print("Profile%d len=%d hdr magic=%#x plen=%d (len-0x38=%d) ver=%#x flag=%d csum=%#x sum8=%#x count=%d nrec=%d end_ok=%s tag=%r name=%r" % (
        n, len(raw), h[0], h[1], len(raw) - 0x38, h[2], h[3], h[4], sum(raw[0x38:]) & 0xFF, h[5], len(recs), p == len(raw), tag, name))
    bysize = {646: "Setup", 1553: "AmdSetup", 1020: "AOD_SETUP", 569: "GcSensorVarName"}
    print("   records:", [(i, len(b)) for i, b in recs])
    for i, b in recs:
        nm = bysize.get(len(b))
        if not nm:
            continue
        cur = live[nm]
        diffs = []
        for (s, o, w), q in sorted(loc.items()):
            if s != nm:
                continue
            a = int.from_bytes(cur[o:o + w], "little"); c = int.from_bytes(b[o:o + w], "little")
            if a != c:
                opt = {x[0]: x[1] for x in q["options"]}
                diffs.append("%s@%d %s: now %s%s -> prof %s%s" % (nm, o, q["prompt"].split("\x7f")[0].strip()[:28], a,
                             "(%s)" % opt[a] if a in opt else "", c, "(%s)" % opt[c] if c in opt else ""))
        nbytes = sum(1 for x, y in zip(cur, b) if x != y)
        print("   rec %d -> %s: %s, %d bytes differ" % (i, nm, "IDENTICAL" if b == cur else "differs", nbytes))
        for d in diffs:
            print("      ", d)
