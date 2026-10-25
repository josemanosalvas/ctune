#!/usr/bin/env python3
"""ctune MCP server: stdio transport, newline-delimited JSON-RPC 2.0, standard library only.

Register (project scope) with .mcp.json in the repo, or:  claude mcp add ctune -- python3 -I <repo>/ctune/mcp_server.py
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from ctune.core import errors as core_errors, journal, sensors  # noqa: E402
from ctune.core.paths import DATA  # noqa: E402
from ctune.gpu import api as gpu  # noqa: E402
from ctune.mem import api as mem  # noqa: E402

VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "ctune tunes RAM, GPU and (later) CPU on an AMD Ryzen + NVIDIA Linux system. mem_* tools read the memory and "
    "fabric configuration (timings from the memory controller, SMU clocks and voltages, every BIOS option decoded from "
    "the EFI variables); the user makes every BIOS change. gpu_* tools read the GPU and apply clock offsets, a max-clock "
    "lock and a power limit at runtime within guardrails; a reboot clears them. errors, sensors and journal_* are shared."
)


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
                       "core_runs": {"type": "integer"}, "note": S}),
                 lambda a: gpu.test(a.get("kind", "screen"), a.get("vram_seconds", 300), a.get("core_runs", 3),
                                    note=a.get("note", ""))),
    "gpu_xid": ("NVIDIA Xid errors from the kernel log ('boot' or a journalctl --since value).",
                _obj({"since": S}), lambda a: gpu.xid_errors(a.get("since", "boot"))),
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
                     "serverInfo": {"name": "ctune", "version": "0.2.0"},
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
