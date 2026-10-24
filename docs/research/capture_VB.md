# Adversarial review: F16b settings decode and termination research (5800X3D, B550 AORUS PRO, 4x8 GB single-rank B-die)

The main conclusions:
- **ProcODT:** it is already set by hand to 40 Ω, so the research is wrong to call it unknown.
- **Auto termination tables:** the board's own Auto tables check out, but only up to DDR4-3200.
- **Same-kit, same-board evidence:** the research overstates it.
- **VSOC:** it is not set to 1.20 V. The most likely setting is about 1.1375 V.
- **Profiles:** the user already tried and dropped most of the earlier review's "Stage A" timings.
- **Gains:** realistic FPS gain from everything below is about 0 %.

## How this was checked

- **New scripts:**
  - `/var/home/josemanosalvas/.t3/scratch/2026-10-24-snapshot-the-current-ram-settings-01633672/scripts/gsat_dump.py` decodes Gigabyte's EasyTune voltage table (the "GSAT" table in an ACPI SSDT; encoding per ACPI 6.5 §20.2).
  - `/var/home/josemanosalvas/.t3/scratch/2026-10-24-snapshot-the-current-ram-settings-01633672/scripts/apcb_tables_check.py` re-decodes the AMD memory tables (APCB) in the F19c image, using the struct layouts in amd-apcb `ondisk.rs` (L2705, L2735, L3240, L3270, L3560, L4781).
- **Their outputs** are in `/var/home/josemanosalvas/.t3/scratch/2026-10-24-snapshot-the-current-ram-settings-01633672/snapshots/20261024-181828_F16b/decoded/`:
  - `gsat_f19c_easytune_voltage_table.txt`
  - `apcb_f19c_memg_tables.txt`
- **Research quotes** were checked against the saved page texts and transcripts in `/tmp/taskB_dl/`.
- **Trap in `decoded/taskc_test/devmem_run.txt`:** it shows ProcODT 40 Ω, RTT 7/3/5 and VDDIO 1540 mV, but it is a hand-made test fixture, not a capture. Its SSDT3 comes from `/tmp/taskc_test_run` and is 179 bytes; the real one is 15,740 bytes, and `test_results.txt` says the input was "hand-made". Do not use it as the APOB (AMD's boot-time memory-config record) reading.

## 1. Corrections to the settings decode

- **VCORE SOC, code 64:** the "not 1.20 V" conclusion holds, since 1.20 V would need code 72 or 74. The primary value of 1.150 V is the less likely one.
  - In the F16b setup forms, Dynamic VCORE SOC (and the CPU Vcore equivalent) is greyed out unless the code equals 1, which the help text calls "Normal". CPU Vcore's default is code 0, described as "Auto".
  - So codes 0 and 1 are reserved, the voltage list starts at code 2, and code 64 = 0.750 + 62 × 6.25 mV = **1.1375 V**.
  - The 0.750 V base and 6.25 mV step come from EasyTune's table in the F19c image, not from the setup screen's own table. Treat this as medium confidence until the BIOS screen is read.
  - The gap between the set value and the 1.1125 V telemetry is therefore about 25–37 mV, not 90 mV. The power-table field reading 1.20 V is flagged by ryzen_monitor as possibly "the default voltage" (`research/pmtable.md` L123).
- **DRAM voltage, code 55:** the "1.550 V MATCH" is overstated.
  - It is 1.55 V if code 0 means 1.000 V, or 1.53 V if codes 0/1 are reserved as for SOC.
  - The termination-voltage reading is 70 counts × 10.9 mV × 2 = 1.526 V, with about ±15 mV uncertainty. That cannot tell 1.53 from 1.55 apart.
  - Report it as **1.53–1.55 V**. The AOD buffer's VDDIO field (in mV) will settle it.
- **Profiles show the tuning history:**
  - Profile 4 is named "14-15-15-15". Its Tweaker timings equal the current ones, while its CBS fields hold the tight 14-14-14 set. So Tweaker is the master and CBS mirrors it one save behind. Enter timings in Tweaker only.
  - Profiles 2 and 3 hold the tight set (tWR 16, tRTP 8, tWTRL 8, SCL 3/2, tWRRD 1, RDRD/WRWR DD 4/6) at DRAM code 52. The current set uses looser timings at code 55, 30 mV higher.
  - "Untested-looking" is not supported. "Tried and not kept" fits better, though this is an inference.
- **Curve Optimizer and PBO "already on":** true in the settings. Whether Curve Optimizer actually takes effect on the X3D is unverified. PBO limits are stock (142 W / 95 A / 140 A).
- **Two Tweaker voltage items that are easy to confuse:**
  - "CPU VDDP" is the external VDDP rail, with a −200…+700 mV range in EasyTune's table. It is not cLDO_VDDP (AMD Community Update #4 sidenote). Tune VDDP only in AMD Overclocking.
  - "DRAM Termination" is the VTT supply voltage (0.45–1.00 V), not on-die termination. Keep it on Auto; the VDIMM estimate from VTT depends on that.

## 2. Verdicts on the termination research

| Claim | Verdict | Evidence |
|---|---|---|
| ProcODT at 3733 is unknown | **Refuted** | Set by hand to 40 Ω: Setup@352 = 0x38 and AmdSetup@256 = 0x38, option text "40 ohm" in the F16b forms, same in all profiles. Any sweep of 36.9 → 40 → 43.6 Ω starts at the current value. |
| Board Auto tables (CAD bus and data bus) | **Confirmed**, but downgrade to medium confidence | All 16 CAD-bus copies are identical: 2-DIMM single-rank row 24/20/24/24 Ω, setup times 0, Gear Down 1 at 2667–3200, 2T on every 2-DIMM row. Every data-bus copy has RttNom 34 Ω and RttPark 240 Ω. RttWr is 120 Ω or 80 Ω depending on the copy. There are 32 copies, not 28. No row covers anything above DDR4-3200 (the rate bitfield has no bit for it), so what Auto does at 3733 is unknown. |
| ODT pattern and JEDEC behaviour | **Confirmed** | ODT pattern table: chip-select 0 = 0x202, chip-select 2 = 0x101. Micron 8Gb Rev R, Fig. 206/207 (p.262–263): the write termination applies with the ODT pin low, and MR2 = 000 means "WRITE does not affect RTT". |
| Auto = 7/3/1, corroborated by forum users | **Weak** | Sleepycat's 7/3/1 is from an ASUS board with 4x16 dual-rank sticks. Dasa never says what his board's Auto was. |
| "Several run this kit at 3800 CL14 GDM on, two on this exact board" | **Partly refuted** | Only Insidious Supra has this board and two 2x8 Viper 4400 kits, and his settings are only in a screenshot. Dasa's sticks are mixed bins that hit walls at 15-16 and 15-17, and he reported "a few WHEA errors due to my CPU\MB at 3800" (#6,803). nexxusty (same kit, 3800 14-14-14-30, 20 h GSAT) and Buildzoid (MSI X570 Godlike) used other boards. |
| Dasa: 1.43 → 1.46 V gave 0 → 46 errors, so termination mismatch | **Misapplied** | It was his two worst sticks, on 2x16 settings, in a 30-minute test, not 4x8 with two DIMMs per channel. The cause is speculation; drop it from the symptom table. |
| 6/3/3 as the first RTT to try | **Weakened** | Veii, who proposed it, says "only 90% fine for 4x8GB" (#6,221) and "a bit unstable for 4x8" (OCN p.312). |
| Veii: avoid RttPark /1 because "RZQ changed since 1.0.8.1" | **Quote verified; mechanism implausible** | RZQ is a fixed 240 Ω reference resistor on the DRAM; AGESA cannot change it. Treat as anecdote. |
| Micron TN-40-40 Table 11: 34–80 Ω is optimal | **Misquoted and out of scope** | It is a point-to-point design guide at 1.26 V. The table lists acceptable controller ODT of 34–120 Ω or more, with no "optimum". |
| Auto can drift after AC loss (Buildzoid [39:38]–[40:26]) | **Quote is real but generic** | On AM4, RTT, CAD and setup Auto values come from tables keyed by DIMM population and speed, so they are fixed for a given BIOS. Trained delays and Vref drift, and those cannot be pinned. Pinning protects against BIOS-version changes, not boot-to-boot drift (inference). |
| Set VSOC runs about −90 mV here | **Refuted** | See §1: the gap is 25–37 mV. |
| Stage 4: +10–20 mV VDIMM for 3800 | **Reject** | That exceeds integralfx's 1.55 V B-die daily limit (L284) and is far above JEDEC's 1.50 V absolute maximum (L274), for +0.1–0.4 % FPS. |
| VDDP rules: ≤ VDIMM − 0.1 V, ≤ 1.05 V, cold reboot | **Text confirmed** | `amd_cu4.txt` L72. It is a 2017 first-generation Ryzen document. The Stilt (integralfx L357) and AMD's own F16b help text disagree on which rail VDDP is derived from. Irrelevant at 0.90 V. |
| VDDG ≤ VSOC − 40 mV | **Confirmed** | Buildzoid [22:46]–[23:11]; integralfx L355–361. Current margin is about 112 mV. |
| The controls exist twice | **Corrected** | They exist three times: Tweaker, AMD CBS and AMD Overclocking. Tweaker wins (see §1). |
| Daisy-chain memory layout | **Kept, medium confidence** | The manual recommends A2/B2 for 2 DIMMs (p.11–12), and Buildzoid supports it. Insidious Supra (same board) claims T-topology without a source. |
| Hard freeze at load-to-idle means RTT or dynamic ODT | **Wrong prime suspect for this system** | Curve Optimizer at −30 on five cores is the more likely cause. MCE banks tell them apart: load_store, insn_fetch, l2_cache, decode, execution or floating_point banks point to Curve Optimizer; pie, nbio or pcie banks point to the fabric. Bit errors on non-ECC DIMMs never produce an MCE. |
| Unsourced numbers | **Label as estimates** | GDM-off 0.5–1.5 ns, VDIMM −0 to −30 mV, 0.7 W per channel, FCLK −0.5 ns. My own estimate of termination power at full bus use is about 1–1.5 W per channel, the same order of magnitude. |
| Save a profile first | **Practical correction** | All 8 user slots are full, and the profiles are stamped "F16b8ARNR003". Save to USB, or use `bios_settings.md` as the re-entry checklist. |
| ~0 % payoff overall | **Agree** | |

## 3. Opportunities, ranked

| # | Setting | Current | Proposed | Expected benefit | Risk | How to test | Confidence |
|---|---|---|---|---|---|---|---|
| 1 | Re-enter the manual settings on F19c explicitly, and pin RTT, CAD drive strength, setup times, PowerDown and BGS/BGSAlt to the values read from F16b | RTT, CAD and setup Auto; PowerDown and BGS Auto (live: PowerDown off, BGS off, BGSAlt on) | Values from the F16b APOB capture, entered in Tweaker only | 0 % FPS; avoids silent drift from AGESA 1.2.0.8 → 1.2.0.12 | Low (a typo means no POST, fixed with CLR_CMOS) | `ram_snapshot.py` + `ram_compare.py`, repeat the APOB capture, 3 cold boots, stress test | High for the process, medium that Auto actually changes |
| 2 | DIMM airflow | Unknown, probably none; 4 DIMMs at ~1.54 V above a 3080 Ti | One fan over the DIMMs | Heat margin for tRFC 288 (154 ns); makes #3 possible | None | 2 h heat soak (GPU load plus memory test), with and without the fan | Medium |
| 3 | VDIMM | Code 55 (1.53–1.55 V) | Find the floor in 10 mV steps (code 52 is a plausible floor); then set floor + 20–30 mV | 0 % FPS; about −0.1 W and −1 to −2 °C per DIMM; gets back under 1.55 V | Silent corruption if tested inside the OS. Test only in memtest86+ from USB first, then the OS stress tests | Memtest86+ 4 passes, then stressapptest 1 h, then the heat soak | Medium-low |
| 4 | tRTP / tWR, tWTRL | 14 / 26, 12 | 8 / 16, then tWTRL 8, as one group | 0–0.3 % FPS | Low-medium (these were in the dropped set) | As #3, plus y-cruncher and 3 cold boots | Medium |
| 5 | tRDRDDD / tWRWRDD, then SCL | 6 / 7, SCL 5/5 | 5 / 6, then SCL 4/4 | 1–3 % in synthetic bandwidth, 0–0.5 % FPS | Medium (two DIMMs per channel; the dropped set used 4/6 and 3/2) | As #4 | Medium-low |
| 6 | tRAS / tRC | 36 / 51 | 30 / 46 | About 0 % | Low | As #4 | Medium |
| 7 | VDDG CCD | 1000 mV | 950 mV (keep IOD 1000, VDDP 900) | About 0.2–0.4 W SoC power, 0 % FPS | Corrected fabric MCEs | Stress test plus MCE check after at least 5 minutes | Low benefit |
| 8 | ProcODT | 40 Ω | 36.9 Ω only if training problems appear or for an FCLK 1900 attempt | 0 % otherwise | Low-medium | Cold-boot training checks | Medium |
| 9 | FCLK 1900 / DDR4-3800 | 1867 | **Skip** | +0.1–0.4 % FPS | Needs real VSOC ≥ ~1.12 V (now 1.1125 V); likely VDIMM above 1.55 V; Dasa saw WHEA errors at 3800 on this board | n/a | Medium-high that it is not worth it |
| 10 | tRCD / tRP | 15 / 15 (probably effectively 16 with Gear Down) | **Skip for now** | About 1 ns at most | High (the 14-14-14 set was dropped and the current set needed +30 mV) | n/a | Medium |
| 11 | GDM off, RTT sweeps, CAD changes | GDM on, Auto | **Skip**; use only as tools if instability appears | 0–0.5 % | High on a two-DIMMs-per-channel daisy chain | n/a | Medium |
| 12 | VCORE SOC | Code 64 (≈1.1375–1.15 V set, 1.1125 V real) | **No change**; do not "restore" 1.20 V | 0 % | Going lower conflicts with a same-board report needing ≥ 1.12 V for 4 DIMMs; going higher scales negatively above 1.15 V (The Stilt, integralfx L353) | n/a | Medium |
| 13 (not RAM) | CSM / Above 4G / ReBAR | CSM on, Above 4G off, ReBAR off | Profile 1 "latest ok" already had CSM off, Above 4G on, ReBAR Auto | 0 to a few % in some games; uncertain with the NVIDIA driver on Linux | Low | Separate step | Low |
| 14 (not RAM) | Curve Optimizer validation | Per core: −20/−30/−30/−20/−20/−30/−30/−30 | Keep, but validate | Temperature and power only (clock is capped at 4.45 GHz) | Idle or light-load crashes | Per-core pinned single-thread stress plus idle soak; check core MCE banks | Medium |

## 4. Values still needed from one `iomem=relaxed` boot on F16b, before flashing

- **APOB memory config, per channel:** ProcODT (to confirm 0x38 was applied), RttNom/RttWr/RttPark, AddrCmd/CsOdt/Cke setup times and Clk/AddrCmd/CsOdt/Cke drive strengths.
  - Needed for #1, #5, #8 and #11.
  - It also shows whether the ≤3200 table row (34/120 or 80/240 Ω, 24/20/24/24) is what runs at 3733.
- **AOD buffer VDDIO/VTT/VPP in mV:** settles whether DRAM code 55 is 1.53 or 1.55 V (#3).
- **DRAM mode registers MR1/MR2/MR5/MR6, if decodable:** termination values as actually programmed, plus trained VrefDQ.
- **Trained values across at least 3 cold boots** (tPHYRDL 26 / tPHYWRL 9, APOB training data): a margin indicator only; these cannot be pinned.
- **Not in the APOB (needs an F12 screenshot of the BIOS):** the VCORE SOC and DRAM Voltage text in Tweaker > Advanced Voltage Settings, and the PC Health readings. These resolve codes 64 and 55.
- **Already known from the memory-controller registers, no capture needed:** all timings, Gear Down on, 1T, PowerDown off, BGS off / BGSAlt on, tCKE 1.