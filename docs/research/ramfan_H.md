**`it8792 fan3` is the SYS_FAN4 header. Confidence is very high: four independent sources agree, and the live readings fit.**

## 1. Which header it is (evidence)

- **LibreHardwareMonitor** (`LibreHardwareMonitorLib/Hardware/Motherboard/SuperIOHardware.cs` @ commit e68a7ef7):
  - The IT8792E section (lines 2930-2955) covers `B550_AORUS_PRO`, `_AC`, `_AX`, MASTER, VISION_D and X570 AORUS PRO/MASTER/ULTRA.
  - Fan index 0 = "System Fan #5 / Pump", 1 = "System Fan #6 / Pump", 2 = "System Fan #4" (lines 2950-2952). Control index 2 = "System Fan #4" (line 2955).
  - The IT8688E section (lines 2536-2560) maps 0..4 to CPU Fan, System Fan #1, #2, #3, CPU Optional Fan.
  - LHM index 2 is Linux fan3. Both read the same tach register 0x0f: LHM `Lpc/IT87XX.cs` line 641 `{0x0d,0x0e,0x0f,...}` and Linux `drivers/hwmon/it87.c` line 246 (torvalds 7f8581c). pwm3 is register 0x17, with duty in 0x73 (it87.c lines 254-255).
- **frankcrawford/it87** `Sensors configs/Gigabyte/configs/gigabyte-it87-amd.conf` @ 626ad08:
  - The stanza for SIV 0x6008090B lists "B550 AORUS PRO / PRO AC / PRO AX / PRO V2" (lines 1094-1103).
  - In that stanza: `label fan3 "SYS_FAN4"` (line 1114) and `label pwm3 "SYS_FAN4"` (line 1117). fan1 = SYS_FAN5_PUMP, fan2 = SYS_FAN6_PUMP, temp3 = "System 2" (line 1111).
  - The same file's it8688 stanza (lines 627-631) gives fan1-5 = CPU_FAN, SYS_FAN1, SYS_FAN2, SYS_FAN3, CPU_OPT.
  - Its older `GA-B550-VISION-D.conf` and `GA-X570-AORUS-PRO.conf` (`it8792` lines 55-57) say the same.
- **Hands-on test on a related board:** on a Gigabyte X399, the IT8792 is connected to SYS_FAN4 (fan3) and SYS_FAN5_PUMP (fan1) (bsodmike, Level1Techs post #11, https://forum.level1techs.com/t/threadripper-lm-sensors-halp/119487/11).
- **The BIOS menu agrees:** the "Fan Control Use Temperature Input" options are 0 System 1, 1 EC_TEMP1, 2 CPU, 3 PCIEX16, 4 VRM MOS, 5 PCH, 6 PCIEX4_1, 7 EC_TEMP2, 8 System 2. That is the IT8688E's six sensors followed by the IT8792E's three. SYS_FAN4's factory default is 8 = "System 2", which is an IT8792E sensor (`bios_settings.json`, `GcSensorVarName@243`, `ifr_default` 8).
- **Your labels fit:**
  - IT8688E: fan1/fan5 are CPU_FAN/CPU_OPT, at the top edge for the top AIO push/pull. fan2 is SYS_FAN1 at the top-left by the rear I/O (rear 120mm). fan3 is SYS_FAN2 on the right edge (front fans). fan4 is SYS_FAN3 on the bottom edge (bottom fans).
  - IT8792E: fan1/fan2 at 0 RPM are SYS_FAN5_PUMP and SYS_FAN6_PUMP with nothing connected. DMI reports board_name "B550 AORUS PRO", BIOS F16b.

## 2. Where it is on the board

From the B550 AORUS PRO (AC) manual (12ME-B55APRW-1001R), layout on page 4 (`/tmp/gbman/man_e.pdf`, rendered at `/tmp/gbman_render/layout-04.png` and `/tmp/gbman_render/rightedge-04.png`):

- SYS_FAN4 is on the right edge of the board, just below the 24-pin ATX connector.
- It is the lowest of three headers stacked top to bottom: SYS_FAN5_PUMP, SYS_FAN2, SYS_FAN4.
- It sits next to the EC_TEMP1 header, level with the lower half of the DIMM slots on the DDR4_B2 side.
- Apart from CPU_FAN and CPU_OPT (used by the AIO), it is the header closest to the DIMMs.

Manual page 16 says it is a standard 4-pin header (pin 2 voltage control, pin 4 PWM). Page 33 describes the Smart Fan 5 options.

## 3. What the BIOS does with SYS_FAN4

Settings in the `GcSensorVarName` EFI variable (`raw/efivars/GcSensorVarName-5489a3e4-...`, decoded in `bios_settings.json`):

| Setting | Offset | Value | Default |
|---|---|---|---|
| Speed Control | 235 | 2 Manual | Normal |
| Temperature Interval | 242 | 1 | |
| Temperature Input | 243 | 2 CPU | 8 System 2 |
| Control Mode | 535 | 0 Auto (BIOS picks PWM or voltage) | |
| SYS_FAN4 Stop | 546 | 0 Disabled (never stops) | |
| Fail Warning | 520 | 1 Enabled | Disabled |

Fail Warning means the board will beep if this fan stops or is unplugged (it needs a front-panel speaker).

The curve is raw bytes; none of it is exposed as normal BIOS questions. Offsets 235-255:
```
2 0 | 20 100 130 127 0 | 1 2 | [20 0 0 0] [20 0 127 128] [93 0 127 0] ...
```
The order of fields matches the curve block frankcrawford's driver uses for this chip (`it87.c` @ cbfeef4, line 1089: "start temp, full temp, start PWM, slope LSB, slope MSB", and lines 3125-3143). Read that way:

- Start temperature is 20 °C, the full-speed temperature is 100 °C, and the starting duty is 130/255 (51%).
- There are three extra curve segments, starting at 20, 20 and 93 °C.
- Saved profile 8 'RAMPBO OK+FANS' holds the identical curve. Profile 1 'latest ok' had an older one: VRM MOS input, 20% duty from 9 °C, segments at 37, 80 and 93 °C.

**In practice:** the duty sits flat at about 51% from 20 °C up to about 93 °C CPU temperature. Above 93 °C it ramps steeply to 100% at 100 °C, and it never stops. The live state matches this: pwm3 = 130 at Tctl 47 °C, fan3 at about 2140 RPM.

**Uncertain:** the slope units and the 0x80 flag on the second extra segment are my inference. I could not read the chip's own copy of the curve without writing to I/O ports, which you ruled out.

## 4. Why pwm3 stays at 130 even though CoolerControl asks for 0%

This is not just a boot-time setting. On Gigabyte AMD boards, the IT8792E runs Gigabyte firmware that keeps applying the BIOS curve while Linux is running:

- LHM describes it as "custom firmware loaded onto the 2nd ITE EC" and switches it off before taking control: `IsaBridgeGigabyteController.cs` lines 14-26, `EcioPortGigabyteController.cs` lines 18-19 (on/off byte at 0x900+0x47), `IT87XX.cs` line 317, `LpcIO.cs` lines 718-738.
- frankcrawford's driver handles the same switch (`IT87_SMARTFAN_ENABLE 0x947`, line 427, with the related vectors and readback at lines 451-459). Its README warns that on some Gigabyte boards "you can measure the speed, you cannot change it".
- The kernel's built-in it87 driver, which you are on now, has no handling for this.

So the firmware overwrites whatever CoolerControl writes. The value held, 130, is exactly the BIOS starting duty for SYS_FAN4. pwm1 and pwm2 read 129 in manual mode even though nothing in Linux controls them, which points the same way.

**Source files:** LHM at `/tmp/lhm_src_dl/`, frankcrawford driver and configs at `/tmp/fc_it87_dl/`, manual at `/tmp/gbman/man_e.pdf`. The script used to compare the BIOS profiles is `/tmp/fanscripts/prof_gc.py`, and the decoded settings are at `/var/home/josemanosalvas/.t3/scratch/2026-10-24-snapshot-the-current-ram-settings-01633672/snapshots/20261024-181828_F16b/decoded/bios_settings.json`.