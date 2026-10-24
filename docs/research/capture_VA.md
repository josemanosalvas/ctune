**Verdict:** I checked every requested setting with my own parser, written from the UEFI spec and not reusing any of their code. All the raw values match the report. I found six problems: one mis-stated voltage, one overstated voltage check, one visibility bug in their decoder, and some imprecision in the profile summary, a mislabelled table row and a mistaken StdDefaults statement. None of them changes a timing value.

**How I checked**
- The snapshot efivar copies are byte-identical to the live `/sys/firmware/efi/efivars` files.
- I walked the HII package lists and string blocks myself (UEFI 2.10 §33.3.1 and §33.3.6, EDK2 `UefiInternalFormRepresentation.h`). For each question I located it by prompt and read the store ID, offset and width straight from the opcode bytes. I then mapped the store ID to the formset's own store declaration and read the bytes from the efivar file.
- Their counts reproduce exactly:
  - 14 package lists and 9 formsets.
  - 1823 question opcodes, with every scope closing.
  - Store sizes Setup 646, AmdSetup 1553, AOD_SETUP 1020.
  - 1453 of 1457 values legal, with the same four exceptions.
  - StdDefaults parses to the same 11 entries; its Setup defaults agree with the form defaults at all 368 offsets.
  - Profile checksums and lengths, Profile 1 differing only in CSM/Above 4G/ReBAR, Profile 7's core-4 Curve Optimizer value, and Profile 8 being identical apart from fans.
  - The `aminbmem.bin` register copies.

**Discrepancies**

1. **VSOC is probably 1.1375 V, not 1.150 V.** The forms themselves show that codes 0 and 1 are not voltages:
   - Dynamic VCORE SOC (DVID) is greyed out whenever the VCORE SOC code is 0, 2 or higher, so it is only editable at code 1. Its help text says DVID only works when the voltage is set to "Normal", so code 1 = Normal.
   - CPU Vcore's default is code 0, in both the form default and StdDefaults, so code 0 must be Auto.
   - Voltage codes therefore start at 2, and code 64 gives 0.750 V + 62 × 6.25 mV = **1.1375 V**.
   - Their conclusion that VSOC is not 1.20 V still holds. Gigabyte's GSAT table drives VCORE SOC by writing the voltage regulator's output directly (PMBus VOUT_COMMAND 0x21 on the RAA229004, loop B).
   - With about 11 A of SoC current, 1.1375 V fits the 1.1125 V reading better than 1.15 V does.
   - The GSAT table is identical in both copies in F17 and in F19c, so assuming F16b matches is reasonable but still unproven.
2. **DRAM voltage "1.550 V MATCH" is overstated.** 1.55 V is only right if code 0 means 1.000 V. If code 0 is Auto, as it is for CPU Vcore, code 55 means 1.54 V. The termination reading (0.763 V, about 1.53 V VDIMM, with roughly ±22 mV resolution) can't tell the two apart. Their check passes on a hard-coded `v == 55`, and the VSOC and ProcODT checks are hard-coded the same way.
3. **Decoder bug: page reachability is ignored.** A question counts as visible if nothing hides it inside its own page, even when every link to that page is permanently hidden.
   - The "second SVM switch" (Setup@565, Enabled) sits on AMI's CPU Configuration page. The only link to it (IFR 0x62f3c) is under a hide-always condition (IFR 0x62f30).
   - Their own JSON marks that link as hidden but the SVM question as visible. So there is only one user-visible SVM switch, the Tweaker one (Disabled).
   - I listed every page that is reachable only through always-hidden links; none is a memory, CBS-memory or AMD Overclocking page.
4. **Profiles are summarised imprecisely.**
   - Profile 4 ("14-15-15-15") has the current Tweaker values, but its CBS copy holds the tight set (tRCD/tRP 14, tRAS 22, tRC 36, tWR 16, tRTP 8, tCWL 12, tRFC 300…).
   - "Power Down explicitly Disabled" applies only to Profile 2 (AmdSetup@259 = 0). Profile 3's CBS memory fields are all Auto.
   - Profile 0's Setup copy is byte-identical to the current settings.
5. **Cosmetic:** the Key-settings row "tRDRD SC/SD/DD" (Setup@408) is only tRDRDSC.
6. **StdDefaults statement is wrong.** Their summary says the form defaults disagree with StdDefaults only on the PlatformLang string, but PlatformLang is one of the four invalid-value placeholders. My check found no disagreements in the Setup store.

**Their parser code (`hii_ifr.py`, `bios_settings_decode.py`):** the four areas you asked about are correct; the bugs I found are the ones listed above.
- **String IDs:** resolved within each package list's en-US strings, with the block types matching EDK2 (SKIP2 0x21, SKIP1 0x22, extended-block lengths include the header).
- **Scope handling:** the "scope bit on the first condition opcode" is standard EDK2 compiler output (`VfrSyntax.g`, "Extend OpCode Scope only for the root expression"), and their flattening handles it correctly.
- **Value width:** option values use the option's own type field and questions use the flags field, both correct.
- **suppressif:** the three-valued evaluation and the ternary operand order are right. Assuming SystemAccess = 0 (admin) is safe, because the forms grey out every item when it equals 1.

**Corrected memory-related settings (F16b, live)**

| Setting | Current | Auto/Manual |
|---|---|---|
| XMP | Disabled | default |
| Memory multiplier / CBS MemClk | 3733 / code 56 = 1867 MHz | Manual |
| FCLK (CBS) / UCLK DIV1 / AOD fabric | 1867 MHz / Auto / Auto | Manual / Auto / Auto |
| Memory Timing Mode | Manual (channel B fields 0xFFFF, unused) | Manual |
| tCL-tRCDRD-tRCDWR-tRP-tRAS-tRC | 14-15-15-15-36-51 (combined tRCD field Auto) | Manual |
| tRFC / tRFC2 / tRFC4 | 288 / Auto / Auto | Manual / Auto |
| tWR / tRTP / tCWL | 26 / 14 / 14 | Manual |
| tRRD_S/L, tFAW, tWTR_S/L | 4/4, 16, 4/12 | Manual |
| tRDWR / tWRRD | 10 / 3 | Manual |
| RDRD SC/SD/DD/SCL; WRWR SC/SD/DD/SCL | 1/4/6/5; 1/6/7/5 | Manual |
| tCKE / tRCPAGE | 1 / Auto | Manual / Auto |
| Command rate / Gear Down | 1T / Enabled (Tweaker and CBS) | Manual |
| Power Down (CBS and AOD) | Auto (live register: disabled) | Auto |
| ProcODT | 40 Ω (code 0x38, Tweaker and CBS) | Manual |
| RttNom / RttWr / RttPark | Auto | Auto |
| CAD setup times / drive strengths | Auto (Tweaker 0xFFFF/0xFF; CBS user controls Auto) | Auto |
| BankGroupSwap / Alt, interleaving, TSME | Auto | Auto |
| DRAM Voltage | code 55 ≈ 1.54–1.55 V (inferred) | Manual |
| DRAM Termination / DDRVPP | Auto | Auto |
| VCORE SOC | code 64 ≈ **1.1375 V** (inferred; 1.150 V less likely) | Manual |
| SOC DVID / SOC LLC | Auto (DVID greyed out) | Auto |
| AOD SoC OC Mode / SoC voltage / CBS SOC OC VID | Disabled / 0 / 0 (CBS SoC OC Mode Auto) | not used |
| VDDP | AOD Manual 900 mV (Tweaker CPU VDDP Auto) | Manual |
| VDDG | AOD Global, CCD 1000 / IOD 1000 mV (the 700 mV fields are hidden) | Manual |
| DF C-states / APBDIS / Fixed SOC Pstate | Auto / Auto / hidden | Auto |
| Curve Optimizer | Per Core, cores 0–7: −20, −30, −30, −20, −20, −30, −30, −30 | Manual |

Files are in `/var/home/josemanosalvas/.t3/scratch/2026-10-24-snapshot-the-current-ram-settings-01633672/`:
- **Scripts** (`scripts/`):
  - `verify_hii_settings.py`: the parser
  - `verify_memory_table.py`: the memory-settings table
  - `verify_lookup.py`, `verify_dumpops.py`: lookup and raw-opcode dump helpers
  - `verify_gsat_aml.py`: reads the GSAT voltage table
  - `verify_lzma_find.py`: finds compressed sections in BIOS images
  - `verify_profiles.py`: profile check
- **Outputs** (`snapshots/20261024-181828_F16b/decoded/verify_independent/`):
  - `memory_settings.tsv` and `.json`
  - `questions_all.json`
  - `profiles_diff.txt`
  - `gsat_F17.txt`, `gsat_F19c.txt`
  - `ifr_tweaker_voltages_dump.txt`, `ifr_aod_vddp_vddg_dump.txt`