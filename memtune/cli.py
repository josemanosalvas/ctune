"""memtune CLI.  Run `memtune -h`."""
import argparse
import json
import sys

from memtune import api


def _out(x):
    print(x if isinstance(x, str) else json.dumps(x, indent=1, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="memtune", description="Agent-assisted RAM / fabric tuning toolkit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="platform, drivers, sensors, error counters, recent journal")
    s = sub.add_parser("snapshot", help="full snapshot of timings/clocks/voltages/BIOS settings (root via pkexec)")
    s.add_argument("--label", default="")
    s.add_argument("--skip-devmem", action="store_true", help="skip /dev/mem reads (HiiDB, AOD/APOB, BCLK)")
    sub.add_parser("snapshots", help="list snapshots")
    s = sub.add_parser("show", help="print a snapshot summary")
    s.add_argument("name", nargs="?", default="latest")
    s = sub.add_parser("compare", help="diff two snapshots (default: previous vs latest)")
    s.add_argument("old", nargs="?", default="previous")
    s.add_argument("new", nargs="?", default="latest")
    s = sub.add_parser("bios", help="search decoded BIOS settings")
    s.add_argument("query")
    s.add_argument("--snapshot", default="latest")
    s.add_argument("--hidden", action="store_true")
    sub.add_parser("sensors", help="VDIMM/VTT/VSOC, fans per header, temps")
    s = sub.add_parser("errors", help="corrected machine-check errors per bank (WHEA equivalent)")
    s.add_argument("--since", default="-24h")
    j = sub.add_parser("journal", help="tuning journal")
    jsub = j.add_subparsers(dest="jcmd", required=True)
    s = jsub.add_parser("add")
    s.add_argument("kind", choices=["plan", "applied", "result", "note"])
    s.add_argument("text")
    s.add_argument("--data", default=None, help="extra JSON object to store")
    s = jsub.add_parser("list")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--kind")
    a = ap.parse_args(argv)

    if a.cmd == "status":
        _out(api.status())
    elif a.cmd == "snapshot":
        r = api.snapshot(a.label, a.skip_devmem)
        _out(r["summary"])
        print(f"\nsaved: {r['dir']}")
    elif a.cmd == "snapshots":
        _out("\n".join(api.snapshots()) or "(none)")
    elif a.cmd == "show":
        _out(api.summary(a.name))
    elif a.cmd == "compare":
        _out(api.compare(a.old, a.new))
    elif a.cmd == "bios":
        for h in api.bios_search(a.query, a.snapshot, include_hidden=a.hidden):
            print(f"{h['location']}  =  {h['value']}  [{h['state']}]")
    elif a.cmd == "sensors":
        _out(api.sensors())
    elif a.cmd == "errors":
        _out(api.errors(a.since))
    elif a.cmd == "journal":
        if a.jcmd == "add":
            extra = json.loads(a.data) if a.data else {}
            _out(api.journal_add(a.kind, text=a.text, **extra))
        else:
            _out(api.journal_list(a.n, a.kind))
    return 0


if __name__ == "__main__":
    sys.exit(main())
