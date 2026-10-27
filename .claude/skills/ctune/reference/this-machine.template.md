# This machine

<!-- Template. On a new machine, copy it to data/this-machine.md (gitignored, ctune's local state directory) and fill it in.
Record facts that `status` and the snapshots cannot show; read live values from the tools, never from this file.
Keep it current: replace stale facts instead of appending, and remove an open question once it is answered. -->

- **Hardware.** CPU, motherboard and revision, memory layout (daisy-chain or T-topology), BIOS chips (single or dual), OS and kernel.
- **Memory kit.** Module count, size and rank, rated speed and timings, IC (for example Samsung B-die), and whether it is one kit or several. Whether the modules have a temperature sensor.
- **Cooling.** What cools the memory, CPU and GPU, which fan header drives each, and what controls it (BIOS curve, CoolerControl, the GPU itself). Which fans a BIOS reset or a "Load Optimized Defaults" changes.
- **Baselines.** Per domain: the snapshot name or journal entry of the last green configuration, its BIOS version, and how it was validated (which tests, how long). Settings entered by hand versus left on Auto.
- **Limits found.** Settings that never ran stable (for example an FCLK ceiling) and tighter sets that were tried and dropped, with the voltage they were tried at, so a campaign does not repeat them. Where those sets are saved (BIOS profile slots).
- **BIOS behaviour.**
  - Where memory settings are entered, and whether another menu copies them (for example Gigabyte Tweaker to AMD CBS one save later).
  - What "Load Optimized Defaults" resets.
  - Profile slots in use, and the BIOS version they were saved on.
  - The current BIOS and AGESA version, any planned update, and what it changes for the decoder (`bios_key_problems` after the first snapshot on a new BIOS).
- **CPU.** SMU firmware version, and whether `cpu_probe` read every BIOS margin back on it. Any open question about the SMU commands on this firmware.
- **GPU.** Model, default and allowed power limit, max boost clock, the stock baseline from `gpu_test` (scores, clocks, power, temperature), what limits each load (power cap, clock lock, temperature), and anything the graphics load needs on this OS.
- **Memory pressure.** What normally holds RAM and VRAM (desktop apps, swap, local model servers), and how it limits test coverage or blocks the VRAM test.
- **Crash evidence.** Whether `ctune boot-check` is installed, whether ramoops or another pstore backend is enabled and tested, and where its records land. See `docs/design.md` section 3.
- **GPU persistence.** Whether the boot side is installed, and whether `/etc/ctune/gpu.json` exists (what is applied at boot).
- **Drivers.** Out-of-tree drivers (for example a sensor driver) and how they are rebuilt for each kernel.
- **Open questions.** What is still unknown about this machine, each with the step that answers it.
