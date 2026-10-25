# This machine

Facts that `status` and the snapshots cannot show. Read live values from the tools.

- **Hardware.** Ryzen 7 5800X3D on a Gigabyte B550 AORUS PRO rev 1.0. The board has a daisy-chain memory layout and a single BIOS chip. The OS is Bazzite.
- **Kit.** 4 × 8 GB Patriot Viper Steel 4400 C19 with Samsung B-die (single rank, probably two 2 × 8 kits). The modules have no temperature sensor. A Corsair Dominator Airflow Platinum fan on SYS_FAN4 cools them at a fixed 51 % set by the BIOS curve. CoolerControl leaves that header unmanaged.
- **Baseline.** Snapshot `data/snapshots/20261024-181828_F16b` on BIOS F16b. The user validated it with many hours of mixed stress tests. ProcODT is set to 40 Ω by hand, RTT and CAD bus are on Auto, and Curve Optimizer is set per core.
- **History.**
  - FCLK 1900 never ran stable, despite extensive attempts. 1867 is this CPU's fabric ceiling so far.
  - The user dropped a tighter set at about 1.51 V VDIMM: tRCD/tRP 14, tRAS 22, tRC 36, tWR 16, tRTP 8, tWTRL 8, tCWL 12, SCL 3/2, WRRD 1, DD 4/6. BIOS profiles 2 and 3 hold it.
- **BIOS behaviour.**
  - Enter memory settings in Tweaker (M.I.T.). AMD CBS copies them one save later.
  - "Load Optimized Defaults" resets everything, including the SYS_FAN4 fan curve.
  - All 8 profile slots are full and stamped F16b.
  - An update to F19c (AGESA 1.2.0.12) is planned. F19c moves the Tweaker settings by +4 or +5 bytes; the decoder finds settings by menu location, and the F19c image resolves every key setting except the per-core CO questions, which AGESA adds at boot. The first F19c snapshot must show no `bios_key_problems`.
- **CPU.** ctune's runtime Curve Optimizer path was checked on F16b with SMU firmware 56.74.0 on 2026-10-25. `cpu_probe` read all eight BIOS margins back exactly. A no-op `cpu_apply` of core 1 at -30 read back -30, with zero corrected errors afterwards. The F19c flash changes the SMU firmware, so the probe has to match again before the first write.
- **GPU.** MSI RTX 3080 Ti Suprim X: 400 W default power limit (100 to 440 W allowed), max boost 2145 MHz.
  - Stock baseline on 2026-10-25, measured with vkpeak before gpu-burn replaced it (1 run): FP32 26,666 GFLOPS, 1,639 MHz average under load, 307 W average, VRAM 768 GB/s with no errors, 78 °C max.
  - The power limit caps the clock about two thirds of the time under load, so an undervolt has real headroom.
  - The GPU controls its own fans; CoolerControl uses GPU temperatures as inputs for the case-fan curves.
- **Memory pressure.** zram swap holds about 10 GB of desktop memory during normal use, so a RAM test with apps open covers only about half the RAM. An ollama `llama-server` can hold about 6 GB of VRAM, which blocks the VRAM test.
- **Drivers.** The out-of-tree it87 driver in `/var/lib/it87-local` is rebuilt for each kernel by `/usr/local/sbin/load-it87-dkms`.
- **Open questions.**
  - The trained RTT and CAD values and the SPD/XMP data need one boot with the kernel argument `iomem=relaxed`, followed by a snapshot.
  - Nobody has shown yet whether MP1 0x35 sets an absolute margin or adds to the current one. Make the first change of the cpu campaign a -30 core to -25. An absolute setter reads back -25. An additive one reads back -30, because -55 clamps to the floor, and `cpu_apply` reports the mismatch. Record the result in `docs/research/cpu-co-smu.md` and remove this item.
