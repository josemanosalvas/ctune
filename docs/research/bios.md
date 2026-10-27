# B550 AORUS PRO (rev 1.0): BIOS history, AGESA and flashing

I checked the firmware of a board running F16b, the BIOS history, the AGESA strings inside each downloadable image and the vendor and AMD sources. The BIOS list is solid. Two points stay unverified: the exact AGESA where Curve Optimizer opened up for the 5800X3D, and any memory/FCLK regressions on later AGESA.

**Reference board.** On a B550 AORUS PRO running F16b, `/sys/class/dmi/id` reports board_name "B550 AORUS PRO", bios_version F16b and bios_date 02/08/2023. gigabyte.com returns HTTP 403 (Akamai) to both WebFetch and curl. So the changelog text below comes from Wayback snapshots of https://www.gigabyte.com/Motherboard/B550-AORUS-PRO-rev-10/support (the snapshot date is in each `web.archive.org/web/<timestamp>/` URL).

**Rev 1.1 / 1.2.** No such revisions exist for this board. Gigabyte only lists "B550 AORUS PRO (Rev. 1.0)"; the page has no revision selector and Wayback has no rev-11/rev-12 pages. These are separate boards with their own BIOS files, so never cross-flash:
- **B550 AORUS PRO AC (rev-1x):** files `mb_bios_b550-aorus-pro-ac_8arnr002_*` (web.archive.org/web/20260912163830/https://www.gigabyte.com/Motherboard/B550-AORUS-PRO-AC-rev-1x/support).
- **B550 AORUS PRO V2:** files `..._8arnr203_*`.
- **B550 AORUS PRO (rev 1.0):** files `mb_bios_b550-aorus-pro_8arnr003_*`.

**What F16b ships.** The 20230311200332 snapshot lists: "F16b | 19.09 MB | 2023/02/08 | Checksum : 7636 | Update AMD AGESA V2 1.2.0.8 | Optimized performance for 5800X3D". The previous release was F15 (2023-01-12, AGESA 1.2.0.7). The F16b image itself could not be checked: `download.gigabyte.com/FileList/BIOS/mb_bios_b550-aorus-pro_f16b.zip` now returns 404 and Wayback has no copy.

**Every BIOS after F16b.** Dates and changelogs are verbatim from the Gigabyte page. The "Embedded AGESA" column is the `AGESA!V9 ComboAM4v2PI …` string found in each official image after decompressing its LZMA sections. Withdrawn versions are no longer on the download server, so their embedded string is unknown.

| BIOS | Date | Checksum | Gigabyte changelog | Embedded AGESA | Status |
|---|---|---|---|---|---|
| F16c | 2023-03-22 | 62EE | "Update AMD AGESA V2 1.2.0.8, Optimized performance for 5800X3D" (snapshot 20230531145821) | unknown | withdrawn |
| F16g | 2023-09-20 | A7A7 | "Update AMD AGESA V2 1.2.0.B, Supports AMD Picasso APU on B550 and A520 MBs, Fix AMD processor vulnerabilities security" (snapshot 20231201122614) | unknown | withdrawn |
| F17 | 2024-03-27 | 2E91 | "Update AMD AGESA V2 1.2.0.B, Fix AMD processor vulnerabilities security, Addresses potential UEFI vulnerabilities. (LogoFAIL)" | 1.2.0.B | available |
| F18b | 2024-07-11 | F5F8 | "Update AMD AGESA V2 1.2.0.C" | 1.2.0.C | available |
| F18d | 2024-09-02 | 3484 | "Update AMD AGESA 1.2.0.Cc for fix Sinkclose Vulnerability of AMD processors (SMM Lock Bypass)" | 1.2.0.Cc | available |
| F18g | 2025-03-11 | A837 | "Update AMD AGESA V2 1.2.0.E for fix AMD CPU microcode signature verification vulnerability (CVE-2024-36347)" | 1.2.0.E | available |
| F18i | 2025-09-03 | 9D24 | "AMD AGESA ComboV2 1.2.0.F, Update TPM-B FW for Raven2/ Picasso, Cezanne, Vermeer/ Matisse & Renoir CPUs, Fix TPM2.0's out-of-bounds read vulnerability (CVE-2025-2884)" (snapshot 20251024193311) | unknown | withdrawn, replaced by F18 |
| F18 | 2025-10-29 | 7F48 | Same text as F18i | 1.2.0.F | available |
| F19a | 2026-04-14 | 624A | "Update AM4 AGESA ComboV2 1.2.0.11, Fix AMD Athlon and Ryzen processor vulnerabilities" | 1.2.0.11 | available |
| F19c | 2026-08-18 | 43EF | "Update AM4 AGESA ComboV2 1.2.0.12, Fix AMD TPM Reference Code Errata (CVE-2026-6726, CVE-2026-6727)" | 1.2.0.12 | available, latest as of snapshot 20261001104850 |

- **Dates:** the Last-Modified headers on the download server run a few days to a month after the listed dates: F17 2024-03-29, F18b 07-16, F18d 09-05, F18g 2025-03-24, F18 10-31, F19a 2026-05-13, F19c 08-21.
- **Gaps:** Gigabyte never shipped AGESA 1.2.0.A, 1.2.0.Ca/Cb, 1.2.0.D or 1.2.0.10 for this board.
- **Missing betas:** Wayback only has snapshots every few months, so short-lived betas may be missing. Probes for f16a–h, f17a–e, f18a/c/e/f/h/i/j, f19/f19b/d/e and f20 all return 404 now.

**Memory-relevant changes since F16b.** None of Gigabyte's changelogs after F16b mention memory, DRAM compatibility or FCLK. The last memory entry for this board is F4 (2020-08-20): "Improve memory compatibility". The Vermeer memory support list is dated 2021-09-14.

AMD's security bulletins tie each later AGESA to a security fix for Vermeer, not to memory changes. Each URL is https://www.amd.com/en/resources/product-security/bulletin/amd-sb-XXXX.html:
- **1.2.0.B (2023-08-25):** Inception (AMD-SB-7005).
- **1.2.0.cb (2024-07-30):** Sinkclose, CVE-2023-31315 (AMD-SB-7014).
- **1.2.0.D (2024-11-07):** CVE-2024-0179 and CVE-2024-21925 (AMD-SB-7027). Gigabyte skipped 1.2.0.D; I'm assuming 1.2.0.E includes its fixes, but no source says so.
- **1.2.0.E (2025-01-22):** EntrySign, CVE-2024-36347 (AMD-SB-7033). Also Transient Scheduler Attacks, CVE-2024-36350/36357, together with OS updates (AMD-SB-7029).
- **1.2.0.F (2025-04-30):** TPM CVE-2025-2884 (AMD-SB-4011).
- **1.2.0.12 (2026-05-27):** CVE-2026-6726/6727 (AMD-SB-7064).

ASUS changelogs for the same AGESA versions also describe them as security/stability releases (rog.asus.com/support/webapi/product/GetPDBIOS?website=global&model=ROG-STRIX-B550-F-GAMING&siteID=www&systemCode=rog). One is 3002 (2023-03-14): "Update AGESA version to ComboV2PI 1208, Mitigate the AMD potential security vulnerabilities".

**fTPM stutter fix.** F16b already has it, as does F15. AMD article PA-410 (https://www.amd.com/en/resources/support-articles/faqs/PA-410.html) says the fix needs a BIOS "based on AMD AGESA 1207 (or newer)". ASUS STRIX B550-F 2803 (2022-04-29) says: "Update AMD AM4 AGESA V2 PI 1.2.0.7. Fix AMD fTPM issue causes random stuttering."

**fTPM and TPM firmware changes after F16b.**
- F18/F18i: "Update TPM-B FW for … Vermeer".
- ASUS 3404 (AGESA 1.2.0.B, 2023-10-30): "Update AMD 5000/3000 Series CPU fTPM version, please back up Bitlocker recovery key before updating".
- My inference: any TPM2-sealed secret (BitLocker, or LUKS enrolled with systemd-cryptenroll) needs its recovery key on hand before flashing.

**FCLK / memory-OC regressions on later AGESA.** I couldn't find any primary or reputable source reporting one for 1.2.0.B through 1.2.0.12 on this board or B550 generally. Searches only turned up forum anecdotes about older AGESA (1.2.0.0, 1.2.0.3B). Treat this as unknown, not as "no regressions".

**X3D Curve Optimizer.** This is not verified to a specific AGESA version.
- **At launch:** HotHardware (2022-06-30, https://hothardware.com/news/msi-kombo-strike-improve-5800x3d-performance) wrote that "AMD also disabled all voltage controls" on the 5800X3D, meaning "no Curve Optimizer, or any undervolting at all—unless you have a board that supports it". MSI added it through beta BIOSes ("Kombo Strike"); that article doesn't name an AGESA version.
- **Today:** TechPowerUp (2026-08-21, https://www.techpowerup.com/review/amd-ryzen-7-5800x3d-10th-anniversary/26.html) says: "What's new since 2022 is that Curve Optimizer is available. Modern BIOS versions enable undervolting … AMD artificially caps the offset range at ±30 (both directions, all-core and per-core)". It doesn't name an AGESA version either.
- **1.2.0.8:** forum posts claim 1.2.0.8 brought PBO/Curve Optimizer controls back for the 5800X3D, but others on the same AGESA say they didn't get it. ASUS's 1208 changelogs say nothing about it. Gigabyte's only related wording is F16b's "Optimized performance for 5800X3D".
- **Neither is confirmed:** I found no vendor changelog or AMD statement for "1.2.0.8" or "1.2.0.A". The surest check is to look for the Curve Optimizer option in the board's own setup screens.

**Flashing caveats.**
- **Profiles may not survive.** Gigabyte's manual says: "Always load the Optimized defaults after updating the BIOS" (manual p.38, https://download.gigabyte.com/FileList/Manual/mb_manual_b550-aorus-pro-ac_1001_e.pdf). The same page says profiles can be saved as "Setup Profile 1~ Setup Profile 8" or to a USB/HDD file. I found no Gigabyte statement on whether stored profiles survive a flash, or whether a profile from F16b loads on F17+. Users report they don't, so record every RAM/PBO setting by hand (or with a ZenTimings snapshot) before flashing.
- **Back up the current image first.** Withdrawn versions such as F16b can no longer be downloaded from Gigabyte (404), and the board has a single flash chip ("1 x 256 Mbit flash", manual p.9). Before updating, save the current image with Q-Flash "Save BIOS to Drive". That is the only way back to a withdrawn version. No source covers AM4 anti-rollback either way.
- **Q-Flash procedure** (Gigabyte's UEFI guide, linked from FAQ 4337; archived at web.archive.org/web/20261016084637/https://www.gigabyte.com/FileUpload/Global/WebPage/20/images/utiltiy_qflash_uefi.pdf):
  1. Extract the zip; the image is `B550AORUSPRO.F19c` and similar.
  2. Copy it to a FAT32/16/12 USB drive.
  3. Press <End> during POST, or <F8> in setup.
  4. Choose Update BIOS From Drive and select the file. Gigabyte warns: "Make sure the BIOS update file matches your motherboard model."
  5. Don't power off or unplug the drive while it flashes, then reboot.
  6. Load Optimized Defaults.
  7. Save & Exit.
- **Q-Flash Plus** (works with the system off in S5): use the labelled "USB 3.2 Gen 1 Port (Q-Flash Plus Port)" and the Q-Flash Plus button; the LED flashes until it's done (manual p.13). Rename the file to `GIGABYTE.bin` on a FAT32/16 drive. The rename step comes from Gigabyte's X570 "Unique Features" guide p.6 (download.gigabyte.com/FileList/Manual/mb_manual_x570-features.pdf); the B550 manual just points to that "Unique Features" page.

**Side notes.**
- **Microcode:** a 5800X3D on F16b shows microcode 0xa201211 in `/proc/cpuinfo`. The images contain 0xa20120e (F17, F18b), 0xa201210 (F18d) and 0xa201213 (F18g through F19c), so 0xa201211 is probably loaded by Linux rather than F16b (inference).
- **ryzen_smu:** a BIOS update also replaces the SMU firmware, so pm_table_version 0x00380905 and its offsets may change. That's untested; re-check after flashing.
