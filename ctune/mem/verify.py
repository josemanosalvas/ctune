"""Check that the BIOS configuration in a snapshot matches what the hardware runs, and build the re-entry
checklist (every setting changed from its default) for after a BIOS flash and "Load Optimized Defaults".

Matching is by the Tweaker (M.I.T.) prompt text, so it also works on a newer BIOS whose variable layout changed.
"""
import json
import os

# maps a Tweaker prompt to its key in the decoded UMC registers (snapshot.json umc.decoded.umcs[*])
TIMINGS = {
    "CAS Latency": "tCL", "tRCDRD": "tRCDRD", "tRCDWR": "tRCDWR", "tRP": "tRP", "tRAS": "tRAS", "tRC": "tRC",
    "tWR": "tWR", "tCWL": "tCWL", "tRRD_S": "tRRDS", "tRRD_L": "tRRDL", "tWTR_S": "tWTRS", "tWTR_L": "tWTRL",
    "tRFC": "tRFC", "tRTP": "tRTP", "tFAW": "tFAW", "tRDWR": "tRDWR", "tWRRD": "tWRRD",
    "tRDRDSC": "tRDRDSC", "tRDRDSD": "tRDRDSD", "tRDRDDD": "tRDRDDD", "tRDRD_SCL": "tRDRDSCL",
    "tWRWRSC": "tWRWRSC", "tWRWRSD": "tWRWRSD", "tWRWRDD": "tWRWRDD", "tWRWR_SCL": "tWRWRSCL", "tCKE": "tCKE",
}
# Gigabyte voltage codes: Auto/Normal occupy the low codes; ranges from the EasyTune GSAT table (inferred).
VCORE_SOC_V = lambda code: (0.750 + (code - 2) * 0.00625, 0.750 + code * 0.00625)  # noqa: E731
DRAM_V = lambda code: (1.000 + (code - 2) * 0.010, 1.000 + code * 0.010)  # noqa: E731


def _load(snap_dir):
    with open(os.path.join(snap_dir, "snapshot.json")) as f:
        snap = json.load(f)
    with open(os.path.join(snap_dir, "decoded", "bios_settings.json")) as f:
        settings = json.load(f)["settings"]
    return snap, settings


def _num(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _bios(settings, prompt, menu="Setup > M.I.T.", numeric=False):
    """Value and Auto/MANUAL state of the first matching question. numeric=True skips mode selectors that share
    the prompt (for example 'VDDP Voltage Control' is both the Auto/Manual switch and the mV value). Channel B's timings
    share the prompts and stay Auto unless Memory Timing Mode is Advanced Manual, so they are skipped."""
    for e in settings:
        for loc in e.get("locations") or []:
            if loc.startswith(menu) and loc.endswith(f":: {prompt}") and "Channel B Memory Sub Timings" not in loc:
                v = e.get("value_text") if e.get("value_text") is not None else e.get("value")
                if numeric and _num(v) is None:
                    continue
                return (_num(v) if numeric else v), e.get("auto_state")
    return None, None


def verify(snap_dir):
    snap, settings = _load(snap_dir)
    umcs = snap["umc"]["decoded"]["umcs"]
    pm = {k: v["median"] for k, v in snap["pm_table"].get("fields", {}).items()}
    vtt = snap.get("vtt_proxy") or {}
    rows = []

    def row(item, configured, state, live, ok):
        rows.append({"item": item, "bios": configured, "state": state, "running": live,
                     "match": "yes" if ok is True else ("NO" if ok is False else "n/a")})

    v, st = _bios(settings, "System Memory Multiplier")
    row("Memory speed", v, st, f"DDR4-{umcs[0].get('MT_s_zentimings_display')}",
        None if v in (None, "Auto") else abs(int(v) - umcs[0]["MT_s"]) <= 2)
    v, st = _bios(settings, "FCLK Frequency", "AMD CBS")
    row("FCLK", v, st, f"{pm.get('FCLK_MHz')} MHz (UCLK {pm.get('UCLK_MHz')}, MCLK {pm.get('MCLK_MHz')})",
        None if not v or "MHz" not in str(v) else abs(int(str(v).replace("MHz", "")) - pm.get("FCLK_MHz", 0)) <= 2)
    for prompt, key in TIMINGS.items():
        v, st = _bios(settings, prompt)
        live = sorted({u.get(key) for u in umcs})
        ok = None if v in (None, 65535, "Auto") else all(int(v) == x for x in live)
        row(prompt, v, st, "/".join(str(x) for x in live), ok)
    for prompt, key, want in (("Command Rate(tCMD)", "Cmd2T", None), ("Gear Down Mode", "GDM", None)):
        v, st = _bios(settings, prompt)
        live = umcs[0].get(key)
        row(prompt, v, st, live, None if v in (None, "Auto") else str(v) == str(live))
    for prompt, key in (("VDDP Voltage Control", "CLDO_VDDP_V"),
                        ("Global VDDG CCD Voltage Control", "VDDG_CCD_V"),
                        ("Global VDDG IOD Voltage Control", "VDDG_IOD_V")):
        v, st = _bios(settings, prompt, "AMD Overclocking", numeric=True)
        live = pm.get(key)
        ok = None if not v or live is None else abs(v / 1000 - live) < 0.01
        row(prompt, f"{v} mV" if v else v, st, f"{live} V", ok)
    v, st = _bios(settings, "VCORE SOC", numeric=True)
    meas = (snap.get("hwmon_sensors") and _hwmon(snap, "it8688", "in4"))
    if isinstance(v, int) and v != 65535:
        lo, hi = VCORE_SOC_V(v)
        row("VCORE SOC", f"code {v} = {lo:.4f}-{hi:.4f} V", st, f"{meas} V measured (under load-line)",
            None if meas is None else lo - 0.06 <= meas <= hi)
    v, st = _bios(settings, "DRAM Voltage      (CH A/B)", numeric=True)
    if isinstance(v, int) and v != 65535:
        lo, hi = DRAM_V(v)
        meas = vtt.get("vdimm_it8688_in6_V")
        row("DRAM Voltage", f"code {v} = {lo:.2f}-{hi:.2f} V", st, f"{meas} V measured",
            None if meas is None else lo - 0.02 <= meas <= hi + 0.02)
    v, st = _bios(settings, "PBO Limits", "AMD Overclocking")
    row("PBO limits", v, st, f"PPT {pm.get('PPT_limit_W')} W / TDC {pm.get('TDC_limit_A')} A / EDC {pm.get('EDC_limit_A')} A",
        None if v != "Disable" else (pm.get("PPT_limit_W"), pm.get("TDC_limit_A"), pm.get("EDC_limit_A")) == (142.0, 95.0, 140.0))
    v, st = _bios(settings, "ProcODT")
    row("ProcODT", v, st, "not readable live (needs the iomem=relaxed APOB capture)", None)
    v, st = _bios(settings, "Curve Optimizer", "AMD Overclocking")
    co = [f"{'-' if _bios(settings, f'Core {i} Curve Optimizer Sign', 'AMD Overclocking')[0] == 'Negative' else '+'}"
          f"{_bios(settings, f'Core {i} Curve Optimizer Magnitude', 'AMD Overclocking', numeric=True)[0]}" for i in range(8)]
    row("Curve Optimizer", f"{v}: " + " ".join(co), st, "not readable live (no reliable SMU getter)", None)
    return rows


def _hwmon(snap, chip, ch):
    try:
        for k, c in json.loads(snap["hwmon_sensors"]).items():
            if k.startswith(chip):
                return c[ch][f"{ch}_input"]
    except Exception:
        return None
    return None


def verify_md(snap_dir):
    rows = verify(snap_dir)
    n_ok = sum(r["match"] == "yes" for r in rows)
    n_bad = [r for r in rows if r["match"] == "NO"]
    out = [f"# BIOS configuration against running hardware: {os.path.basename(snap_dir)}", "",
           f"{n_ok} of {len(rows)} checks match, {len(n_bad)} mismatch, the rest cannot be read live.", "",
           "| Setting | BIOS | State | Running | Match |", "|---|---|---|---|---|"]
    out += [f"| {r['item']} | {r['bios']} | {r['state']} | {r['running']} | {r['match']} |" for r in rows]
    return "\n".join(out) + "\n"


def checklist_md(snap_dir):
    """Every visible setting changed from its default, grouped by menu, Tweaker first. CBS memory items mirror Tweaker."""
    _, settings = _load(snap_dir)
    groups = {}
    for e in settings:
        if e.get("auto_state") != "MANUAL" or e.get("visible_now") is False:
            continue
        loc = (e.get("locations") or ["?"])[0]
        menu, _, prompt = loc.rpartition(" :: ")
        if menu in ("Setup > Main", "Setup > BIOS", "AMD PBS Option") and prompt in ("System Language", "Boot Option #%d", "CPU Revision"):
            continue  # language / boot order / read-only revision: not tuning settings
        v = e.get("value_text") if e.get("value_text") is not None else e.get("value")
        d = e.get("default_text")
        d = "Auto" if str(d) == "65535" else d
        g = groups.setdefault(menu, [])
        name = prompt.strip()
        if any(p == name for p, _, _ in g):
            name += " (value)"
        g.append((name, v, d))

    def rank(menu):
        order = ["Setup > M.I.T. > Advanced Frequency", "Setup > M.I.T. > Advanced Memory", "Setup > M.I.T. > Advanced Voltage",
                 "AMD Overclocking", "AMD CBS > XFR", "Setup > M.I.T. > PC Health", "AMD PBS", "AMD CBS > FCH",
                 "AMD CBS > Chipset", "AMD CBS > UMC"]
        return next((i for i, p in enumerate(order) if menu.startswith(p)), len(order))

    out = [f"# Re-entry checklist from {os.path.basename(snap_dir)}", "",
           "Every visible BIOS setting that differs from its default. After the flash and \"Load Optimized Defaults\",",
           "enter these and leave everything else on Auto/default. Enter memory settings in Tweaker (M.I.T.); the AMD CBS",
           "memory section at the end mirrors Tweaker, so only check it.", "",
           "Voltage codes: VCORE SOC 64 is about 1.1375 V, DRAM Voltage 55 is about 1.53-1.55 V (measured 1.536 V).",
           "SYS_FAN4 (RAM fans) curve: Manual, CPU temperature input, flat 51 % (start PWM 130/255 from 20 C up to about 93 C,",
           "100 % at 100 C), SYS_FAN4 Stop disabled. The curve points are not BIOS questions, so set them in the Smart Fan 5 graph.", ""]
    for menu in sorted(groups, key=lambda m: (rank(m), m)):
        mirror = " (mirror of Tweaker, check only)" if menu.startswith("AMD CBS > UMC") else ""
        out += [f"## {menu}{mirror}", "", "| Setting | Value | Default |", "|---|---|---|"]
        out += [f"| {p} | {v} | {d} |" for p, v, d in groups[menu]]
        out.append("")
    return "\n".join(out)
