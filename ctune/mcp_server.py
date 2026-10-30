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
from ctune.gpu import persist as gpu_persist  # noqa: E402
from ctune.mem import api as mem  # noqa: E402
from ctune.mem import test as memtest  # noqa: E402

VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "ctune tunes RAM, CPU and GPU on a Linux system with an AMD Ryzen CPU and an NVIDIA GPU. mem_* tools read the "
    "memory and fabric configuration: timings from the memory controller, SMU clocks and voltages, and every BIOS "
    "option decoded from the EFI variables. The user makes every BIOS change. cpu_* tools read and set the per-core "
    "Curve Optimizer margin through the SMU at runtime and test cores one at a time. gpu_* tools read the GPU and apply "
    "clock offsets, a max-clock lock and a power limit at runtime. cpu and gpu settings stay within guardrails, and a "
    "reboot clears them. mem_test, cpu_test and gpu_test are the per-step stability tests, and signoff is the "
    "multi-hour suite. Long tests run detached, so poll test_status. errors, sensors and journal_* serve every domain."
)


def _run(kind, fn, detach, /, **kw):
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
    "mem_snapshot": ("Take a full memory snapshot: timings per channel, FCLK, UCLK and MCLK, voltages, DIMMs, decoded BIOS "
                     "settings and the platform fingerprint. It needs root. Once `ctune passwordless` is installed it "
                     "runs without a prompt; otherwise the user approves a password prompt.",
                     _obj({"label": S, "skip_devmem": {"type": "boolean"}}),
                     lambda a: mem.snapshot(a.get("label", ""), a.get("skip_devmem", False))),
    "mem_list_snapshots": ("List snapshot directory names (oldest first).", _obj(), lambda a: mem.snapshots()),
    "mem_show_snapshot": ("Markdown summary of a snapshot ('latest', 'previous', or a name).",
                          _obj({"name": S}), lambda a: mem.summary(a.get("name", "latest"))),
    "mem_compare": ("Diff two snapshots: timings, raw registers, clocks, voltages, VTT and VDIMM, the platform "
                    "fingerprint, every BIOS setting by menu path, and the AOD and APOB values. The default compares the previous "
                    "snapshot with the latest.",
                    _obj({"old": S, "new": S}), lambda a: mem.compare(a.get("old", "previous"), a.get("new", "latest"))),
    "mem_verify": ("Check a snapshot's BIOS configuration against the running hardware (timings, speed, FCLK, voltages, "
                   "PBO limits). Writes verify.md into the snapshot.", _obj({"name": S}), lambda a: mem.verify(a.get("name", "latest"))),
    "mem_checklist": ("Re-entry checklist for after a BIOS flash: every visible setting that differs from its default, by "
                      "menu. Writes reentry_checklist.md into the snapshot.", _obj({"name": S}),
                      lambda a: mem.checklist(a.get("name", "latest"))),
    "mem_test": ("RAM stability test, recorded in the journal. screen (25 min) runs stressapptest -W over the free RAM "
                 "while memtest_vulkan heats the case. fabric (90 min, then 20 min idle) runs the screen with NVMe "
                 "reads, then an idle soak, for FCLK steps and after a BIOS flash. It runs detached by default, so poll "
                 "test_status. tm5 runs TestMem5 with the anta777 Extreme1 profile under Proton (3 cycles, as long "
                 "as they take; minutes only caps it, default 240), with the same GPU heat load; it needs `ctune mem "
                 "tm5-setup` once and is inconclusive when it cannot start or runs out of memory. The verdict is red on "
                 "stressapptest or TM5 errors, new corrected machine-check errors (umc points at memory, fabric at "
                 "FCLK, VDDG or VSOC), VRAM or Xid errors, or a crash.",
                 _obj({"kind": {"type": "string", "enum": ["screen", "fabric", "tm5"]}, "minutes": {"type": "number"},
                       "gpu_heat": {"type": "boolean"}, "detach": {"type": "boolean"}, "note": S}),
                 lambda a: _run("mem_test", memtest.test, a.get("detach", True), kind=a.get("kind", "screen"),
                                minutes=a.get("minutes"), gpu_heat=a.get("gpu_heat", True), note=a.get("note", ""))),
    "mem_bios_search": ("Search the decoded BIOS settings by menu path or prompt text, for example 'ProcODT', 'VCORE SOC'.",
                        _obj({"query": S, "snapshot": S, "include_hidden": {"type": "boolean"}}, ["query"]),
                        lambda a: mem.bios_search(a["query"], a.get("snapshot", "latest"),
                                                  include_hidden=a.get("include_hidden", False))),
    # gpu
    "gpu_status": ("GPU clocks, offsets with their allowed ranges, power draw and limits, temperature, what currently "
                   "limits the clock (power cap, temperature and so on), the settings ctune applied this boot, and the Xid error count.",
                   _obj(), lambda a: gpu.status()),
    "gpu_apply": ("Apply GPU settings at runtime, as root: with no prompt once `ctune passwordless` is installed, otherwise "
                  "with a password prompt. core_offset and mem_offset are in MHz (NVML units; the memory offset is half "
                  "of what nvidia-settings shows). max_clock locks the core clock to the range 0 to max_clock MHz, and "
                  "with a positive core_offset this is the undervolt. power_limit is in W. ctune rejects out-of-range "
                  "values.",
                  _obj({"core_offset": {"type": "integer"}, "mem_offset": {"type": "integer"},
                        "max_clock": {"type": "integer"}, "power_limit": {"type": "number"}}),
                  lambda a: gpu.apply(a.get("core_offset"), a.get("mem_offset"), a.get("max_clock"), a.get("power_limit"))),
    "gpu_reset": ("Reset GPU tuning to offsets 0, unlocked clocks and the default power limit, as root: with no prompt once "
                  "`ctune passwordless` is installed, otherwise with a password prompt.",
                  _obj(), lambda a: gpu.reset()),
    "gpu_test": ("GPU stability test with telemetry once a second, recorded in the journal. kind core runs gpu-burn, "
                 "which compares the results of cuBLAS matrix products, so silent compute errors count. It runs FP32 "
                 "for burn_seconds (default 600, at the power cap; this is the score), FP64 for fp64_seconds (default "
                 "600, at the lock below the cap; compare heat and fan here), tensor cores for tc_seconds (default "
                 "120), then FP32 frozen 1 s in every 10 for transient_seconds (default 180), so the load keeps "
                 "stepping between idle and full at the top clock. graphics runs Quake II RTX headless for "
                 "graphics_seconds (default 300). It loads the RT cores at the lock at high power, but nothing it "
                 "renders is checked, so it is red only on a crash, a lost device, a hang or an Xid error. raster "
                 "runs Unigine Superposition headless for raster_seconds (default 300): OpenGL raster, the ROPs and "
                 "texture units, the load closest to most games. tessellation runs Unigine Heaven for "
                 "tessellation_seconds (default 300) at extreme tessellation and 8x MSAA. Neither checks what it "
                 "draws. vram runs memtest_vulkan. screen runs core, graphics, raster, tessellation and vram (about "
                 "44 min, detached by default; poll "
                 "test_status). vkpeak gives only the old throughput score. 0 seconds skips a part. The test stops at "
                 "83 °C. It returns the verdict (green, red or inconclusive), Gflop/s, clock_stretch (GFLOPS per MHz "
                 "against the last green result at the same lock), per-part at_lock_share, power, temperature and fan "
                 "(average, maximum, and *_end over the last 60 s), and new Xid errors.",
                 _obj({"kind": {"type": "string", "enum": list(gpu.KINDS)},
                       "burn_seconds": {"type": "integer"}, "fp64_seconds": {"type": "integer"},
                       "tc_seconds": {"type": "integer"}, "transient_seconds": {"type": "integer"},
                       "graphics_seconds": {"type": "integer"}, "raster_seconds": {"type": "integer"},
                       "tessellation_seconds": {"type": "integer"},
                       "vram_seconds": {"type": "integer"},
                       "core_runs": {"type": "integer"}, "detach": {"type": "boolean"}, "note": S}),
                 # the defaults live in gpu.test only
                 lambda a: _run("gpu_test", gpu.test, a.get("detach", True),
                                **{k: v for k, v in a.items() if k != "detach"})),
    "gpu_persist": ("Re-apply the GPU settings applied now at every boot and after resume (as root, with a password prompt). "
                    "It refuses unless the latest sign-off with exactly these settings, on this GPU and driver, was green. "
                    "A root-owned boot unit applies them with nvidia_oc; it skips them after a driver update or two "
                    "boots in a row in which the GPU failed (a crash within 10 minutes, or an Xid error). dry_run shows the "
                    "config or the reasons; reset_guard only clears a tripped guard.",
                    _obj({"dry_run": {"type": "boolean"}, "reset_guard": {"type": "boolean"}}),
                    lambda a: gpu_persist.reset_guard() if a.get("reset_guard")
                    else gpu_persist.persist(a.get("dry_run", False))),
    "gpu_unpersist": ("Stop re-applying GPU settings at boot (as root, with a password prompt). Settings applied now stay until "
                      "reboot. uninstall also removes the units and the root-owned copies.",
                      _obj({"uninstall": {"type": "boolean"}}), lambda a: gpu_persist.unpersist(a.get("uninstall", False))),
    "gpu_xid": ("NVIDIA Xid errors from the kernel log ('boot' or a journalctl --since value).",
                _obj({"since": S}), lambda a: gpu.xid_errors(a.get("since", "boot"))),
    # cpu
    "cpu_status": ("Curve Optimizer margin per core (from the BIOS snapshot, or what ctune applied this boot), per-core "
                   "clock, effective clock, voltage, power, temperature and C0 from the SMU pm_table, package power, "
                   "core machine-check counts per CPU, this boot's probe result, and any test cut short by a crash.",
                   _obj(), lambda a: cpu.status()),
    "cpu_probe": ("Read every core's margin through the SMU (read-only, as root, with no prompt once `ctune passwordless` is "
                  "installed) and compare it with the "
                  "BIOS values from the latest mem snapshot. cpu_apply refuses to write until a probe in the same "
                  "boot matches, so run it once per boot before the first cpu_apply.",
                  _obj(), lambda a: cpu.probe()),
    "cpu_apply": ("Set one core's Curve Optimizer margin at runtime (as root, with no prompt once `ctune passwordless` is "
                  "installed), then read it back. margin "
                  "is -30 to 30 and at most 5 away from the core's current value. dry_run checks the guardrails and "
                  "shows the SMU mailbox word without sending anything. The margin lasts until reboot.",
                  _obj({"core": {"type": "integer"}, "margin": {"type": "integer"}, "dry_run": {"type": "boolean"}},
                       ["core", "margin"]),
                  lambda a: cpu.apply(a["core"], a["margin"], a.get("dry_run", False))),
    "cpu_reset": ("Restore the boot (BIOS) margin of every core ctune changed this boot (as root, with no prompt once "
                  "`ctune passwordless` is installed).",
                  _obj(), lambda a: cpu.reset()),
    "cpu_test": ("Per-core stability test, recorded in the journal. Prime95 (mprime) runs one thread pinned to each "
                 "core in turn, suspended 1 s in every 10 so the core keeps leaving and re-entering boost, as "
                 "CoreCycler does. workload is sse-huge (the default, with the least heat and the highest boost), "
                 "avx2-1344k (heavy), sse-heavyshort, avx2-moderate, sse-all, or stress-ng (a mix of stress-ng "
                 "stressors). seconds_per_core defaults to 360, and full_cycle runs each core until every FFT length "
                 "has passed once (up to 40 min). It returns the verdict (green or red), the red cores, the passed and "
                 "failed FFT sizes, per-phase clocks and voltages, and new corrected machine-check errors.",
                 _obj({"cores": {"type": "array", "items": {"type": "integer"}}, "seconds_per_core": {"type": "integer"},
                       "order": {"type": "string", "enum": ["sequential", "random"]},
                       "workload": {"type": "string", "enum": cpu.WORKLOADS}, "full_cycle": {"type": "boolean"},
                       "detach": {"type": "boolean"}, "note": S}),
                 lambda a: _run("cpu_test", cpu.test, a.get("detach", True), cores=a.get("cores"),
                                seconds_per_core=a.get("seconds_per_core", 360), order=a.get("order", "sequential"),
                                workload=a.get("workload", "sse-huge"), full_cycle=a.get("full_cycle", False),
                                note=a.get("note", ""))),
    # suite
    "signoff": ("Multi-hour sign-off that ends a campaign, against every domain's current settings: ram (60 min), "
                "tm5 (TestMem5 anta777 Extreme1, 3 cycles, capped at 240), ycruncher (45), cpu per-core cycle "
                "(about 50), gpu (90, including Quake II RTX), combined RAM, GPU and NVMe load (30), idle (30). cpu_heavy (25) runs mprime AVX2 per core after the SSE cpu phase. It "
                "stops at the first red phase unless keep_going is set. It always runs detached, so poll test_status, "
                "and the result goes into the journal. minutes overrides the length per phase, for example "
                "{\"ram\": 120}.",
                _obj({"phases": {"type": "array", "items": {"type": "string", "enum": list(signoff.PHASES)}},
                      "minutes": {"type": "object"}, "keep_going": {"type": "boolean"}, "note": S}),
                lambda a: runner.start("signoff", phases=a.get("phases"), minutes=a.get("minutes"),
                                       keep_going=a.get("keep_going", False), note=a.get("note", ""))),
    "test_status": ("The test running now (domain, phase, elapsed minutes, log tail), failed runs, and the last test "
                    "result. Also records a test that a crash or reboot cut short.", _obj(), lambda a: runner.status()),
    "test_stop": ("Stop the running detached test. The test gets SIGINT and stops its loads; systemd kills whatever is "
                  "left after 90 s. The journal records that it was stopped.", _obj(), lambda a: runner.stop()),
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
    import signal

    def raise_interrupt(*_):   # on SIGTERM or SIGHUP from the client, a running inline test's finally stops its loads
        raise KeyboardInterrupt
    for s in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, raise_interrupt)
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
