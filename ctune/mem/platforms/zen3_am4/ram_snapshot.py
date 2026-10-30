#!/usr/bin/env python3
"""Snapshot the live DDR4 and Infinity Fabric configuration of a Zen3 AM4 system, the way ZenTimings shows it.

Run as root:  python3 -I ram_snapshot.py [--out DIR] [--label TEXT] [--owner UID:GID]
or as the user once `ctune passwordless` has installed collect_root.py: the reads that need root go through that
root-owned collector with no password prompt, and this script writes the snapshot as the user.
Writes DIR/<timestamp>_<bios>/{snapshot.json,snapshot.md,raw/*}. Compare two snapshots with ram_compare.py.

Read-only. Sources:
  * UMC registers over SMN via ryzen_smu (/sys/kernel/ryzen_smu_drv/smn), decoded by zen3_ddr4_decode.py
    (ZenStates-Core DDR4Dictionary map, cross-checked against CoreFreq).
  * SMU pm_table (FCLK, UCLK, MCLK, VSOC, VDDP, VDDG, PPT, TDC, EDC), at the ZenStates-Core PowerTable.cs offsets.
  * SVI2 SoC telemetry (SMN 0x5A00C), dmidecode types 0, 2, 16 and 17, hwmon, and the AOD and APOB buffers (only if
    /dev/mem allows).
"""
import argparse, base64, glob, importlib.util, json, os, re, statistics, struct, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SMN = "/sys/kernel/ryzen_smu_drv/smn"
SMU = "/sys/kernel/ryzen_smu_drv"
COLLECTOR = "/usr/local/libexec/ctune/mem/zen3_am4/collect_root.py"   # `ctune passwordless`
DMIDECODE = "/usr/bin/dmidecode"

spec = importlib.util.spec_from_file_location("zdec", os.path.join(HERE, "zen3_ddr4_decode.py"))
zdec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zdec)

# pm_table 0x380905 (Vermeer). Bold rows in research/pmtable.md are confirmed by two sources
# (ZenStates-Core PowerTable.cs L300 and ryzen_monitor); the rest are ryzen_monitor labels.
PM_FIELDS = {
    0x380905: [
        ("PPT_limit_W", 0x000), ("TDC_limit_A", 0x008), ("THM_limit_C", 0x010), ("EDC_limit_A", 0x020),
        ("FCLK_MHz", 0x0C0), ("FCLK_eff_MHz", 0x0C4), ("UCLK_MHz", 0x0C8), ("MCLK_MHz", 0x0CC),
        ("VSOC_set_V", 0x0B0), ("VSOC_V", 0x0B4), ("SOC_current_A", 0x0B8), ("SOC_power_W", 0x0BC),
        ("VDDIO_MEM_power_W", 0x068), ("SOC_temp_C", 0x1FC),
        ("FCLK_DPM0_MHz", 0x128), ("UCLK_DPM0_MHz", 0x138), ("MCLK_DPM0_MHz", 0x148),
        ("CLDO_VDDM_V", 0x220), ("CLDO_VDDP_V", 0x224), ("VDDG_IOD_V", 0x228), ("VDDG_CCD_V", 0x22C),
        ("CCLK_limit_GHz", 0x24C), ("FIT_limit", 0x018), ("TDP_W", 0x218),
    ]
}


def clock_mode(fclk, uclk, mclk):
    """Name the FCLK:UCLK:MCLK relation from the pm_table medians."""
    if not (uclk and mclk):
        return "clock mode unknown"
    if abs(uclk - mclk) >= 5:
        return "UCLK != MCLK (2:1)"
    if fclk and abs(fclk - uclk) >= 5:
        return "FCLK != UCLK (desync)"
    return "1:1:1 coupled"


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except Exception as e:  # a missing tool or a similar failure
        return f"<error: {e!r}>"


def rd(path, binary=False):
    try:
        with open(path, "rb" if binary else "r") as f:
            return f.read() if binary else f.read().strip()
    except OSError as e:
        return None if binary else f"<error: {e.strerror}>"


# ---------------------------------------------------------------- SMN
def smn_read_many(addrs):
    """A 4-byte write is a read request (ryzen_smu drv.c). A failed read keeps the previous value without an error,
    so callers read everything twice and interleave a canary to detect stale results."""
    fd = os.open(SMN, os.O_RDWR)
    try:
        out = {}
        for a in addrs:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.write(fd, struct.pack("<I", a)) != 4:
                raise OSError(f"smn address write failed for {a:#x}")
            os.lseek(fd, 0, os.SEEK_SET)
            b = os.read(fd, 4)
            if len(b) != 4:
                raise OSError(f"smn short read for {a:#x}")
            out[a] = struct.unpack("<I", b)[0]
        return out
    finally:
        os.close(fd)


def smn_snapshot():
    addrs = list(zdec.required_smn_addresses(channels=(0, 1), include_svi2=False))
    # the whole P-state-0 timing block and config block, so any BIOS change shows up even in undecoded registers
    for base in (0x050000, 0x150000):
        addrs += [base | o for o in range(0x200, 0x300, 4)]
        addrs += [base | o for o in range(0x000, 0x040, 4)]
    addrs = sorted(set(addrs))
    p1 = smn_read_many(addrs)
    p2 = smn_read_many(list(reversed(addrs)))
    unstable = {hex(a): [hex(p1[a]), hex(p2[a])] for a in addrs if p1[a] != p2[a]}
    # VSOC via SVI2: ZenTimings re-reads while bits[15:8] != 0
    svi = None
    for _ in range(20):
        v = smn_read_many([zdec.SVI2_SOC_PLANE])[zdec.SVI2_SOC_PLANE]
        svi = v
        if zdec.bits(v, 15, 8) == 0:
            break
        time.sleep(0.01)
    regs = dict(p1)
    regs[zdec.SVI2_SOC_PLANE] = svi
    return regs, unstable


# ---------------------------------------------------------------- pm_table
def pm_snapshot(samples=15, interval=0.2):
    # ryzen_smu only creates pm_table* when it knows the table version (smu.c); a new SMU firmware may drop it.
    # The SMN timing read does not depend on it, so degrade instead of aborting.
    vraw = rd(f"{SMU}/pm_table_version", True)
    if not vraw or not rd(f"{SMU}/pm_table", True):
        return ({"version": None, "size": 0, "known_layout": False, "fields": {},
                 "error": "ryzen_smu exposes no pm_table (dmesg: 'Unknown PM table version'?)"}, b"")
    ver = struct.unpack("<I", vraw[:4])[0]
    # the first read after idle is often an outlier (long SMU averaging window): discard it, keep medians
    raws = []
    for _ in range(samples):
        time.sleep(interval)
        r = rd(f"{SMU}/pm_table", True)
        if r:
            raws.append(r)
    fields = PM_FIELDS.get(ver)
    out = {"version": hex(ver), "size": len(raws[-1]), "known_layout": bool(fields), "fields": {}}
    if fields:
        for name, off in fields:
            vals = [struct.unpack_from("<f", r, off)[0] for r in raws]
            out["fields"][name] = {"median": round(statistics.median(vals), 4),
                                   "min": round(min(vals), 4), "max": round(max(vals), 4), "offset": hex(off)}
    return out, raws[-1]


# ---------------------------------------------------------------- dmidecode
def dmi_dimms(text):
    dimms = []
    for blk in text.split("\n\n"):
        if "Memory Device" not in blk:
            continue
        d = {}
        for line in blk.splitlines():
            m = re.match(r"\s+([^:]+):\s*(.*)", line)
            if m:
                d[m.group(1)] = m.group(2)
        dimms.append({k: d.get(k) for k in ("Locator", "Bank Locator", "Size", "Rank", "Part Number",
                                            "Serial Number", "Module Manufacturer ID", "Speed",
                                            "Configured Memory Speed", "Configured Voltage",
                                            "Minimum Voltage", "Maximum Voltage")})
    return dimms


# ---------------------------------------------------------------- VDIMM proxy and platform fingerprint
def vtt_proxy(sensors_json):
    """IT8792E in1 = 'DDRVTT AB' on the B550 AORUS PRO (LibreHardwareMonitor SuperIOHardware.cs). VTT tracks
    VDIMM/2 only while BIOS 'DRAM Termination (CH A/B)' is Auto. On the reference board VDIMM/VTT = 1.380/0.682 = 2.02."""
    try:
        chips = json.loads(sensors_json)
        v = next(c["in1"]["in1_input"] for k, c in chips.items() if k.startswith("it8792"))
    except Exception:
        return None
    out = {"in1_V": v, "adc_counts": round(v / 0.0109), "vdimm_est_V": round(v * 2.02, 3)}
    try:  # direct DRAM rail on the main IT8688E (in6 'DIMM', LHM), needs the out-of-tree it87 driver
        out["vdimm_it8688_in6_V"] = next(c["in6"]["in6_input"] for k, c in chips.items() if k.startswith("it8688"))
    except Exception:
        pass
    return out


def fingerprint():
    """Linux-visible proxies for non-memory BIOS settings that 'Load Optimized Defaults' may flip (no root needed)."""
    smi = sh(["nvidia-smi", "-q", "-d", "MEMORY"])
    bar1 = re.search(r"BAR1 Memory Usage\s*\n\s*Total\s*:\s*(.+)", smi)
    cpuinfo = rd("/proc/cpuinfo")
    efi = sh(["efibootmgr"])
    try:
        dep = next(d for d in json.loads(sh(["rpm-ostree", "status", "--json"]))["deployments"] if d.get("booted"))
        ostree = f"{dep.get('version')} {dep.get('checksum', '')[:12]}"
    except Exception:
        ostree = None
    return {
        "resizable_bar_bar1": bar1.group(1).strip() if bar1 else None,
        "secure_boot": sh(["mokutil", "--sb-state"]).strip().replace("\n", " / "),
        "csm_legacy_boot_entries": len(re.findall(r"BBS\(", efi)),
        "boot_current": (re.search(r"BootCurrent:\s*(\S+)", efi) or [None, None])[1],
        "svm_enabled": " svm " in f" {cpuinfo} ",
        "dev_kvm": os.path.exists("/dev/kvm"),
        "iommu": sorted(os.listdir("/sys/class/iommu")) if os.path.isdir("/sys/class/iommu") else [],
        "tpm": {t: rd(f"/sys/class/tpm/{t}/tpm_version_major") for t in sorted(os.listdir("/sys/class/tpm"))}
               if os.path.isdir("/sys/class/tpm") else {},
        "cpuidle_states": [rd(p) for p in sorted(glob.glob("/sys/devices/system/cpu/cpu0/cpuidle/state*/name"))],
        "amd_pstate": rd("/sys/devices/system/cpu/amd_pstate/status"),
        "cpb_boost": rd("/sys/devices/system/cpu/cpufreq/boost"),
        "ostree_booted": ostree,
    }


# ---------------------------------------------------------------- optional /dev/mem bits
def bclk_mmio():
    """FCH CGPLLConfig3 @0xFED80E10 (ZenStates-Core Mmio.cs). Only the raw value and the ZSC formula; the read may fail with EPERM."""
    import mmap
    try:
        fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
        try:
            mm = mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ, offset=0xFED80000)
            v = struct.unpack_from("<I", mm, 0xE10)[0]
            mm.close()
        finally:
            os.close(fd)
    except Exception as e:
        return {"error": repr(e)}
    idx, frac = (v >> 4) & 0x1FF, (v >> 25) & 0xF
    base = (idx ^ 100) if idx < 32 else (idx ^ 164)
    return {"raw": hex(v), "bclk_mhz_zsc_formula": base + frac * 0.0625}


EFIVAR_KEEP = (r"(AmdSetup|AOD_SETUP|Setup|AMD_PBS_SETUP|StdDefaults|Profile\d|AMITSESetup|SioSetupData|UsbSupport|"
               r"\w+MitAttrib|HiiDB|Easy\w+Address|AmiNbMemAddrData|GbtCmosLoadDefaultInfo|SecureBootSetup|AMD_RAID|"
               r"GcSensorVarName|PlatformLang|PlatformLangCodes|NetworkStackVar|XhciDID|PluginDevices|Timeout|BootOrder|"
               r"LegacyDevOrder|SecureBoot|SetupMode|AuditMode|DeployedMode|VendorKeys)-")


# ---------------------------------------------------------------- render
TIMING_ROWS = [
    ("tCL", "tRCDRD", "tRCDWR", "tRP", "tRAS", "tRC"),
    ("tRRDS", "tRRDL", "tFAW", "tWTRS", "tWTRL", "tWR", "tRTP", "tCWL"),
    ("tRFC", "tRFC2", "tRFC4", "tRFC_ns", "tREFI", "tREFI_ns", "tSTAG", "tCKE"),
    ("tRDRDSCL", "tWRWRSCL", "tRDWR", "tWRRD", "tRDRDSC", "tRDRDSD", "tRDRDDD", "tWRWRSC", "tWRWRSD", "tWRWRDD"),
    ("tMOD", "tMODPDA", "tMRD", "tMRDPDA", "tPHYWRD", "tPHYWRL", "tPHYRDL", "WRMPR"),
    ("GDM", "Cmd2T", "PowerDown", "BGS", "BGSAlt", "Refresh"),
]


def fmt(v):
    if isinstance(v, float):
        return f"{v:.2f}"
    return "-" if v is None else str(v)


def render_md(s):
    L = [f"# RAM snapshot {s['meta']['captured']}  ({s['meta']['label']})", ""]
    m = s["meta"]
    L += [f"- Board: {m['board']}, BIOS {m['bios_version']} ({m['bios_date']})",
          f"- CPU: {m['cpu']}, microcode {m['microcode']}, SMU fw {m['smu_fw']}, pm_table {s['pm_table']['version']}",
          f"- Kernel: {m['kernel']}", ""]
    u = s["umc"]["decoded"]["umcs"]
    a, b = u[0], u[1]
    L += ["## Memory", "", f"- Speed: **DDR4-{a.get('MT_s_zentimings_display')}** (ratio raw {a.get('ratio_raw')}, "
          f"MEMCLK {fmt(a.get('MEMCLK_MHz_from_ratio'))} MHz from ratio)"]
    pf = s["pm_table"]["fields"]
    if pf:
        g = lambda k: pf.get(k, {}).get("median")
        L.append(f"- FCLK / UCLK / MCLK: {fmt(g('FCLK_MHz'))} / {fmt(g('UCLK_MHz'))} / {fmt(g('MCLK_MHz'))} MHz "
                 f"(DPM0 {fmt(g('FCLK_DPM0_MHz'))}/{fmt(g('UCLK_DPM0_MHz'))}/{fmt(g('MCLK_DPM0_MHz'))}), "
                 f"{clock_mode(g('FCLK_MHz'), g('UCLK_MHz'), g('MCLK_MHz'))}")
        L.append(f"- VSOC: set {fmt(g('VSOC_set_V'))} V, telemetry {fmt(g('VSOC_V'))} V (pm_table); "
                 f"SVI2 {s['umc']['decoded']['vsoc_svi2']['VSOC_V'] if s['umc']['decoded']['vsoc_svi2'] else '-'} V")
        L.append(f"- cLDO VDDP {fmt(g('CLDO_VDDP_V'))} V, VDDG IOD {fmt(g('VDDG_IOD_V'))} V, VDDG CCD "
                 f"{fmt(g('VDDG_CCD_V'))} V, VDDM {fmt(g('CLDO_VDDM_V'))} V")
        L.append(f"- CPU limits: PPT {fmt(g('PPT_limit_W'))} W, TDC {fmt(g('TDC_limit_A'))} A, EDC "
                 f"{fmt(g('EDC_limit_A'))} A, THM {fmt(g('THM_limit_C'))} C, CCLK limit {fmt(g('CCLK_limit_GHz'))} GHz")
    dv = sorted({d.get("Configured Voltage") for d in s["dmi"]["dimms"]} - {None})
    L.append(f"- DMI configured DIMM voltage: {', '.join(dv) or '-'} (SPD nominal, not the real VDIMM)")
    vt = s.get("vtt_proxy")
    if vt:
        L.append(f"- DDR VTT (IT8792E in1): {vt['in1_V']:.3f} V = {vt['adc_counts']} ADC counts, so VDIMM is about {vt['vdimm_est_V']:.2f} V "
                 "(valid only if BIOS 'DRAM Termination' = Auto)")
    L += ["", "## Modules", "", "| Locator | Size | Rank | Part | Serial | Speed / configured | Voltage |", "|---|---|---|---|---|---|---|"]
    for d in s["dmi"]["dimms"]:
        L.append(f"| {d['Bank Locator']} {d['Locator']} | {d['Size']} | {d['Rank']} | {d['Part Number']} | "
                 f"{d['Serial Number']} | {d['Speed']} / {d['Configured Memory Speed']} | {d['Configured Voltage']} |")
    pop = []
    for ch in u:
        for dm in ch.get("dimms", []):
            pop.append(f"{dm['slot_zsc_label']}: {'present, ' + str(dm.get('ranks')) + 'R' if dm['present'] else 'empty'}")
    L += ["", f"UMC population: {'; '.join(pop)}", "", "## Timings (UMC0 = channel A, UMC1 = channel B)", ""]
    for row in TIMING_ROWS:
        L.append("| " + " | ".join(row) + " |")
        L.append("|" + "---|" * len(row))
        va = [fmt(a.get(k)) for k in row]
        vb = [fmt(b.get(k)) for k in row]
        L.append("| " + " | ".join(va) + " |")
        if va != vb:
            L.append("| " + " | ".join(f"B:{x}" for x in vb) + " |")
        L.append("")
    if s["umc"]["unstable"]:
        L += [f"Registers that changed between two reads (expected only for status/counter regs): {s['umc']['unstable']}", ""]
    L += ["## BIOS-side parameters (AOD/APOB)", "", "```", s["aod_apob"].strip() or "-", "```", ""]
    L += [f"BCLK (FCH MMIO): {s['bclk']}", ""]
    if s.get("fingerprint"):
        L += ["## Platform fingerprint", ""] + [f"- {k}: {v}" for k, v in s["fingerprint"].items()] + [""]
    return "\n".join(L)


# ---------------------------------------------------------------- the reads that need root
def collect_privileged(skip_devmem, dump_root, nvs_dump=True):
    """Everything a snapshot reads as root: the SMN registers, dmidecode and, unless skipped, /dev/mem (BCLK, the
    AOD/APOB buffers, the bulk ACPI NVS dumps unless nvs_dump is off, the HII database). The firmware dumps land below
    dump_root/raw. collect_root.py runs this as the root-owned passwordless collector, without the NVS dumps."""
    regs, unstable = smn_snapshot()
    raw = os.path.join(dump_root, "raw")
    os.makedirs(raw, exist_ok=True)
    out = {"regs": {hex(k): v for k, v in regs.items()}, "unstable": unstable,
           "dmidecode": sh([DMIDECODE, "-t", "0", "-t", "2", "-t", "16", "-t", "17"]),
           "dmidecode_t17_dump": sh([DMIDECODE, "-t", "17", "-u"]),
           "bclk": {"skipped": True} if skip_devmem else bclk_mmio(), "aod": "skipped", "fwmem": None}
    if not skip_devmem:
        out["aod"] = sh([sys.executable, "-I", "-B", os.path.join(HERE, "am4_bios_memcfg.py"), "--dump-dir", raw]
                        + ([] if nvs_dump else ["--no-nvs-dump"]))
        out["fwmem"] = sh([sys.executable, "-I", "-B", os.path.join(HERE, "dump_fw_mem.py"),
                           os.path.join(raw, "fwmem")]).strip()
    return out


def collect_passwordless(skip_devmem, d, collector=COLLECTOR, run=subprocess.run):
    """collect_privileged through the root-owned collector, with no password prompt. Its firmware dumps come back
    inline and are written below d as the user."""
    r = run(["pkexec", collector] + (["--skip-devmem"] if skip_devmem else []), capture_output=True, text=True,
            timeout=900)
    if r.returncode:
        raise RuntimeError(f"collector failed ({r.returncode}): {r.stderr.strip()[-600:]}")
    out = json.loads(r.stdout)
    root = os.path.realpath(d)
    for rel, b64 in out.pop("files", {}).items():
        p = os.path.realpath(os.path.join(root, rel))
        if not p.startswith(root + os.sep):
            raise ValueError(f"the collector returned a path outside the snapshot: {rel!r}")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(base64.b64decode(b64))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(HERE), "snapshots"))
    ap.add_argument("--label", default="")
    ap.add_argument("--owner", default="", help="uid:gid to chown the output to (when run via sudo/pkexec)")
    ap.add_argument("--skip-devmem", action="store_true", help="skip /dev/mem reads (BCLK, AOD, APOB)")
    a = ap.parse_args()
    root = os.geteuid() == 0
    if not root and not os.path.isfile(COLLECTOR):
        sys.exit("must run as root (SMN reads need write access to the ryzen_smu smn node), or install the "
                 "passwordless collector with `ctune passwordless`")

    meta = {
        "captured": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "label": a.label,
        "board": f"{rd('/sys/class/dmi/id/board_vendor')} {rd('/sys/class/dmi/id/board_name')}",
        "bios_version": rd("/sys/class/dmi/id/bios_version"), "bios_date": rd("/sys/class/dmi/id/bios_date"),
        "kernel": os.uname().release,
        "cpu": re.search(r"model name\s*:\s*(.*)", rd("/proc/cpuinfo")).group(1),
        "microcode": re.search(r"microcode\s*:\s*(\S+)", rd("/proc/cpuinfo")).group(1),
        "smu_fw": rd(f"{SMU}/version"), "ryzen_smu_drv": rd(f"{SMU}/drv_version"),
        "cmdline": rd("/proc/cmdline"),
    }
    pm, pm_raw = pm_snapshot()
    tag = f"{time.strftime('%Y%m%d-%H%M%S')}_{meta['bios_version']}"
    d = os.path.join(a.out, tag)
    os.makedirs(os.path.join(d, "raw"), exist_ok=True)
    priv = collect_privileged(a.skip_devmem, d) if root else collect_passwordless(a.skip_devmem, d)
    regs = {int(k, 16): v for k, v in priv["regs"].items()}
    unstable, dmi_text, bclk, aod = priv["unstable"], priv["dmidecode"], priv["bclk"], priv["aod"]
    mclk = pm["fields"].get("MCLK_MHz", {}).get("median")
    decoded = zdec.decode_all(regs, pm_mclk_mhz=mclk)
    sensors = sh(["sensors", "-j"])

    snap = {
        "meta": meta,
        "umc": {"raw": {hex(k): hex(v) for k, v in sorted(regs.items())}, "decoded": decoded, "unstable": unstable},
        "pm_table": pm,
        "dmi": {"dimms": dmi_dimms(dmi_text)},
        "bclk": bclk,
        "aod_apob": aod,
        "hwmon_sensors": sensors,
        "vtt_proxy": vtt_proxy(sensors),
        "fingerprint": fingerprint(),
        "amd_pstate": {"status": rd("/sys/devices/system/cpu/amd_pstate/status"),
                       "driver": rd("/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver"),
                       "boost": rd("/sys/devices/system/cpu/cpufreq/boost")},
    }
    with open(os.path.join(d, "snapshot.json"), "w") as f:
        json.dump(snap, f, indent=1)
    with open(os.path.join(d, "snapshot.md"), "w") as f:
        f.write(render_md(snap))
    with open(os.path.join(d, "raw", "pm_table.bin"), "wb") as f:
        f.write(pm_raw)
    with open(os.path.join(d, "raw", "dmidecode.txt"), "w") as f:
        f.write(dmi_text)
    with open(os.path.join(d, "raw", "dmidecode_t17_dump.txt"), "w") as f:
        f.write(priv["dmidecode_t17_dump"])
    # BIOS setup configuration (EFI variables) and the HII database needed to decode it (bios_settings_decode.py)
    ev = os.path.join(d, "raw", "efivars")
    os.makedirs(ev, exist_ok=True)
    for n in sorted(os.listdir("/sys/firmware/efi/efivars")):
        if re.match(EFIVAR_KEEP, n):
            try:
                with open(os.path.join("/sys/firmware/efi/efivars", n), "rb") as src, open(os.path.join(ev, n), "wb") as dst:
                    dst.write(src.read())
            except OSError:
                pass
    if priv["fwmem"]:
        print(priv["fwmem"])
    hiidb = os.path.join(d, "raw", "fwmem", "hiidb.bin")
    if os.path.exists(hiidb):  # decode every BIOS setup question against this BIOS's own forms
        print(sh([sys.executable, "-I", "-B", os.path.join(HERE, "bios_settings_decode.py"), "--hiidb", hiidb,
                  "--efivars", ev, "--live", "", "--out", os.path.join(d, "decoded")]).strip()[-400:])
    if a.owner and root:
        uid, gid = (int(x) for x in a.owner.split(":"))
        for root, dirs, files in os.walk(a.out):
            for n in [root] + [os.path.join(root, x) for x in files]:
                os.chown(n, uid, gid)
    print(d)


if __name__ == "__main__":
    main()
