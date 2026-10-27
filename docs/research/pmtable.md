# SMU pm_table 0x380905 (Vermeer, 1488 bytes): field map and example values

**Example reading used throughout:** a 5800X3D on a B550 AORUS PRO with four 8 GiB single-rank DIMMs (two per channel) at DDR4-3733, with FCLK, UCLK and MCLK coupled 1:1:1 at about 1867 MHz. One dump has 1867.2683 / 1867.1724 / 1867.2683 / 1867.2683 at 0x0C0..0x0CC; five more reads 1 s apart gave 1867.00–1868.34.

## Sources
Every source is pinned to a commit:
- **RM** — hattedsquirrel/ryzen_monitor @7529c7c, `src/pm_tables.c`: https://github.com/hattedsquirrel/ryzen_monitor/blob/7529c7cfc2688d9781912c84adfc0343d83dc78c/src/pm_tables.c
  - Handler `pm_table_0x380905` is at L662–L860. Its own comment at L663 says "Pure guess. Derived from 0x380805 and 0x380904".
  - Handler 0x380904 is at L462–L660 and 0x380805 at L256–L460.
  - Display code is in `src/ryzen_monitor.c` (same commit) at L179, L187–L221, L242–L305.
- **ZSC** — irusanov/ZenStates-Core @869b7e8, `Hardware/PowerTable.cs` L300: `{ 0x380905, 0x5D0, 0xC0, 0xC8, 0xCC, 0xB4, 0x224, 0x228, 0x22C, 0x2B0, -1 }`.
  - Column order (L332): size, FCLK, UCLK, MCLK, VDDCR_SOC, CLDO_VDDP, CLDO_VDDG_IOD, CLDO_VDDG_CCD, CoresPower, MISC.
  - URL: https://github.com/irusanov/ZenStates-Core/blob/869b7e82572337e5a673e92230edc92bddf0e44b/Hardware/PowerTable.cs#L300
- **Table size** — 0x5D0 = 1488 is confirmed three ways: ZSC L300, amkillam/ryzen_smu @d298366 `smu.c` L990–991, and RM L858 (`min_size = 372*4`).
- **Comment error in RM** — `pm_tables.h` L370–373 says 0x3809xx is the 16-core part. That contradicts `pm_tables.c`, which sets `max_cores = 8` at L667. 372 floats fits the 8-core layout, so the code is right and the header comment is wrong.

Offset = element index × 4. All values are float32 little-endian.

## Field map, with example values
Values are from the example reading above. Rows in **bold** have two independent sources (RM and ZSC). Everything else is a ryzen_monitor label only, so treat it as unverified.

| Offset | Idx | Field (RM 0x380905 line) | Value |
|---|---|---|---|
| 0x000/0x004 | 0/1 | PPT limit / value (L672-3) | 142.0 W / 33.19 W |
| 0x008/0x00C | 2/3 | TDC limit / value (L674-5) | 95.0 A / 8.04 A |
| 0x010/0x014 | 4/5 | THM limit / value (L676-7) | 90.0 °C / 35.47 °C |
| 0x018/0x01C | 6/7 | FIT limit / value | 4520.6 / 0.0368 |
| 0x020/0x024 | 8/9 | EDC limit / value (L680-1) | 140.0 A / 76.79 A (RM rescales the EDC value by core usage, ryzen_monitor.c L184) |
| 0x028/0x02C | 10/11 | VID limit / value | 1.3005 V / 1.0522 V |
| 0x030–0x05C | 12–23 | *_WC / *_ACTUAL pairs | PPT_ACT 33.19, TDC_ACT 6.82, THM_ACT 35.47, EDC_ACT 76.79 |
| 0x060 | 24 | VDDCR_CPU_POWER | 8.737 W |
| 0x064 | 25 | VDDCR_SOC_POWER | 12.351 W |
| 0x068 | 26 | VDDIO_MEM_POWER | 10.137 W |
| 0x06C | 27 | VDD18_POWER | 0.963 W |
| 0x070 | 28 | ROC_POWER | 1.0 |
| 0x074 | 29 | SOCKET_POWER | 33.19 W |
| 0x078–0x0A4 | 30–41 | Disputed labels (see note A) | 4.4593, then 4.5482 ×7, then 1.3005, 1.3005, 1.0522, 1.0480 |
| 0x0A8 | 42 | CPU_TELEMETRY_CURRENT | 8.043 A |
| 0x0AC | 43 | CPU_TELEMETRY_POWER | 8.737 W |
| 0x0B0 | 44 | SOC_SET_VOLTAGE | 1.1995 V |
| **0x0B4** | 45 | **SOC_TELEMETRY_VOLTAGE = ZSC VDDCR_SOC** | **1.1129 V** |
| 0x0B8 | 46 | SOC_TELEMETRY_CURRENT | 11.103 A |
| 0x0BC | 47 | SOC_TELEMETRY_POWER | 12.351 W (1.11294 × 11.1031 = 12.357, matches) |
| **0x0C0** | 48 | **FCLK_FREQ** | **1867.268 MHz** |
| 0x0C4 | 49 | FCLK_FREQ_EFF (average) | 1867.172 MHz |
| **0x0C8** | 50 | **UCLK_FREQ** | **1867.268 MHz** |
| **0x0CC** | 51 | **MEMCLK_FREQ** | **1867.268 MHz** |
| 0x0D0–0x0FC | 52–63 | FCLK_{DRAM,GMI,IOHC,MEM_LATENCY,CCLK,XGMI} setpoint / busy | DRAM_BUSY 1.760, GMI_BUSY 1.845, IOHC_BUSY 0.124, CCLK_SETPOINT 3.571, rest 0. Units unknown. |
| 0x100–0x114 | 64–69 | CCM_READS, CCM_WRITES, IOMS, XGMI, CS_UMC_READS, CS_UMC_WRITES | 0.734, 0.266, 0.074, 0.074, 0.770, 0.282. Units unknown. |
| 0x118–0x124 | 70–73 | FCLK_RESIDENCY[0..3] (L749) | 100.0, 0, 0, 0 |
| 0x128–0x134 | 74–77 | FCLK_FREQ_TABLE[0..3] = FCLK DPM states (L750) | **1867.0**, 0, 0, 0 |
| 0x138–0x144 | 78–81 | UCLK_FREQ_TABLE[0..3] (L751) | **1867.0**, 0, 0, 0 |
| 0x148–0x154 | 82–85 | MEMCLK_FREQ_TABLE[0..3] (L752) | **1867.0**, 0, 0, 0 |
| 0x158–0x164 | 86–89 | FCLK_VOLTAGE[0..3] | all 0 |
| 0x168–0x1E4 | 90–121 | LCLK blocks (setpoint, busy, freq, freq_eff, max/min DPM, SOCCLK_eff, SHUBCLK_eff) ×4, stride 0x20 | setpoints 1.0; max/min 592.59 / 301.89 MHz; block 3 LCLK freq 592.52, eff 572.31; SHUBCLK 500 |
| 0x1E8–0x1F4 | 122–125 | XGMI setpoint / busy / width / rate | 20, 0, 0, 0 |
| 0x1F8 | 126 | SOC_POWER (marked `//x` for 0x380805, L369) | 0.0, unusable |
| 0x1FC | 127 | SOC_TEMP (`//o?` at L370; also ZSC `PawnIo/RyzenSmu.cs` L98 idx 127 = "SoC Temperature" for 0x380805) | 30.02 °C |
| 0x200 | 128 | DDR_VDDP_POWER | 5.468 W |
| 0x204 | 129 | DDR_VDDIO_MEM_POWER | 0 |
| 0x208 | 130 | GMI2_VDDG_POWER | 4.068 W |
| 0x20C | 131 | IO_VDDCR_SOC_POWER | 0 |
| 0x210 | 132 | IOD_VDDIO_MEM_POWER | 2.192 W |
| 0x214 | 133 | IO_VDD18_POWER | 0.963 W |
| 0x218 | 134 | TDP | 105.0 W |
| 0x21C | 135 | DETERMINISM | 0 |
| 0x220 | 136 | V_VDDM (cLDO_VDDM, ryzen_monitor.c L217) | 0.9504 V |
| **0x224** | 137 | **V_VDDP = CLDO_VDDP** | **0.8973 V** |
| **0x228** | 138 | **V_VDDG_IOD** | **0.9976 V** |
| **0x22C** | 139 | **V_VDDG_CCD** | **0.9976 V** |
| 0x230 | 140 | PEAK_TEMP | 56.5 °C |
| 0x234 | 141 | PEAK_VOLTAGE | 1.2188 V |
| 0x238 | 142 | PEAK_CCLK_FREQ | 4.55 GHz |
| 0x23C/0x240/0x244 | 143–145 | Unknown (RM L787-789) | 1.2062 / 49.8 / 58.83 |
| 0x248 | 146 | AVG_CORE_COUNT | 1.195 |
| 0x24C | 147 | CCLK_LIMIT | 4.4593 GHz |
| 0x250 | 148 | MAX_SOC_VOLTAGE (label doubtful: 0.97 V is below the 1.2 V set point) | 0.9739 |
| 0x254–0x274 | 149–157 | DVO_V, APML_POWER, CPU_DC_BTC, SOC_DC_BTC, CSTATE_BOOST, PROCHOT, PC6, SELF_REFRESH, PWM | 0, 33.13, 0.0155, 0, 7.48, 0, 0, 0, 0 |
| 0x278–0x2A0 | 158–168 | SOCCLK, SHUBCLK, SMNCLK, SMNCLK_EFF, MP0CLK, MP0CLK_EFF, MP1CLK, MP1CLK_EFF, MP5CLK, TWIXCLK, WAFLCLK | 500, 500, 500, 500, 457.1, 457.2, 457.1, 140.7, 400, 400, 0 |
| 0x2A4/0x2A8 | 169/170 | DPM_BUSY / MP1_BUSY | 37.17 / 30.43 |
| 0x2AC | 171 | DPM_Skipped (L815) | 1593.0 (see below) |
| **0x2B0**–0x2CC | 172–179 | **CORE_POWER[0..7]** (ZSC CoresPower) | 0.67, 1.47, 0.49, 1.52, 0.78, 0.67, 0.60, 0.89 W |
| 0x2D0–0x2EC | 180–187 | CORE_VOLTAGE | 0.883, 0.898, 0.860, 0.925, 0.891, 0.865, 0.860, 0.877 V |
| 0x2F0–0x30C | 188–195 | CORE_TEMP | 29.4–32.6 °C |
| 0x310–0x34C | 196–211 | CORE_FIT, CORE_IDDMAX | small values; 7.5–8.1 |
| 0x350–0x36C | 212–219 | CORE_FREQ | 3.54–3.76 GHz |
| 0x370–0x38C | 220–227 | CORE_FREQEFF | 0.24–1.19 GHz |
| 0x390–0x3EC | 228–251 | CORE_C0 / CC1 / CC6 | core 0: 10.16 + 79.54 + 10.30 = 100.0 %, which confirms the alignment |
| 0x3F0–0x46C | 252–283 | CKS_FDD, CI_FDD, IRM, PSTATE | CI_FDD is non-zero here (31.1, 1.7, …) although RM marks it "z" (always zero) for 0x380805. Label suspect. |
| 0x470 / 0x490 | 284 / 292 | FREQ_LIM_MAX / MIN | 4.5482 / 1.7813 GHz |
| 0x4B0–0x56C | 300–347 | CORE_unk, SC_LIMIT, SC_CAC, SC_RESIDENCY, UOPS_CLK, UOPS | 128.0 ×8, … |
| 0x570–0x58C | 348–355 | CORE_MEM_LATECY | 254.8, 195.6, 132.1, 222.9, 159.5, 191.1, 195.1, 157.7. Units unknown. |
| 0x590–0x5CC | 356–371 | L3 block | logic power 0.52 W, VDDM power 0.48 W, temp 30.58 °C, FIT 0.0151, IDDMAX 17.48, freq 4.024 / eff 4.020 GHz, CKS_FDD 0, CCA thr 2047.8, CCA CAC 13.18, CCA act 0, EDC lim 15.0, EDC CAC 0.206, EDC res 0, FLL_BTC 0.9957, MP5_BUSY 32.38 |

**Note A (idx 30–41).** The 0x380905 handler (RM L705–717) labels idx 36 and 37 as FIT_VOLTAGE and FIT_PRE_VOLTAGE. Both read 4.5482, which is a GHz value, not a voltage.

The 0x380904 handler (RM L508–520) fits the data better:

| Offset | Value | 0x380904 label |
|---|---|---|
| 0x078 | 4.4593 | CCLK_GLOBAL_FREQ |
| 0x07C–0x094 | 4.5482 | PPT, TDC, THM, HTFMAX, PROCHOT, VOLTAGE, CCA frequency limits |
| 0x098 | 1.3005 | FIT_VOLTAGE |
| 0x09C | 1.3005 | LATCHUP_VOLTAGE |
| 0x0A0 | 1.0522 | CPU_SET_VOLTAGE (equals VID_VALUE at 0x02C) |
| 0x0A4 | 1.0480 | CPU_TELEMETRY_VOLTAGE |

The 0x380905 handler calls 0x0A0 CPU_TELEMETRY_VOLTAGE, and ryzen_monitor prints it as "Peak Core Voltage" (ryzen_monitor.c L179).

## Memory-relevant values and arithmetic
- **Memory speed.** The DDR4 UMC ratio is MemClkFreq bits[6:0] / 3 (ZSC `Hardware/DRAM/Ddr4Timings.cs` L71). DDR4-3733 has raw ratio 56, so MCLK = 56/3 × 100 = 1866.67 MHz.
  - The SMU's DPM entries at 0x128, 0x138 and 0x148 are exactly 1867.0 (raw 0x44E96000). That reads as the integer-MHz form of 1866.67 (an inference).
  - Error: 1867 − 1866.67 = 0.33 MHz, or 0.018 %.
  - DMI agrees: configured speed 3733 MT/s (SMBIOS type 17).
- **Coupled mode is ON.** UCLK equals MCLK, which is ryzen_monitor's own test (ryzen_monitor.c L210).
- **DPM.** FCLK, UCLK and MCLK each have only DPM state 0 filled (1867), states 1–3 are 0, and FCLK residency is 100 % in state 0.
- **ZenTimings display.** ZenTimings takes frequency = pm MCLK × 2 for DDR4 (ZSC `BaseDramTimings.cs` L256–266) and shows it floored (ZenTimings @7532394 `WPF/ViewModels/MainViewModel.cs` L103). That gives 2 × 1867.0 = 3734, so it would show "3734 MT/s", assuming no BCLK correction is applied.
- **BCLK.** ZSC multiplies the pm_table FCLK, UCLK and MCLK by BCLK/100 (`PowerTable.cs` L632–645). So the pm_table reports clocks against a nominal 100 MHz and can't reveal the real BCLK.
- **Valid MCLK steps** are 33.33 MHz apart (ratio/3 × 100 MHz), for example 1866.67 (ratio 56, DDR4-3733), 1900.00 (ratio 57, DDR4-3800) and 1933.33 (ratio 58, DDR4-3866). A clock between those steps is not a DDR4 UMC ratio.
- **VSOC.** Set point 0x0B0 = 1.1995 V (repeated reads 1.2000–1.2015). Telemetry 0x0B4 = 1.1129 V (repeated reads 1.1112–1.1125); this is what ZenTimings shows as VSOC. ryzen_monitor.c L216 warns the set value "might be the default voltage".
- **SoC current and power.** 11.10 A (repeated reads 10.71–11.65 A); 12.35 W at both 0x064 and 0x0BC. SoC temperature is 30.02 °C at 0x1FC.
- **LDO voltages.** cLDO_VDDP 0.8973 V, VDDG_IOD 0.9976 V, VDDG_CCD 0.9976 V, VDDM 0.9504 V. These were the same in all repeated reads.
- **Memory-related power (SMU estimates, unverified labels).**

| Offset | Field | Value |
|---|---|---|
| 0x068 | VDDIO_MEM | 10.137 W |
| 0x200 | DDR_VDDP | 5.468 W |
| 0x208 | GMI2_VDDG | 4.068 W |
| 0x210 | IOD_VDDIO_MEM | 2.192 W |
| 0x06C | VDD18 | 0.963 W |

- **No DRAM voltage.** This table has no field for the DIMM supply (VDIMM) in any source.
- **0x2AC = 1593.0** is idx 171, labelled DPM_Skipped (RM L815, and L415 for 0x380805).
  - It read 1593.0 in the dump and in all 5 repeated reads, so it is not acting like a running counter.
  - `pm_tables.h` L249–251 says the DPM_Skipped / MP5_BUSY naming is uncertain.
  - What it means is unknown. No source ties it to memory.
