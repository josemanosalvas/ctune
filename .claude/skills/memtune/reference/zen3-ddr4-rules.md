# Zen 3 / DDR4 tuning rules

Sources: integralfx DDR4 OC guide, Taraquin's OCN Zen 3 guide (anta777 formulas), ZenStates-Core register map, PCGH 5800X3D scaling. Full research with citations: `docs/research/` and `docs/REVIEW-2026-10-24.md`.

## Timing relationships
- tRC = tRAS + tRP. tRAS ≥ tRCD + tRTP.
- tFAW ≥ 4 × tRRDS. Lowering tFAW below that gains nothing.
- tWR = 2 × tRTP (the DRAM's MR0 encodes them as a pair). Keep both even.
- tWTRL ≈ tWR/2, tWTRS 3–4.
- **GDM (gear-down)**: commands latch on every other clock. tCL/tCWL/tWR/tRTP are forced even, and odd tRCD/tRP/tRC effectively run one clock longer. Tune in even steps.
- **2 DIMMs per channel, single rank**: two ranks per channel, so tWRRD ≥ 3. tRDRDDD/tWRWRDD (different DIMM) matter; SD (same DIMM) is unused. With 2T preamble the DD floor is 5.
- tRDRDSCL/tWRWRSCL: 5 is acceptable, 4/4 then 3/3 on B-die; if only one fails it is usually RDRDSCL.
- tRFC (B-die): 135–160 ns typical. It is the most heat-sensitive timing; warm-only errors → raise by 16.
- tREFI is not adjustable on AM4. PowerDown off makes tCKE irrelevant.

## Voltages and fabric
- FCLK = UCLK = MCLK (1:1). Above the CPU's fabric ceiling you get corrected errors in fabric/pcie banks, or random reboots at idle.
- VDDG ≤ VSOC − 40 mV (cLDO dropout). VDDP ≤ VDIMM − 0.1 V, ≤ 1.05 V; VDDP can have "holes" where single values fail.
- SoC above ~1.15 V can scale negatively. 4 DIMMs need more SoC than 2 (≥ 1.12 V reported on this board+kit).
- PCIe link errors at high FCLK are sometimes fixed by forcing PCIe Gen3 on the chipset or GPU link.
- ProcODT 28–48 Ω for 4 × 8 GB. RTT for 2DPC: RttNom matters (non-target DIMM terminates); 6/3/3 (RZQ) is a common start but not universal.

## Error signatures (from `errors`)
- fabric (coherent_station, pie, param_block) → FCLK/VDDG/VSOC.
- pcie/io (nbio) → PCIe link at that FCLK; try Gen3.
- memory controller (umc) → memory timings/voltage/termination.
- core banks (load_store, l2, decode, execution, floating_point) → Curve Optimizer, not memory.
- Bit errors on non-ECC DIMMs never raise an MCE: they show only as test failures, crashes, or btrfs checksum errors.

## What it is worth on a 5800X3D
The 96 MB L3 hides most memory latency: perfect tertiaries ≈ +0.5–1.5 % average FPS when CPU-bound, more on 1 % lows, ~0 % GPU-bound. Say so when the user weighs effort.
