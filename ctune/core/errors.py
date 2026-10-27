"""Machine-check error counters: the Linux counterpart of Windows WHEA-17/18/19.

AMD's mce_amd threshold driver exposes a per-bank `error_count` under
/sys/devices/system/machinecheck/machinecheck<cpu>/<bank>/<block>/error_count. The counter is
reset at boot and counts corrected errors per bank, so it works as an "errors per hour" signal,
not only as a pass or fail result. The kernel polls corrected errors every `check_interval` seconds
(300 s by default), so read again at least 5 minutes after a test before trusting a zero.
"""
import glob
import os
import subprocess

ROOT = "/sys/devices/system/machinecheck"

# What each bank family points at when tuning memory, fabric or CPU.
SUBSYSTEM = {
    "coherent_station": "fabric", "pie": "fabric", "param_block": "fabric",
    "nbio": "pcie/io", "umc": "memory controller",
    "l3_cache": "core/l3", "load_store": "core", "insn_fetch": "core", "l2_cache": "core",
    "decode_unit": "core", "execution_unit": "core", "floating_point": "core",
    "mp5": "firmware", "psp": "firmware", "smu": "firmware",
}


def _family(bank):
    return bank.rstrip("0123456789").rstrip("_")


def counts():
    """{bank: total corrected-error count summed over CPUs} for banks with a counter."""
    out = {}
    for p in glob.glob(f"{ROOT}/machinecheck*/*/*/error_count"):
        bank = p.split("/")[-3]
        try:
            n = int(open(p).read().strip() or 0)
        except (OSError, ValueError):
            continue
        out[bank] = out.get(bank, 0) + n
    return out


def counts_by_cpu(root=ROOT, subsystems=("core", "core/l3")):
    """{cpu: {bank: count}} per logical CPU for the banks of the given subsystems. A Curve Optimizer error lands in
    the core banks of the CPU that ran the failing code, so a per-CPU delta points at the core."""
    out = {}
    for p in glob.glob(f"{root}/machinecheck*/*/*/error_count"):
        parts = p.split("/")
        bank = parts[-3]
        if SUBSYSTEM.get(_family(bank)) not in subsystems:
            continue
        try:
            with open(p) as f:
                cpu, n = int(parts[-4][len("machinecheck"):]), int(f.read().strip() or 0)
        except (OSError, ValueError):
            continue
        out.setdefault(cpu, {})[bank] = n
    return out


def delta_by_cpu(before, after):
    """{cpu: {bank: increase}} for counters that went up between two counts_by_cpu() readings."""
    out = {}
    for cpu, banks in after.items():
        for bank, n in banks.items():
            d = n - before.get(cpu, {}).get(bank, 0)
            if d > 0:
                out.setdefault(cpu, {})[bank] = d
    return out


def check_interval():
    try:
        return int(open(f"{ROOT}/machinecheck0/check_interval").read())
    except (OSError, ValueError):
        return None


def kernel_hw_errors(since="-24h", limit=40):
    """Recent 'Hardware Error' and mce lines from the kernel log (empty if the journal has rotated them away)."""
    try:
        r = subprocess.run(["journalctl", "-k", "--no-pager", "-o", "short-iso", "--since", since,
                            "-g", "Hardware Error|mce:|Machine check"],
                           capture_output=True, text=True, timeout=30)
        lines = [l for l in r.stdout.splitlines() if l and not l.startswith("-- ")]
        return lines[-limit:]
    except Exception as e:
        return [f"<journalctl failed: {e!r}>"]


def summary(since="-24h"):
    c = counts()
    nonzero = {b: n for b, n in c.items() if n}
    by_sub = {}
    for b, n in nonzero.items():
        s = SUBSYSTEM.get(_family(b), "other")
        by_sub[s] = by_sub.get(s, 0) + n
    return {
        "banks_monitored": len(c),
        "total_corrected_errors_since_boot": sum(c.values()),
        "nonzero_banks": nonzero,
        "by_subsystem": by_sub,
        "poll_interval_s": check_interval(),
        "uptime_s": float(open("/proc/uptime").read().split()[0]),
        "kernel_log": kernel_hw_errors(since),
        "note": "The kernel polls corrected errors every poll_interval_s; wait that long after a test before reading.",
    }
