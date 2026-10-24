# memtune

Agent-assisted RAM and Infinity Fabric tuning for AMD Ryzen on Linux. An agent plans single-variable tuning
steps; memtune verifies what the BIOS actually applied, measures stability as corrected machine-check errors per
subsystem, and keeps a journal of the campaign. memtune only reads hardware: BIOS changes are made by a human.

Personal tool first (Ryzen 7 5800X3D, B550 AORUS PRO, DDR4). The layout is meant to grow into a public AM5 / DDR5 tool.

## What it reads
- **Timings, per channel**: straight from the memory controller over SMN, via `ryzen_smu` (ZenStates-Core register map).
- **Clocks and voltages**: FCLK/UCLK/MCLK, VSOC, VDDG, VDDP and PPT/TDC/EDC from the SMU pm_table. Measured VDIMM, VTT and VSOC from the Super I/O.
- **The whole BIOS configuration**: every setup option decoded by menu path, from the EFI variables plus the BIOS's own HII form database. Works on AMI Aptio boards.
- **Trained memory configuration**: ProcODT, RTT, CAD bus, MR0–MR6 and SPD/XMP from the APOB/AOD buffers. Needs one boot with `iomem=relaxed`.
- **Stability**: corrected machine-check errors per bank, grouped by subsystem (fabric, PCIe, memory controller, core). This is the WHEA equivalent, usable as an errors-per-hour signal.

## Use
```bash
bin/memtune status                 # platform, drivers, sensors, error total, journal tail
bin/memtune snapshot --label X     # full snapshot (root via pkexec)
bin/memtune compare                # previous vs latest: timings, voltages, every BIOS setting
bin/memtune bios ProcODT           # search decoded BIOS settings
bin/memtune errors                 # corrected MCEs by bank/subsystem
bin/memtune journal add plan "tRTP 14->8, tWR 26->16"
```
For agents: `.mcp.json` registers the MCP server (`memtune/mcp_server.py`, stdlib only) for this project, and
`.claude/skills/memtune/` holds the tuning-loop skill and its reference.

## Layout
- `memtune/core/`: platform-neutral pieces: journal, MCE error counters, sensors with board maps.
- `memtune/platforms/<platform>/`: collector + decoders per platform. `zen3_am4` is implemented; `zen4_am5` / `zen5_am5` are detected but not built yet.
- `memtune/api.py`: the single API behind both the CLI and the MCP server.
- `data/` (gitignored): snapshots and `journal.jsonl`.
- `docs/`: the original review and research notes with sources.

## Roadmap
1. Stress runner + post-boot service (snapshot → verify → screen → errors → journal), with a T3 webhook to wake the agent.
2. Passwordless, read-only root helper (polkit rule) instead of a pkexec prompt per snapshot.
3. Gigabyte profile-file generation for "Load Profile" (needs a sample profile saved to USB).
4. Refactor `platforms/zen3_am4` scripts into modules with a platform-neutral snapshot schema.
5. `zen4_am5`: DDR5 UMC map (ZenStates-Core DDR5 dictionary), pm_table 0x54xxxx, AM5 boards.
