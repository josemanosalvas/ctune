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
  - An update to F19c (AGESA 1.2.0.12) is planned.
- **Drivers.** The out-of-tree it87 driver in `/var/lib/it87-local` is rebuilt for each kernel by `/usr/local/sbin/load-it87-dkms`.
- **Open questions.** The trained RTT and CAD values and the SPD/XMP data need one boot with the kernel argument `iomem=relaxed`, followed by a snapshot.
