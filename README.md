<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/lockup-animated-light.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/brand/lockup-animated-dark.svg">
    <img alt="ctune" src="docs/brand/lockup-animated.svg" width="440">
  </picture>
</h1>

<p align="center">Agent-driven RAM, CPU and GPU tuning for AMD Ryzen + NVIDIA on Linux.</p>

ctune helps an agent tune RAM, CPU and GPU on an AMD Ryzen + NVIDIA Linux system. The agent runs campaigns of single-variable steps. ctune applies or verifies each change, measures stability as corrected machine-check errors, stress-test verification errors and NVIDIA Xid errors, scores performance, and logs every step in a journal. The name is a nod to C'Thun, and the mascot is Claude Code's Clawd crossed with it.

It is a personal tool for a Ryzen 7 5800X3D on a B550 AORUS PRO with DDR4 and an RTX 3080 Ti. The layout leaves room for an AM5 and DDR5 version later.

## Domains
- **mem** reads, and never writes, the memory configuration. It decodes timings per channel from the memory controller (over SMN through `ryzen_smu`), SMU clocks and voltages, every BIOS setup option by menu path (from the EFI variables and the BIOS's HII form database), and, after one boot with `iomem=relaxed`, the trained ProcODT/RTT/CAD values and SPD. The user makes every BIOS change; ctune verifies it with a snapshot and a diff. Stability tests run stressapptest over the free RAM while memtest_vulkan heats the case, plus NVMe reads and an idle soak for fabric checks.
- **gpu** applies NVIDIA settings at runtime through NVML: core and memory clock offsets, a locked max core clock (the undervolt) and the power limit. A root helper checks every value against NVML's ranges and ctune's caps before writing. A reboot clears everything, except settings that passed the sign-off and were persisted: a root-owned boot unit re-applies those with nvidia_oc. Tests run gpu-burn in a podman container (cuBLAS results compared, so silent compute errors count; FP32, FP64, tensor cores, and load transients with the container frozen 1 s in every 10), Quake II RTX headless in gamescope for the RT cores, and memtest_vulkan (VRAM errors and bandwidth), with 1 Hz telemetry of clock, power, temperature and fan.
- **cpu** sets the Curve Optimizer margin of one core at a time at runtime, with SMU mailbox command MP1 0x35, and reads it back with 0x48. `docs/research/cpu-co-smu.md` has the sources. Before the first write in a boot, a read-only probe must read the BIOS margins back on every core. A root helper then allows -30 to +30 and at most 5 counts per change. A reboot restores the BIOS margins. Tests run Prime95 (mprime) the way CoreCycler does: one thread pinned to each core in turn, SSE Huge FFTs by default, suspended 1 s in every 10. ctune logs per-core clocks and voltages from the SMU pm_table.

Shared: corrected machine-check errors per bank and subsystem, sensors with per-board labels, the journal, and the test runner. Every test writes a crash marker, so a freeze or reboot during a test is recorded as red on the next boot. Long tests run detached as systemd user units (`systemd-run --user`) and report through `test-status`; `test-stop` stops one. `boot-check --install` adds a user unit that runs at every boot, records a test cut short by a crash, and keeps that boot's hardware-error lines. `signoff` is the multi-hour suite that ends a campaign: RAM, y-cruncher, the per-core cycle, the GPU, a combined load and an idle soak.

## Use
```bash
bin/ctune status                         # memory, CPU and GPU status, sensors, errors, journal tail
bin/ctune mem snapshot --label X         # full memory snapshot; asks for root through pkexec
bin/ctune mem compare                    # previous vs latest: timings, voltages, every BIOS setting
bin/ctune mem bios ProcODT               # search the decoded BIOS settings
bin/ctune mem test                       # 25 min RAM screen: stressapptest + GPU heat, verdict green/red
bin/ctune mem test fabric --detach       # 90 min + 20 min idle, in the background
bin/ctune cpu status                     # margin per core (BIOS or applied), clocks, voltage, temperature
bin/ctune cpu probe                      # read every core's margin through the SMU; asks for root
bin/ctune cpu apply --core 0 --margin -25 --dry-run   # guardrails and the mailbox word; drop --dry-run to apply
bin/ctune cpu test --cores 0             # mprime SSE Huge, about 6 min per core, verdict green/red
bin/ctune cpu reset                      # back to the BIOS margins
bin/ctune gpu status                     # clocks, offsets, power, what limits the clock
bin/ctune gpu apply --max-clock 1860 --core-offset 150   # asks for root; lasts until reboot
bin/ctune gpu burn-setup                 # once: build the gpu-burn image (podman, about 4 GB download)
bin/ctune gpu q2rtx-setup                # once: Quake II RTX and the Steam runtime it runs in (about 1.3 GB)
bin/ctune gpu test screen                # about 36 min: gpu-burn, Quake II RTX, memtest_vulkan; green/red/inconclusive
bin/ctune gpu reset
bin/ctune gpu persist --dry-run          # after a green sign-off: re-apply these settings at every boot
bin/ctune errors                         # corrected machine-check errors by bank and subsystem
bin/ctune signoff --detach               # about 5 h 30 min: ram, ycruncher, cpu, cpu_heavy, gpu, combined, idle
bin/ctune test-status                    # the running test and its phase, or the last result
bin/ctune test-stop                      # stop the running detached test
```
Agents get the same commands as an MCP server (`ctune/mcp_server.py`, standard library only), except the one-time setup commands `gpu burn-setup`, `gpu q2rtx-setup`, `gpu persist-setup`, `gpu persist-selftest` and `boot-check`, registered for this project in `.mcp.json`. The campaign skill and its references live in `.claude/skills/ctune/`.

## Layout
- `ctune/core/`: journal, machine-check counters, sensors, paths, the shared test runner (`stress.py`), the sign-off suite and the detached runner.
- `ctune/mem/`: memory API and platform collectors (`platforms/zen3_am4` works; `zen4_am5` and `zen5_am5` are detected but not built).
- `ctune/cpu/`: SMU mailbox and pm_table access, the guarded root helper, and the per-core test runner.
- `ctune/gpu/`: NVML binding (ctypes), the guarded root helper, the test runner, and persistence (`persist.py`, the pkexec installer `install.py`, and `boot_apply.py`, which is installed as a root-owned copy).
- `tests/`: offline tests (`python3 -B -m unittest discover -s tests`).
- `data/` (gitignored): snapshots, `journal.jsonl`, `cpu_state.json`, `gpu_state.json`.
- `tools/` (gitignored): memtest_vulkan v0.5.0 and vkpeak 20260527 from their GitHub releases, y-cruncher v0.8.7.9547 (static Linux build from its GitHub release), stressapptest v1.0.11 built from source (`./configure && make`, binary in `tools/stressapptest/`), mprime 30.19b20 from mersenne.org (EULA: never commit it), the gpu-burn source at a pinned commit (`ctune gpu burn-setup` builds the image), nvidia_oc 0.1.24 built from a pinned commit (`ctune gpu persist-setup`), and Quake II RTX 1.8.1 with the Steam Linux Runtime sniper it runs in (`ctune gpu q2rtx-setup`, SHA256 pinned). `docs/research/reuse-tools.md` and `docs/research/gpu-graphics-loads.md` have the sources.
- `ctune/mascot.py`: the mascot as pixel sprites, and the banner `ctune status` prints in a terminal.
- `docs/brand/`: logo, wordmark, icons, animation and social preview; `python3 docs/brand/build.py` regenerates them from `ctune/mascot.py` (needs Pillow).
- `docs/`: the first RAM review and the research notes with their sources.

## Roadmap
1. A read-only root helper allowed by a polkit rule, so steps need no password prompt, then an unattended loop: a post-boot or post-apply runner that tests, writes the journal, and wakes the agent through a T3 webhook.
2. Gigabyte profile files the user loads with "Load Profile" (needs one sample profile saved to USB), and a boot-time apply of the last green GPU settings.
3. A platform-neutral snapshot format, then `zen4_am5` with the DDR5 register map and AM5 boards (Zen 4 Curve Optimizer runs from -50 to +50 and needs its own probe).

## License
MIT, see [LICENSE](LICENSE).
