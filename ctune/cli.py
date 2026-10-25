"""ctune CLI.  `ctune -h`, `ctune mem -h`, `ctune gpu -h`, `ctune cpu -h`."""
import argparse
import json
import sys

from ctune.core import errors as core_errors
from ctune.core import journal
from ctune.core import sensors
from ctune.core.paths import DATA
from ctune.cpu import api as cpu
from ctune.gpu import api as gpu
from ctune.mem import api as mem


def _out(x):
    print(x if isinstance(x, str) else json.dumps(x, indent=1, default=str))


def build():
    ap = argparse.ArgumentParser(prog="ctune", description="Agent-assisted RAM / CPU / GPU tuning toolkit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="memory, CPU and GPU status, sensors, error counters, journal tail")
    sub.add_parser("sensors", help="VDIMM/VTT/VSOC, fans per header, temps")
    s = sub.add_parser("errors", help="corrected machine-check errors per bank (WHEA equivalent)")
    s.add_argument("--since", default="-24h")
    j = sub.add_parser("journal", help="tuning journal")
    jsub = j.add_subparsers(dest="jcmd", required=True)
    s = jsub.add_parser("add")
    s.add_argument("kind", choices=journal.KINDS)
    s.add_argument("text")
    s.add_argument("--data", help="extra JSON object to store")
    s = jsub.add_parser("list")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--kind")

    m = sub.add_parser("mem", help="RAM / fabric").add_subparsers(dest="mcmd", required=True)
    s = m.add_parser("snapshot", help="full snapshot of timings/clocks/voltages/BIOS settings (root via pkexec)")
    s.add_argument("--label", default="")
    s.add_argument("--skip-devmem", action="store_true")
    m.add_parser("snapshots", help="list snapshots")
    s = m.add_parser("show", help="print a snapshot summary")
    s.add_argument("name", nargs="?", default="latest")
    s = m.add_parser("compare", help="diff two snapshots (default previous vs latest)")
    s.add_argument("old", nargs="?", default="previous")
    s.add_argument("new", nargs="?", default="latest")
    s = m.add_parser("verify", help="BIOS configuration vs running hardware (writes verify.md)")
    s.add_argument("name", nargs="?", default="latest")
    s = m.add_parser("checklist", help="settings to re-enter after a BIOS flash (writes reentry_checklist.md)")
    s.add_argument("name", nargs="?", default="latest")
    s = m.add_parser("bios", help="search decoded BIOS settings")
    s.add_argument("query")
    s.add_argument("--snapshot", default="latest")
    s.add_argument("--hidden", action="store_true")

    g = sub.add_parser("gpu", help="NVIDIA GPU").add_subparsers(dest="gcmd", required=True)
    g.add_parser("status", help="clocks, offsets, power, limiters, applied settings, Xid count")
    s = g.add_parser("apply", help="apply settings (root via pkexec); they last until reboot")
    s.add_argument("--core-offset", type=int)
    s.add_argument("--mem-offset", type=int, help="NVML units (half of what nvidia-settings shows)")
    s.add_argument("--max-clock", type=int, help="lock the core clock to 0..MHZ")
    s.add_argument("--power-limit", type=float, help="watts")
    g.add_parser("reset", help="offsets 0, unlock clocks, default power limit")
    s = g.add_parser("test", help="core (vkpeak), vram (memtest_vulkan) or screen (both), with telemetry")
    s.add_argument("kind", nargs="?", default="screen", choices=["core", "vram", "screen"])
    s.add_argument("--vram-seconds", type=int, default=300)
    s.add_argument("--core-runs", type=int, default=3)
    s.add_argument("--note", default="")
    s = g.add_parser("xid", help="NVIDIA Xid errors from the kernel log")
    s.add_argument("--since", default="boot")

    c = sub.add_parser("cpu", help="Curve Optimizer per core").add_subparsers(dest="ccmd", required=True)
    c.add_parser("status", help="margin per core (BIOS or ctune), clocks, voltage, temperature, core MCA errors")
    c.add_parser("probe", help="read every core's margin through the SMU and compare with the BIOS (root via pkexec)")
    s = c.add_parser("apply", help="set one core's margin (root via pkexec); lasts until reboot")
    s.add_argument("--core", type=int, required=True)
    s.add_argument("--margin", type=int, required=True, help="-30..30, at most 5 away from the current value")
    s.add_argument("--dry-run", action="store_true", help="check the guardrails and print the mailbox word; send nothing")
    c.add_parser("reset", help="restore the boot (BIOS) margin of every core ctune changed (root via pkexec)")
    s = c.add_parser("test", help="one verified stress-ng thread per core, varied loads and idle gaps, with telemetry")
    s.add_argument("--cores", help="comma-separated core numbers (default all)")
    s.add_argument("--seconds-per-core", type=int, default=360)
    s.add_argument("--order", default="sequential", choices=["sequential", "random"])
    s.add_argument("--note", default="")
    return ap


def main(argv=None):
    a = build().parse_args(argv)
    if a.cmd == "status":
        st = mem.status()
        for name, fn in (("cpu", cpu.status), ("gpu", gpu.status)):
            try:
                st[name] = fn()
            except Exception as e:
                st[name] = f"unavailable: {e}"
        _out(st)
    elif a.cmd == "sensors":
        _out(sensors.read())
    elif a.cmd == "errors":
        _out(core_errors.summary(a.since))
    elif a.cmd == "journal":
        if a.jcmd == "add":
            _out(journal.add(DATA, a.kind, text=a.text, **(json.loads(a.data) if a.data else {})))
        else:
            _out(journal.tail(DATA, a.n, a.kind))
    elif a.cmd == "mem":
        if a.mcmd == "snapshot":
            r = mem.snapshot(a.label, a.skip_devmem)
            _out(r["summary"])
            print(f"\nsaved: {r['dir']}")
        elif a.mcmd == "snapshots":
            _out("\n".join(mem.snapshots()) or "(none)")
        elif a.mcmd == "show":
            _out(mem.summary(a.name))
        elif a.mcmd == "compare":
            _out(mem.compare(a.old, a.new))
        elif a.mcmd == "verify":
            _out(mem.verify(a.name))
        elif a.mcmd == "checklist":
            _out(mem.checklist(a.name))
        elif a.mcmd == "bios":
            for h in mem.bios_search(a.query, a.snapshot, include_hidden=a.hidden):
                print(f"{h['location']}  =  {h['value']}  [{h['state']}]")
    elif a.cmd == "gpu":
        if a.gcmd == "status":
            _out(gpu.status())
        elif a.gcmd == "apply":
            _out(gpu.apply(a.core_offset, a.mem_offset, a.max_clock, a.power_limit))
        elif a.gcmd == "reset":
            _out(gpu.reset())
        elif a.gcmd == "test":
            _out(gpu.test(a.kind, a.vram_seconds, a.core_runs, note=a.note))
        elif a.gcmd == "xid":
            _out(gpu.xid_errors(a.since))
    elif a.cmd == "cpu":
        if a.ccmd == "status":
            _out(cpu.status())
        elif a.ccmd == "probe":
            _out(cpu.probe())
        elif a.ccmd == "apply":
            _out(cpu.apply(a.core, a.margin, a.dry_run))
        elif a.ccmd == "reset":
            _out(cpu.reset())
        elif a.ccmd == "test":
            cores = [int(x) for x in a.cores.split(",")] if a.cores else None
            _out(cpu.test(cores, a.seconds_per_core, a.order, note=a.note))
    return 0


if __name__ == "__main__":
    sys.exit(main())
