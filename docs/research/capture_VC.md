I found no errors in the decoders, but fixed five smaller bugs in `am4_bios_memcfg.py`. Two of them made the decode silently drop data whenever a file couldn't be written to `--dump-dir`. All 82 offline tests pass after the fixes.

**What I checked**
- **ZenStates-Core:** I cloned it fresh at `/tmp/review_zsc/repo`; HEAD is `869b7e8`, the same commit as `/tmp/zsc_taskc`.
- **Timing-block search:** I wrote a separate port of ZenStates-Core's search (`ApobChannelTimings.Read` + `ApobDdr4ModeRegisters.Find`) and ran both on 3,000 random entries holding 4,857 blocks. Every result matched: block offsets, MEMCLK, clock bytes, tRFC, channel and P-state numbering, and MR offsets. The only extra matches from the script are the flagged relaxed tCCD_L ones.
- **MR0–MR6 fields:** every bit field and code table matches coreboot `ddr4.c` / `ddr4.h` and JESD79-4.
- **SPD and XMP 2.0:** offsets, fine-timebase pairs, CRCs, enable/DPC bits and clock rounding match `Ddr4SpdDecoder.cs`. The XMP voltage byte is read as binary hundredths, as ZenStates-Core does. Bits 6:0 can't hold BCD 00–99, so binary is the only consistent reading.
- **Other APOB entries:** the SPD records, boot info, SMBIOS info, event log, memory map and entry names all match ZenStates-Core and openSIL `ApobCmn.h`.
- **JEP106 table:** all 43 manufacturer codes match ZenStates-Core; 12 names are shortened.
- **EDK2 pool header:** matches `Pool.c` (`0a503bc`) lines 15-45, 395-440 and 515-540.
- **/dev/mem:** every open is read-only and all new reads go page by page. The only multi-page single read is the old AODT read in `aod()`. The kernel already checks each page there, so it is no less safe, and I left it alone to keep its EPERM hint.
- **Crash safety:** an extra fuzz of 4,000 corrupt APOB tables let no exception escape. The only guard hit is the deliberate "entry data too short" message.
- **CLI paths:** I ran the real sibling-board SSDT (`/tmp/aod_dsl/linuxhw/tables/SSDT7.dat`) live as non-root with `--ssdt` only, and offline with a region file and an APOB file. Exit code is 0 on every path. Against the old script, the original sections differ only in live PM-table values and the intended EACCES wording.

**Changes**
1. **`dump()`, lines 83-91:** if `--dump-dir` was missing or unwritable, the write error stopped `aod()`/`apob()` before they stored their buffers. The AOD decode and every APOB-detail section were then lost. A failed save now prints "NOT saved" and the decode continues; normal output is unchanged.
2. **`save()`, lines 360-372, and the NVS line at 1453:** the same problem in the new sections. A failed write dropped the SPD decodes and the Easy\* and NVS lines. It now prints a note and records it in `OUT["errors"]`. The NVS line no longer claims "no --dump-dir" when one was given but couldn't be written.
3. **SPD duplicate check, line 851:** it skipped bytes 320-322 (module maker and location), yet printed "same SPD except manufacturing date/serial". It now compares `[:323]`, so only the date (323-324) and serial (325-328) may differ.
4. **Raw card, lines 733-739:** `chr(65+card)` named the wrong letter from code 8 on, because JEDEC skips I, O, Q, S, X and Z. It also printed "A (extended)" for the extension bit and ignored the revision extension in byte 128. It now uses the JEDEC letters for codes 0-19, taken from i2c-tools `decode-dimms` (commit `24553db`, lines 1561-1586), and prints a plain code number otherwise. The revision follows `Ddr4SpdDecoder.cs` lines 443-445.
5. **APOB entry list, lines 1016-1019:** the text printed each entry's non-zero byte count, against the agent's own rule that values changing every boot go only in the JSON. Trained data changes those counts each boot, which would add noise to `ram_compare.py` diffs. The count now goes only to the JSON, and the text just flags "(data all zero)".

**Tests:** I re-ran the offline suite into `snapshots/20261024-181828_F16b/decoded/taskc_test/`, which now records the fixed script's sha256 (`8168dfaa…`), plus my ZenStates-Core port check and fuzz (`/tmp/review_work/zsc_port_check.py`, `/tmp/review_work/fuzz2.py`). All pass.

**Left unchanged, for your judgement**
- **Diff noise:** the Easy\* "first bytes as text" line and buffer addresses may also change between boots; I can't tell until a real run.
- **Reserved ranges:** the reader accepts "Reserved" memory below 0xC0000000. On this machine's map that includes the legacy VGA/ROM area and 0xBF000000-0xBFFFFFFF (probably the protected SMM area). It is only reached if an Easy\* variable points there; today all three point into the NVS range 0xBC9F2000-0xBD0D8FFF.
- **"DDR4-3734":** the timing blocks show 2 × MEMCLK 1867, the same way ZenStates-Core does.
- **Old SSDT search:** if any SSDT's AODT/AODE region has a non-constant address, the search stops there and the AOD section fails. This predates the agent's changes.

Files are in `/var/home/josemanosalvas/.t3/scratch/2026-10-24-snapshot-the-current-ram-settings-01633672/`:
- `scripts/am4_bios_memcfg.py`
- `snapshots/20261024-181828_F16b/decoded/taskc_test/test_results.txt`