#!/usr/bin/env python3
"""Compare two ram_snapshot.py snapshots.  Usage: python3 -I ram_compare.py OLD_DIR NEW_DIR  (no root needed)"""
import difflib, json, os, sys

TOL = {"_V": 0.006, "_MHz": 2.0, "_W": 1.0, "_A": 1.0, "_C": 1.0, "_GHz": 0.02}
TELEMETRY = {"FCLK_eff_MHz", "VSOC_V", "SOC_current_A", "SOC_power_W", "VDDIO_MEM_power_W", "SOC_temp_C",
             "FIT_limit", "CCLK_limit_GHz"}
SKIP_UMC = {"missing", "smn_base", "tRFC_reg_dimm0", "tRFC_reg_dimm1"}


def load(d):
    with open(os.path.join(d, "snapshot.json")) as f:
        return json.load(f)


def vtt(s):
    """IT8792E in1 'DDRVTT AB' (VDIMM/2 while BIOS DRAM Termination = Auto); older snapshots only have sensors -j."""
    if s.get("vtt_proxy"):
        return s["vtt_proxy"]["in1_V"]
    try:
        return next(c["in1"]["in1_input"] for k, c in json.loads(s["hwmon_sensors"]).items() if k.startswith("it8792"))
    except Exception:
        return None


def bios_settings(d):
    """{location: 'value (option text) [AUTO/MANUAL]'} for visible questions, from bios_settings_decode.py output."""
    p = os.path.join(d, "decoded", "bios_settings.json")
    if not os.path.exists(p):
        return None
    out = {}
    with open(p) as f:
        settings = json.load(f)["settings"]
    for e in settings:
        if e.get("visible_now") is False:
            continue
        v = e.get("value_text") if e.get("value_text") is not None else e.get("value")
        for loc in e.get("locations") or []:
            out[loc] = f"{v} [{e.get('auto_state')}]"
    return out


def tol(k):
    return next((t for suf, t in TOL.items() if k.endswith(suf)), 0.0)


def main(old_dir, new_dir):
    a, b = load(old_dir), load(new_dir)
    L = [f"# RAM snapshot diff", "", f"- OLD: {a['meta']['captured']} BIOS {a['meta']['bios_version']} - {old_dir}",
         f"- NEW: {b['meta']['captured']} BIOS {b['meta']['bios_version']} - {new_dir}", ""]

    L += ["## Platform", "", "| Item | Old | New |", "|---|---|---|"]
    for k in ("bios_version", "bios_date", "smu_fw", "microcode", "kernel", "ryzen_smu_drv"):
        L.append(f"| {k} | {a['meta'].get(k)} | {b['meta'].get(k)} |" + (" " if a['meta'].get(k) == b['meta'].get(k) else " **changed**"))
    L.append(f"| pm_table version | {a['pm_table']['version']} | {b['pm_table']['version']} |")

    L += ["", "## Memory timings / modes (decoded)", ""]
    rows = []
    for ch, (ua, ub) in enumerate(zip(a["umc"]["decoded"]["umcs"], b["umc"]["decoded"]["umcs"])):
        for k in sorted(set(ua) | set(ub)):
            if k in SKIP_UMC:
                continue
            va, vb = ua.get(k), ub.get(k)
            if isinstance(va, float) and isinstance(vb, float):
                same = abs(va - vb) < 0.05
            else:
                same = va == vb
            if not same:
                rows.append(f"| {'AB'[ch]} | {k} | {va} | {vb} |")
    L += (["| Ch | Field | Old | New |", "|---|---|---|---|"] + rows) if rows else ["No decoded timing differences."]

    ra, rb = a["umc"]["raw"], b["umc"]["raw"]
    raw = [f"| {k} | {ra.get(k)} | {rb.get(k)} |" for k in sorted(set(ra) | set(rb)) if ra.get(k) != rb.get(k)]
    L += ["", "## Raw UMC/SMN registers that differ", ""]
    L += (["| SMN | Old | New |", "|---|---|---|"] + raw) if raw else ["None."]

    L += ["", "## Clocks, voltages, limits (pm_table medians)", "", "| Field | Old | New | |", "|---|---|---|---|"]
    fa, fb = a["pm_table"]["fields"], b["pm_table"]["fields"]
    for k in sorted(set(fa) | set(fb)):
        va, vb = (fa.get(k) or {}).get("median"), (fb.get(k) or {}).get("median")
        if va is None or vb is None:
            flag = "missing"
        else:
            flag = "" if abs(va - vb) <= tol(k) else ("changed (telemetry)" if k in TELEMETRY else "**changed**")
        L.append(f"| {k} | {va} | {vb} | {flag} |")

    va, vb = vtt(a), vtt(b)
    L += ["", "## DRAM voltage proxy (IT8792E in1 = DDR VTT)", ""]
    if va is None or vb is None:
        L.append(f"VTT old {va} / new {vb} (missing)")
    else:
        steps = round((vb - va) / 0.0109)
        L.append(f"VTT old {va:.3f} V ({round(va / 0.0109)} counts, VDIMM ~{va * 2.02:.2f} V) / new {vb:.3f} V "
                 f"({round(vb / 0.0109)} counts, VDIMM ~{vb * 2.02:.2f} V)"
                 + (f" - **changed by {steps:+d} ADC steps (~{steps * 22:+d} mV VDIMM)**" if abs(steps) >= 2 else " - same"))
        L.append("(Only meaningful while BIOS 'DRAM Termination (CH A/B)' is Auto; confirm in BIOS PC Health.)")

    L += ["", "## Platform fingerprint (non-memory BIOS settings)", ""]
    fa, fb = a.get("fingerprint") or {}, b.get("fingerprint") or {}
    fd = [f"| {k} | {fa.get(k)} | {fb.get(k)} |" for k in sorted(set(fa) | set(fb)) if not k.startswith("_") and fa.get(k) != fb.get(k)]
    L += (["| Item | Old | New |", "|---|---|---|"] + fd) if fd else ["No differences."]

    L += ["", "## DIMMs (DMI)", ""]
    da, db = a["dmi"]["dimms"], b["dmi"]["dimms"]
    dd = []
    for x, y in zip(da, db):
        for k in x:
            if x.get(k) != y.get(k):
                dd.append(f"| {x.get('Bank Locator')} {x.get('Locator')} | {k} | {x.get(k)} | {y.get(k)} |")
    L += (["| DIMM | Field | Old | New |", "|---|---|---|---|"] + dd) if dd else ["No differences."]

    L += ["", "## BIOS setup settings (decoded EFI variables, matched by menu path + prompt)", ""]
    ba, bb = bios_settings(old_dir), bios_settings(new_dir)
    if not ba or not bb:
        L.append("Not available for both snapshots (needs decoded/bios_settings.json; run scripts/bios_settings_decode.py).")
    else:
        rows = []
        for k in sorted(set(ba) | set(bb)):
            x, y = ba.get(k), bb.get(k)
            if x == y:
                continue
            if x is None or y is None:
                rows.append(f"| {k} | {x if x else '(not in old BIOS)'} | {y if y else '(not in new BIOS)'} |")
            else:
                rows.append(f"| {k} | {x} | {y} |")
        L.append(f"{len(rows)} differences. Settings the user re-enters should match; new/removed entries are menu changes.")
        L += ["", "| Setting | Old | New |", "|---|---|---|"] + rows if rows else []

    L += ["", "## BIOS-side parameters (AOD/APOB) diff", "", "```"]
    diff = list(difflib.unified_diff(a["aod_apob"].splitlines(), b["aod_apob"].splitlines(), "old", "new", lineterm=""))
    L += diff or ["(identical)"]
    L += ["```", "", f"BCLK: old {a.get('bclk')} / new {b.get('bclk')}"]
    print("\n".join(L))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
