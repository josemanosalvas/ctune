# Runtime Curve Optimizer on Vermeer (Zen 3): SMU commands and encoding

**The per-core setter is MP1 0x35 and the all-core setter is MP1 0x36.** Earlier notes had them the other way round. Three independent sources agree. The per-core getter MP1 0x48 has only one source, so ctune treats it as unverified on a machine until a read-only probe returns the BIOS values for every core.

## Sources
Each is pinned to a commit.
- **ZSC.** irusanov/ZenStates-Core @869b7e8, the C# library behind ZenTimings and ryzen-smu-cli. CoreCycler drives ryzen-smu-cli.
- **UXTU.** JamesCJ60/Universal-x86-Tuning-Utility @eeb4304, `Universal x86 Tuning Utility/Scripts/AMD Backend/RyzenSmu.cs`.
- **ruv.** svenlange2/Ryzen-5800x3d-linux-undervolting @c08815d, `ruv.py`, a Linux script for the 5800X3D on ryzen_smu. Its README shows set and readback output on that CPU.
- **RSMU.** amkillam/ryzen_smu @d298366 (`smu.c`, `smu.h`, `drv.c`), driver 0.1.7, the version ctune was checked against.
- **CC.** sp00n/corecycler @a95b523, `configs/default.config.ini`, for the test method.

## Command IDs (MP1 mailbox)
| ID | Function | Sources |
|---|---|---|
| 0x35 | SetDldoPsmMargin, one core | ZSC `Hardware/Smu/Settings/Zen3Settings.cs` L22; UXTU L422 (`set-coper`, MP1, in `Socket_AM4_V2` = Matisse/Vermeer, L391); ruv L113-114 |
| 0x36 | SetAllDldoPsmMargin, all cores | ZSC Zen3Settings.cs L23; UXTU L420 (`set-coall`); ruv L147 (reset to 0) |
| 0x48 | GetDldoPsmMargin, one core | ruv L103-110 only |

- **Zen 2 is off by one.** ZSC `Zen2Settings.cs` L69-70 has 0x34 for one core and 0x35 for all cores. Zen3Settings overrides both. Sending a Zen 2 table to Vermeer would set every core at once. UXTU uses one table for Matisse and Vermeer, so its Matisse IDs disagree with ZSC. That does not matter here.
- **RSMU alternatives.** ZSC has 0x0A, 0x0B and 0x7C (get), each commented "Not sure" (Zen2Settings.cs L33-35), and Zen3Settings inherits them. UXTU lists RSMU 0x0A, 0x0B and 0x7C (`get-coper-options`, L432). ctune uses none of these.
- **0x48 on other families.** It is SetMaxSocClkFreq on Picasso APUs (ZSC `APUSettings0_Picasso.cs` L57). That is a different mailbox address and family. No source gives 0x48 another meaning on Vermeer.

## Argument 0
```
[31:28] CCD index      (0 on a single-CCD part)
[27:24] CCX index      (always 0 on Zen 3: one CCX per CCD)
[23:20] core index within the CCD (0-7)
[19:16] zero
[15:0]  margin, 16-bit two's complement (-30 is 0xFFE2, +5 is 0x0005)
```
- ZSC `Hardware/Smu/Commands/SetPsmMarginSingleCore.cs` L3-10 documents the layout, and L25 builds `(coreMask & 0xfff00000) | margin`.
- ZSC `Utils.cs` L180-184 `MakePsmMarginArg`: `(margin < 0 ? 0x100000 + margin : margin) & 0xffff`. That is 16-bit two's complement with bits 19:16 clear.
- ZSC `Cpu.cs` L932-937 `MakeCoreMask` for family > 17h: `(ccd << 28) | ((core % 8) << 20)`.
- ruv L114: `((core & 8) << 5 | core & 7) << 20 | value & 0xFFFF`. That is the same word, with core 8+ mapped to CCD 1.
- UXTU `Views/Pages/CustomPresets.xaml.cs` L1188-1189 (non-AM5 path): `(core << 20) | (value & 0xFFFF)`.
- Other arguments are 0. The all-core form takes only the margin, with no core bits.
- **Getter return.** ruv L105-110 reads response argument 0 and subtracts 2^32 when it exceeds 2^31. So the value comes back as a sign-extended 32-bit integer: -30 is 0xFFFFFFE2. Its README shows `Core 0 set to: -30 readback: -30`, and says requests beyond -30 read back as -30, so the SMU clamps.
- **Range.** The margin runs from -30 to +30 before Zen 4 (ZSC SetPsmMarginSingleCore.cs L4; CC config L539, L547).
- **Semantics.** Every tool treats the value as absolute, and the command is named Set, not Add. No source shows a set on a core whose BIOS margin was not 0, so an additive setter is not ruled out. ruv's machine most likely had no BIOS margin, and there an absolute and an additive setter both read back -30. A reboot restores the BIOS values (ruv README; CC config L523-526, "NOT permanent").

## Mailbox and driver behaviour
- **Addresses.** ryzen_smu uses MP1 cmd 0x3B10530, rsp 0x3B1057C and args 0x3B109C4 for Vermeer (`smu.c` L528-531). ZSC Zen3Settings.cs L11-13 and UXTU L393-395 agree.
- **Sysfs protocol.** Write exactly 24 bytes (six little-endian u32) to `smu_args`. Any other size is ignored (`drv.c` L223-228). Then write a 4-byte ID to `mp1_smu_cmd` (`drv.c` L169-186). Reading `mp1_smu_cmd` returns the status (1 = OK). Reading `smu_args` returns the six response words.
- **The status cannot prove a write took effect.** `smu_send_command` (`smu.c` L201-213) reports a failure only when the retries run out. A quick error response such as 0xFE (unknown command) or 0xFA (invalid argument) falls through, and the driver returns OK. ctune therefore reads the margin back with 0x48 after every write.
- **The argument buffer is shared.** `smu_args` is one buffer for every process using the driver, and the write and the command are two separate syscalls. ctune's helper serialises its own calls with a lock file; another program sending SMU commands at the same time could still interleave with it.
- **Driver identification.** `codename` 12 is CODENAME_VERMEER (`smu.h` L66-79). `mp1_if_version` 2 is IF_VERSION_11 (`smu.h` L100-103). A 5800X3D reports family 25 (0x19) and model 33 (0x21).

## Test method (CoreCycler defaults, CC config)
- Prime95 SSE with Huge FFTs (L47, L247, L265), one thread on the first logical CPU of the core (L136, L145).
- 6 minutes per core (L79). Random order on CPUs with 8 or fewer cores (L94). 15 s between cores (L197).
- Periodic suspend/resume to create load-to-idle transitions (L82-89).
- On an error, skip the rest of that core and continue (L117).
- ctune now runs mprime (Prime95 for Linux) the same way: SSE Huge FFTs by default, one thread pinned per core, suspended 1 s in every 10. The settings and their sources are in [reuse-tools.md](reuse-tools.md). The earlier stress-ng mix (integer, FFT, matrix, wide-vector, FMA and hash stressors that check their own results, plus a bursty phase at 25 % load with random 0 to 0.5 s busy slices) is still available as the `stress-ng` workload. With fixed 10 to 50 ms slices the pm_table never showed more than 3.7 GHz on the tested core; random slices reached 4.2 GHz.

## Open points
- **0x48 has one source.** `ctune cpu probe` checks it: the readback must equal the BIOS Curve Optimizer of every core, from the latest snapshot. The root helper refuses to write until a probe in the same boot has matched. Re-run the probe after every BIOS or SMU firmware change.
- **Checked on a 5800X3D** (B550 AORUS PRO, BIOS F16b, SMU 56.74.0, ryzen_smu 0.1.7):
  - With BIOS per-core margins of -20, -30, -30, -20, -20, -30, -30, -30, 0x48 returned exactly those values. The mixed pattern rules out a constant or a coincidence, and it confirms the sign-extended 32-bit return.
  - 0x35 with arg0 0x0010FFE2 (core 1, -30, a no-op) returned status OK and read back -30.
  - Both runs left zero corrected machine-check errors and no kernel SMU messages. A 1-minute screen of core 1 afterwards was green.
  - Still open: whether 0x35 sets an absolute value or adds to the current one. A no-op at -30 cannot tell the two apart, because -30 is the floor. Setting a -30 core to -25 can: an absolute setter reads back -25, and an additive one reads back -30, because -55 clamps, and `cpu_apply` reports the mismatch.
- **PBO2 Tuner** is closed source, so it was not checked directly. CoreCycler drives ryzen-smu-cli, which is built on ZSC.
