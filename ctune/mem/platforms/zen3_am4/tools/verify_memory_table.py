#!/usr/bin/env python3
"""Independent extraction of the memory-related setup values (it uses neither hii_ifr.py nor bios_settings_decode.py).

For each item: find the ONE_OF/NUMERIC/CHECKBOX question by (formset title, prompt regex[, form title regex]) in
hiidb.bin, take VarStoreId/VarOffset/width straight from the opcode bytes, map VarStoreId to the VARSTORE(_EFI)
name and GUID of the same formset, read the bytes from the efivar copy, and resolve the option text.
Writes <snapshot>/decoded/verify_independent/{questions_all.json, memory_settings.json, memory_settings.tsv}.
Run: python3 -I verify_memory_table.py
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_hii_settings as v  # noqa: E402

OUT = os.path.join(v.SNAP, "decoded", "verify_independent")

ITEMS = [
    # label, formset, prompt regex, form regex (optional)
    ("XMP", "Setup", r"^Extreme Memory Profile", r"Advanced Memory Settings"),
    ("System Memory Multiplier (x100 MT/s)", "Setup", r"^System Memory Multiplier", r"Advanced Memory Settings"),
    ("Memory Timing Mode", "Setup", r"^Memory Timing Mode", r"Channel A"),
    ("CBS Overclock (UMC overrides)", "AMD CBS", r"^Overclock$", None),
    ("CBS Memory Clock Speed", "AMD CBS", r"^Memory Clock Speed", None),
    ("CBS FCLK Frequency", "AMD CBS", r"^FCLK Frequency", None),
    ("CBS UCLK DIV1 MODE", "AMD CBS", r"^UCLK DIV1", None),
    ("AOD Infinity Fabric Frequency and Dividers", "AMD Overclocking", r"^Infinity Fabric Frequency", None),
    ("tCL", "Setup", r"^CAS Latency", r"Advanced Memory Settings"),
    ("tRCD (combined)", "Setup", r"^tRCD$", r"Advanced Memory Settings"),
    ("tRCDRD", "Setup", r"^tRCDRD", r"Advanced Memory Settings"),
    ("tRCDWR", "Setup", r"^tRCDWR", r"Advanced Memory Settings"),
    ("tRP", "Setup", r"^tRP$", r"Advanced Memory Settings"),
    ("tRAS", "Setup", r"^tRAS$", r"Advanced Memory Settings"),
    ("tRC", "Setup", r"^tRC$", r"Advanced Memory Settings"),
    ("tRFC", "Setup", r"^tRFC$", r"Advanced Memory Settings"),
    ("tRFC2", "Setup", r"^tRFC2$", r"Advanced Memory Settings"),
    ("tRFC4", "Setup", r"^tRFC4$", r"Advanced Memory Settings"),
    ("tWR", "Setup", r"^tWR$", r"Advanced Memory Settings"),
    ("tRTP", "Setup", r"^tRTP$", r"Advanced Memory Settings"),
    ("tCWL", "Setup", r"^tCWL$", r"Advanced Memory Settings"),
    ("tRRD_S", "Setup", r"^tRRD_S", r"Advanced Memory Settings"),
    ("tRRD_L", "Setup", r"^tRRD_L", r"Advanced Memory Settings"),
    ("tFAW", "Setup", r"^tFAW", r"Advanced Memory Settings"),
    ("tWTR_S", "Setup", r"^tWTR_S", r"Advanced Memory Settings"),
    ("tWTR_L", "Setup", r"^tWTR_L", r"Advanced Memory Settings"),
    ("tRDWR", "Setup", r"^tRDWR", r"Advanced Memory Settings"),
    ("tWRRD", "Setup", r"^tWRRD", r"Advanced Memory Settings"),
    ("tRDRDSC", "Setup", r"^tRDRDSC", r"Advanced Memory Settings"),
    ("tRDRDSD", "Setup", r"^tRDRDSD", r"Advanced Memory Settings"),
    ("tRDRDDD", "Setup", r"^tRDRDDD", r"Advanced Memory Settings"),
    ("tRDRD_SCL", "Setup", r"^tRDRD_SCL", r"Advanced Memory Settings"),
    ("tWRWRSC", "Setup", r"^tWRWRSC", r"Advanced Memory Settings"),
    ("tWRWRSD", "Setup", r"^tWRWRSD", r"Advanced Memory Settings"),
    ("tWRWRDD", "Setup", r"^tWRWRDD", r"Advanced Memory Settings"),
    ("tWRWR_SCL", "Setup", r"^tWRWR_SCL", r"Advanced Memory Settings"),
    ("tCKE", "Setup", r"^tCKE", r"Advanced Memory Settings"),
    ("tRCPAGE", "Setup", r"^tRCPAGE", r"Advanced Memory Settings"),
    ("Command Rate", "Setup", r"^Command Rate", r"Advanced Memory Settings"),
    ("Gear Down Mode (Tweaker)", "Setup", r"^Gear Down", r"Advanced Memory Settings"),
    ("Gear Down Mode (CBS)", "AMD CBS", r"^Gear Down", None),
    ("Power Down Enable (CBS)", "AMD CBS", r"^Power Down Enable", None),
    ("Power Down Enable (AOD)", "AMD Overclocking", r"^Power Down Enable", r"DRAM Power"),
    ("ProcODT (Tweaker)", "Setup", r"^ProcODT", r"Advanced Memory Settings"),
    ("ProcODT (CBS)", "AMD CBS", r"^ProcODT", None),
    ("RttNom (Tweaker)", "Setup", r"^RttNom", r"Advanced Memory Settings"),
    ("RttWr (Tweaker)", "Setup", r"^RttWr", r"Advanced Memory Settings"),
    ("RttPark (Tweaker)", "Setup", r"^RttPark", r"Advanced Memory Settings"),
    ("CBS Data Bus User Controls", "AMD CBS", r"^Data Bus Configuration User", None),
    ("AddrCmdSetup (Tweaker)", "Setup", r"^AddrCmdSetup", r"Advanced Memory Settings"),
    ("CsOdtSetup (Tweaker)", "Setup", r"^CsOdtSetup", r"Advanced Memory Settings"),
    ("CkeSetup (Tweaker)", "Setup", r"^CkeSetup", r"Advanced Memory Settings"),
    ("ClkDrvStren (Tweaker)", "Setup", r"^ClkDrvStren", r"Advanced Memory Settings"),
    ("AddrCmdDrvStren (Tweaker)", "Setup", r"^AddrCmdDrvStren", r"Advanced Memory Settings"),
    ("CsOdtDrvStren (Tweaker)", "Setup", r"^CsOdtDrvStren", r"Advanced Memory Settings"),
    ("CkeDrvStren (Tweaker)", "Setup", r"^CkeDrvStren", r"Advanced Memory Settings"),
    ("CBS CAD Bus Timing User Controls", "AMD CBS", r"^CAD Bus Timing User", None),
    ("CBS CAD Bus Drive Strength User Controls", "AMD CBS", r"^CAD Bus Drive Strength User", None),
    ("CBS BankGroupSwap", "AMD CBS", r"^BankGroupSwap$", None),
    ("CBS BankGroupSwapAlt", "AMD CBS", r"^BankGroupSwapAlt", None),
    ("DRAM Voltage (code)", "Setup", r"^DRAM Voltage +\(CH A/B\)", None),
    ("DRAM Termination (code)", "Setup", r"^DRAM Termination +\(CH A/B\)", None),
    ("DDRVPP (code)", "Setup", r"^DDRVPP Voltage +\(CH A/B\)", None),
    ("VCORE SOC (code)", "Setup", r"^VCORE SOC$", None),
    ("Dynamic VCORE SOC DVID (code)", "Setup", r"^Dynamic VCORE SOC", None),
    ("Vcore SOC LLC (code)", "Setup", r"^Vcore SOC Loadline", None),
    ("CPU VDDP (Tweaker code)", "Setup", r"^CPU VDDP$", r"Voltage"),
    ("AOD SoC/Uncore OC Mode", "AMD Overclocking", r"^SoC/Uncore OC Mode", None),
    ("AOD SoC Voltage [mV]", "AMD Overclocking", r"^SoC Voltage", None),
    ("CBS SOC OVERCLOCK VID", "AMD CBS", r"^SOC OVERCLOCK VID", None),
    ("AOD VDDP Voltage Control (mode)", "AMD Overclocking", r"^VDDP Voltage Control", None),
    ("AOD VDDG Voltage Control (mode)", "AMD Overclocking", r"^VDDG Voltage Control", None),
    ("AOD VDDG CCD (per-type) [mV]", "AMD Overclocking", r"^VDDG CCD Voltage", None),
    ("AOD VDDG IOD (per-type) [mV]", "AMD Overclocking", r"^VDDG IOD Voltage", None),
    ("AOD Global VDDG CCD [mV]", "AMD Overclocking", r"^Global VDDG CCD", None),
    ("AOD Global VDDG IOD [mV]", "AMD Overclocking", r"^Global VDDG IOD", None),
    ("AOD hidden flag @576 (selects 3-way VDDG menu)", "AMD Overclocking", r"^<sid 0>$", None),
    ("CBS DF Cstates", "AMD CBS", r"^DF Cstates", None),
    ("CBS APBDIS", "AMD CBS", r"^APBDIS", None),
    ("CBS Fixed SOC Pstate", "AMD CBS", r"^Fixed SOC Pstate", None),
    ("AOD Curve Optimizer mode", "AMD Overclocking", r"^Curve Optimizer$", None),
] + [("CO core %d sign" % i, "AMD Overclocking", r"^Core %d Curve Optimizer Sign" % i, None) for i in range(8)] \
  + [("CO core %d magnitude" % i, "AMD Overclocking", r"^Core %d Curve Optimizer Magnitude" % i, None) for i in range(8)]


def main():
    lists, allq, stores = v.main()
    os.makedirs(OUT, exist_ok=True)
    clean = [{k: val for k, val in q.items() if not k.startswith("_")} for q in allq]
    json.dump(clean, open(os.path.join(OUT, "questions_all.json"), "w"), indent=0, default=str)
    cache = {}
    rows = []
    for label, fs, prx, frx in ITEMS:
        cands = [q for q in allq if q["formset"] == fs and re.search(prx, (q["prompt"] or "").split("\x7f")[0].strip())
                 and (frx is None or re.search(frx, q["form"] or ""))]
        if label.startswith("AOD hidden flag"):
            cands = [q for q in cands if q["voff"] == 576]
        locs = sorted({(q["store"][0] if q["store"] else None, q["voff"], q["width"]) for q in cands})
        for (sname, off, w) in locs:
            q = [c for c in cands if c["voff"] == off and c["width"] == w][0]
            st = q["store"]
            k = (st[0], st[1])
            if k not in cache:
                cache[k] = v.efivar(*k)
            d = cache[k]
            raw = d[off:off + w]
            val = int.from_bytes(raw, "little")
            texts = sorted({o[1] for c in cands if c["voff"] == off for o in c["options"] if o[0] == val})
            dflt = sorted({o[0] for c in cands if c["voff"] == off for o in c["options"] if o[2] & 0x10}
                          | {dd[1] for c in cands if c["voff"] == off for dd in c["defaults"] if dd[0] == 0})
            rows.append(dict(item=label, formset=fs, prompt=q["prompt"].split("\x7f")[0].strip(), form=q["form"],
                             ifr_offsets=[hex(c["off"]) for c in cands if c["voff"] == off],
                             qids=[c["qid"] for c in cands if c["voff"] == off], varstore_id=q["vsid"],
                             varstore=st[0], guid=st[1], var_offset=off, width=w, raw_hex=raw.hex(), value=val,
                             option_text=" / ".join(texts), ifr_default=dflt))
    json.dump(rows, open(os.path.join(OUT, "memory_settings.json"), "w"), indent=1)
    with open(os.path.join(OUT, "memory_settings.tsv"), "w") as f:
        f.write("item\tvarstore@off/w\traw\tvalue\toption_text\tifr_default\tqids\n")
        for r in rows:
            f.write("%s\t%s@%d/%d\t%s\t%d\t%s\t%s\t%s\n" % (r["item"], r["varstore"], r["var_offset"], r["width"],
                    r["raw_hex"], r["value"], r["option_text"], r["ifr_default"], r["qids"]))
    for r in rows:
        print("%-44s %-10s@%-4d/%d raw=%-8s %-6d %-30s dflt=%s" % (r["item"][:44], r["varstore"], r["var_offset"],
              r["width"], r["raw_hex"], r["value"], r["option_text"][:30], r["ifr_default"]))


if __name__ == "__main__":
    main()
