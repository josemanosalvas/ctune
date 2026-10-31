# The integralfx DDR4 OC guide

The guide is the community reference for DDR4 tuning. This note lists what the [ctune-mem skill](../../.claude/skills/ctune-mem/SKILL.md) and its [rules](../../.claude/skills/ctune-mem/reference/rules.md) take from it. Source: `DDR4 OC Guide.md` in integralfx/MemTestHelper, branch `oc-guide` at 480c3a562c7ce2c4e77fe90efa1c9c7ec686d444 (https://github.com/integralfx/MemTestHelper/blob/oc-guide/DDR4%20OC%20Guide.md).

## Order
- Baseline: DRAM PowerDown off, and on AMD Gear Down Mode on. Starting voltages on AMD: 1.10 V SOC, 0.95 V VDDP, 0.95 V VDDG CCD, 1.05 V VDDG IOD.
- Loose primaries from the guide's table, then "Increase DRAM frequency until unstable", with FCLK at half the effective DRAM speed on Ryzen 3000 and 5000.
- Secondaries: tRRDS, tRRDL and tFAW; tWR and tRTP; tRFC; then tWTRS, tWTRL and tCWL.
- Tertiaries: tRDRDSCL and tWRWRSCL on AMD.
- Primaries again: tCL, then tRCD (on AMD before tRP), then tRP.
- Derived: tRAS, then tRC.
- Command rate last: GDM off with 1T on AMD.
- "frequency should be prioritized over tighter timings".

## Testing
- "Make sure to run a memory test and benchmark after each change to ensure performance is improving."
- Minimum coverage for a stable result, one of: "TM5 anta777 Extreme: 3 cycles", "Karhu RAMTest: 5000 %", "MemTestHelper (HCI MemTest): 20 % per thread", "OCCT Memory: 30 minutes each for SSE and AVX". More TM5 configs widen the coverage.
- AIDA64's memory test and Memtest64 are "not very good at finding memory errors".
- "it is good to stress test your GPU while running a memory test to simulate stability while gaming."
- Benchmarks: run 3 to 5 times and average.
- Heat: "Generally, the hotter your RAM is, the less stability it will have at higher frequencies and/or tighter timings." tRFC has to rise with temperature.

## Timings
- ns = 2000 × timing / DDR frequency.
- tRAS = tRCD(RD) + tRTP, tRC = tRP + tRAS, tWR = 2 × tRTP (citing the Micron datasheet).
- The lowest tFAW that still changes performance is 4 × tRRDS or 4 × tRRDL, whichever is lower.
- Presets, Safe / Tight / Extreme: tRRDS tRRDL tFAW 6 6 24 / 4 6 16 / 4 4 16; tWR tRTP 20 10 / 16 8 / 12 6; tWTRS tWTRL 4 12 / 4 10 / 4 8; tCWL tCL / tCL−1 / tCL−2; AMD tRDRDSCL tWRWRSCL 4 4 / 3 3 / 2 2.
- Floors: tRTP 5 (6 with GDM); tWTRS and tWTRL 1 and 6; some boards accept tCWL down to tCL−6.
- tRFC: 350 ns is the default for 8 Gb ICs. Samsung 8 Gb B-die (S8B) reaches 120 to 180 ns. Search it by halving.

## AMD
- "Geardown mode (GDM) is automatically enabled above DDR4-2666." It forces even tCL, tCWL, tRTP and tWR. Performance order: GDM off 1T, then GDM on 1T, then GDM off 2T.
- "Desynchronising MCLK and FCLK can incur a massive latency penalty."
- ProcODT: 28 to 40 Ω on Ryzen 3000 and 5000; higher values can stabilise at the cost of voltage.
- CAD bus: "Often, a drive strength above 24 ohms may hurt stability."
- VDDG at least 40 mV below SOC; VDDP at most VDIMM − 0.1 V. Near 1800 MHz FCLK, "intermittent RAM training errors may be alleviated or eliminated by increasing VDDG CCD". Negative SOC scaling usually starts between 1.15 and 1.25 V.
