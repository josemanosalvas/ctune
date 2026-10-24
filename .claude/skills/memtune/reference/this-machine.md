# This machine

- **CPU / board:** Ryzen 7 5800X3D. Gigabyte B550 AORUS PRO rev 1.0 (daisy chain, single BIOS chip). BIOS F16b (AGESA 1.2.0.8); an update to F19c (AGESA 1.2.0.12) is planned.
- **OS / drivers:** Bazzite. Uses ryzen_smu, plus the out-of-tree it87 driver in `/var/lib/it87-local`, rebuilt per kernel by `/usr/local/sbin/load-it87-dkms`.
- **RAM:** 4 × 8 GB Patriot Viper Steel 4400 C19 (Samsung B-die, single rank), probably two 2 × 8 kits. 2 DIMMs per channel.
  - It has no DIMM temperature sensor.
  - A Corsair Dominator Airflow Platinum fan sits on SYS_FAN4, held at a fixed 51 % (~2,130 RPM) by the BIOS curve. CoolerControl leaves it Unmanaged.
- **Baseline (validated by the user with many hours of mixed stress tests):** DDR4-3733 14-15-15-15-36-51, GDM on, 1T, FCLK 1867 1:1, tRFC 288.
  - Voltages: VDIMM 1.536 V measured (BIOS code 55), VCORE SOC code 64 ≈ 1.14 V set (1.12 V measured), VDDG 1.00/1.00 V, VDDP 0.90 V.
  - ProcODT 40 Ω (manual). RTT and CAD bus on Auto.
  - Curve Optimizer per core: −20/−30/−30/−20/−20/−30/−30/−30.
  - Snapshot: `data/snapshots/20261024-181828_F16b`.
- **History:**
  - FCLK 1900 was never stable despite extensive attempts.
  - A tighter set (tRCD/tRP 14, tRAS 22, tRC 36, tWR 16, tRTP 8, tWTRL 8, tCWL 12, SCL 3/2, WRRD 1, DD 4/6) at ~1.51 V was dropped. It is stored in BIOS profiles 2 and 3.
- **BIOS:**
  - Enter memory settings under Tweaker (M.I.T.). AMD CBS mirrors it one save behind.
  - "Load Optimized Defaults" resets everything, including the SYS_FAN4 curve.
  - All 8 profile slots are full and stamped F16b.
- **Open questions:** the trained RTT/CAD values and the SPD/XMP contents need one boot with kernel arg `iomem=relaxed`, then a snapshot.
