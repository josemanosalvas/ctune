"""ctune CLI.  `ctune -h`, `ctune mem -h`, `ctune gpu -h`, `ctune cpu -h`."""
import argparse
import json
import sys

from ctune.core import errors as core_errors
from ctune.core import journal, runner, sensors, signoff
from ctune import mascot
from ctune.core.paths import DATA
from ctune.cpu import api as cpu
from ctune.gpu import api as gpu
from ctune.mem import api as mem
from ctune.mem import test as memtest


def _out(x):
    print(x if isinstance(x, str) else json.dumps(x, indent=1, default=str))


def _banner(st):
    """Mascot beside a one-glance summary; the error line turns red when this boot has corrected MCEs or Xid errors."""
    mce = st.get("errors_since_boot") or 0
    xid = st["gpu"].get("xid_errors_this_boot", 0) if isinstance(st.get("gpu"), dict) else 0
    errors = bool(mce or xid)
    counts = f"{mce} corrected MCE · {xid} Xid this boot"
    return mascot.banner([
        mascot.TITLE,
        f"{st.get('board')} · BIOS {st.get('bios')}",
        mascot.paint(counts, "r") if errors else counts,
        "see `ctune errors` / `ctune gpu xid`" if errors else "all quiet",
    ]) + "\n"


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
    s = m.add_parser("test", help="RAM stability: stressapptest over the free RAM with a GPU heat load")
    s.add_argument("kind", nargs="?", default="screen", choices=["screen", "fabric"])
    s.add_argument("--minutes", type=float, help="default 25 (screen) or 90 + 20 idle (fabric)")
    s.add_argument("--no-gpu-heat", action="store_true")
    s.add_argument("--mb", type=int, help="cap the RAM the test takes (default: free RAM minus 1.5 GB)")
    s.add_argument("--detach", action="store_true", help="run in the background; follow with `ctune test-status`")
    s.add_argument("--note", default="")
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
    s.add_argument("--detach", action="store_true")
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
    s.add_argument("--detach", action="store_true")
    s.add_argument("--note", default="")

    s = sub.add_parser("signoff", help="multi-hour suite: ram, ycruncher, cpu, gpu, combined, idle (about 4 h)")
    s.add_argument("--phases", help=f"comma-separated subset of {','.join(signoff.PHASES)}")
    s.add_argument("--minutes", help="per-phase overrides, e.g. ram=120,idle=60")
    s.add_argument("--keep-going", action="store_true", help="run every phase even after a red one")
    s.add_argument("--mb", type=int, help="cap the RAM the memory phases take")
    s.add_argument("--detach", action="store_true")
    s.add_argument("--note", default="")
    sub.add_parser("test-status", help="the running test and its phase, or the last test result")
    s = sub.add_parser("_run", help=argparse.SUPPRESS)
    s.add_argument("spec")
    return ap


def main(argv=None):
    a = build().parse_args(argv)
    if a.cmd == "status":
        st = mem.status()
        st["test"] = runner.status()
        for name, fn in (("cpu", cpu.status), ("gpu", gpu.status)):
            try:
                st[name] = fn()
            except Exception as e:
                st[name] = f"unavailable: {e}"
        if mascot.color_ok(sys.stdout):
            print(_banner(st))
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
        elif a.mcmd == "test":
            kw = {"kind": a.kind, "minutes": a.minutes, "gpu_heat": not a.no_gpu_heat, "note": a.note, "mb": a.mb}
            _out(runner.start("mem_test", **kw) if a.detach else memtest.test(**kw))
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
            kw = {"kind": a.kind, "vram_seconds": a.vram_seconds, "core_runs": a.core_runs, "note": a.note}
            _out(runner.start("gpu_test", **kw) if a.detach else gpu.test(**kw))
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
            kw = {"cores": cores, "seconds_per_core": a.seconds_per_core, "order": a.order, "note": a.note}
            _out(runner.start("cpu_test", **kw) if a.detach else cpu.test(**kw))
    elif a.cmd == "signoff":
        kw = {"phases": a.phases.split(",") if a.phases else None, "keep_going": a.keep_going, "note": a.note,
              "mb": a.mb,
              "minutes": {k: float(v) for k, v in (x.split("=") for x in a.minutes.split(","))} if a.minutes else None}
        _out(runner.start("signoff", **kw) if a.detach else signoff.run(**kw))
    elif a.cmd == "test-status":
        _out(runner.status())
    elif a.cmd == "_run":
        _out(runner.dispatch(a.spec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
