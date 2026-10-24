The live F16b setup configuration is fully decoded, and 33 of the 37 sanity checks match exactly. The other four are not mismatches: Power Down is set to Auto, two voltages could only be inferred, and ProcODT had no prior value to check. One finding matters: VCORE SOC is probably set to about 1.14–1.15 V, not 1.20 V.

**Files**
- Parser: `/var/home/josemanosalvas/.t3/scratch/2026-10-24-snapshot-the-current-ram-settings-01633672/scripts/hii_ifr.py`
- Decoder: `/var/home/josemanosalvas/.t3/scratch/2026-10-24-snapshot-the-current-ram-settings-01633672/scripts/bios_settings_decode.py` (runs in about 2 s with `python3 -I`)
- Outputs in `/var/home/josemanosalvas/.t3/scratch/2026-10-24-snapshot-the-current-ram-settings-01633672/snapshots/20261024-181828_F16b/decoded/`:
  - `bios_settings.json` and `bios_settings.md`
  - `hii_questions.json`: the raw question table from the parser.
  - `efivars_extra/`: copies of 13 small variables read from the live system because the snapshot didn't capture them (PlatformLang, BootOrder, Timeout and similar).
- There is also a `decoded/taskc_test/` folder. I didn't create it and left it alone.

**What was decoded**
- **HII database:** 14 package lists and 9 formsets: Setup (Gigabyte Tweaker, labelled "M.I.T." internally), AMD CBS, AMD Overclocking, AMD PBS, Realtek and PCI. That is 1823 questions over 1370 storage locations. Every scope opens and closes cleanly, and all 1823 question opcodes are accounted for.
  - One parser detail: this firmware sets the scope bit on the first opcode of a condition (e.g. `12 86` = EQ_ID_VAL with scope), so a NOT that follows sits inside that scope. The parser handles it.
  - Hidden/visible status is worked out from current values. I assumed SystemAccess = admin, since the setup password buffers are all zero. Conditions that depend on boot-time-only variables show as "?".
- **Where settings are stored:**
  - Tweaker → `Setup-ec87d643` at offsets 288–520.
  - AMD CBS → `AmdSetup`.
  - AMD Overclocking → `AOD_SETUP`.
  - Fan control → `GcSensorVarName`.
  - The CBS memory-timing fields currently mirror the Tweaker values.
- **StdDefaults:** the AMI NVAR format (UEFITool `common/nvram.h`) parses into 11 entries: Setup, PlatformLang, AMITSESetup, XhciDID, GcSensorVarName, PCI_COMMON, SioSetupData, UsbSupport, NetworkStackVar, SecureBootSetup and Setup-80e1202e. It has no entries for AmdSetup, AOD_SETUP or AMD_PBS_SETUP. The defaults in the setup forms agree with StdDefaults for 586 of 587 comparable questions; the one exception is the PlatformLang string.
- **Gigabyte profiles:** these are not NVAR. Each is a 0x38-byte header followed by records of (TSE variable index, length, raw variable bytes). For all 9 slots the length adds up, the record count matches, and the checksum byte equals the 8-bit sum of the payload. Record 0 is Setup, 110 is AOD_SETUP, 112 is AmdSetup. Slot 0 is unnamed (purpose unknown). Slots 1–8 hold user profiles:
  - 1 "latest ok": same as now except CSM off, Above 4G on, ReBAR Auto, plus fan settings.
  - 2 "TEMP" and 3 "ProctODT 40": a tighter timing set (see below).
  - 4 "14-15-15-15", 5 "RAMOK", 6 "PBO -30 + RAMOK".
  - 7 "core 1&3 -20": only core 4's Curve Optimizer value differs from now (−30 there, −20 now).
  - 8 "RAMPBO OK+FANS": Setup, CBS and AOD are byte-identical to the current settings; only fan options differ.
  - Each header carries "F16b8ARNR003", so they may not load on F19c.

**Sanity checks**
- Size check: every main store matches its declared size exactly (Setup 646, AmdSetup 1553, AOD_SETUP 1020, AMD_PBS_SETUP 128, GcSensorVarName 569, and so on). PlatformLang and BootOrder are variable-length by design.
- Values: 1453 of 1457 checkable values are legal options or in range. The four exceptions are PBS CPU Revision, a hidden AOD "Adjust VTT MEM" field, and the PlatformLang and BootOrder placeholders.
- These match exactly:
  - XMP off; memory multiplier 3733; CBS Memory Clock 1867 MHz (code 56, the same as the live MEMCLK ratio); FCLK 1867 MHz.
  - Timings 14-15-15-15-36-51, tRFC 288, tRRDS/L 4/4, tFAW 16, tWTRS/L 4/12, tWR 26, tRTP 14, tCWL 14, RDWR/WRRD 10/3, SCL 5/5, RDRD DD 6, WRWR DD 7.
  - Gear Down Enabled, 1T.
  - VDDP 900 mV, VDDG CCD/IOD 1000/1000 mV.
- Power Down is set to Auto, not Disabled; the live memory-controller register shows it disabled.
- VSOC and VDIMM are stored as codes that I could only convert by inference (see below).

**Memory, fabric and voltage settings** (full tables are in `bios_settings.md` under "Key settings")

| Item | Where set | Value | Auto or manual |
|---|---|---|---|
| Speed / mode | Tweaker | XMP Disabled, multiplier 3733, Memory Timing Mode = Manual (applies to both channels) | Manual |
| FCLK | CBS > XFR Enhancement | 1867 MHz; UCLK DIV1 Auto; AOD fabric setting Auto | FCLK manual |
| Primary/secondary timings | Tweaker, mirrored in CBS | as listed above; tRCD, tRFC2/4, tRCPAGE on Auto | Manual |
| ProcODT | Tweaker and CBS | 40 Ω | Manual |
| Command rate / Gear Down | Tweaker and CBS | 1T / Enabled | Manual |
| RttNom/Wr/Park, CAD bus setup and drive strengths | Tweaker and AOD | Auto | Auto |
| Power Down, BankGroupSwap/Alt, interleaving, TSME | CBS and AOD | Auto | Auto |
| DRAM Voltage | Tweaker | code 55 → about 1.550 V | Manual |
| DDRVPP / DRAM Termination | Tweaker | Auto | Auto |
| VCORE SOC | Tweaker | code 64 → about 1.150 V (1.1375 V if codes 0/1 are reserved) | Manual |
| SOC DVID / SOC LLC | Tweaker | Auto | Auto |
| AOD SoC voltage / SoC OC Mode / CBS SOC OVERCLOCK VID | AOD and CBS | 0 / Disabled / 0 (not applied; live VID is 70) | Off |
| VDDP / VDDG | AMD Overclocking | 900 mV Manual / "Global" 1000 + 1000 mV; Tweaker CPU VDDP is Auto | Manual |
| DF C-states, APBDIS, Global C-state, Power Supply Idle, CPPC, IOMMU | CBS and Tweaker | Auto; Fixed SOC Pstate hidden | Auto |
| PBO | AMD Overclocking | Advanced, PBO Limits "Disable" (AMD stock 142 W/95 A/140 A, matching the live table), Scalar Auto | Manual |
| Curve Optimizer | AMD Overclocking | Per Core, cores 0–7: −20, −30, −30, −20, −20, −30, −30, −30 | Manual |
| Other | Tweaker / Setup | Spread Spectrum Disabled, SVM Disabled, CSM Enabled, Above 4G off, ReBAR off | Mixed |

**How the two voltage codes were converted**
- Gigabyte stores a step code, not millivolts; 0xFFFF means Auto. The ranges come from Gigabyte's EasyTune voltage table ("GSAT") in the F19c image: VCORE SOC 0.750–1.600 V in 6.25 mV steps, DRAM 1.000–2.000 V in 10 mV steps. To check it: `/tmp/f19c_probe/big.bin` at offset 0x91ABF8, decompressed from `B550AORUSPRO.F19c`. The F16b copy needs root to read, so I assume it is the same.
- I'm assuming code 0 means the bottom of the range. For DRAM that gives 1.55 V, which agrees with the termination-voltage reading (~1.53 V). Counting from the top would give 1.45 V, which doesn't fit.

**Surprises**
- **VSOC is probably not 1.20 V.** The 1.20 V figure comes from the SMU's own set-point in the power table. The board's voltage regulator is driven by the Tweaker code instead, which works out to about 1.14–1.15 V. That fits the measured 1.11 V after normal load droop; if the regulator were at 1.20 V it would read about 1.18 V. A one-line look at Tweaker > Advanced Voltage Settings > VCORE SOC would confirm it.
- **Curve Optimizer and PBO are already on.** The earlier review listed Curve Optimizer as a future step, but per-core negative offsets are active now.
- **ProcODT is now known:** 40 Ω, set manually.
- **Profiles 2 and 3 hold a tighter, untested-looking set:** 14-14-14-22, tRC 36, tWR 16, tRTP 8, tWTRL 8, tCWL 12, tRFC 300, SCL 3/2, WRRD 1, RDRD SD/DD 3/4, WRWR SD/DD 5/6, DRAM code 52 (about 1.52 V), Power Down explicitly Disabled.
- **There are two SVM switches.** The Tweaker one is Disabled. A second copy under Peripherals > CPU Configuration says Enabled, but the live system has no KVM, so the Tweaker one controls it.
- **CPU Vcore is hidden** by a flag in ProcMitAttrib, consistent with the 5800X3D's locked core voltage (my inference).
- **`aminbmem.bin`** appears to hold copies of memory-controller address-mapping registers: 0x111107F1 and 0x22220001 equal the values at 0x500D0/0x500D4, and 0x87654321 equals 0x50050. These are not settings, so I didn't decode it further.