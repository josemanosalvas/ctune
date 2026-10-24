# Facts report: Patriot "4400 C19 Series" memory kit, IT8792E voltage channels, and 5800X3D FCLK with 4 DIMMs

## 1) Which memory kit this is

**Identification: high confidence.** The kit is Patriot Viper Steel DDR4-4400 CL19, 16 GB (2x8 GB), SKU PVS416G440C9K.
- Overclockers.com reviewed this SKU (Bartosz Waluk, 2019-03-06) and published a Thaiphoon Burner screenshot of the module's SPD, which I downloaded and read. It shows:
  - Manufacturer "Patriot Memory" and part number "**4400 C19 Series**". This is the same string your DMI reports.
  - Serial number **00000000h**.
  - Label "8GB 1Rx8 PC4-2133P-UA0-10", 1 rank, 8 Gb die, 78-ball FBGA, raw card **A0 (8 layers)**.
  - DRAM manufacturer and part number "Undefined"; manufacturing date "Undefined"; made in Taipei.
  - JEDEC profile: 1067 MHz, 15-15-15-36-50, 1.20 V.
  - **XMP profile 1: 2198 MHz (DDR4-4400), 19-19-19-39-69 (tRC 69), 1.45 V.**
  - **XMP profile 2: 2132 MHz (DDR4-4266), 19-19-19-39-68 (tRC 68), 1.35 V.**
  - Supported CAS 10-16. PassMark's entry for "4400 C19 Series 8GB" agrees: "PC4-17000, 1.2V, CAS 10-16".
  - Sources: https://www.overclockers.com/patriot-viper-steel-16gb-ddr4-4400-memory-kit-review/ , https://www.memorybenchmark.net/ram.php?id=13349
- **The DRAM chips are Samsung 8 Gb B-die, K4A8G085WB-BCPB.** This rests on physical teardowns, not on the SPD:
  - TweakTown removed the heat spreader and found "Samsung K4A8G085WB BCPB".
  - KitGuru (2019-09) says the same.
  - Tom's Hardware describes "8GB single-rank modules equipped with Samsung's venerable B-Die".
  - Overclockers.com notes a similar IC and PCB (A0) in Team Xtreem kits.
  - Sources: https://www.tweaktown.com/reviews/8946/patriot-viper-steel-extreme-performance-ddr4-4400-review/index.html , https://www.kitguru.net/components/memory/ryan-martin/patriot-viper-steel-ddr4-4400mhz-16gb-2x8gb-memory-review/ , https://www.tomshardware.com/reviews/patriot-viper-steel-ddr4-4400-c19-16gb-memory-kit,5991.html
- **Other Patriot lines:** I found no other Patriot 4400 CL19 SKU.
  - Viper 4 Blackout 4400 (PVB416G440C8K) is CL18, 18-26-26-46, 1.45–1.5 V, so it is a different product.
  - The only 4400 CL19 Viper Steel kit I found is the 2x8 GB one. Its SPD string for a Blackout version is unverified.
- **Caveats on the IC:**
  - The SPD cannot prove B-die because it leaves the DRAM manufacturer field empty.
  - An OcUK forum post (poster not verified; page returned 403) says the 4000/4400 kits use "Samsung B-Die IC, but its not screened".
  - An OCN post claims later production switched to Hynix under the same part number. I could not open that thread (402, archive offline), so treat it as unverified.
  - **Inference:** your running settings (3733 CL14-15-15, tRFC 288 = 154 ns, RRDS/RRDL/FAW 4/4/16) are typical of B-die only. Taraquin's OCN guide gives B-die tRFC of 256–304 at DDR4-3800 (135–160 ns), versus starting points of 560 for Hynix and 600 for Micron. If this setup passes a memory test, that is strong evidence all four DIMMs are B-die.
- **Serial 00000000: medium-high confidence it is the real SPD value, not a BIOS quirk.**
  - The 2019 review sample of this exact SKU had serial 00000000h in its SPD.
  - Your BIOS reports non-zero serials in DIMM 0 of both channels, so it does copy the SPD serial field. A quirk that only hits DIMM 1 is unlikely, though not ruled out.
  - The non-zero serials 0x101206F0 (B1) and 0x101206FF (B1… correction: A1 is 0x101206FF) are 15 apart, which suggests they were programmed in the same run.
  - **Inference:** this pattern (a serialised pair in A1/B1 and a zero-serial pair in A2/B2) fits two 2x8 kits from different production runs. Reading the SPD would settle it (manufacturing date is undefined on the sample, so it may not help).
- **DMI voltage of 1.2 V:** the raw Type 17 bytes are B0 04 three times, i.e. 1200 mV for min, max and configured. That is the SPD's nominal JEDEC voltage, not the real VDIMM.
- **Mixing two kits:**
  - Gigabyte's manual says: "recommended that memory of the same capacity, brand, speed, and chips be used."
  - I found no Patriot statement and no documented issue specific to this SKU.
  - One OCN user on a B550 Aorus Pro with a 5800X ran "2 different kits of 2x8 cl19 4400 Viper steel" at 4x8 GB "3800mhz CL14". The same user reported 4 sticks of anything needed at least 1.12 V VSOC.
  - Another user ran "Patriot 4400 kits" at 3600 CL16, 1.35 V, on an ASRock B550 Extreme4.
  - These are anecdotes from 2021-09-29: https://www.overclock.net/threads/5800x-cant-go-high-speed-memory-with-4-dimms-sr.1794389/ (I read it via Wayback).

## 2) IT8792E voltage channels on the B550 AORUS PRO and the real VDIMM

**Channel labels: high confidence.**
- **LibreHardwareMonitor source** (master @ feb0e0e, `SuperIOHardware.cs` lines 2930–2946, IT8792E section for `B550_AORUS_PRO`, same as the X570 Aorus Master/Pro/Ultra):

  | Input | Label | Scaling |
  |---|---|---|
  | in0 | "VIN0" | none |
  | in1 | **"DDRVTT AB"** | none |
  | in2 | "Chipset Core" | none |
  | in3 | "Voltage #4" (hidden) | none |
  | in4 | "CPU VDD18" | none |
  | in5 | "PM_CLDO12" | none |
  | in6 | "Voltage #7" (hidden) | none |
  | in7 | 3VSB | ×2 |
  | in8 | Vbat | ×2 |

  "None" means LHM's defaults Ri=0, Rf=1, i.e. no divider.
- **The DRAM voltage itself is on the main IT8688E**, input in6 "DIMM" (lines 2536–2548, no scaling). That chip is not supported by the in-tree `it87` driver: this kernel's `it87.ko` lists IT8689E but not IT8688E. The out-of-tree frankcrawford/it87 driver does support it.
- **ADC step size:**
  - Linux `it87.c` uses 10.9 mV per step for the IT8792E (`FEAT_10_9MV_ADC`, `adc_lsb()` = 109). 255 steps × 10.9 mV = 2.780 V, which is why your in6 shows exactly 2.780.
  - LHM uses 11 mV, because the author of PR #1036 ("Add support for Gigabyte B550 Aorus Pro sensors", merged 2023-04-03) found that value matched Gigabyte SIV, the BIOS and HWiNFO.
- **Reference readings from a real B550 AORUS PRO** (PR #1036 screenshots, which I downloaded and viewed):
  - BIOS PC Health: "DDRVtt A/B 0.682 V", "DRAM Channel A/B Voltage 1.380 V", CPU VDD18 1.815, CHIPSET Core 1.056, PM_2V5 2.503.
  - HWiNFO 7.42, IT8792E section: VIN0 1.815, DDR VTT 0.682, Chipset Core 1.056, VIN3 1.705, CPU VDD18 1.815, PM_CLDO12 1.518.
  - LHM: IT8688E DRAM 1.380; hidden Voltage #7 2.805, which is also saturated.
- **Your readings versus that reference** (raw ADC counts = reading ÷ 10.9 mV):

  | Input | Your raw count | Reference raw count | Match |
  |---|---|---|---|
  | in0 | 164 | 165 | within 1 |
  | in2 | 96 | 96 | identical |
  | in4 | 164 | 165 | within 1 |
  | in5 | 137 | 138 | within 1 |
  | in6 | 255 | 255 | both saturated, so in6 is a meaningless input |
  | in3 | 156 | 155 | matches (Linux's "+3.3V" 3.401 is doubled as internal AVCC; HWiNFO shows the undoubled 1.705) |
  | **in1 (VTT)** | **70** | **62** | **only channel that differs: +8 steps** |

  So the reference board's mapping fits yours.
- **in5 is not VDIMM.** It read 1.518 V on the reference board while that board's DRAM was at 1.380 V. Its meaning ("PM_CLDO12") is unverified. No DDR4 VPP channel is mapped for this board; the BIOS "PM_2V5" source chip is unknown.
- **VDIMM estimate:** DDR4 VTT is VDDQ/2. On the reference board, DRAM ÷ VTT = 1.380 ÷ 0.682 = 2.02.
  - 2 × 0.763 V (Linux step size) = **1.526 V**
  - 2 × 0.770 V (11 mV step size) = **1.540 V**
  - Scaled from the reference board: 70/62 × 1.380 = **1.558 V**. Rounding in both readings gives a range of about 1.53–1.59 V.
  - **Best estimate: actual VDIMM is about 1.53–1.56 V.** The Gigabyte BIOS/SIV "DRAM" readout would probably show about 1.55 V.
  - Confidence: medium-high, roughly ±30 mV. It is clearly not 1.2 V, and it is above the kit's 1.45 V XMP rating. A VDIMM of 1.45 V would read about 66 steps or fewer.
  - Sources: https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/master/LibreHardwareMonitorLib/Hardware/Motherboard/SuperIOHardware.cs , https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/pull/1036 , https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/drivers/hwmon/it87.c

## 3) 5800X3D FCLK range, and the effect of 4 DIMMs on a daisy-chain board

**FCLK: medium confidence.** I found no statistically valid survey; all of this is reviews and anecdotes.
- **AMD (via PC Gamer, 2020-11-05):** "AMD cannot guarantee any samples… will be able to hit these speeds [1900–2000]… some current Ryzen 5000 samples are able to achieve a 1900+ fabric speed." https://www.pcgamer.com/amd-zen-3-infinity-fabric-lottery/
- **Tom's Hardware 5800X3D review (2022):** "as with most Ryzen chips… we were only able to reach DDR4-3800 with the fabric dialed in at 1900 MHz." https://www.tomshardware.com/reviews/amd-ryzen-7-5800x3d-review/4
- **SkatterBencher #39 (5800X3D, 2 DIMMs, 2022-04-14):** "I was able to increase the fabric clock to 2133 MHz, but there are WHEA errors… if the frequency is over 1900 MHz." https://skatterbencher.com/2022/04/14/skatterbencher-39-amd-ryzen-7-5800x3d-overclocked-to-4741-mhz/
- **HardForum (5800X3D, 2023-08-30):** WHEA-19 errors at 2000, 1966 and 1933; no POST at 1900 (a "hole"); the user settled on 3600 CL14 with no WHEA errors. https://hardforum.com/threads/5800x3d-upgrade-help.2030224/
- **OCN Zen 3 guide (Taraquin, 2022-04-19):** "Most Zen 3 can do… fclk of 1900"; at 1933 or higher "most will get lots of WHEA19s (some 5600(X), 5700X and 5800(X) on 2dimm motherboards can run without WHEA19)". The same guide says 4x8 GB and dual-rank setups need more SOC/VDDG voltage than 2x8 GB.
  - A reply in the same thread disagrees: "most end up at 3733".
  - https://www.overclock.net/threads/a-guide-to-ram-overclocking-on-zen-3.1798093/
- **Tom's Ryzen 5000 RAM guide:** claims 2000 MHz for "most" chips, but does not mention WHEA testing.
- **Inference:** the 5800X3D uses the same 12 nm GlobalFoundries I/O die as other Vermeer chips (AMD spec page), so its FCLK spread should be the same. Roughly: 1800–1867 is common, 1900 often works but not always, and WHEA-free 1933–2000 is uncommon.
- Your snapshot (1867 FCLK 1:1:1, VSOC 1.20 V set) does not show whether you get WHEA-equivalent errors.

**4 DIMMs (2 per channel, single rank) on a daisy-chain board:**
- **AMD's official speed limits for the 5800X3D:** 2x1R DDR4-3200, 2x2R DDR4-3200, **4x1R DDR4-2933**, 4x2R DDR4-2667. Four single-rank DIMMs lose one speed step. https://www.amd.com/en/products/processors/desktops/ryzen/5000-series/amd-ryzen-7-5800x3d.html
- **Gigabyte:**
  - The manual recommends A2/B2 for 2 modules, i.e. the slots at the end of the chain.
  - The spec pages list overclocked speeds up to DDR4-5400.
  - Gigabyte's B550 AORUS marketing describes the memory routing as daisy-chain and says it removes the stub effect for one DIMM per channel. I only saw this through search snippets; the pages returned 403.
  - One OCN user believed his B550 Aorus Pro was T-topology, which conflicts with this. I found no schematic either way.
- **Why 4 DIMMs are harder on daisy-chain:** with two DIMMs per channel, the second load and the reflections between the DIMMs lower the maximum speed. Patent US9791899 says daisy-chain is "generally… inferior… to T type topologies where two DIMMs exist per channel". https://patents.google.com/patent/US9791899
- **Real-world 4x8 GB single-rank B-die results on Zen 3 (anecdotes):**
  - 5800X on an X570 board: 3800 with 2 DIMMs dropped to 3466 with 4. After fixing RTT, tRFC and GDM, the user got a stable 24/7 setup (speed not stated in the post) and a 3733 config that needed a DIMM fan; the DIMMs reached 45–50 °C.
  - B550 Aorus Pro, 5800X, two Viper Steel kits: 3800 CL14.
  - ASRock B550 Extreme4, Patriot 4400 kits: 3600 CL16.
  - **Typical range: about 3600–3800**, which is close to the usual FCLK limit anyway.

**Local copies for checking:**
- LHM source: /tmp/ramfacts_dl/lhm/SuperIOHardware.cs
- Kernel driver: /tmp/ramfacts_dl/k/it87.c
- PR #1036 BIOS/HWiNFO/SIV screenshots: /tmp/ramfacts_dl/img/a.png and /tmp/ramfacts_dl/img/b.png
- Viper Steel Thaiphoon SPD screenshot: /tmp/ramfacts_dl/img/tb.jpg