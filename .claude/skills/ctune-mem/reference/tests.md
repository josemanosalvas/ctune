# RAM tests

`mem_test` runs each of these over the free RAM while memtest_vulkan heats the case, so the DIMMs warm up the way they do in a game.

- **Screen** (25 min): stressapptest -W.
- **Fabric**: the screen with NVMe reads for 90 min, then 20 min idle, because random reboots at idle are the classic FCLK symptom.
- **TM5**: TestMem5 with the anta777 Extreme1 profile under Proton; three cycles of it is the integralfx guide's minimum for a stable result. It runs as long as TM5 needs for the RAM it covers, and `minutes` only caps it. Install it once with `ctune mem tm5-setup`. Swap is off for TM5, so ask the user to close large apps first.
  - Inconclusive: the scope ran out of memory, or TM5 had not started testing after 5 min.
  - Red: TM5's log reports errors, or a TM5 message box stopped it mid-test (a worker that died); the result quotes the box.
- **memtest86+** needs a boot from USB, so ctune cannot run it. It adds coverage of the memory the running OS holds; offer it when the user wants a full-coverage pass.

## Reading a TM5 failure
TM5 records no address, bit or worker. Its log gives the error total and sometimes the failing test's number and time; the result's `screenshot` shows TM5's window with the errors per test.
- Look the test up in `tools/tm5/extreme@anta777.cfg` (`[TestN]`: its `Function` and pattern), and in the profile's `Test Sequence` to see where it runs in each cycle. Community error tables number the tests of the 1usmus_V3 profile, so their numbers mean other tests in this profile.
- Errors that come and go between cycles point at a marginal timing, voltage or heat.
- TM5 loads every core, so a Curve Optimizer margin too deep for that load miscomputes a comparison, and TM5 counts it as a memory error with no machine check. When TM5 fails with no `umc` or core bank, rule the CPU out first: soften the suspect core by 10 (`cpu_apply`, two steps of 5), keep the timings, and run one TM5 cycle.
- With no clean TM5 yet, test the campaign's starting configuration first: a pass there limits the suspects to the campaign's steps, and a failure there points at the CPU, tRRD and tFAW, tRFC or heat.
