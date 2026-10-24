I extended `am4_bios_memcfg.py` and wrote a test script. All 82 offline checks pass, using synthetic buffers and a fake `/dev/mem` image. None of the new decoders has been tested against this PC's real APOB or AODT. That needs the `iomem=relaxed` boot.

**Compatibility.** The CLI and the three original sections (AOD, APOB 1/2, PM table) print byte-for-byte what they did before; a test compares against a saved copy of the old script. The existing code changed in five small ways:
- `/dev/mem` can be swapped for a test file.
- The buffers already read are reused by the new sections.
- A corrupt APOB HeaderSize is clamped so it can't read past the table.
- `--dump-dir` is created if missing. `ram_snapshot.py` already creates it, but a manual run would crash in the AOD section without this.
- A not-root error (EACCES) now says "run as root" instead of suggesting `iomem=relaxed`. The EPERM message text is unchanged.

**What the next reboot will capture (new sections, printed after the PM table):**
- **APOB entry list:** group/type, size, non-zero bytes, and which entries are encrypted.
- **Timing blocks with MR0–MR6** (the ABL's per-channel timing blocks), found with ZenStates-Core's search (commit `869b7e8`). For each channel and P-state it shows the ABL's timings in clocks, tRFC1/2/4 in ns, and a full decode of MR0–MR6:
  - CL, WR/RTP, CWL, tCCD_L
  - RttNom, RttWr, RttPark, DRAM Ron
  - VrefDQ as a % of VDDQ, plus mV when the AOD VDIMM value is available
  - read/write preamble, gear-down, DBI, CA parity, and so on
- **SPD per slot** from the APOB, saved as `apob_spd_<slot>.bin`, with:
  - part number, module and DRAM manufacturer, date, serial, CRC check
  - JEDEC timings
  - each XMP 2.0 profile: speed, CL-tRCD-tRP-tRAS-tRC, tRFC1, voltage, DIMMs per channel
- **The AOD buffer's SPD copies**, compared byte-for-byte with the APOB copies.
- **Other APOB data:** SMBIOS memory info (per-DIMM speed and voltage), boot info (date of the last full memory training), the training-failure and MBIST entries, the firmware event log, and the memory map.
- **Gigabyte EasyTuneSetup / EasyVoltage / EasyHealth buffers**, dumped raw:
  - The address comes from the EFI variable.
  - The size comes from the firmware allocator's header just in front of each buffer; if that isn't there, it reads 64 KB.
  - As a self-check, the same method is applied to the AOD buffer, whose length is known.
  - Only firmware RAM ranges are read, never device (MMIO) regions.
- **Every "ACPI Non-volatile Storage" range dumped whole:** about 7.1 MB, about 0.1 s. This is a safety net: if any decoder or size guess is wrong, the raw data is still captured. It is my addition beyond the brief, on by default with `--dump-dir`; `--no-nvs-dump` turns it off. These ranges hold firmware data, so treat the dumps as local-only.
- **`bios_memcfg.json`** in the dump folder with everything decoded. Values that change every boot (hashes, non-zero counts) go only in the JSON, so `ram_compare.py` diffs stay clean.

New options are `--devmem`, `--efivars`, `--memmap`, `--acpi-dir` and `--no-nvs-dump`. Format sources are cited by file and line in the code: ZenStates-Core `869b7e8`, openSIL `9648171` `ApobCmn.h`, EDK2 `0a503bc` `Pool.c`, coreboot `80dd4ac` `ddr4.c/h`, JESD79-4 §3.5, JESD21-C Annex L.

**Test evidence** (`test_results.txt`):
- **Mode registers:** decoding matches coreboot's encoder tables for every CL, WR and CWL. 20,000 random MR sets never raise.
- **SPD:** a hand-built Patriot/Samsung 4400 C19 SPD decodes exactly (JEDEC 2133 15-15-15-36-51; XMP 4400 19-19-19-39-58, tRFC1 770, 1.45 V; second profile 3600). A bad CRC is detected. 3,000 random or short images never raise.
- **Timing blocks:** 4 blocks are found with the right channel, P-state and active flag. MRs are found at both offsets ZenStates-Core mentions (+0x26 and +0x62).
- **Fuzzing:** 2,038 corrupt APOB tables, 600 corrupt AOD buffers, and 40 corrupt file pairs through the whole program. No exception escaped, every section header still printed, and no decoder ever needed its error guard.
- **Fake `/dev/mem` run** (the exact path the reboot uses):
  - finds the APOB at 0xA200000
  - all three Easy* sizing cases behave correctly
  - the AOD self-check reports consistent
  - dumped files equal the planted bytes
  - runs in 0.1 s, well inside `ram_snapshot.py`'s 60 s limit
- **Failure modes:**
  - Simulated root without `iomem=relaxed`: hints printed, a partly refused range is saved zero-filled.
  - The real machine as non-root: exits 0 and explains each refusal.

**Residual uncertainty:**
- **Timing-block layout:** ZenStates-Core worked it out from DDR4-2133 dumps.
  - Two of the timing positions are tentative.
  - The MR offset on Vermeer is unknown.
  - Its MEMCLK search can latch onto the tFAW value; I added a note that flags this.
  - At 3733 the block's tCCD_L may exceed the MR6 maximum of 8. ZenStates-Core would then find no MRs; I added a flagged relaxed match.
- **Per-DIMM MR sets:** ZenStates-Core finds one set per channel. My scan for further sets right behind it is speculation and is labelled tentative.
- **SMBIOS info and memory map:** layouts were confirmed only on Zen 5. The boot-info date being BCD on Zen 3 is ZenStates-Core's claim.
- **SPD sources:** that the Zen 3 APOB contains the SPD entry at all is unverified. Reading the AOD buffer's SPD copies with the APOB record layout rests only on the matching 0x214 record size, and that buffer may be empty until the firmware fills it.
- **Easy* buffers:** their contents are undocumented. The sizing assumes AMI uses the standard EDK2 allocator header; the AOD self-check will confirm or refute that on the real run.
- **Manufacturer names:** only about 45 common vendors are built in; others print as bank/code.
- **Live readings:** the non-root test run also read the live PM table once: FCLK/UCLK/MCLK 1877.67 MHz and VSOC 1.080 V, against 1867 MHz and 1.112 V in the baseline. That's a single sample and I didn't look into it.

To re-run the tests: `python3 -I scripts/test_am4_bios_memcfg.py snapshots/20261024-181828_F16b/decoded/taskc_test`. The reference sources are cloned under `/tmp/zsc_taskc`, `/tmp/taskc_src_opensil`, `/tmp/taskc_src_edk2` and `/tmp/taskc_src_coreboot`. Nothing was written to `/sys`, `/dev` or EFI variables, and no root was used.

Files are in `/var/home/josemanosalvas/.t3/scratch/2026-10-24-snapshot-the-current-ram-settings-01633672/`:
- `scripts/am4_bios_memcfg.py`
- `scripts/test_am4_bios_memcfg.py`
- `snapshots/20261024-181828_F16b/decoded/taskc_test/`, which holds:
  - `test_results.txt`
  - `devmem_run.txt`
  - `offline_run.txt`
  - `eperm_simulation.txt`
  - `nonroot_live_run.txt`
  - `devmem_run_bios_memcfg.json`
  - `am4_bios_memcfg.before_taskC.py`