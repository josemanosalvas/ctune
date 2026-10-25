# memtune

memtune helps an agent tune RAM and the Infinity Fabric on AMD Ryzen under Linux. The agent plans one change at a time. memtune checks what the BIOS actually applied, counts corrected machine-check errors per subsystem as the stability signal, and logs every step in a journal. memtune reads hardware, and a human makes every BIOS change.

It is a personal tool for a Ryzen 7 5800X3D on a B550 AORUS PRO with DDR4. The layout leaves room for an AM5 and DDR5 version later.

## What it reads
- Timings per channel, from the memory controller over SMN through `ryzen_smu`, decoded with the ZenStates-Core register map.
- FCLK, UCLK, MCLK, VSOC, VDDG, VDDP and the PPT/TDC/EDC limits, from the SMU pm_table. Measured VDIMM, VTT and VSOC come from the Super I/O chips.
- Every BIOS setup option, by menu path. The decoder reads the EFI variables and the BIOS's own HII form database, which works on AMI Aptio boards.
- The trained memory configuration (ProcODT, RTT, CAD bus, MR0 to MR6, SPD and XMP) from the APOB and AOD buffers. This needs one boot with the kernel argument `iomem=relaxed`.
- Corrected machine-check errors per bank, grouped as fabric, PCIe, memory controller or core. This is the Linux counterpart of WHEA errors, read as errors per hour.

## Use
```bash
bin/memtune status                 # platform, drivers, sensors, error total, journal tail
bin/memtune snapshot --label X     # full snapshot; asks for root through pkexec
bin/memtune compare                # previous vs latest: timings, voltages, every BIOS setting
bin/memtune bios ProcODT           # search the decoded BIOS settings
bin/memtune errors                 # corrected machine-check errors by bank and subsystem
bin/memtune journal add plan "tRTP 14 to 8, tWR 26 to 16"
```
Agents get the same commands as an MCP server (`memtune/mcp_server.py`, standard library only), registered for this project in `.mcp.json`. The tuning-loop skill and its reference live in `.claude/skills/memtune/`.

## Layout
- `memtune/core/` holds the platform-neutral parts: the journal, the machine-check counters, and sensors with per-board labels.
- `memtune/platforms/<platform>/` holds the collector and decoders for one platform. `zen3_am4` works. `zen4_am5` and `zen5_am5` are detected but not built.
- `memtune/api.py` is the one API behind the CLI and the MCP server.
- `data/` (gitignored) holds the snapshots and `journal.jsonl`.
- `docs/` holds the first review and the research notes with their sources.

## Roadmap
1. A stress runner and a post-boot service. After each boot it takes a snapshot, checks the planned values, runs a screen, reads the error counters and writes the journal. A T3 webhook then wakes the agent.
2. A read-only root helper allowed by a polkit rule, so a snapshot no longer needs a password prompt.
3. Gigabyte profile files the user loads with "Load Profile", instead of typing values. This needs one sample profile saved to USB.
4. Refactor the `platforms/zen3_am4` scripts into modules with a platform-neutral snapshot format.
5. `zen4_am5`, with the DDR5 register map from ZenStates-Core, the pm_table 0x54xxxx layouts and AM5 boards.
