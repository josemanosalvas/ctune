#!/usr/bin/env python3
"""ctune MCP server: stdio transport, newline-delimited JSON-RPC 2.0, standard library only.

Register (project scope) with .mcp.json in the repo, or:  claude mcp add ctune -- python3 -I <repo>/ctune/mcp_server.py
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from ctune.core import errors as core_errors, journal, runner, sensors, signoff  # noqa: E402
from ctune.core.paths import DATA  # noqa: E402
from ctune.cpu import api as cpu  # noqa: E402
from ctune.gpu import api as gpu  # noqa: E402
from ctune.mem import api as mem  # noqa: E402
from ctune.mem import test as memtest  # noqa: E402

VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "ctune tunes RAM, CPU and GPU on an AMD Ryzen + NVIDIA Linux system. mem_* tools read the memory and "
    "fabric configuration (timings from the memory controller, SMU clocks and voltages, every BIOS option decoded from "
    "the EFI variables); the user makes every BIOS change. cpu_* tools read and set the per-core Curve Optimizer margin "
    "through the SMU at runtime and test cores one at a time. gpu_* tools read the GPU and apply clock offsets, a "
    "max-clock lock and a power limit at runtime. cpu and gpu settings stay within guardrails and a reboot clears them. "
    "mem_test, cpu_test and gpu_test are the per-step stability tests and signoff is the multi-hour suite; long tests run "
    "detached, so poll test_status. errors, sensors and journal_* are shared."
)


def _run(kind, fn, detach, **kw):
    """Long tests run detached (poll test_status); short ones can run inline."""
    return runner.start(kind, **kw) if detach else fn(**kw)


def _obj(props=None, required=None):
    return {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}


S = {"type": "string"}
TOOLS = {
    # shared
    "errors": ("Corrected machine-check errors per MCA bank since boot, grouped by subsystem (fabric, pcie/io, memory "
               "controller, core), plus kernel 'Hardware Error' lines. This is the Linux counterpart of WHEA 17/18/19.",
               _obj({"since": S}), lambda a: core_errors.summary(a.get("since", "-24h"))),
    "sensors": ("Live hwmon readings with board labels: VDIMM, VSOC, VTT, fan RPM and PWM per header, CPU and GPU temperatures.",
                _obj(), lambda a: sensors.read()),
    "journal_add": ("Append to the tuning journal. kind is plan (an intended change), applied, result (a test outcome) "
                    "or note. Set data.domain to mem, gpu or cpu, and put structured details in data.",
                    _obj({"kind": {"type": "string", "enum": ["plan", "applied", "result", "note"]}, "text": S,
                          "data": {"type": "object"}}, ["kind", "text"]),
                    lambda a: journal.add(DATA, a["kind"], text=a["text"], **(a.get("data") or {}))),
    "journal_list": ("Last n journal records, optionally filtered by kind.",
                     _obj({"n": {"type": "integer"}, "kind": S}), lambda a: journal.tail(DATA, a.get("n", 20), a.get("kind"))),
    # memory
    "mem_status": ("Platform, BIOS, drivers, live sensors, the error total, the latest snapshots and the journal tail.",
                   _obj(), lambda a: mem.status()),
    "mem_snapshot": ("Take a full memory snapshot: timings per channel, FCLK/UCLK/MCLK, voltages, DIMMs, decoded BIOS "
                     "settings and the platform fingerprint. It needs root, so the user approves a password prompt.",
                     _obj({"label": S, "skip_devmem": {"type": "boolean"}}),
                     lambda a: mem.snapshot(a.get("label", ""), a.get("skip_devmem", False))),
    "mem_list_snapshots": ("List snapshot directory names (oldest first).", _obj(), lambda a: mem.snapshots()),
    "mem_show_snapshot": ("Markdown summary of a snapshot ('latest', 'previous', or a name).",
                          _obj({"name": S}), lambda a: mem.summary(a.get("name", "latest"))),
    "mem_compare": ("Diff two snapshots: timings, raw registers, clocks, voltages, VTT and VDIMM, the platform "
                    "fingerprint, every BIOS setting by menu path, and AOD/APOB. Defaults to previous against latest.",
                    _obj({"old": S, "new": S}), lambda a: mem.compare(a.get("old", "previous"), a.get("new", "latest"))),
    "mem_verify": ("Check a snapshot's BIOS configuration against the running hardware (timings, speed, FCLK, voltages, "
                   "PBO limits). Writes verify.md into the snapshot.", _obj({"name": S}), lambda a: mem.verify(a.get("name", "latest"))),
    "mem_checklist": ("Re-entry checklist for after a BIOS flash: every visible setting that differs from its default, by "
                      "menu. Writes reentry_checklist.md into the snapshot.", _obj({"name": S}),
                      lambda a: mem.checklist(a.get("name", "latest"))),
    "mem_test": ("RAM stability test, recorded in the journal. screen (25 min): stressapptest -W over the free RAM "
                 "while memtest_vulkan heats the case. fabric (90 min + 20 min idle): the screen plus NVMe reads, then "
                 "an idle soak, for FCLK steps and after a BIOS flash. Runs detached by default; poll test_status. Red "
                 "on stressapptest errors, new corrected machine-check errors (umc = memory, fabric = FCLK/VDDG/VSOC), "
                 "VRAM or Xid errors, or a crash.",
                 _obj({"kind": {"type": "string", "enum": ["screen", "fabric"]}, "minutes": {"type": "number"},
                       "gpu_heat": {"type": "boolean"}, "detach": {"type": "boolean"}, "note": S}),
                 lambda a: _run("mem_test", memtest.test, a.get("detach", True), kind=a.get("kind", "screen"),
                                minutes=a.get("minutes"), gpu_heat=a.get("gpu_heat", True), note=a.get("note", ""))),
    "mem_bios_search": ("Search the decoded BIOS settings by menu path or prompt text, for example 'ProcODT', 'VCORE SOC'.",
                        _obj({"query": S, "snapshot": S, "include_hidden": {"type": "boolean"}}, ["query"]),
                        lambda a: mem.bios_search(a["query"], a.get("snapshot", "latest"),
                                                  include_hidden=a.get("include_hidden", False))),
    # gpu
    "gpu_status": ("GPU clocks, offsets with their allowed ranges, power draw and limits, temperature, what currently "
                   "limits the clock (power cap, thermal...), settings ctune applied this boot, and the Xid error count.",
                   _obj(), lambda a: gpu.status()),
    "gpu_apply": ("Apply GPU settings at runtime (root, password prompt). core_offset/mem_offset in MHz (NVML units; "
                  "the memory offset is half of what nvidia-settings shows), max_clock locks the core clock to 0..MHz "
                  "(with a positive core_offset this is the undervolt), power_limit in W. Out-of-range values are rejected.",
                  _obj({"core_offset": {"type": "integer"}, "mem_offset": {"type": "integer"},
                        "max_clock": {"type": "integer"}, "power_limit": {"type": "number"}}),
                  lambda a: gpu.apply(a.get("core_offset"), a.get("mem_offset"), a.get("max_clock"), a.get("power_limit"))),
    "gpu_reset": ("Reset GPU tuning: offsets 0, clocks unlocked, default power limit (root, password prompt).",
                  _obj(), lambda a: gpu.reset()),
    "gpu_test": ("Run a GPU test with 1 Hz telemetry and record the result in the journal. kind: core (vkpeak FP32 "
                 "score and load, about 2 min per run), vram (memtest_vulkan), screen (both). Returns verdict green/red, "
                 "score, average clock, power, efficiency and any new Xid errors.",
                 _obj({"kind": {"type": "string", "enum": ["core", "vram", "screen"]}, "vram_seconds": {"type": "integer"},
                       "core_runs": {"type": "integer"}, "detach": {"type": "boolean"}, "note": S}),
                 lambda a: _run("gpu_test", gpu.test, a.get("detach", False), kind=a.get("kind", "screen"),
                                vram_seconds=a.get("vram_seconds", 300), core_runs=a.get("core_runs", 3),
                                note=a.get("note", ""))),
    "gpu_xid": ("NVIDIA Xid errors from the kernel log ('boot' or a journalctl --since value).",
                _obj({"since": S}), lambda a: gpu.xid_errors(a.get("since", "boot"))),
    # cpu
    "cpu_status": ("Curve Optimizer margin per core (from the BIOS snapshot, or what ctune applied this boot), per-core "
                   "clock, effective clock, voltage, power, temperature and C0 from the SMU pm_table, package power, "
                   "core machine-check counts per CPU, this boot's probe result, and any test cut short by a crash.",
                   _obj(), lambda a: cpu.status()),
    "cpu_probe": ("Read every core's margin through the SMU (read-only, root, password prompt) and compare it with the "
                  "BIOS values from the latest mem snapshot. cpu_apply refuses to write until a probe in the same "
                  "boot matches, so run it once per boot before the first cpu_apply.",
                  _obj(), lambda a: cpu.probe()),
    "cpu_apply": ("Set one core's Curve Optimizer margin at runtime (root, password prompt), then read it back. margin "
                  "is -30 to 30 and at most 5 away from the core's current value. dry_run checks the guardrails and "
                  "shows the SMU mailbox word without sending anything. The margin lasts until reboot.",
                  _obj({"core": {"type": "integer"}, "margin": {"type": "integer"}, "dry_run": {"type": "boolean"}},
                       ["core", "margin"]),
                  lambda a: cpu.apply(a["core"], a["margin"], a.get("dry_run", False))),
    "cpu_reset": ("Restore the boot (BIOS) margin of every core ctune changed this boot (root, password prompt).",
                  _obj(), lambda a: cpu.reset()),
    "cpu_test": ("Per-core stability test, recorded in the journal. One self-checking stress-ng thread is pinned to each "
                 "core in turn (FFT, int64, matrix, wide vector, FMA, hash, then a bursty 25 % load), with idle gaps between "
                 "phases and 1 Hz per-core telemetry. Takes about seconds_per_core (default 360) per core. Returns "
                 "verdict green/red, red cores, per-phase clocks and voltages, and new corrected machine-check errors.",
                 _obj({"cores": {"type": "array", "items": {"type": "integer"}}, "seconds_per_core": {"type": "integer"},
                       "order": {"type": "string", "enum": ["sequential", "random"]}, "detach": {"type": "boolean"},
                       "note": S}),
                 lambda a: _run("cpu_test", cpu.test, a.get("detach", False), cores=a.get("cores"),
                                seconds_per_core=a.get("seconds_per_core", 360), order=a.get("order", "sequential"),
                                note=a.get("note", ""))),
    # suite
    "signoff": ("Multi-hour sign-off that ends a campaign, against every domain's current settings: ram (60 min), "
                "ycruncher (45), cpu per-core cycle (about 50), gpu (20), combined RAM + GPU + NVMe load (30), idle "
                "(30). Stops at the first red phase unless keep_going. Always runs detached: poll test_status; the "
                "result lands in the journal. minutes overrides per phase, e.g. {\"ram\": 120}.",
                _obj({"phases": {"type": "array", "items": {"type": "string", "enum": list(signoff.PHASES)}},
                      "minutes": {"type": "object"}, "keep_going": {"type": "boolean"}, "note": S}),
                lambda a: runner.start("signoff", phases=a.get("phases"), minutes=a.get("minutes"),
                                       keep_going=a.get("keep_going", False), note=a.get("note", ""))),
    "test_status": ("The test running now (domain, phase, elapsed minutes, log tail), or the last test result. Also "
                    "records a test that a crash or reboot cut short.", _obj(), lambda a: runner.status()),
}


def _reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, default=str) + "\n")
    sys.stdout.flush()


def handle(msg):
    method, id_, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if id_ is None:  # notification (notifications/initialized, cancelled, ...)
        return
    if method == "initialize":
        v = params.get("protocolVersion")
        _reply(id_, {"protocolVersion": v if v in VERSIONS else VERSIONS[0],
                     "capabilities": {"tools": {"listChanged": False}},
                     "serverInfo": {"name": "ctune", "version": "0.3.0"},
                     "instructions": INSTRUCTIONS})
    elif method == "ping":
        _reply(id_, {})
    elif method == "tools/list":
        _reply(id_, {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (d, s, _) in TOOLS.items()]})
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in TOOLS:
            return _reply(id_, error={"code": -32602, "message": f"unknown tool {name}"})
        try:
            out = TOOLS[name][2](args)
            text = out if isinstance(out, str) else json.dumps(out, indent=1, default=str)
            _reply(id_, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            _reply(id_, {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True})
    else:
        _reply(id_, error={"code": -32601, "message": f"method not found: {method}"})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _reply(None, error={"code": -32700, "message": "parse error"})
            continue
        for m in msg if isinstance(msg, list) else [msg]:
            handle(m)


if __name__ == "__main__":
    main()
