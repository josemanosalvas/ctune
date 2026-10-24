#!/usr/bin/env python3
"""memtune MCP server: stdio transport, newline-delimited JSON-RPC 2.0, standard library only.

Register (project scope) with .mcp.json in the repo, or:  claude mcp add memtune -- python3 -I <repo>/memtune/mcp_server.py
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from memtune import api  # noqa: E402

VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "memtune reads the live memory/fabric configuration of an AMD Ryzen system (timings from the memory "
    "controller, clocks/voltages from the SMU, every BIOS setup option decoded from EFI variables), sensors, "
    "and corrected machine-check error counters, and keeps a tuning journal. It never changes hardware settings: "
    "BIOS changes are made by the user. 'snapshot' needs root and shows a polkit password prompt on the desktop."
)


def _obj(props=None, required=None):
    return {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}


S = {"type": "string"}
TOOLS = {
    "status": ("Platform, BIOS, drivers, live sensors (VDIMM/VSOC/VTT, fans per header, temps), error total, "
               "latest snapshots and journal tail.", _obj(), lambda a: api.status()),
    "snapshot": ("Take a full snapshot (timings per channel, FCLK/UCLK/MCLK, voltages, DIMMs, decoded BIOS settings, "
                 "platform fingerprint). Root via pkexec: the user must approve a password prompt. Returns the summary.",
                 _obj({"label": S, "skip_devmem": {"type": "boolean"}}),
                 lambda a: api.snapshot(a.get("label", ""), a.get("skip_devmem", False))),
    "list_snapshots": ("List snapshot directory names (oldest first).", _obj(), lambda a: api.snapshots()),
    "show_snapshot": ("Markdown summary of a snapshot ('latest', 'previous', or a name).",
                      _obj({"name": S}), lambda a: api.summary(a.get("name", "latest"))),
    "compare": ("Diff two snapshots: timings, raw registers, clocks/voltages, VTT/VDIMM, platform fingerprint, every "
                "BIOS setting by menu path, AOD/APOB. Defaults: previous vs latest.",
                _obj({"old": S, "new": S}), lambda a: api.compare(a.get("old", "previous"), a.get("new", "latest"))),
    "bios_search": ("Search decoded BIOS settings by menu path/prompt text, e.g. 'ProcODT', 'VCORE SOC', 'SYS_FAN4'.",
                    _obj({"query": S, "snapshot": S, "include_hidden": {"type": "boolean"}}, ["query"]),
                    lambda a: api.bios_search(a["query"], a.get("snapshot", "latest"),
                                              include_hidden=a.get("include_hidden", False))),
    "sensors": ("Live hwmon readings with board labels: VDIMM, VSOC, VTT, fan RPM/PWM per header, CPU/GPU temps.",
                _obj(), lambda a: api.sensors()),
    "errors": ("Corrected machine-check errors per MCA bank since boot, grouped by subsystem (fabric, pcie/io, memory "
               "controller, core), plus kernel 'Hardware Error' lines. The Linux equivalent of WHEA 17/18/19.",
               _obj({"since": S}), lambda a: api.errors(a.get("since", "-24h"))),
    "journal_add": ("Append to the tuning journal. kind: plan (intended BIOS change), applied, result (test outcome), "
                    "note. Put structured details in data.",
                    _obj({"kind": {"type": "string", "enum": ["plan", "applied", "result", "note"]}, "text": S,
                          "data": {"type": "object"}}, ["kind", "text"]),
                    lambda a: api.journal_add(a["kind"], text=a["text"], **(a.get("data") or {}))),
    "journal_list": ("Last n journal records, optionally filtered by kind.",
                     _obj({"n": {"type": "integer"}, "kind": S}), lambda a: api.journal_list(a.get("n", 20), a.get("kind"))),
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
                     "serverInfo": {"name": "memtune", "version": "0.1.0"},
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
