# ctune

ctune helps an agent tune RAM, GPU and (later) CPU on an AMD Ryzen + NVIDIA Linux system. The agent runs campaigns of single-variable steps. ctune applies or verifies each change, measures stability as corrected machine-check errors and NVIDIA Xid errors, scores performance, and logs every step in a journal. The name is a nod to C'Thun.

It is a personal tool for a Ryzen 7 5800X3D on a B550 AORUS PRO with DDR4 and an RTX 3080 Ti. The layout leaves room for an AM5 and DDR5 version later.

## Domains
- **mem** reads, and never writes, the memory configuration. It decodes timings per channel from the memory controller (over SMN through `ryzen_smu`), SMU clocks and voltages, every BIOS setup option by menu path (from the EFI variables and the BIOS's HII form database), and, after one boot with `iomem=relaxed`, the trained ProcODT/RTT/CAD values and SPD. The user makes every BIOS change; ctune verifies it with a snapshot and a diff.
- **gpu** applies NVIDIA settings at runtime through NVML: core and memory clock offsets, a locked max core clock (the undervolt) and the power limit. A root helper checks every value against NVML's ranges and ctune's caps before writing. A reboot clears everything. Tests run vkpeak (FP32 score and load) and memtest_vulkan (VRAM errors and bandwidth) with 1 Hz telemetry.
- **cpu** is planned: per-core Curve Optimizer at runtime through SMU commands, tested per core.

Shared: corrected machine-check errors per bank and subsystem, sensors with per-board labels, and the journal.

## Use
```bash
bin/ctune status                         # memory + GPU status, sensors, errors, journal tail
bin/ctune mem snapshot --label X         # full memory snapshot; asks for root through pkexec
bin/ctune mem compare                    # previous vs latest: timings, voltages, every BIOS setting
bin/ctune mem bios ProcODT               # search the decoded BIOS settings
bin/ctune gpu status                     # clocks, offsets, power, what limits the clock
bin/ctune gpu apply --max-clock 1860 --core-offset 150   # asks for root; lasts until reboot
bin/ctune gpu test screen                # vkpeak + memtest_vulkan with telemetry, verdict green/red
bin/ctune gpu reset
bin/ctune errors                         # corrected machine-check errors by bank and subsystem
```
Agents get the same commands as an MCP server (`ctune/mcp_server.py`, standard library only), registered for this project in `.mcp.json`. The campaign skill and its references live in `.claude/skills/ctune/`.

## Layout
- `ctune/core/`: journal, machine-check counters, sensors, paths.
- `ctune/mem/`: memory API and platform collectors (`platforms/zen3_am4` works; `zen4_am5` and `zen5_am5` are detected but not built).
- `ctune/gpu/`: NVML binding (ctypes), the guarded root helper, and the test runner.
- `data/` (gitignored): snapshots, `journal.jsonl`, `gpu_state.json`.
- `tools/` (gitignored): memtest_vulkan v0.5.0 and vkpeak 20260527 from their GitHub releases.
- `docs/`: the first RAM review and the research notes with their sources.

## Roadmap
1. cpu domain: runtime Curve Optimizer and per-core testing.
2. A read-only root helper allowed by a polkit rule, so steps need no password prompt, then an unattended loop: a post-boot or post-apply runner that tests, writes the journal, and wakes the agent through a T3 webhook.
3. A RAM stress runner, and Gigabyte profile files the user loads with "Load Profile" (needs one sample profile saved to USB).
4. A platform-neutral snapshot format, then `zen4_am5` with the DDR5 register map and AM5 boards.
