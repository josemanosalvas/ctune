# Task B report: termination, drive-strength and setup tuning for a 5800X3D on a B550 AORUS PRO with 4x8 GB single-rank B-die (2 DIMMs per channel, daisy chain)

No code was written. I did read some bytes from the F19c BIOS image with `xxd` and `grep` to check the board's own Auto tables (§1). Downloaded sources, page text and Buildzoid auto-transcripts are in `/tmp/taskB_dl/`, which is temporary.

## 0. Bottom line

- **What Auto most likely is now (medium-high confidence):** RttNom RZQ/7 (34 Ω). RttWr RZQ/2 (120 Ω) or RZQ/3 (80 Ω). RttPark RZQ/1 (240 Ω). CAD drive strengths 24-20-24-24 (Clk-AddrCmd-CsOdt-Cke). Setup times 0/0/0.
  - These come straight from the board's own tables in the F19c image and match what owners of 4-DIMM systems report.
  - ProcODT at DDR4-3733 is **unknown** on this Gigabyte board (low confidence). Read it from AOD/APOB with `iomem=relaxed`.
- **Several people already run this exact kit as 4x8 at 3800 CL14 with GDM on, at about 1.50–1.56 V**, two of them on this exact board. Termination changes did not lower VDIMM much for anyone. B-die's CL14 voltage need is mostly a property of the DRAM itself.
- **Realistic payoff on a 5800X3D is about 0 %.**
  - VDIMM: maybe −0 to −30 mV, which is under 1 °C per DIMM.
  - VSOC/VDDG: about −1 W SoC power, with no effect on clocks.
  - FCLK 1900: about +0.1–0.4 % FPS.
  - GDM-off 1T: within run-to-run noise in games.
  - ODT cannot "unlock" tRFC. tRFC is set by DRAM refresh and temperature.
- **Where ODT tuning is actually worth it:** making a config train reliably on cold boots, keeping 3800/1900 stable, or as a requirement for GDM-off 1T.
  - The cheapest real gains are a DIMM fan or better airflow, and pinning the current Auto values so they can't drift.

## 1. AGESA Auto values for 2DPC single-rank (what the BIOS most likely uses)

### 1a. Primary evidence: the board's APCB tables in B550AORUSPRO.F19c

I located the APCB entries in group MEMG (0x1704) and decoded them using the published struct layouts (oxidecomputer/amd-apcb `src/ondisk.rs` @1ec66d6: entry IDs L729-733, CAD element L3055, data-bus element L3560, ODT-pattern element L4781, enums L2705/L3240/L3270).

There are 16 copies of the CAD table and 28 of the data-bus table across the image's PSP directories. **All of them agree** on the both-slots-single-rank row (rates DDR4-1600 to 3200):

| Parameter | APCB value (2 slots, SR + SR) | Same table, 1DPC SR (sanity check) |
|---|---|---|
| RttNom | 7 = RZQ/7, 34 Ω | Off |
| RttWr | 1 = RZQ/2, 120 Ω in instances 0x0000/0xAD80; 4 = RZQ/3, 80 Ω in instance 0xC804 and at 2667 | Off |
| RttPark | 4 = RZQ/1, 240 Ω | 5 = RZQ/5, 48 Ω (the well-known "Disabled/Off/RZQ5" 2x8 Auto, so the decode is right) |
| Clk / AddrCmd / CsOdt / Cke drive | 24 / **20** / 24 / 24 Ω (raw 0x0f, 0x1f, 0x0f, 0x0f) | 24/24/24/24 |
| AddrCmdSetup / CsOdtSetup / CkeSetup | 0 / 0 / 0 | 0/0/0 |
| GearDown / SlowMode (2T) | GDM = 1 at 2667–3200; SlowMode = 1 (2T) on every 2DPC row | |
| "odt_drive_strength" | 0x18 (0x19 in some copies). If this is ProcODT encoding, that is 60 Ω (53.3 Ω) | |

- **ODT pattern table** (offset 0x19D828, rank bitmap 0x22): accessing DIMM0 asserts ODT1, and accessing DIMM1 asserts ODT0, on both reads and writes. So the non-target DIMM uses RttNom, the target DIMM uses RttWr on writes and goes Hi-Z on reads, and RttPark applies only while ODT is low.
  - This is Micron's "Case D", which Micron says is "generally best for multi-slotted memories" (Micron 8Gb DDR4 datasheet Rev R, Table 70, p.256-257).
- **Example offsets:** CAD table at 0x19D620 (14 × 36 B). Data-bus tables at 0x19D8D8, 0x19DD78 and 0x19E358 (52 B elements).

**Caveats:**
- The rate bitmask stops at DDR4-3200. I am inferring, not verifying, that AGESA reuses the 3200 row at 3733.
- Board vendors often override ProcODT above JEDEC speeds:
  - ASUS uses 40 Ω (The Stilt, quoted in integralfx).
  - MSI showed 36.9 Ω Auto on 4x8 at 3800 (Buildzoid, below).
- I can't tell which of the three data-bus instances Vermeer uses.
- The tables come from F19c, not the running F16b. Board tables rarely change between BIOS versions.

### 1b. Corroboration from users (medium confidence)

- **OCN 24/7 stability thread #6,802 (Sleepycat, 2021-03-20, ASUS, 4x16):** "RttNom/RttWr/RttPark all at Auto, which is RZQ/7, RZQ/3 and RZQ/1."
- **OCN #6,801/#6,803 (Dasa, 2021-03-20, B550 AORUS PRO, 5800X, 4x8 B-die):** ran Auto 7/3/1, then switched to 6/3/3.
- **OCN #7,710 (2021-05-03):** "I have 4x8 sticks and I thought 7/3/1 is default", and "Rtt_nom off just doesn't boot".
- **Buildzoid, "4x8GB Patriot ViperSteel 4400C19 overclocked to 3800 CL14 on MSI X570 Godlike with Ryzen 9 3950X"** (2020-03-05, https://www.youtube.com/watch?v=j3z7Ggh2_xI):
  - [40:30]–[40:56]: Auto CPU on-die termination 36.9 Ω and drive strengths 24-20-24-24 on 4x8 at 3800.
  - [39:38]–[41:05]: "Auto is not a fixed value". Copy the Auto values into the BIOS, because they can change after an AC power loss.
- **This BIOS has these controls (F16b HII strings):**
  - ProcODT 480…28.2 Ω.
  - Clk/AddrCmd/CsOdt/CkeDrvStren 120/60/40/30/24/20 Ω.
  - Setup times 0–0x3F.
  - RttNom/RttPark Disable…RZQ/7; RttWr Off/RZQ/2/RZQ/1/Hi-Z/RZQ/3.
  - They exist twice: AMD CBS/Overclocking and Tweaker → Advanced Memory Settings → Memory Subtimings (manual p.28). Set them in one place only.
- **Live state from the snapshot:** GDM on with Cmd2T = 1T, so 1T is not AGESA's 2DPC default (2T). tCKE = 1, PowerDown off.

## 2. Known-good manual values for 4x8 single-rank B-die at 3733–3800

### 2a. Examples (most of them with GDM on)

| Source | Hardware | Result | Termination / CAD | Voltages |
|---|---|---|---|---|
| Buildzoid video above, [09:50]–[10:22], [19:49]–[20:23], [44:27] | **Same kit** (4x8 Viper Steel 4400C19), MSI X570 Godlike, 3950X | 3800 14-16-13-27, GDM on | ProcODT 36.9 Ω (Auto); 24-20-24-24; RTT copied from Auto (values not spoken) | 1.50 V plus a DIMM fan ("errors after ~200 % memtest without fan"; at 3733 CL14 the fan "wouldn't be necessary"); SOC 1.15 V, VDDG 1.10 V ("safe, not minimised") |
| OCN #6,262 / #6,267, nexxusty, 2021-03-03 | **Same kit** 4x8 Viper 4400 B-die | 3800 14-14-14-30, GDM on, 20 h GSAT pass | ProcODT 43.6 Ω, RTT 7/Off/5 (34 Ω / off / 48 Ω), setup 0/0/0, CAD 24-20-24-24. Later 40 Ω and 7/3/1 while working toward GDM-off 1T (unfinished) | "1.5 V+" with 2×50 mm fans |
| OCN #6,801–6,803, Dasa, 2021-03-20 | **B550 AORUS PRO**, 5800X, 4x8 B-die (mixed bins) | 3800, 1.51 V | Auto 7/3/1, then **6/3/3** (40/80/80 Ω), described as "much safer". ClkDrvStr 40–60 helped | VSOC 1.2 V. Raising VDIMM 1.43 → 1.46 V went from 0 to 46 errors |
| OCN thread 1794389, #13, Insidious Supra, 2021-09-29 | **B550 AORUS PRO**, 5800X, two 2x8 Viper 4400 kits (like yours) | 4x8 3800 CL14 (values only in a screenshot) | n/a | "Would not run 4 sticks … with less than 1.12 V vsoc" |
| Same thread, rul3s (original post's solution edit), 2021 | X570 Strix-F (daisy chain), 5800X, 4x8 HOF 4000C19 B-die | 3600 daily; 3733 needed a DIMM fan | **6/3/3** "for 4 DIMMs"; GDM off | DIMMs at 45–50 °C under stress |
| Veii, OCN #6,264, 2021-03-03 | Advice for the 4x8 Viper case above | | ProcODT about 36.9 Ω "at worst 40". For GDM off: CAD 40-20-30-20 or 60-20-40-20, or 40-20-20-20 with tCKE 9 and setup 3-3-15 at 3800 | "3800C14 requires 1.48–1.56 V" |
| Veii, #6,221 (2021-02-28), #6,235 (2021-03-01) | | | 6/3/3 is "perfect for 2x16, ~90 % fine for 4x8". Avoids RttPark /1 on Vermeer since AGESA 1.0.8.1. "RTTNom as weak as possible under /7" | |
| Taraquin's OCN Zen 3 guide (first post, Wayback 2023) | | | ProcODT 28–48 Ω for 4x8; DrvStr 24-20-24-24 ("for 1T you often need to change this") | 1900: SOC 1.05–1.15, VDDP 0.80–1.00, CCD 0.85–1.00, IOD 0.95–1.05 V |
| dasa on HardForum, Zen 3 memory thread #22, 2020-12-17 | 4x8 B-die | | RttNom RZQ/7, RttWr RZQ/3, RttPark RZQ/1 or /2 | VDDP 850–900 mV ("25 mV below stable → no POST") |
| AMD/Xilinx PG150 tables (Xilinx wiki "DDR Controller Settings", Table 4/9) | FPGA, 2-slot single-rank | Reference only | DRAM Ron 34 Ω, RTT_NOM 60 Ω, RTT_PARK 40 Ω, controller ODT 40 Ω (UltraScale) / 60 Ω (UltraScale+) | |

- **DRAM Calculator for Ryzen (1usmus):** its 4-DIMM presets are compiled into the binary and I could not extract them.
  - The changelogs say it handles 4 single-rank modules separately (v1.4.0, v1.6.2 per ComputerBase 2019-09, v1.7.1).
  - 1usmus: Zen 2 ProcODT 28–40 Ω (OCN calc thread #4,800, 2019-07-18).
  - 1usmus, Zen+ era: RttNom "Disabled, RZQ/7, RZQ/6 optimal", ProcODT 43–60 Ω, and CAD 20-20-20-20 raised his error-free temperature from 52 to 58 °C (#3,906, 2019-01-28).
  - nexxusty's 43.6 Ω / 7/0/5 starting point came "from DRAM Calc" (#6,267).

### 2b. Why these values (JEDEC rules, high confidence)

**Which termination applies when.** In DDR4 the target rank uses RTT_WR on writes, regardless of the ODT pin. A DRAM whose ODT pin is high uses RTT_NOM. RTT_PARK applies when ODT is low. A rank that is being read goes Hi-Z. Priority is disable > WR > NOM > PARK (Micron 8Gb DDR4 datasheet p.256).

**What AMD's ODT pattern does with that.** The pattern asserts the non-target DIMM's ODT on every access. Consequences:
- **RttNom is the main stub/reflection absorber.** Turning it off on 2DPC typically means no POST. Keep it at RZQ/7 or RZQ/6.
- **RttWr is the target's termination during writes.** If RttWr is off, the target falls back to RttPark during writes. That is why both 7/0/5 and 7/3/1 work: Park 48 Ω takes over the RttWr job.
- **RttPark carries current only at the edges of ODT windows.** With single-rank 2DPC it mostly matters when RttWr is off. POD12 terminates to VDDQ, so an idle bus draws no DC current. Claims that a "strong Park heats the DIMMs" are therefore weak for this configuration (my inference).
- **ProcODT is the CPU's termination on reads.** Micron TN-40-40 (Table 11, p.27) gives 34–80 Ω as the signal-integrity optimum for point-to-point links.

**CAD bus.** Command/address lines are shared by both DIMMs, so the load doubles. That is why AGESA moves AddrCmd from 24 Ω to 20 Ω on 2DPC, defaults to 2T, and turns on GDM.

**Daisy chain.** The second DIMM is a stub and the two slots see different delays. Buildzoid, "Rambling about motherboard memory layouts" (3vQwGGbW1AE): [06:41]–[09:01] and [11:53]–[12:43].

**This board's topology.** Buildzoid, "First impressions of Gigabyte's B550 motherboard lineup" (BkmDXgA8V4M), [18:32]–[19:15]: the Pro has "the same memory topology" as the Master. "PCB Breakdown B550 Aorus Master" (Zy-nxryZztA), [22:36]–[23:18]: "six layer shielded daisy chain". Medium confidence.

**Terminology trap.** Veii says "stronger RTT means more impedance, lower divider" (#7,161). In standard terms, the strongest termination is the *lowest* Ω (RZQ/7 = 34 Ω). Translate his advice before using it. His electrical explanations (for example "25/75 amperage split") are not standard engineering. His presets are useful as empirical results.

### 2c. Candidates to try, in order

1. **Pin the current Auto values** first.
2. **RTT:**
   - 6/3/3 (40/80/80 Ω): the B550 AORUS PRO 4x8 example.
   - 7/3/5 (34/80/48 Ω).
   - 7/0/5 (34/off/48 Ω): same kit, 20 h stable at 3800 GDM on.
   - Never set RttNom to Off.
3. **ProcODT:** 36.9 → 40 → 43.6 Ω.
4. **CAD:** keep 24-20-24-24 with GDM on. Try ClkDrvStr 30 or 40 only if you get training or cold-boot problems.
5. **Setup times:** keep 0/0/0 with GDM on.

## 3. What tuning can realistically buy on this system

| Lever | Realistic effect | Game impact on 5800X3D | Confidence |
|---|---|---|---|
| ODT/RTT to lower VDIMM at the same timings | 0 to −30 mV. CL14 voltage scaling is intrinsic to the DRAM: integralfx shows 3200C14@1.35 → 3733C14@1.50 V, and Veii puts 3800C14 at 1.48–1.56 V. My rough estimate: about −4 % module power (~0.1–0.2 W per DIMM, <1 °C), and termination power about 0.7 W per channel at full bandwidth, split CPU/DIMM. A DIMM fan does far more (Buildzoid heat video, see §5) | 0 | Medium-low (estimate) |
| Lower VSOC/VDDG | Measured SoC power is 12.0 W median at 1.11 V. −50 mV is about −1 W. PPT is 142 W and the CPU is capped at 4.45 GHz, so there is no clock gain | 0 % | High |
| FCLK 1900 / DDR4-3800 | +1.8 % clock, about −0.5 ns. PCGH (2022-05-06) measured the whole 3200C16→3800 step on the X3D at only +1.9 / +2.6 / +3.5 % across 3 games; this step is a small slice of that. Needs a lucky IOD and, with 4 sticks, VSOC ≥ ~1.12 V | +0.1 to 0.4 % | Medium |
| Tighter tRFC (288 → 256) | Refresh overhead goes from 1.98 % to 1.76 % of time. **ODT has no influence on tRFC**; heat does | ~0 % | High |
| GDM off, 1T | About 0.5–1.5 ns lower latency, and odd primaries take effect. Very hard on 2DPC daisy chain (Veii: "hell"; Buildzoid stayed on GDM [30:32]–[31:24]). Ancient Gameplays, "Gear Down Mode ON vs OFF" (8NukWYjWpm0), [11:00]–[12:08]: on a 5600X the difference is "within margin of error" | 0 to 0.5 % | Medium |
| GDM off, 2T | Slower than GDM on 1T (integralfx L396). Only useful for testing odd timings | Negative | High |
| Training robustness (cold boots, after AC loss) | The real value of pinning or tuning ODT/CAD | Stability, not FPS | Medium |

## 4. Staged test procedure (one change per step)

**Stage 0 — before touching anything:**
- Read the live ProcODT/RTT/CAD values: boot once with `iomem=relaxed` and run `scripts/am4_bios_memcfg.py`.
- Save a BIOS profile: Save & Exit → Save Profiles, 8 slots or a file on USB (manual p.38). Profiles probably won't load on a different BIOS version (my assumption). Do this work after F19c is flashed and re-baselined.
- Get a clean baseline run: 1 h stressapptest, memtest86+, MCE counters at zero.

**Stage 1 — pin Auto:** type the values you read into the BIOS (Buildzoid [39:38]). Expect identical behaviour.

**Stage 2 — find the margin:**
- Lower VDIMM in 10 mV steps until short tests start to fail (Buildzoid [21:13]–[21:57]).
- At that marginal voltage, compare one ODT setting at a time by error count. This is the ocod.home.blog "Rev. 2" (2020-02-05) method.
- Sweep in this order: ProcODT (one menu step), then RTT (one field, one RZQ step), then ClkDrvStr, then other drive strengths.
- Finally restore VDIMM to floor + 20–30 mV.

**Stage 3 — SoC rails at 1867:**
- VSOC (judge by the pm_table real value, not the set value, which runs about −90 mV here) in 12.5 mV steps.
- VDDG IOD/CCD in 25 mV steps, keeping VSOC ≥ VDDG + 40–50 mV (Buildzoid [22:20]–[23:33]; The Stilt via integralfx L356-361).
- VDDP in 25 mV steps, ≤ VDIMM − 0.1 V and ≤ 1.05 V, and it needs a **cold** reboot (AMD Community Update #4, 2017).
- Check for MCE / WHEA-19-equivalent errors.

**Stage 4 — optional 3800/1900:**
- Real VSOC 1.12–1.15 V (≤ 1.20 V), IOD 1.05 V, CCD 0.95–1.00 V, VDDP 0.90–0.95 V, ProcODT 36.9–40 Ω.
- VDIMM may need about +10–20 mV for CL14 at 3800.
- Pass criteria: 2 h CPU+GPU heat-soak and one week with zero corrected MCEs.
- Buildzoid's brute-force 1900 voltages: SOC 1.2 V so that IOD can reach 1.15 V ("Early information on Ryzen 5000 FCLK overclocking", BH0ATuUwNRo, [07:01]–[08:06]).

**Stage 5 — optional GDM off:**
- Do 2T first, to check that the odd timings themselves are fine.
- Then 1T with CAD 40-20-30-20 or 60-20-40-20 (Veii), or 60-20-20-24 with setups 63-63-63, adjusting all three setups together (integralfx L678-688).
- Expect many no-POST cycles.

**Testing each step:**
- Run each step warm.
- Do at least 3 true cold boots: switch the PSU off for 10 minutes or more.
- Compare tPHYRDL/tPHYWRL/tSTAG across boots with `ram_snapshot.py`. If the trained values change between boots, the training margin is thin.

**Symptom → likely cause:**

| Symptom | Likely cause |
|---|---|
| No POST / DRAM status LED stays on / training loop | RttNom off on 2DPC, ProcODT far off, CAD too weak for 1T, VDDP 25 mV off |
| Boots warm but fails after a cold boot or AC loss | Thin CAD/training margin; Auto values drifted. Veii prefers 24-20-24-24 over 24-20-20-24 against cold-boot failures (#7,710) |
| Errors get **worse** when VDIMM goes up (e.g. Dasa: 0 → 46 errors at +30 mV) | Termination/overshoot mismatch, not too little voltage |
| Errors only after heat-soak | tRFC/temperature, not ODT (integralfx L306-309; Buildzoid "RAM overclock stability and heat management", iCD0ih4qzHw, [06:49]–[08:11]) |
| Hard freeze with no MCE at load→idle transitions | RTT / dynamic ODT / power-state interaction (Veii #6,235) |
| Corrected MCEs (WHEA-19 equivalent) | FCLK/VSOC/VDDG, not DRAM termination |
| TM5 #1/#11 errors | tRRD/tWTR or RttNom (Veii #7,161; TM5-specific, low confidence) |

**Recovery on the B550 AORUS PRO (manual Rev 1001):**
- CLR_CMOS jumper: power off and unplug, short the two pins for a few seconds (p.22). Or remove the battery for about 1 minute (p.22).
- Then Load Optimized Defaults (p.23) and Load Profiles (p.38).
- Q-Flash Plus button for BIOS recovery.
- Keep the F16b "Save BIOS" backup.
- Anecdote: pressing reset at a POST hang can bring the board back on Gigabyte (HardForum dasa, 2020-12-17).
- Another anecdote, from a *different* board (B550i): its CMOS jumper only worked with standby power present. Try the manual's way first.

## 5. Sources

- **integralfx DDR4 OC Guide** (oc-guide branch @29ab7a4, 2024-02-27): https://github.com/integralfx/MemTestHelper/blob/oc-guide/DDR4%20OC%20Guide.md. Lines: AMD termination L441-456, GDM L394-396, GDM-off L672-688, voltage tables L273-289, temperatures L304-309, SOC/VDDG L351-366.
- **Taraquin, "A guide to ram overclocking on Zen 3"**: https://www.overclock.net/threads/a-guide-to-ram-overclocking-on-zen-3.1798093/ (read via Wayback).
- **OCN "[Official] AMD Ryzen DDR4 24/7 Memory Stability Thread"**: https://www.overclock.net/threads/official-amd-ryzen-ddr4-24-7-memory-stability-thread.1628751/ — page-312 (#6,221, #6,235), page-314 (#6,262–#6,278), page-341 (#6,801–#6,807), page-359 (#7,161), page-386 (#7,710).
- **OCN "5800x cant go high speed memory with 4 dimms SR"**: https://www.overclock.net/threads/5800x-cant-go-high-speed-memory-with-4-dimms-sr.1794389/
- **OCN DRAM Calculator thread**: https://www.overclock.net/threads/new-dram-calculator-for-ryzen%E2%84%A2-1-7-3-overclocking-dram-on-am4-membench-0-8-dram-bench.1640919/ — page-196 #3,906, page-240 #4,800.
- **ComputerBase on DRAM Calculator 1.6.2**: https://www.computerbase.de/2019-09/dram-calculator-for-ryzen-162-release/
- **HardForum:**
  - https://hardforum.com/threads/zen-3-owners-5xxx-series-memory-discussion.2004660/ (#22)
  - https://hardforum.com/threads/bdie-x570-master-5800x3d-ram-oc-timings-advice.2027537/
- **Buildzoid videos:**
  - "4x8GB Patriot ViperSteel 4400C19 … 3800 CL14 …": https://www.youtube.com/watch?v=j3z7Ggh2_xI
  - "Rambling about motherboard memory layouts.": https://www.youtube.com/watch?v=3vQwGGbW1AE
  - "RAM overclock stability and heat management.": https://www.youtube.com/watch?v=iCD0ih4qzHw
  - "Early information on Ryzen 5000 FCLK overclocking …": https://www.youtube.com/watch?v=BH0ATuUwNRo
  - "First impressions of Gigabyte's B550 motherboard lineup": https://www.youtube.com/watch?v=BkmDXgA8V4M
  - "mobo PCB Breakdown Gigabyte B550 Aorus Master": https://www.youtube.com/watch?v=Zy-nxryZztA
  - Timestamps come from YouTube auto-captions.
- **Ancient Gameplays GDM test**: https://www.youtube.com/watch?v=8NukWYjWpm0
- **Micron:**
  - 8Gb DDR4 datasheet Rev R (Wayback): Table 70, p.256-257.
  - TN-40-40 Rev H: Table 11, p.27.
- **AMD Community Update #4** (Wayback 20210520): https://community.amd.com/t5/blogs/community-update-4-let-s-talk-dram/ba-p/415902
- **AMD/Xilinx DDR controller settings wiki** (PG150 tables): https://xilinx-wiki.atlassian.net/wiki/spaces/A/pages/747012338
- **oxidecomputer/amd-apcb** `src/ondisk.rs` @1ec66d6: https://github.com/oxidecomputer/amd-apcb
- **PCGH 5800X3D RAM scaling** (2022-05-06): https://www.pcgameshardware.de/Ryzen-7-5800X3D-CPU-278064/Specials/Ryzen-7-5800X3D-5800X-RAM-Skalierung-3D-V-Cache-1394526/2/
- **Ocod blog**: https://ocod.home.blog/2020/02/05/how-to-do-stable-ddr4-tuning-on-ryzen-finding-the-appropriate-values-of-the-termination-resistance-rev-2/
- **Gigabyte B550 AORUS PRO manual Rev 1001**, local copy: `/tmp/bios-research/docs/manual_e.pdf`
- **Your own data:** `snapshots/20261024-181828_F16b/snapshot.json` (pm_table SOC_power_W, UMC GDM/Cmd2T/tCKE) and the F16b `raw/fwmem/hiidb.bin` strings.

## 6. Not verified

- The live ProcODT and RTT values on F16b at 3733.
- Which APCB instance (RttWr 120 Ω vs 80 Ω) applies to Vermeer.
- Whether AGESA reuses the 3200 row above 3200.
- DRAM Calculator's numeric 4-DIMM presets.
- Whether Gigabyte profiles load across BIOS versions.

**Possible follow-up for a coding task:** a proper APCB decoder for the F19c image (and the F16b backup once it is saved), plus the AOD/APOB read with `iomem=relaxed`. The offsets and struct references in §1a are enough to start.