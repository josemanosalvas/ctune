# Zen 3 and DDR4 tuning rules

Sources are the integralfx DDR4 OC guide, Taraquin's OCN Zen 3 guide (with anta777's formulas), the ZenStates-Core register map and PCGH's 5800X3D scaling test. The research with citations is in `docs/research/` and `docs/REVIEW-2026-10-24.md`.

## Timings
- tRC = tRAS + tRP, and tRAS ≥ tRCD + tRTP.
- tFAW ≥ 4 × tRRDS. A lower tFAW gains nothing.
- tWR = 2 × tRTP, because the DRAM's MR0 register encodes them as a pair. Keep both even.
- tWTRL is about tWR / 2, and tWTRS is 3 or 4.
- **Gear-down mode (GDM)** latches commands on every other clock. The BIOS forces tCL, tCWL, tWR and tRTP to even values, and odd tRCD, tRP and tRC run one clock longer. Tune in even steps.
- **Two single-rank DIMMs per channel** give two ranks per channel. tWRRD needs 3 or more. The different-DIMM turnarounds (tRDRDDD, tWRWRDD) matter, and the same-DIMM ones (SD) are unused. With the 2T preamble the DD floor is 5.
- tRDRDSCL and tWRWRSCL: 5 is acceptable. B-die usually reaches 4/4, then 3/3. When only one fails, it is usually tRDRDSCL.
- tRFC on B-die is typically 135 to 160 ns. It is the most heat-sensitive timing. If errors appear only when warm, raise it by 16.
- AM4 has no tREFI setting. With PowerDown disabled, tCKE has no effect.

## Voltages and fabric
- The target is FCLK = UCLK = MCLK. Above the CPU's fabric ceiling you get corrected errors in the fabric or PCIe banks, or random reboots at idle.
- VDDG must sit at least 40 mV under the real VSOC (the cLDO dropout). VDDP must sit at least 0.1 V under VDIMM and at most 1.05 V. Some single VDDP values fail while their neighbours work.
- SoC voltage above about 1.15 V can reduce stability. Four DIMMs need more SoC voltage than two. A user with this board and kit reported needing at least 1.12 V.
- At high FCLK, PCIe link errors sometimes disappear when the chipset or GPU link is forced to PCIe Gen3.
- ProcODT for 4 × 8 GB is usually 28 to 48 Ω. With two DIMMs per channel, RttNom matters because the idle DIMM terminates the bus. RTT 6/3/3 (in RZQ divisors) is a common starting point and fails on some kits.

## Error signatures
The `errors` tool groups machine-check banks by subsystem. Each group points at a different fix:
- Fabric banks (coherent_station, pie, param_block): FCLK, VDDG or VSOC.
- PCIe/IO bank (nbio): the PCIe link at this FCLK. Try Gen3.
- Memory controller banks (umc): memory timings, voltage or termination.
- Core banks (load_store, l2_cache, decode_unit, execution_unit, floating_point): Curve Optimizer.

Bit errors on non-ECC DIMMs raise no machine check. They show up as test failures, crashes or btrfs checksum errors.

## Payoff on a 5800X3D
The 96 MB L3 cache hides most memory latency. Perfect tertiaries are worth about 0.5 to 1.5 % average FPS when CPU-bound, more on 1 % lows, and about 0 % when GPU-bound. Tell the user this when they weigh effort against gain.
