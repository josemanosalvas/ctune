<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/lockup-animated-light.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/brand/lockup-animated-dark.svg">
    <img alt="ctune" src="docs/brand/lockup-animated.svg" width="440">
  </picture>
</h1>

<p align="center">Agent-driven RAM, CPU and GPU tuning for AMD Ryzen and NVIDIA on Linux.</p>

ctune helps an agent tune RAM, CPU and GPU on a Linux system with an AMD Ryzen CPU and an NVIDIA GPU. The agent runs campaigns of single-variable steps. For each step, ctune applies or verifies the change and measures stability as corrected machine-check errors, stress-test verification errors and NVIDIA Xid errors. It also scores performance and logs the step in a journal. The name comes from C'Thun, and the mascot is Claude Code's Clawd crossed with it.

ctune supports AMD Zen 3 on AM4 with DDR4 and NVIDIA GPUs through NVML. Its BIOS setup decoder covers the Gigabyte B550 AORUS PRO. ctune is tested on a Ryzen 7 5800X3D, a B550 AORUS PRO rev 1.0 with DDR4 and an RTX 3080 Ti, on Bazzite. Other Zen 3 boards read timings and SMU values the same way, but their BIOS menus need their own decoder keys. Each CPU platform has its own package under `ctune/mem/platforms/`, so AM5 and DDR5 can be added later.

## Domains
- **mem** reads the memory configuration and never writes it. It decodes the per-channel timings from the memory controller over SMN through `ryzen_smu`, and reads the SMU clocks and voltages. It decodes every BIOS setup option by menu path, from the EFI variables and the BIOS's HII form database. After one boot with `iomem=relaxed`, it also reads the trained ProcODT, RTT and CAD values and the SPD. The user makes every BIOS change, and ctune verifies it with a snapshot and a diff. The stability test runs stressapptest over the free RAM while memtest_vulkan heats the case. The fabric test adds NVMe reads and an idle soak. The tm5 test runs TestMem5 with the anta777 Extreme1 profile under Proton, in a systemd scope with swap off, and reads its verdict from TM5's log.
- **gpu** applies NVIDIA settings at runtime through NVML: core and memory clock offsets, a locked maximum core clock (the undervolt) and the power limit. A root helper checks every value against NVML's ranges and ctune's caps before it writes. A reboot clears these settings. Settings that passed the sign-off can be persisted, and a root-owned boot unit then re-applies them with nvidia_oc at every boot. The GPU test has three loads, and ctune records clock, power, temperature and fan once a second during all of them:
  - gpu-burn in a podman container. It compares cuBLAS results, so a silent compute error counts as a failure. It runs FP32, FP64 and tensor-core phases, and a load-transient phase that freezes the container for 1 s in every 10.
  - Quake II RTX, headless in gamescope, to load the RT cores.
  - memtest_vulkan, for VRAM errors and bandwidth.
- **cpu** sets the Curve Optimizer margin of one core at a time at runtime with SMU mailbox command MP1 0x35, and reads it back with 0x48. `docs/research/cpu-co-smu.md` has the sources. Before the first write in a boot, a read-only probe must read the BIOS margins back on every core. The root helper then allows margins from -30 to +30 and changes of at most 5 counts. A reboot restores the BIOS margins. The CPU test runs Prime95 (mprime) the way CoreCycler does. It pins one thread to each core in turn, runs SSE Huge FFTs by default, and suspends the load for 1 s in every 10. ctune logs per-core clocks and voltages from the SMU pm_table.

The three domains share the machine-check counters per bank and subsystem, the sensors with per-board labels, the journal and the test runner. Every test writes a crash marker, so the next boot records a freeze or reboot during a test as red. Long tests run detached as systemd user units (`systemd-run --user`) and report through `test-status`, and `test-stop` stops one. `boot-check --install` adds a user unit that runs at every boot, records a test that a crash cut short, and keeps that boot's hardware-error lines. `signoff` is the multi-hour suite that ends a campaign. It runs RAM, y-cruncher, the per-core cycle, the GPU, a combined load and an idle soak.

## Use
```bash
bin/ctune status                         # memory, CPU and GPU status, sensors, errors, journal tail
bin/ctune mem snapshot --label X         # full memory snapshot; asks for root through pkexec
bin/ctune mem compare                    # previous and latest snapshot: timings, voltages, every BIOS setting
bin/ctune mem bios ProcODT               # search the decoded BIOS settings
bin/ctune mem test                       # 25 min RAM screen with stressapptest and GPU heat; verdict green or red
bin/ctune mem test fabric --detach       # 90 min, then 20 min idle, in the background
bin/ctune mem tm5-setup                  # once: TestMem5 0.12, the anta777 Extreme1 profile and a Proton prefix
bin/ctune mem test tm5 --detach          # TestMem5 anta777 Extreme1, 3 cycles, with GPU heat
bin/ctune cpu status                     # margin per core (BIOS or applied), clocks, voltage, temperature
bin/ctune cpu probe                      # read every core's margin through the SMU; asks for root
bin/ctune cpu apply --core 0 --margin -25 --dry-run   # show the guardrails and the mailbox word; drop --dry-run to apply
bin/ctune cpu test --cores 0             # mprime SSE Huge, about 6 min per core; verdict green or red
bin/ctune cpu reset                      # back to the BIOS margins
bin/ctune gpu status                     # clocks, offsets, power, and what limits the clock
bin/ctune gpu apply --max-clock 1860 --core-offset 150   # asks for root; lasts until reboot
bin/ctune gpu burn-setup                 # once: build the gpu-burn image (podman, about 4 GB download)
bin/ctune gpu q2rtx-setup                # once: Quake II RTX and the Steam runtime it runs in (about 1.3 GB)
bin/ctune gpu unigine-setup              # once: Unigine Superposition and Heaven, the raster loads (about 1.9 GB)
bin/ctune gpu test screen                # about 44 min: gpu-burn, Quake II RTX, Superposition, Heaven, memtest_vulkan
bin/ctune gpu reset
bin/ctune gpu persist --dry-run          # after a green sign-off: re-apply these settings at every boot
bin/ctune errors                         # corrected machine-check errors by bank and subsystem
bin/ctune signoff --detach               # about 5 h 30 min plus TM5: ram, tm5, ycruncher, cpu, cpu_heavy, gpu, combined, idle
bin/ctune test-status                    # the running test and its phase, or the last result
bin/ctune test-stop                      # stop the running detached test
```
Agents get the same commands from an MCP server (`ctune/mcp_server.py`, standard library only). The server leaves out the one-time setup commands `mem tm5-setup`, `gpu burn-setup`, `gpu q2rtx-setup`, `gpu unigine-setup`, `gpu persist-setup`, `gpu persist-selftest` and `boot-check`. `.mcp.json` registers the server for this project. It starts the server from `$CLAUDE_PROJECT_DIR`, which Claude Code sets to the project root, so it needs no local path. To register it elsewhere, run `claude mcp add ctune -- python3 -I <repo>/ctune/mcp_server.py`. Three skills run the campaigns: `ctune-mem`, `ctune-cpu` and `ctune-gpu` in `.claude/skills/`. They share the campaign frame in `docs/campaign.md` (journal, verdicts, long tests, sign-off, crash handling). The agent keeps the machine's own facts and history in `data/this-machine.md`, which it creates from `docs/this-machine.template.md`.

## Layout
- `ctune/core/`: the journal, machine-check counters, sensors, paths, the shared test runner (`stress.py`), the sign-off suite and the detached runner.
- `ctune/mem/`: the memory API and the platform collectors. `platforms/zen3_am4` works. ctune detects Zen 4 and Zen 5 CPUs as `zen4_am5` and `zen5_am5`, but has no collectors for them yet.
- `ctune/cpu/`: SMU mailbox and pm_table access, the guarded root helper, and the per-core test runner.
- `ctune/gpu/`: the NVML binding (ctypes), the guarded root helper, the test runner, and persistence. Persistence is `persist.py`, the pkexec installer `install.py`, and `boot_apply.py`, which the installer copies to a root-owned path.
- `tests/`: offline tests (`python3 -B -m unittest discover -s tests`).
- `data/` (gitignored, or `$CTUNE_DATA`): snapshots, `journal.jsonl`, `cpu_state.json`, `gpu_state.json` and `this-machine.md`.
- `tools/` (gitignored): the third-party tools. `docs/research/reuse-tools.md` and `docs/research/gpu-graphics-loads.md` have their sources.
  - memtest_vulkan v0.5.0 and vkpeak 20260527 from their GitHub releases.
  - y-cruncher v0.8.7.9547, the static Linux build from its GitHub release.
  - stressapptest v1.0.11, built from source with `./configure && make` into `tools/stressapptest/`.
  - TestMem5 0.12 from testmem.tz.ru and the anta777 Extreme1 profile from integralfx/MemTestHelper, installed by `ctune mem tm5-setup` with pinned SHA256 hashes. TM5 is freeware under its author's copyright, so never commit it. It runs through umu-run (Bazzite ships it), which fetches GE-Proton once. xdotool (Bazzite ships it too) closes the AWE privilege box that TM5 opens under Wine.
  - mprime 30.19b20 from mersenne.org. Its EULA forbids redistribution, so never commit it.
  - The gpu-burn source at a pinned commit. `ctune gpu burn-setup` builds the image.
  - nvidia_oc 0.1.24, built from a pinned commit by `ctune gpu persist-setup`.
  - Quake II RTX 1.8.1 and the Steam Linux Runtime sniper it runs in, installed by `ctune gpu q2rtx-setup` with pinned SHA256 hashes.
  - Unigine Superposition 1.1 and Heaven 4.0, the free Basic editions, installed by `ctune gpu unigine-setup` with pinned SHA256 hashes. Their EULA allows personal use only and forbids redistribution, so never commit them.
- `ctune/mascot.py`: the mascot as pixel sprites, and the banner `ctune status` prints in a terminal.
- `docs/brand/`: the logo, wordmark, icons, animation and social preview. `python3 docs/brand/build.py` regenerates them from `ctune/mascot.py` and needs Pillow.
- `docs/`: the design notes and the research notes with their sources. `design.md` covers GPU persistence, the passwordless root path, crash evidence and the BIOS decoder.

## Roadmap
1. An unattended loop: a runner that starts after a boot or an apply, runs the test, writes the journal, and wakes the agent through a T3 webhook.
2. Gigabyte profile files the user loads with "Load Profile". This needs one sample profile saved to USB.
3. A platform-neutral snapshot format, then `zen4_am5` with the DDR5 register map and AM5 boards. The Zen 4 Curve Optimizer runs from -50 to +50 and needs its own probe.

## License
MIT, see [LICENSE](LICENSE).
