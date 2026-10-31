# Zen 3 and DDR4 tuning rules

Sources: the integralfx DDR4 OC guide (quoted in `docs/research/ddr4-oc-guide.md`), Taraquin's OCN Zen 3 guide with anta777's formulas, the ZenStates-Core register map and PCGH's 5800X3D scaling test. The research with citations is in `docs/research/`.

## Presets
The guide's Safe, Tight and Extreme values. Tight is the usual daily target for B-die; Extreme is the floor to try.

| Timing | Safe | Tight | Extreme |
|---|---|---|---|
| tRRDS / tRRDL / tFAW | 6 / 6 / 24 | 4 / 6 / 16 | 4 / 4 / 16 |
| tWR / tRTP | 20 / 10 | 16 / 8 | 12 / 6 |
| tWTRS / tWTRL | 4 / 12 | 4 / 10 | 4 / 8 |
| tCWL | tCL | tCL−1 | tCL−2 |
| tRDRDSCL / tWRWRSCL | 4 / 4 | 3 / 3 | 2 / 2 |

Convert between clocks and time with ns = 2000 × clocks / MT/s, to compare settings at different speeds.

## Timings
- tRC = tRAS + tRP, and tRAS is at least tRCD + tRTP.
- tFAW is at least 4 × tRRDS. A lower tFAW gains nothing.
- tWR = 2 × tRTP, because the DRAM's MR0 register encodes them as a pair. Keep both even. tRTP goes down to 6 with GDM.
- tWTRL is about tWR / 2, and tWTRS is 3 or 4.
- **Gear-down mode (GDM)** latches commands on every other clock. The BIOS forces tCL, tCWL, tWR and tRTP to even values, and odd tRCD, tRP and tRC run one clock longer. Tune in even steps.
- **Two single-rank DIMMs per channel** give two ranks per channel. tWRRD needs 3 or more. The different-DIMM turnarounds (tRDRDDD, tWRWRDD) matter, and the same-DIMM ones (SD) are unused. With the 2T preamble the DD floor is 5.
- Step tRDRDSCL and tWRWRSCL down together through the presets. When only one fails, it is usually tRDRDSCL.
- tRFC on B-die spans 120 to 180 ns, and 135 to 160 ns is the usual daily range. It is the most heat-sensitive timing. If errors appear only when warm, raise it by 16. Search it by halving the gap between a passing and a failing value.
- AM4 has no tREFI setting. With PowerDown disabled, tCKE has no effect.

## Voltages and fabric
- The target is FCLK = UCLK = MCLK. Above the CPU's fabric ceiling you get corrected errors in the fabric or PCIe banks, or random reboots at idle.
- VDDG must be at least 40 mV below the real VSOC (the cLDO dropout). VDDP must be at least 0.1 V below VDIMM and at most 1.05 V. Some single VDDP values fail while their neighbours work.
- SoC voltage above about 1.15 V can reduce stability. Four DIMMs need more SoC voltage than two. One report from a B550 AORUS PRO with four DIMMs needed at least 1.12 V.
- At high FCLK, PCIe link errors sometimes disappear when the chipset or GPU link is forced to PCIe Gen3.
- At an FCLK near 1800 MHz, intermittent training failures often go away with more VDDG CCD.
- ProcODT on Ryzen 5000 is usually 28 to 40 Ω, and 4 × 8 GB sometimes needs up to 48 Ω. With two DIMMs per channel, RttNom matters because the idle DIMM terminates the bus. RTT 6/3/3 (in RZQ divisors) is a common starting point and fails on some kits.

## Error signatures
The `errors` tool groups machine-check banks by subsystem. Each group points at a different fix:
- Fabric banks (coherent_station, pie, param_block): FCLK, VDDG or VSOC.
- PCIe/IO bank (nbio): the PCIe link at this FCLK. Try Gen3.
- Memory controller banks (umc): memory timings, voltage or termination.
- Core banks (load_store, l2_cache, decode_unit, execution_unit, floating_point): Curve Optimizer.

Bit errors on non-ECC DIMMs raise no machine check. They show up as test failures, crashes or btrfs checksum errors.

## Payoff on a 5800X3D
The 96 MB L3 cache hides most memory latency. Perfect tertiaries are worth about 0.5 to 1.5 % average FPS when CPU-bound, more on 1 % lows, and about 0 % when GPU-bound. Tell the user this when they weigh effort against gain.
