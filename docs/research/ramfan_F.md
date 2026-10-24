TASK 2 REPORT: RAM fan on it8792 fan3 (board header SYS_FAN4)

Everything here was read-only: sysfs reads, config and journal reads, and the BIOS snapshot. Nothing was written. Confidence levels are HIGH, MED or LOW.

---

## Summary

- **Why it sits at 51% (HIGH):** CoolerControl (CC) does write 0. The board firmware puts 130 back in under about 1.5 s. 130 is the value of your own BIOS SYS_FAN4 "Manual" curve. The IT8792E's SmartFan firmware keeps rewriting the duty register even though `pwm3_enable` reads 1. The in-tree driver does not handle that firmware, so CC's profile has almost no effect.
- **The fan (HIGH for specs, LOW for idle behaviour):** Corsair Dominator Airflow Platinum. It has two 50 mm ball-bearing 4-pin PWM fans rated 3,700 RPM, 10.6 CFM each (21.2 CFM total) and 25.6 dBA max. Your ~2,130 RPM is about 58% of max at 51% duty. No minimum RPM is published, and whether it stops at 0% is unknown.
- **Is the airflow enough (MED-LOW):** Yes as it runs today, at a fixed ~2,130 RPM. If CC actually took control, "RAM Fans Late" would leave the fan at 0% almost all the time. At 1.54 V with tRFC 154 ns, that is not safe for memory-heavy CPU loads. The fix is a 25% floor and a better temperature source, ideally a thermistor on a DIMM.

---

## A) Fan identification and specs

**Product.** Your wording fits two Corsair kits. The original is CMDAF (LED version, Corsair Link lighting, now end-of-life). The RGB version is CMDAF2 (Lighting Node PRO). The fan specs are the same for both:
- 2 x 50 mm fans, ball bearing, "dual PWM-controlled"
- Up to 3,700 RPM and 10.6 CFM per fan, so 21.2 CFM for the pair
- Max noise 25.6 dBA (spec tables for CMDAF2)
- Fan cable is a 4-pin PWM connector: HIGH confidence for CMDAF2 (TechPowerUp says "two 50 mm 4-pin PWM fans"), MED for CMDAF.
- I could not tell which variant you have from software. The fan behaviour is the same either way.

**Header.** fan3 on hwmon6 is SYS_FAN4 (LibreHardwareMonitor `SuperIOHardware.cs` lines 2934-2955: IT8792E fan index 2 = "System Fan #4").
- All headers on this board are 4-pin (manual p.16, `/tmp/gbman/man_e.txt` line 732).
- BIOS "SYS_FAN4 Control Mode" is Auto (`GcSensorVarName@535` = 0), so the board detects 3-pin vs 4-pin itself.
- `pwm3_freq` = 23,437 Hz, which is a normal 4-wire PWM frequency.
- The fan clearly responds to duty: in each of CC's brief 0% writes, RPM fell from ~2,149 to 1,767-2,002 within about 1.5 s.

**What ~2,130 RPM at 130/255 means.**
- 130/255 is 51% duty, and 2,130 / 3,700 is about 58% of rated max.
- A straight-line estimate through (51%, 2,130) and (100%, 3,700) gives roughly 1,140 RPM at 20%, 1,300 at 25%, 1,460 at 30% and 1,780 at 40% (LOW-MED). The line implies about 500 RPM at 0%, so the fan probably does not fully stop at 0% PWM. Corsair publishes no minimum RPM, so this is unconfirmed (LOW).
- Airflow scales with RPM: about 12 CFM total now, about 7.5 CFM at 25%.
- Fan-law noise relative to the 25.6 dBA rating: about 13.6 dBA now and about 3 dBA at 25%. Small fans whine more than these numbers suggest, but 25-30% should be inaudible among your other case fans.
- **Test to settle the 0% question:** once you can control the header (BIOS curve, or a working driver), hold 0%, then 20%, then 25% for 30 s each and read `fan3_input`.

---

## B) Why `pwm3` = 130 while CC asks for 0%

### 1. Direct observation (HIGH)
I sampled hwmon6 at 2-5 Hz for about 3 minutes.
- **pwm3:** read 0 at 22:17:50.1, 22:19:54.1 and 22:20:25.0. Each time it was back to 130 on the very next driver refresh (≤1.5 s later).
  - The 0% writes repeat every ~31 s.
  - RPM dipped to 2,002 at 22:17:50, 1,839 at 22:20:25 and 1,767 at 22:20:56, so the 0% was real for that moment.
- **pwm1/pwm2 (SYS_FAN5/6_PUMP, BIOS "Normal" curve using CPU temperature, nothing plugged in):** these follow CPU temperature, not CC.
  - CPU 35 °C gave 122, 45 °C gave 161, 50 °C gave 183.
  - CC's own Mix values (61, 64, 69) showed up only for a single sample each.
- **Conclusion:** firmware is computing fan curves and writing all three IT8792E duty registers continuously. `pwm3_enable` stays 1 the whole time.

### 2. The 130 is your BIOS SYS_FAN4 Manual curve (MED-HIGH)
- Settings: SYS_FAN4 Speed Control = Manual (`GcSensorVarName@235` = 2), temperature input = CPU (`@243` = 2), Fan Stop = Disabled (`@546`).
- Raw block from `.../snapshots/20261024-181828_F16b/raw/efivars/GcSensorVarName-5489a3e4-...`, bytes 235 onward: `[2,0,20,100,130,127,0,1,2,20,0,0,0,20,0,127,128,93,0,127,...]`
- Byte 239 = 130, which matches the register exactly.
- Compare CPU_FAN "Silent": `[1,0,20,65,70,16,...]`. That looks like start temp / full temp / start PWM / slope. On SYS_FAN4 the 127 bytes look like "unused" markers.
- The observed result is a flat 130 for CPU 34-51 °C. My byte decoding is a best guess (LOW-MED); the match itself is solid.

### 3. Who does the rewrite (MED-HIGH)
- The out-of-tree frankcrawford/it87 driver (commit bd7a566c8a60, 2026-10-02) documents an "IT879x H2RAM SmartFan" firmware on Gigabyte boards for IT8790/IT8792/IT87952.
  - It keeps per-channel curve parameters (start temp, full temp, start PWM, slope) in shared memory (H2RAM) and drives the PWM itself.
  - To get manual control, that driver keeps SmartFan on and flattens the curve: start temp 0, full temp 0x7f, start PWM = requested duty, slope 0 (`it87.c` lines 3115-3147, comment at line 3122).
  - Its README "SUPPORT NOTE - Aug 2026" (lines 174-188) says this fixes manual control on IT8688, IT8689, IT8790, IT8792/IT8795 and IT87952, and that it now uses Gigabyte SMI commands.
  - README lines 237-243 note that some Gigabyte boards have a separate fan-control chip where "you can measure the speed, you cannot change it".
- The in-tree driver has none of this.
- I could not read H2RAM directly (that needs root/MMIO), so "IT8792E firmware" rather than "BIOS SMM" is inferred, not seen.

### 4. In-tree it87 does not block 0 (HIGH)
Source: torvalds master `drivers/hwmon/it87.c` at 7f8581c70a3b.
- IT8792E features (lines 461-470): `FEAT_NEWER_AUTOPWM | FEAT_16BIT_FANS | FEAT_TEMP_OFFSET | FEAT_TEMP_OLD_PECI | FEAT_TEMP_PECI | FEAT_10_9MV_ADC | FEAT_IN7_INTERNAL | FEAT_FANCTL_ONOFF | FEAT_NOCONF`
- `pwm_to_reg()` (lines 704-710) passes the value through unchanged for newer-autopwm chips.
- `set_pwm()` (lines 1604-1648) accepts 0-255, refuses only when bit 7 (hardware auto) is set, and writes duty register 0x73 for pwm3 (`IT87_REG_PWM_DUTY`, line 255). Writing 0 is valid.
- The driver caches readings for 1.5 s (line 885: `HZ + HZ/2`). That is why a 0 stays visible for up to 1.5 s before the firmware's value shows.
- On newer-autopwm chips, `pwm3` and `pwm3_auto_start` read the same register (lines 812-815 and 850-855). That is why both show 130.
- The visible `pwm3_auto_*` values (start 20, full 65, slope 28, temp map 4) belong to the chip's legacy hardware-auto mode. That mode is off (`pwm_enable` = 1), so they don't matter here.

### 5. CoolerControl 5.0.1 behaviour (HIGH)
Source: gitlab coolercontrol tag 5.0.1, `coolercontrold/daemon/src`.
- **No floor or fan-stop protection on hwmon writes:**
  - `fans.rs:933-937` `duty_to_pwm_value()` only clamps to 0..100, so 0% becomes pwm 0.
  - `fans.rs:905-924` `set_pwm_duty()` writes `pwmN` directly.
  - `hwmon_repo.rs:1945-1977` `apply_pwm_duty_write()` is the call path.
- **Why the write repeats every ~31 s:**
  - Graph processing chain is in `engine/commanders/graph.rs:260-283`.
  - With the Default (Identity) function, the minimum step is 2% (`setting.rs:563-575`). A repeated 0 → 0 is a 0% change, so it is skipped (`functions.rs:502-572`).
  - The "safety latch" re-sends the duty after 30 polls at 1 s (`functions.rs:20-22` and `674-760`). That matches the ~31 s dips.
- **The writer's skip check compares against the real reading:** `current_duty_matches_target` (`hwmon_repo.rs:1925`) uses the pwm value read each poll (51%). Because 51% ≠ 0%, every latch write goes through.
- **CC already expects firmware to fight back:** `apply_setting_manual_control()` (`hwmon_repo.rs:2376-2395`) says the forced first write "Anchors manual control on boards whose EC reclaims the hardware fan curve shortly after we set pwm_enable=1".
- **CC cannot detect this case:**
  - Its `FirmwareOverride` verdict is based only on reading back `pwm_enable` (`hardware_support.rs:148-170`).
  - The check that would test whether the fan follows the duty is described as not implemented (`hardware_support.rs:43-46`).
  - Here `pwm_enable` stays 1, so CC logs "Successfully applied" even though it isn't.
- **Config gotcha:** in CC 5.x the function key `duty_minimum` is the minimum step size, not a minimum duty (`setting.rs:527-531`, `#[serde(rename = "duty_minimum")] step_size_min`). Your "Silent Balanced ... Function" `duty_minimum = 22/20` therefore means "ignore changes smaller than 22/20%", not "never go below 22/20%". Any floor has to be built into the curve points.
- **Side effect:** with the it8688 driver missing, the five main case fans run on the BIOS "Silent" curves. The it8792 channels get a 1-1.5 s pulse to CC's value every ~31 s, which causes the periodic RPM dip.

### 6. What would fix control (MED)
Do one of these. Each is the user's call; I changed nothing.
- **Option (a):** get the frankcrawford it87 module building on 7.2.9. It is the only Linux path that takes over the IT8792E H2RAM SmartFan, and it also restores the it8688. `dkms` is missing on Bazzite, which is why `it87-dkms-load.service` fails.
- **Option (b):** leave the header to the firmware and set the BIOS SYS_FAN4 curve the way you want (section C). In that case set the three it8792 channels in CC to "Unmanaged" so CC stops pulsing them.

---

## C) Is the airflow enough for 4 x 8 GB B-die at ~1.54 V, tRFC 288 (154 ns)?

### Heat load (estimate, MED-LOW)
- Crucial's rule of thumb is about 3 W per 8 GB of DDR3/DDR4 at stock; higher voltage raises it.
- Scaling for 1.54 V vs 1.2 V (about 1.65x on dynamic power) and 3733 MT/s gives:
  - About 1-2 W per DIMM at idle or light load
  - About 2-3 W while gaming
  - About 3-4.5 W under memory stress (TM5, y-cruncher, large compiles)
- That is about 12-18 W for the four DIMMs at full load. Micron TN-40-07 describes the method; this is not a measurement.

### Typical temperatures (anecdotal sources, MED)
- B-die at 1.5-1.6 V under stress, with no direct airflow and two DIMMs per channel (inner modules worst): about 40-55 °C, or 10-25 °C above case air. Temperatures can take close to an hour to level off.
- A directed fan typically lowers DIMM temperature by about 10-15 °C:
  - A 92 mm fan gave about -10 °C (TechPowerUp user).
  - Cooler Master claims up to 15 °C for its MasterDIMM fan.
- Most of the gain comes from going from still air to any forced airflow. 25-30% duty (about 1,300-1,460 RPM by the estimate above) captures most of it; 51% adds only a few °C more (my estimate from typical convection behaviour, MED-LOW).

### B-die temperature sensitivity
- integralfx DDR4 OC Guide ("Temperatures and Its Effect on Stability", lines 304-309):
  - tRFC depends strongly on temperature.
  - The ideal range is about 30-40 °C.
  - Memory can pass a memtest and still crash while gaming because CPU/GPU heat raises RAM temperature.
- integralfx voltage table (lines 286-292): B-die (S8B) is "Up to 1.55" V daily, and above that "A RAM fan is recommended". At 1.53-1.55 V you are right at that edge.
- Igor's Lab: DDR4-3800 CL14 B-die (2 x 16 GB) started erroring in TM5 at about 45.4 °C after 27 minutes, and one tRCD tick made the difference. In the same article, GPU exhaust during 30 minutes of gaming made the RAM unstable.
- Forums (OCN/TechPowerUp): errors often start around 45-50 °C on tight timings, and sometimes around 40-42 °C at low tRFC.
- Your 154 ns tRFC is moderately tight. Treat **≤40-42 °C under combined load** as the target and **45 °C as the danger line**.

### Verdict
- **Now (firmware fixed at ~2,130 RPM, ~12 CFM over the DIMMs):** probably enough. My estimate is about 35-42 °C under memory stress plus gaming (MED-LOW, no sensor).
- **The intended "RAM Fans Late" design** (0% until GPU hotspot 80 °C, then only 18-30%) is not reasonable as the only control:
  - DIMM self-heating does not follow GPU temperature. Memory-heavy CPU work keeps the fan off at 1.54 V.
  - GPU hotspot is a spiky signal, which means on/off cycling.
  - 80 °C hotspot is reached only in heavy gaming.
  - 0% may not even stop this fan.
  - GPU heat is a real second source, so keeping it as an input is fine.

### Proposed curves
**1. Best option: real DIMM temperature through the board's thermistor header (works today, no driver needed).**
- hwmon6 `temp2` is EC_TEMP2. It is a thermistor input (`temp2_type` = 4) reading -55 °C, i.e. open/nothing connected. LHM (line 2948) maps IT8792E temp index 1 to "External #2".
- Hardware: tape a 2-pin 10 kΩ NTC thermistor probe to the heatspreader centre of an inner DIMM (A2/B1 side, the hottest spot). Connect it to EC_TEMP2 (manual p.18, line 820).
  - No thermistor cable comes in the box (manual p.4 contents).
  - Calibrate against a reference; 10 kΩ NTC is the usual type but should be checked.
- BIOS settings: SYS_FAN4 → Manual, "Fan Control Use Temperature Input" = EC_TEMP2. EC_TEMP1/2 are valid choices for `@243` (options 0-8: System 1, EC_TEMP1, CPU, PCIEX16, VRM MOS, PCH, PCIEX4_1, EC_TEMP2, System 2). Smart Fan 5 is described on manual p.33, lines 1686-1730.
- Curve:

| DIMM temp | Duty |
|---|---|
| ≤34 °C | 25% |
| 38 °C | 30% |
| 42 °C | 45% |
| 45 °C | 70% |
| ≥48 °C | 100% |

- Keep Fan Stop Disabled. If you want 0 RPM at idle, enable Stop below about 32 °C only after confirming the fan restarts at 25%.
- The firmware then handles it in hardware, independent of the OS. Once connected, the same sensor also appears in Linux as it8792 `temp2`.

**2. Without a probe (BIOS only):** SYS_FAN4 Manual, input = CPU.

| CPU temp | Duty |
|---|---|
| ≤50 °C | 25% |
| 60 °C | 30% |
| 70 °C | 40% |
| 80 °C | 55% |
| 90 °C | 70% |

- The BIOS can't read the GPU sensor. "PCIEX16" is the closest stand-in for GPU-exhaust heat but cannot be combined with CPU in the BIOS.
- If in doubt, leave your current flat 51%. It is the safe, slightly louder choice.

**3. In CoolerControl, only once the header is truly controllable** (frankcrawford driver working, and `pwm3` stays where CC puts it). Use a Mix (Max) profile with a 25% floor in every member's first point (remember `duty_minimum` is not a floor):
- **DIMM probe (it8792 temp2):** `[[30,25],[38,30],[42,45],[45,70],[48,100]]`
- **GPU Temp (core):** `[[40,25],[65,25],[72,35],[80,50],[88,65]]`
  - If you stay on GPU Hotspot instead, shift the temperatures about +12 °C.
- **CPU temp1 (Tctl):** `[[40,25],[65,25],[75,35],[85,50]]`
  - Use a Standard function with about 10 s response delay and 2 °C deviance to smooth Tctl and hotspot spikes.
  - Use a step size of about 2-5. Do not use 20-22.

### How to measure (there is no DIMM sensor)
- Patriot Viper Steel DDR4 has no on-module thermal sensor and no jc42 device, so you need a proxy or an external probe.
  1. **Thermistor on EC_TEMP2:** the best option; it is logged by both the BIOS and Linux.
  2. **IR thermometer:** bare or anodised aluminium reads low. Put a piece of matte electrical or Kapton tape on the spreader and measure the tape.
- **Test procedure:**
  - Close the case.
  - Run TM5 or y-cruncher VST together with a GPU load for at least 60 minutes, since temperatures need about an hour to settle.
  - Log the probe, find where errors start, and keep at least 5 °C of margin.
  - Repeat at 25% fixed duty to confirm the floor is enough.

---

## Sources

**Local files (read-only)**
- `/sys/class/hwmon/hwmon6/*` and the 5 Hz sample log above
- `/etc/coolercontrol/config.toml`, `/etc/coolercontrol/modes.json`, `/etc/coolercontrol/overrides.toml`
- `journalctl -u coolercontrold` at 17:32:37
- `systemctl status it87-dkms-load` ("dkms: command not found")
- `/var/home/josemanosalvas/.t3/scratch/2026-10-24-snapshot-the-current-ram-settings-01633672/snapshots/20261024-181828_F16b/decoded/bios_settings.md` (lines 2691-2702) and `bios_settings.json`
- `.../snapshots/20261024-181828_F16b/raw/efivars/GcSensorVarName-5489a3e4-c745-11ea-87d0-0242ac130003` (bytes 235-279)
- `/tmp/gbman/man_e.txt` (B550 AORUS PRO manual): line 732 (p.16), line 749 (p.16), line 820 (p.18), lines 1686-1730 (p.33)

**Code**
- CoolerControl 5.0.1 (gitlab tag 5.0.1, cloned to `/tmp/cc_src_dl/cc/coolercontrold/daemon/src`): `repositories/hwmon/fans.rs`, `repositories/hwmon/hwmon_repo.rs`, `engine/processors/functions.rs`, `engine/commanders/graph.rs`, `setting.rs`, `hardware_support.rs` (lines as cited above)
- Linux `drivers/hwmon/it87.c`, torvalds master 7f8581c70a3b (`/tmp/it87_src_dl/it87.c`): lines 255, 461-470, 704-710, 805-856, 885, 1604-1648
- frankcrawford/it87 bd7a566c8a60 (`/tmp/it87_oot_dl/README` lines 174-188 and 237-243; `/tmp/it87_oot_dl/it87_oot.c` lines 426-460, 2910-2958, 3115-3147, 6494-6566): https://github.com/frankcrawford/it87
- LibreHardwareMonitor `SuperIOHardware.cs` e68a7ef7326f, lines 2537-2565 and 2934-2955 (`/tmp/lhm_dl/SuperIOHardware.cs`)

**Web**
- Corsair CMDAF2: https://www.corsair.com/pl/pl/p/pc-components-accessories/cmdaf2/dominator-airflow-platinum-rgb-fan-cmdaf2
- Scan CMDAF (EOL): https://www.scan.co.uk/products/corsair-dominator-airflow-platinum-led-memory-dual-pwm-controlled-50mm-cooling-fans
- AnandTech (21.2 CFM, 25.6 dBA): https://www.anandtech.com/show/11990
- Guru3D: https://www.guru3d.com/story/dominator-platinum-airflow-rgb/
- integralfx DDR4 OC Guide (oc-guide branch, 29ab7a43f334): https://github.com/integralfx/MemTestHelper/blob/oc-guide/DDR4%20OC%20Guide.md
- Igor's Lab B-die test: https://www.igorslab.de/haelt-der-neue-high-end-ram-fuer-ryzen-5000-was-er-verspricht-g-skill-ddr4-3800-cl14-2x-16gb-kit-im-haertetest/3/
- OCN thread: https://www.overclock.net/threads/does-samsung-b-die-create-errors-if-it-overheats-or-does-it-just-crash-the-pc.1796298
- TechPowerUp posts: https://www.techpowerup.com/forums/goto/post?id=4830781 and https://techpowerup.com/forums/goto/post?id=4578023
- Tom's Hardware (MasterDIMM, up to 15 °C): https://www.tomshardware.com/pc-components/ddr5/cooler-master-is-bringing-active-cooling-to-ddr5-ram-promising-up-to-15-degree-temperature-drops-masterdimm-combines-g-skill-memory-with-a-built-in-fan-kits-run-up-to-128gb
- Crucial power FAQ (~3 W per 8 GB): https://content.crucial.com/content/crucial/es-la/home/support/articles-faq-memory/how-much-power-does-memory-use.html
- Micron TN-40-07 (method only): https://web.pa.msu.edu/people/edmunds/Disco_Kraken/Memory_Design/micron_tn4007_ddr4_power_calculation.pdf

**Not covered:** I did not look at Buildzoid or Taraquin sources directly.