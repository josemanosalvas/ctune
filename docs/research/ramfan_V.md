**Verification of both research reports (read-only; nothing was written to /sys, /dev or /etc)**

**A. Which header is it8792 fan3**
- **CONFIRMED: it8792 fan3/pwm3 is the SYS_FAN4 header.** I re-downloaded each source myself:
  - LibreHardwareMonitor master `SuperIOHardware.cs`: lines 2934-2936 list B550_AORUS_PRO / _AC / _AX. Lines 2950-2952 give fans "System Fan #5 / Pump" (0), "#6 / Pump" (1), "System Fan #4" (2), and line 2955 gives control 2 = "System Fan #4".
  - frankcrawford/it87 HEAD bd7a566c `Sensors configs/Gigabyte/configs/gigabyte-it87-amd.conf`: line 1094 lists "B550 AORUS PRO" for SIV 0x6008090B. Lines 1103-1117 set fan3 and pwm3 to "SYS_FAN4", temp2 to "EC_TEMP2" and temp3 to "System 2". Lines 627-631 map it8688 fan1-5 to CPU_FAN, SYS_FAN1, SYS_FAN2, SYS_FAN3, CPU_OPT.
  - Linux torvalds master `drivers/hwmon/it87.c`: the fan3 tach register is 0x0f (line 246) and the pwm3 duty register is 0x73 (line 255).
  - A Level1Techs X399 hands-on report says the IT8792 drives SYS_FAN4 as fan3: https://forum.level1techs.com/t/threadripper-lm-sensors-halp/119487/11
  - DMI reports "B550 AORUS PRO" and BIOS F16b.
- **CONFIRMED: BIOS settings** (`bios_settings.json` plus the raw `GcSensorVarName` efivar):

  | Offset | Setting | Value |
  |---|---|---|
  | @235 | Speed Control | 2 Manual |
  | @242 | Temperature Interval | 1 |
  | @243 | Temperature input | 2 CPU (factory default 8 "System 2") |
  | @520 | Fail Warning | Enabled |
  | @535 | Control Mode | Auto |
  | @546 | Fan Stop | Disabled |

  The @243 options are 0-8: System 1, EC_TEMP1, CPU, PCIEX16, VRM MOS, PCH, PCIEX4_1, EC_TEMP2, System 2. Raw bytes 235 onward are `2,0,20,100,130,127,0,1,2,20,0,0,0,20,0,127,128,93,0,127`.
- **CONFIRMED: location.** I re-downloaded the manual from download.gigabyte.com (`mb_manual_b550-aorus-pro-ac_1001_e.pdf`; same sha256 as `/tmp/gbman/man_e.pdf`) and rendered page 4. SYS_FAN4 is on the right board edge, below the 24-pin ATX. It is the lowest of three stacked headers (SYS_FAN5_PUMP, SYS_FAN2, SYS_FAN4), to the right of DDR4_B2 and level with the lower part of the DIMM slots. EC_TEMP1 is right beside it. Page 16 says all fan headers are 4-pin.
- **CORRECTED: "the header closest to the DIMMs apart from CPU_FAN/CPU_OPT".** SYS_FAN5_PUMP and SYS_FAN2 are stacked directly above it at about the same distance. It is one of three equally close headers.
- **UNVERIFIED (not re-checked, low relevance):** the claims about BIOS Profile 8 and Profile 1 curves.

**B. Fan model and specs**
- **CONFIRMED** (Corsair CMDAF2 page, Scan CMDAF page, Guru3D):
  - Two 50 mm ball-bearing fans with PWM control.
  - "up to 10.6 CFM at 3,700 RPM".
  - No minimum RPM is published.
- **PLAUSIBLE:** 10.6 CFM per fan, so 21.2 CFM for the pair. Corsair's wording is ambiguous; 21.2 comes from a TechPowerUp spec repost.
- **UNVERIFIED (secondary sources only, not on Corsair's page):** 25.6 dBA maximum and the 4-pin connector (TechPowerUp, which I could not fetch: 403). AnandTech /show/11990 now redirects to the forums, so I could not check it.
- **CORRECTED: `pwm3_freq` = 23437 Hz says nothing about the fan's connector.** It is the chip's output frequency. What is proven is that the fan follows duty: in my sample, rpm dropped from 2142 to 1834 after a 0% write.
- **UNVERIFIED:** the straight-line rpm estimates (about 1300 at 25%, 1460 at 30%, about 500 at 0%) and the fan-law noise figures. The arithmetic is correct, but the model is an extrapolation.

**C. Why pwm3 stays at 130 while the profile asks for 0%**
- **CONFIRMED live.** I sampled at 5 Hz for 100 s (`/tmp/verify_scripts/sample1.log`):
  - pwm3 read 0 at 22:32:18.1, 22:32:49.2 and 22:33:20.1, i.e. every ~31 s.
  - It was back to 130 on the next driver refresh each time.
  - fan3 dipped to 1834 and 2089 rpm, so the 0% briefly reached the hardware.
  - pwm3 was otherwise a flat 130 for Tctl 36-58 °C, and `pwm3_enable` stayed 1 throughout.
  - The only fan-control process running is coolercontrold. `/etc/sensors.d` is empty, and lm_sensors only loads it87.
- **CONFIRMED: the in-tree it87 driver (`intree: Y`) accepts 0.**
  - `pwm_to_reg` passes the value through for newer-autopwm chips (lines 704-710).
  - `set_pwm` (1604-1648) refuses only when bit 7 (hardware auto) is set, and writes register 0x73.
  - Readings are cached for 1.5 s (line 885).
  - `pwm3_auto_start` reads the same register 0x73 (AUTO_BASE 0x70 + 3, lines 274-276). That is why it also shows 130.
  - The driver has no handling for Gigabyte's fan firmware. Its only Gigabyte note is the LPC-bus workaround at line 2686.
- **CONFIRMED: the firmware takeover mechanism exists.**
  - LHM `IsaBridgeGigabyteController.cs` lines 14-26 describe "custom firmware loaded onto the 2nd ITE EC", with its enable byte at 0x900+0x47.
  - The frankcrawford driver defines `IT87_SMARTFAN_ENABLE 0x947` (line 427) and the curve layout "start temp, full temp, start PWM, slope LSB, slope MSB" (line 1089).
  - Its `it87_h2ram_set_manual` (lines 3115-3146) flattens the curve with SmartFan left on.
  - Its README "SUPPORT NOTE - Aug 2026" (lines 174-188) says this fixes manual control for IT8792/IT8795.
- **PLAUSIBLE, not proven: the restorer is the IT8792E's own firmware rather than BIOS SMM.** Either way it is outside Linux.
- **CONFIRMED: 130 equals the BIOS SYS_FAN4 start duty** (byte 239).
- **UNVERIFIED: the reading of the rest of the curve** ("flat 51% to about 93 °C, then a ramp to 100% at 100 °C"). Above 58 °C nothing was observed.
- **CORRECTED (Researcher 1): "nothing in Linux controls pwm1/pwm2".** CoolerControl does assign "Silent Balanced Safe Mix" to it8792 fan1 and fan2 (`config.toml` lines 61-62; the journal shows "Successfully applied" at 17:32:37). The firmware overrides it. I saw pwm1/2 move between 129 and 231 with CPU temperature, and CoolerControl's own values (66-77) showed up for only one sample each.
- **CONFIRMED: CoolerControl 5.0.1 behaviour** (rpm 5.0.1-1.fc44; I re-cloned tag 5.0.1, commit d9201247):
  - `fans.rs` 905-937: writes the duty unchanged, clamped only to 0-100.
  - `hwmon_repo.rs` 1925-1943: `current_duty_matches_target` compares against the read-back value (51%), so 0% is always written.
  - `hwmon_repo.rs` 2386-2390: the "EC reclaims" comment.
  - `functions.rs` 20-22: the safety latch fires after 30 polls with no duty set. That matches the 31 s pulses.
  - `functions.rs` 502-572: the step-size gate.
  - `setting.rs` 527-531: `duty_minimum` is the minimum step size, not a minimum duty. Your "Silent Balanced ..." functions with `duty_minimum` 22/20 are therefore not floors.
  - `hardware_support.rs` 43-46 and 148-170: the FirmwareOverride verdict comes only from reading back `pwm_enable`, so this case goes undetected.

**D. Airflow and thermal conclusions**
- **CONFIRMED: integralfx DDR4 OC Guide** (oc-guide HEAD 480c3a5, lines 283, 287 and 304-309):
  - S8B (B-die) is "Up to 1.55" V daily; above 1.55 V "A RAM fan is recommended".
  - The ideal range is about 30-40 °C, and tRFC depends strongly on temperature.
  - Memory can pass a memtest and still crash in games because of GPU/CPU heat.
- **CONFIRMED: Igor's Lab.** DDR4-3800 CL14 B-die went unstable in TM5 at about 45.4 °C after 27 minutes, one RCD tick made the difference, and GPU waste heat during gaming caused instability.
- **CONFIRMED (search snippet; direct fetch failed on an expired certificate): Crucial's rule of about 3 W per 8 GB.**
- **UNVERIFIED (estimates or anecdotes I did not fetch):**
  - The heat-load estimate of about 12-18 W for the four DIMMs under stress.
  - The OCN/TechPowerUp "errors at 45-50 °C" and "-10 °C with a 92 mm fan" posts, and the MasterDIMM "-15 °C" claim.
  - That the DIMMs have no thermal sensor. No jc42 hwmon device exists, but SPD byte 14 is unread because the SMBus driver is not attached.
- **CONFIRMED: EC_TEMP2 is readable today.** It is it8792 `temp2`, a thermistor input (`temp2_type` = 4) that reads -55 °C because nothing is connected.
- **CORRECTED (Researcher 2's probe advice): think about which EC_TEMP header to use.**
  - EC_TEMP2 is on the bottom board edge, far from the DIMMs, so the probe lead has to be long.
  - EC_TEMP1 is right next to SYS_FAN4 and the DIMMs. It sits on the IT8688E, so Linux can only read it once the out-of-tree driver works, but the BIOS can use it as SYS_FAN4's temperature input (option 1).
  - So: EC_TEMP1 for BIOS-only control, EC_TEMP2 if CoolerControl must see the probe now.
- **CONFIRMED: "RAM Fans Late" is a poor design.** It asks for 0% until the GPU hotspot reaches 80 °C (`config.toml` lines 140-147), which leaves DIMMs at 1.53-1.55 V without airflow during memory-heavy CPU work. Even 80-95 °C hotspot only gives 18-30%.

**Final answer**
- **Header and location:** it8792 fan3 is **SYS_FAN4**. It is on the right edge of the board below the 24-pin ATX connector: the lowest of the three stacked headers (SYS_FAN5_PUMP, SYS_FAN2, SYS_FAN4), right of DDR4_B2, with EC_TEMP1 beside it.
- **Current speed:** the duty is 130/255 = **51%**, and the fan turns at 2115-2163 rpm, which is **about 57-58% of the rated 3,700 rpm**. That is about 12 CFM if 21.2 CFM is the combined rating.
  - The BIOS SYS_FAN4 Manual curve sets this, not CoolerControl. The IT8792E fan firmware rewrites the duty within about a second.
  - CoolerControl's only effect is a brief 0% pulse every ~31 s, which causes the rpm dips.
- **Is the airflow enough:** at today's fixed ~2,130 rpm, very likely yes for 4x8 GB B-die at about 1.54 V, but this is unmeasured. If "RAM Fans Late" ever took effect, no. Aim for 40 °C or less on the DIMMs under combined CPU+GPU load, and treat 45 °C as the danger line.
- **Recommended CoolerControl setup:**
  - **Now (stock in-tree driver):** set it8792 fan1, fan2 and fan3 to **Unmanaged** so CoolerControl stops pulsing them. Control SYS_FAN4 in the BIOS instead. Either keep the current flat 51%, which is safe, or set Smart Fan 5 Manual on CPU: 30% up to 60 °C, 40% at 70 °C, 55% at 80 °C, 70% at 90 °C, 100% at 95 °C. Keep Fan Stop Disabled.
  - **After the frankcrawford it87 module loads** and pwm3 stays where CoolerControl puts it, use a **Mix (Max)** profile on it8792 fan3. Put the floor in the curve points, because `duty_minimum` is not a floor:
    - NVIDIA "GPU Temp" (core, not hotspot): `[[30,30],[60,30],[70,40],[78,55],[85,70]]`
    - k10temp temp1 (Tctl): `[[30,30],[65,30],[75,40],[85,55]]`
    - Optional DIMM thermistor (it8792 temp2 on EC_TEMP2): `[[30,30],[38,35],[42,50],[45,75],[48,100]]`
    - Function: Standard, response_delay 5-10 s, deviance 2 °C, `duty_minimum` (step size) 2, only_downward on.
    - Check that `fan3_input` stays above about 1,000 rpm at 30%. Drop the floor to 25% only if it holds speed there.

**Files:**
- `/tmp/verify_scripts/sample1.log`
- `/tmp/verify_dl/lhm/`
- `/tmp/verify_dl/fc/`
- `/tmp/verify_dl/linux/it87.c`
- `/tmp/verify_dl/cc/src/coolercontrold/daemon/src/`
- `/tmp/verify_dl/ifx/guide.md`
- `/tmp/verify_dl/gb/manual.pdf`
- `/tmp/verify_dl/gb/crop.png`
- `/var/home/josemanosalvas/.t3/scratch/2026-10-24-snapshot-the-current-ram-settings-01633672/snapshots/20261024-181828_F16b/decoded/bios_settings.json`
- `/etc/coolercontrol/config.toml`

**Sources:**
- https://github.com/LibreHardwareMonitor/LibreHardwareMonitor
- https://github.com/frankcrawford/it87
- https://github.com/torvalds/linux/blob/master/drivers/hwmon/it87.c
- https://gitlab.com/coolercontrol/coolercontrol (tag 5.0.1)
- https://download.gigabyte.com/FileList/Manual/mb_manual_b550-aorus-pro-ac_1001_e.pdf
- https://www.corsair.com/us/en/p/pc-components-accessories/cmdaf2/dominator-airflow-platinum-rgb-fan-cmdaf2
- https://www.scan.co.uk/products/corsair-dominator-airflow-platinum-led-memory-dual-pwm-controlled-50mm-cooling-fans
- https://www.guru3d.com/story/dominator-platinum-airflow-rgb/
- https://www.techpowerup.com/forums/goto/post?id=3745450
- https://forum.level1techs.com/t/threadripper-lm-sensors-halp/119487/11
- https://github.com/integralfx/MemTestHelper/blob/oc-guide/DDR4%20OC%20Guide.md
- https://www.igorslab.de/haelt-der-neue-high-end-ram-fuer-ryzen-5000-was-er-verspricht-g-skill-ddr4-3800-cl14-2x-16gb-kit-im-haertetest/3/
- https://content.crucial.com/content/crucial/es-la/home/support/articles-faq-memory/how-much-power-does-memory-use.html