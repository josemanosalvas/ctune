#!/usr/bin/env python3
"""ctune MCP server, on the official MCP Python SDK, over stdio.

Each tool is a function: the SDK derives its input schema from the signature and validates the arguments, and the
docstring is the description the agent reads. The SDK lives in its own virtual environment, which `ctune mcp-setup`
creates. Register (project scope) with .mcp.json in the repo, or:  claude mcp add ctune -- <repo>/bin/ctune-mcp
"""
import functools
import inspect
import json
import os
import signal
import sys
import traceback
from typing import Any, Literal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.server.mcpserver.tools import Tool
except ModuleNotFoundError as e:
    if __name__ != "__main__":
        raise
    sys.exit(f"ctune: the MCP SDK is not installed for {sys.executable} ({e}). Run `bin/ctune mcp-setup`, and start "
             "the server with bin/ctune-mcp.")

from ctune.core import errors as core_errors, journal, runner  # noqa: E402
from ctune.core import sensors as core_sensors, signoff as core_signoff  # noqa: E402
from ctune.core.paths import DATA  # noqa: E402
from ctune.cpu import api as cpu  # noqa: E402
from ctune.gpu import api as gpu  # noqa: E402
from ctune.gpu import persist as gpu_persist  # noqa: E402
from ctune.mem import api as mem  # noqa: E402
from ctune.mem import test as memtest  # noqa: E402

VERSION = "0.4.0"
INSTRUCTIONS = (
    "ctune tunes RAM, CPU and GPU on a Linux system with an AMD Ryzen CPU and an NVIDIA GPU. mem_* tools read the "
    "memory and fabric configuration: timings from the memory controller, SMU clocks and voltages, and every BIOS "
    "option decoded from the EFI variables. The user makes every BIOS change. cpu_* tools read and set the per-core "
    "Curve Optimizer margin through the SMU at runtime and test cores one at a time. gpu_* tools read the GPU and "
    "apply clock offsets, a max-clock lock and a power limit at runtime. cpu and gpu settings stay within guardrails, "
    "and a reboot clears them. mem_test, cpu_test and gpu_test are the per-step stability tests, and signoff is the "
    "multi-hour suite. Long tests run detached, so poll test_status. errors, sensors and journal_* serve every domain."
)
STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
TOOLS = []


def _interrupt(signum, _frame):
    raise KeyboardInterrupt(signum)


def tool(fn):
    """Register fn as a tool and return it unchanged, so a test can call it directly.

    The call runs on the main thread, where a signal can reach it. When the client stops the server during an inline
    test, the signal unwinds the test, whose `finally` stops its loads, and the server then exits. An unknown argument
    is an error, so a misspelt one cannot fall back to its default. A ctune error reaches the agent with its text.
    """
    @functools.wraps(fn)
    async def call(**kw):
        before = {s: signal.signal(s, _interrupt) for s in STOP_SIGNALS}
        try:
            out = fn(**kw)
        except KeyboardInterrupt as stop:
            os._exit(128 + (stop.args[0] if stop.args else signal.SIGINT))
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            raise ToolError(f"{type(e).__name__}: {e}") from e
        finally:
            for s, handler in before.items():
                signal.signal(s, handler)
        return out if isinstance(out, str) else json.dumps(out, indent=1, default=str)

    made = Tool.from_function(call, description=" ".join(inspect.getdoc(fn).split()), structured_output=False)
    arguments = made.fn_metadata.arg_model
    arguments.model_config["extra"] = "forbid"
    arguments.model_rebuild(force=True)
    made.parameters = arguments.model_json_schema(by_alias=True)
    TOOLS.append(made)
    return fn


def _run(kind, fn, detach, /, **kw):
    """Long tests run detached (poll test_status); short ones can run inline."""
    return runner.start(kind, **kw) if detach else fn(**kw)


# shared

@tool
def errors(since: str = "-24h"):
    """Corrected machine-check errors per MCA bank since boot, grouped by subsystem (fabric, pcie/io, memory
    controller, core), plus kernel 'Hardware Error' lines. This is the Linux counterpart of WHEA 17/18/19."""
    return core_errors.summary(since)


@tool
def sensors():
    """Live hwmon readings with board labels: VDIMM, VSOC, VTT, fan RPM and PWM per header, CPU and GPU
    temperatures."""
    return core_sensors.read()


@tool
def journal_add(kind: Literal["plan", "applied", "result", "note"], text: str, data: dict[str, Any] | None = None):
    """Append to the tuning journal. kind is plan (an intended change), applied, result (a test outcome) or note. Set
    data.domain to mem, gpu or cpu, and put structured details in data."""
    return journal.add(DATA, kind, text=text, **(data or {}))


@tool
def journal_list(n: int = 20, kind: str | None = None):
    """Last n journal records, optionally filtered by kind."""
    return journal.tail(DATA, n, kind)


# memory

@tool
def mem_status():
    """Platform, BIOS, drivers, live sensors, the error total, the latest snapshots and the journal tail."""
    return mem.status()


@tool
def mem_snapshot(label: str = "", skip_devmem: bool = False):
    """Take a full memory snapshot: timings per channel, FCLK, UCLK and MCLK, voltages, DIMMs, decoded BIOS settings
    and the platform fingerprint. It needs root. Once `ctune passwordless` is installed it runs without a prompt;
    otherwise the user approves a password prompt."""
    return mem.snapshot(label, skip_devmem)


@tool
def mem_list_snapshots():
    """List snapshot directory names (oldest first)."""
    return mem.snapshots()


@tool
def mem_show_snapshot(name: str = "latest"):
    """Markdown summary of a snapshot ('latest', 'previous', or a name)."""
    return mem.summary(name)


@tool
def mem_compare(old: str = "previous", new: str = "latest"):
    """Diff two snapshots: timings, raw registers, clocks, voltages, VTT and VDIMM, the platform fingerprint, every
    BIOS setting by menu path, and the AOD and APOB values. The default compares the previous snapshot with the
    latest."""
    return mem.compare(old, new)


@tool
def mem_verify(name: str = "latest"):
    """Check a snapshot's BIOS configuration against the running hardware (timings, speed, FCLK, voltages, PBO
    limits). Writes verify.md into the snapshot."""
    return mem.verify(name)


@tool
def mem_checklist(name: str = "latest"):
    """Re-entry checklist for after a BIOS flash: every visible setting that differs from its default, by menu. Writes
    reentry_checklist.md into the snapshot."""
    return mem.checklist(name)


@tool
def mem_test(kind: Literal["screen", "fabric", "tm5"] = "screen", minutes: float | None = None,
             cycles: int | None = None, gpu_heat: bool = True, detach: bool = True, note: str = ""):
    """RAM stability test, recorded in the journal. screen (25 min) runs stressapptest -W over the free RAM while
    memtest_vulkan heats the case. fabric (90 min, then 20 min idle) runs the screen with NVMe reads, then an idle
    soak, for FCLK steps and after a BIOS flash. It runs detached by default, so poll test_status. tm5 runs TestMem5
    with the anta777 Extreme1 profile under Proton, with the same GPU heat load. It runs its 3 cycles as long as they
    take (cycles sets fewer for a quick check that is not a full pass), and minutes only caps it (default 240). It
    needs `ctune mem tm5-setup` once and is inconclusive when it cannot start or runs out of memory, and red when a TM5
    message box stops it mid-test. It stops at TM5's first error, since one error makes it red. The verdict is red on
    stressapptest or TM5 errors, new corrected machine-check errors (umc points at memory, fabric at FCLK, VDDG or
    VSOC), VRAM or Xid errors, or a crash."""
    return _run("mem_test", memtest.test, detach, kind=kind, minutes=minutes, gpu_heat=gpu_heat, note=note,
                cycles=cycles)


@tool
def mem_bios_search(query: str, snapshot: str = "latest", include_hidden: bool = False):
    """Search the decoded BIOS settings by menu path or prompt text, for example 'ProcODT', 'VCORE SOC'."""
    return mem.bios_search(query, snapshot, include_hidden=include_hidden)


# gpu

@tool
def gpu_status():
    """GPU clocks, offsets with their allowed ranges, power draw and limits, temperature, what currently limits the
    clock (power cap, temperature and so on), the settings ctune applied this boot, and the Xid error count."""
    return gpu.status()


@tool
def gpu_apply(core_offset: int | None = None, mem_offset: int | None = None, max_clock: int | None = None,
              power_limit: float | None = None):
    """Apply GPU settings at runtime, as root: with no prompt once `ctune passwordless` is installed, otherwise with a
    password prompt. core_offset and mem_offset are in MHz (NVML units; the memory offset is half of what
    nvidia-settings shows). max_clock locks the core clock to the range 0 to max_clock MHz, and with a positive
    core_offset this is the undervolt. power_limit is in W. ctune rejects out-of-range values."""
    return gpu.apply(core_offset, mem_offset, max_clock, power_limit)


@tool
def gpu_reset():
    """Reset GPU tuning to offsets 0, unlocked clocks and the default power limit, as root: with no prompt once
    `ctune passwordless` is installed, otherwise with a password prompt."""
    return gpu.reset()


@tool
def gpu_test(kind: Literal[*gpu.KINDS] | None = None, burn_seconds: int | None = None,
             fp64_seconds: int | None = None, tc_seconds: int | None = None, transient_seconds: int | None = None,
             graphics_seconds: int | None = None, raster_seconds: int | None = None,
             tessellation_seconds: int | None = None, vram_seconds: int | None = None, core_runs: int | None = None,
             detach: bool = True, note: str | None = None):
    """GPU stability test with telemetry once a second, recorded in the journal. kind core runs gpu-burn, which
    compares the results of cuBLAS matrix products, so silent compute errors count. It runs FP32 for burn_seconds
    (default 600, at the power cap; this is the score), FP64 for fp64_seconds (default 600, at the lock below the cap;
    compare heat and fan here), tensor cores for tc_seconds (default 120), then FP32 frozen 1 s in every 10 for
    transient_seconds (default 180), so the load keeps stepping between idle and full at the top clock. graphics runs
    Quake II RTX headless for graphics_seconds (default 300). It loads the RT cores at the lock at high power, but
    nothing it renders is checked, so it is red only on a crash, a lost device, a hang or an Xid error. raster runs
    Unigine Superposition headless for raster_seconds (default 300): OpenGL raster, the ROPs and texture units, the
    load closest to most games. tessellation runs Unigine Heaven for tessellation_seconds (default 300) at extreme
    tessellation and 8x MSAA. Neither checks what it draws. vram runs memtest_vulkan. screen runs core, graphics,
    raster, tessellation and vram (about 44 min, detached by default; poll test_status). vkpeak gives only the old
    throughput score. 0 seconds skips a part. The test stops at 83 °C. It returns the verdict (green, red or
    inconclusive), Gflop/s, clock_stretch (GFLOPS per MHz against the last green result at the same lock), per-part
    at_lock_share, power, temperature and fan (average, maximum, and *_end over the last 60 s), and new Xid errors."""
    # the defaults live in gpu.test only
    given = {k: v for k, v in locals().items() if k != "detach" and v is not None}
    return _run("gpu_test", gpu.test, detach, **given)


@tool
def gpu_persist(dry_run: bool = False, reset_guard: bool = False):
    """Re-apply the GPU settings applied now at every boot and after resume (as root, with a password prompt). It
    refuses unless the latest sign-off with exactly these settings, on this GPU and driver, was green. A root-owned
    boot unit applies them with nvidia_oc; it skips them after a driver update or two boots in a row in which the GPU
    failed (a crash within 10 minutes, or an Xid error). dry_run shows the config or the reasons; reset_guard only
    clears a tripped guard."""
    return gpu_persist.reset_guard() if reset_guard else gpu_persist.persist(dry_run)


@tool
def gpu_unpersist(uninstall: bool = False):
    """Stop re-applying GPU settings at boot (as root, with a password prompt). Settings applied now stay until
    reboot. uninstall also removes the units and the root-owned copies."""
    return gpu_persist.unpersist(uninstall)


@tool
def gpu_xid(since: str = "boot"):
    """NVIDIA Xid errors from the kernel log ('boot' or a journalctl --since value)."""
    return gpu.xid_errors(since)


# cpu

@tool
def cpu_status():
    """Curve Optimizer margin per core (from the BIOS snapshot, or what ctune applied this boot), per-core clock,
    effective clock, voltage, power, temperature and C0 from the SMU pm_table, package power, core machine-check
    counts per CPU, this boot's probe result, and any test cut short by a crash."""
    return cpu.status()


@tool
def cpu_probe():
    """Read every core's margin through the SMU (read-only, as root, with no prompt once `ctune passwordless` is
    installed) and compare it with the BIOS values from the latest mem snapshot. cpu_apply refuses to write until a
    probe in the same boot matches, so run it once per boot before the first cpu_apply."""
    return cpu.probe()


@tool
def cpu_apply(core: int, margin: int, dry_run: bool = False):
    """Set one core's Curve Optimizer margin at runtime (as root, with no prompt once `ctune passwordless` is
    installed), then read it back. margin is -30 to 30 and at most 5 away from the core's current value. dry_run
    checks the guardrails and shows the SMU mailbox word without sending anything. The margin lasts until reboot."""
    return cpu.apply(core, margin, dry_run)


@tool
def cpu_reset():
    """Restore the boot (BIOS) margin of every core ctune changed this boot (as root, with no prompt once
    `ctune passwordless` is installed)."""
    return cpu.reset()


@tool
def cpu_test(cores: list[int] | None = None, seconds_per_core: int = 360,
             order: Literal["sequential", "random"] = "sequential", workload: Literal[*cpu.WORKLOADS] = "sse-huge",
             full_cycle: bool = False, detach: bool = True, note: str = ""):
    """Per-core stability test, recorded in the journal. Prime95 (mprime) runs one thread pinned to each core in
    turn, suspended 1 s in every 10 so the core keeps leaving and re-entering boost, as CoreCycler does. workload is
    sse-huge (the default, with the least heat and the highest boost), avx2-1344k (heavy), sse-heavyshort,
    avx2-moderate, sse-all, or stress-ng (a mix of stress-ng stressors). seconds_per_core defaults to 360, and
    full_cycle runs each core until every FFT length has passed once (up to 40 min). It returns the verdict (green or
    red), the red cores, the passed and failed FFT sizes, per-phase clocks and voltages, and new corrected
    machine-check errors."""
    return _run("cpu_test", cpu.test, detach, cores=cores, seconds_per_core=seconds_per_core, order=order,
                workload=workload, full_cycle=full_cycle, note=note)


# suite

@tool
def signoff(phases: list[Literal[*core_signoff.PHASES]] | None = None, minutes: dict[str, float] | None = None,
            keep_going: bool = False, note: str = ""):
    """Multi-hour sign-off that ends a campaign, against every domain's current settings: ram (60 min), tm5 (TestMem5
    anta777 Extreme1, 3 cycles, capped at 240), ycruncher (45), cpu per-core cycle (about 50), gpu (90, including
    Quake II RTX), combined RAM, GPU and NVMe load (30), idle (30). cpu_heavy (25) runs mprime AVX2 per core after the
    SSE cpu phase. It stops at the first red phase unless keep_going is set. It always runs detached, so poll
    test_status, and the result goes into the journal. minutes overrides the length per phase, for example
    {"ram": 120}."""
    return runner.start("signoff", phases=phases, minutes=minutes, keep_going=keep_going, note=note)


@tool
def test_status():
    """The test running now (domain, phase, elapsed minutes, log tail), failed runs, and the last test result. Also
    records a test that a crash or reboot cut short."""
    return runner.status()


@tool
def test_stop():
    """Stop the running detached test. The test gets SIGINT and stops its loads; systemd kills whatever is left after
    90 s. The journal records that it was stopped."""
    return runner.stop()


def main():
    MCPServer("ctune", instructions=INSTRUCTIONS, version=VERSION, tools=TOOLS, log_level="WARNING").run()


if __name__ == "__main__":
    main()
