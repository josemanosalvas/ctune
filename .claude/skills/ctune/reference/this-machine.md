# This machine

Facts that `status` and the snapshots cannot show. Read live values from the tools.

- **Hardware.** Ryzen 7 5800X3D on a Gigabyte B550 AORUS PRO rev 1.0. The board has a daisy-chain memory layout and a single BIOS chip. The OS is Bazzite.
- **Kit.** 4 × 8 GB Patriot Viper Steel 4400 C19 with Samsung B-die (single rank, probably two 2 × 8 kits). The modules have no temperature sensor. A Corsair Dominator Airflow Platinum fan on SYS_FAN4 cools them. CoolerControl leaves that header unmanaged. On F16b a manual BIOS curve held it at a fixed 51 %; since the F19c flash it runs the BIOS "Normal" curve on the System 2 temperature (45 % PWM at idle). The user plans to redo the BIOS fan settings, so check SYS_FAN4 before a heat-sensitive RAM step (tRFC).
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
  - Stock baseline on 2026-10-27 with gpu-burn: FP32 26,438 GFLOPS at 1682 MHz and 397 W, at the power cap 100 % of the time; FP64 542 GFLOPS at 1988 MHz and 180 W; VRAM 799 GB/s with no errors; 75 °C max.
  - At the 400 W limit every heavy load sits at the power cap, at the 1905 MHz lock too: FP32, tensor cores (1772 MHz at 380 W) and Quake II RTX at 3440×1440 (1882 MHz). Only FP64 runs at the lock below it, so an undervolt has real headroom.
  - Quake II RTX 1.8.1 needs libssl 1.1, which Fedora 44 lacks, so ctune runs it in Steam's sniper runtime inside gamescope's headless backend (`tools/q2rtx/VERSION`).
  - The GPU controls its own fans; CoolerControl uses GPU temperatures as inputs for the case-fan curves.
- **Memory pressure.** zram swap holds about 10 GB of desktop memory during normal use, so a RAM test with apps open covers only about half the RAM. An ollama `llama-server` can hold about 6 GB of VRAM, which blocks the VRAM test.
- **Crash evidence.** Since 2026-10-25:
  - `ctune-boot-check` (a user unit) runs at every boot.
  - ramoops is enabled and takes effect from the next boot: `reserve_mem` plus `pstore.backend=ramoops`, `kernel.panic=5`, and a drop-in that orders systemd-pstore after ramoops. Records land in `/var/lib/systemd/pstore`.
  - It stays only if a deliberate panic survives a reset on this board; that test is in the open questions.
- **GPU persistence.** The boot side is installed but inert: no `/etc/ctune/gpu.json`, so nothing is applied at boot. `gpu_persist` writes the config after a green GPU sign-off.
- **Drivers.** The out-of-tree it87 driver in `/var/lib/it87-local` is rebuilt for each kernel by `/usr/local/sbin/load-it87-dkms`.
- **Open questions.**
  - The trained RTT and CAD values and the SPD/XMP data need one boot with the kernel argument `iomem=relaxed`, followed by a snapshot.
  - Does ramoops survive a reset on this board? Run the test after the F19c flash, once the settings are re-entered, because firmware decides whether RAM survives a warm reset. Steps:
    1. Check that `dmesg | grep -i ramoops` shows the region.
    2. Save your work and stop the database containers.
    3. Run `sync`, then `echo c | sudo tee /proc/sysrq-trigger`. The kernel panics and reboots after 5 s.
    4. After the boot: a new directory in `/var/lib/systemd/pstore` holding `dmesg-ramoops-*` means keep ramoops. Then teach `boot-check` to save it. Nothing there means remove it with `ujust toggle-save-panics disable`, and delete `/etc/systemd/system/systemd-pstore.service.d/ctune-ramoops.conf`.
  - Nobody has shown yet whether MP1 0x35 sets an absolute margin or adds to the current one. Make the first change of the cpu campaign a -30 core to -25. An absolute setter reads back -25. An additive one reads back -30, because -55 clamps to the floor, and `cpu_apply` reports the mismatch. Record the result in `docs/research/cpu-co-smu.md` and remove this item.
