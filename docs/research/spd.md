**FCH SMBus / DDR4 SPD on Bazzite (B550 AORUS PRO, 5800X3D, kernel 7.2.9)**

The most likely reason the adapter is missing is an ACPI resource conflict at I/O port 0xB00, but I could not confirm it: reading the kernel log needs root, and the early-boot lines are gone. Everything below was checked without root, and nothing was written to the SMBus.

## Facts checked on this machine
- **SMBus location:** the controller is 00:14.0 [1022:790b], rev 0x61, subsystem 1458:5001. It sits on root bus 00, inside the CPU's FCH. The B550 chipset's devices (1022:43ee, 43eb, 43e9) are on bus 02. So the SMBus is not part of the B550 chipset.
- **The driver is loaded but not attached.** `lspci -k -s 00:14.0` lists "Kernel modules: i2c_piix4, sp5100_tco" with no "Kernel driver in use" line.
  - `/sys/bus/pci/devices/0000:00:14.0/driver` does not exist, and `/sys/bus/pci/drivers/piix4_smbus/` has no device link. So `i2c_piix4` tried to probe 00:14.0 and failed.
  - Neither `/proc/ioports` nor `/proc/iomem` shows a `piix4_smbus` or `sb800_piix4_smb` entry.
- **ACPI resource checking is at its default (strict).** `/proc/cmdline` has no `acpi_enforce_resources=` option.
- **Why the failure reason can't be read yet:**
  - `kernel.dmesg_restrict=1`.
  - The journal for this boot starts about 148,644 s after boot (uptime is about 255,209 s). The oldest lines were deleted by log spam from the `stash` process.
- **Memory is 4 DIMMs, not 2.** Your earlier udev capture (`raw/dmi_memory_udev.txt`) shows 4 filled slots, each DDR4 8 GiB, single rank. Part number is "4400 C19 Series" and the module manufacturer ID is Bank 6, Hex 0x02, which decode-dimms' JEP106 table maps to "Patriot Memory".
- **Installed:**
  - i2c-tools-4.4-4.fc44: `i2cdetect`, `i2cdump`, `i2cget`, `i2cset`, `i2ctransfer`, `eeprog`.
  - dmidecode-3.7, acpica-tools (`acpidump`, `iasl`), perl 5.42 with the modules decode-dimms needs, distrobox 1.8.2.5 and podman.
  - Kernel config: `CONFIG_I2C_PIIX4=m`, `CONFIG_EEPROM_EE1004=m`, `CONFIG_SENSORS_JC42=m`, `CONFIG_DMI=y`.
- **Not installed:** i2c-tools-perl, so there is no `decode-dimms`.
- **`i2c_piix4` options:** it only has `force` and `force_addr`. It has no `ignore_resource_conflict` option.

## (1) Why the FCH adapter doesn't register
These are the steps the driver takes for 1022:790b rev 0x61. Line numbers are in `drivers/i2c/busses/i2c-piix4.c` at tag v7.2 (https://github.com/torvalds/linux/blob/v7.2/drivers/i2c/busses/i2c-piix4.c).

1. `piix4_probe` calls `piix4_setup_sb800(dev,id,0)` (L1056).
2. The driver takes the newer "find the base address" path for this revision, because it is 0x49 or higher (L389-397).
3. Because the revision is 0x51 or higher, it reads the FCH power-management register through memory-mapped I/O instead of ports 0xCD6/0xCD7 (`piix4_sb800_use_mmio`, L213-221).
   - It maps `FCH_PM_BASE` 0xFED80300 (`include/linux/platform_data/x86/amd-fch.h` L5) via `request_mem_region_muxed` (L165-171).
   - If that region is busy it logs "SMBus base address memory region 0x%x already in use."
   - This memory-mapped path first appears in v5.18; it is absent in v5.17 (I compared both tags).
4. If the enable bit is clear, it logs "SMBus Host Controller not enabled!" (L361-365).
5. `acpi_check_region(0xB00, 9)` (L406-407): if this fails, the probe returns -ENODEV and `i2c_piix4` prints nothing itself.
   - Only ACPI lines appear. ACPICA prints `ACPI Warning: SystemIO range 0x…0B00-0x…0B08 conflicts with OpRegion … (\PATH)` (`drivers/acpi/acpica/utaddress.c` L204-213).
   - The kernel then prints `ACPI: OSL: Resource conflict; ACPI support missing from driver?` and, in strict mode, returns -EBUSY (`drivers/acpi/osl.c` L12, L1503-1530).
6. If the ports are taken, it logs "SMBus region 0x%x already in use!" (L409-413).

The memory-mapped access does not get around the step 5 check. That path is only used to read the base address, enable bit and port select. The SMBus registers themselves are still port I/O at the base address (`i2c-piix4.h` L18-29), and that I/O range is what gets checked against ACPI.

**Most likely cause: step 5 (status: PLAUSIBLE, not confirmed).**
- OpenRGB's docs say: "Some Gigabyte/Aorus motherboards have an ACPI conflict with the SMBus controller… `acpi_enforce_resources=lax`" (https://gitlab.com/CalcProgrammer1/OpenRGB/-/raw/master/Documentation/SMBusAccess.md L43; same in https://openrgb-wiki.readthedocs.io/en/latest/).
- The exact ACPI region name on BIOS F16b is unknown.

**Ruled out or unrelated:**
- **sp5100_tco** maps the same 0xFED80300 window, but also with `request_mem_region_muxed` (`drivers/watchdog/sp5100_tco.c` L363), so the two coexist.
- **IT8688E/IT8792E** are LPC Super I/O chips (`drivers/hwmon/it87.c` L37), not on the SMBus.
  - it87 loaded with no `ignore_resource_conflict` option set anywhere in modprobe.d or the cmdline, so its own I/O range has no ACPI conflict (it87.c L3703-3707).
- **gigabyte_wmi** works through ACPI WMI methods. Whether that firmware code touches the SMBus is unknown.
- **No module-option bypass:** `force` and `force_addr` are rejected on SB800-and-later controllers (L380-385). `acpi_enforce_resources` can only be set at boot (osl.c L1484-1499).

**Commands to confirm (root):**
```
sudo dmesg | grep -iE 'piix4|sb800|OpRegion|Resource conflict|0B00|fed80300'
# boot lines are gone from the journal here, so re-run the probe (module has 0 users):
sudo modprobe -r i2c_piix4 && sudo modprobe i2c_piix4 && sudo dmesg | tail -n 15
# find which ACPI code owns 0xB00:
mkdir -p ~/acpi && cd ~/acpi && sudo acpidump -b && sudo chown "$USER" ./*.dat
for f in *.dat; do iasl -d "$f" >/dev/null 2>&1; done
grep -n -i 'SystemIO' *.dsl | grep -iE '0x0B[0-9A-F]{2}|SMB'
```

**Fix on Bazzite.** `lax` makes the check log a warning and continue instead of failing. The kernel then prints "Resource conflict: System may be unstable or behave erratically" (osl.c L1522-1526). Firmware code and `i2c_piix4` can then use the SMBus at the same time with nothing coordinating them. The it87 docs describe the same kind of risk, plus reports of boot failures with system-wide lax (`Documentation/hwmon/it87.rst` L212-221).
- **One boot only (preferred):** at the GRUB menu press `e`, add `acpi_enforce_resources=lax` to the `linux` line, then press Ctrl-X.
- **Persistent:** `sudo rpm-ostree kargs --append-if-missing=acpi_enforce_resources=lax`, then `systemctl reboot`.
  - The man page says this creates a new deployment from the default (next-boot) one. There is a staged 44.20261023 update pending, so that reboot also applies the update.
  - To undo: `sudo rpm-ostree kargs --delete-if-present=acpi_enforce_resources=lax`.
- **Verify after reboot:**
  ```
  grep -o 'acpi_enforce_resources=[a-z]*' /proc/cmdline
  i2cdetect -l
  grep -H . /sys/bus/i2c/devices/i2c-*/name | grep PIIX4
  ```
  `i2cdetect -l` only lists adapters; it sends nothing on the bus. Expected adapter names: "SMBus PIIX4 adapter port 0 at 0b00", "port 2 at 0b00", and "port 1 at 0b20". Port 1 only appears if `\_SB.ASFC` is absent (L955-956, L1093-1102; `Documentation/i2c/busses/i2c-piix4.rst` L118-124).

## (2) Cleanest way to read the SPD
- **It should happen automatically.** Since v6.11 (present in v6.11, absent in v6.10), port 0 calls `i2c_register_spd_write_enable` (i2c-piix4.c L968-974).
  - That code counts the memory slots in DMI. If every filled slot is DDR4 (type 0x1A), it creates an "ee1004" device at 0x50+n for each slot (n<4 here) (`drivers/i2c/i2c-smbus.c` L374-475, specifically L434-436 and L462).
  - It checks each address with a one-byte read, not a write, in the 0x50-0x5F range (`drivers/i2c/i2c-core-base.c` L2456-2462), and logs "Successfully instantiated SPD at 0x50" (L470).
  - If SPD byte 14 bit 7 says a thermal sensor exists, ee1004 also loads jc42 at 0x18-0x1B (`drivers/misc/eeprom/ee1004.c` L183-213).
- **Check:** `ls /sys/bus/i2c/drivers/ee1004/`
- **Manual fallback, only if those devices are missing:**
  ```
  BUS=$(grep -l 'SMBus PIIX4 adapter port 0 at 0b00' /sys/bus/i2c/devices/i2c-*/name | sed -E 's#.*/i2c-([0-9]+)/name#\1#')
  sudo modprobe ee1004
  for a in 0x50 0x51 0x52 0x53; do echo ee1004 $a | sudo tee /sys/bus/i2c/devices/i2c-$BUS/new_device; done
  # to undo: echo 0x50 | sudo tee /sys/bus/i2c/devices/i2c-$BUS/delete_device
  ```
  The `new_device` interface is documented in `Documentation/i2c/instantiating-devices.rst` L236-251.
- **ee1004 is read-only and needs no root to read.**
  - It exposes 512 bytes as a read-only `eeprom` file with no write method (ee1004.c L280-294).
  - The file mode works out to 0444 (`drivers/nvmem/core.c` L278-295, L400-445).
- **Raw capture (one read per DIMM):**
  ```
  mkdir -p ~/spd && cd ~/spd
  for d in /sys/bus/i2c/drivers/ee1004/*-005?; do n=$(basename "$d"); cat "$d/eeprom" > "spd-$n.bin"; hexdump -C "spd-$n.bin" > "spd-$n.hex"; done
  head -c 1 /sys/bus/i2c/drivers/ee1004/*-0050/eeprom >/dev/null   # optional: leaves page 0 selected, see (4)
  ```
- **decode-dimms is not available on Bazzite.** Fedora puts `decode-*` in the i2c-tools-perl subpackage (https://src.fedoraproject.org/rpms/i2c-tools/raw/rawhide/f/i2c-tools.spec L143-148), which isn't installed. decode-dimms looks under `/sys/bus/i2c/drivers/{eeprom,at24,ee1004}` and reads `<dev>/eeprom` (decode-dimms v4.4 L2450, L2613-2657). With `-x` it accepts `hexdump -C` files (L2544). Options, cleanest first:
  - **(a) Run the upstream script with the system perl, as your normal user (no layering):**
    ```
    curl -fLo ~/spd/decode-dimms 'https://git.kernel.org/pub/scm/utils/i2c-tools/i2c-tools.git/plain/eeprom/decode-dimms?h=v4.4'
    sha256sum ~/spd/decode-dimms   # 691efbbf06b8a66c8b0b6a6811e36ac7cb17cda8b14a51d42a51b7022f6830e5 on 2026-10-24
    perl ~/spd/decode-dimms --side-by-side
    perl ~/spd/decode-dimms -x ~/spd/spd-*.hex
    ```
  - **(b) distrobox:** `distrobox create -n spd -i registry.fedoraproject.org/fedora-toolbox:44 && distrobox enter spd -- sudo dnf -y install i2c-tools-perl && distrobox enter spd -- decode-dimms -x ~/spd/spd-*.hex`. Using `-x` on the saved dumps means it doesn't depend on whether the container can see the host's `/sys`.
  - **(c) Layer the package:** `sudo rpm-ostree install i2c-tools-perl`, then reboot. It requires the same libi2c version (spec L53); libi2c-4.4-4.fc44 is installed.

## (3) What `dmidecode -t 17` shows
- **Commands:** `sudo dmidecode -t 17`, and `sudo dmidecode -t 17 -u` for the raw bytes. The second byte of the raw data is the record length, which decides which fields are present.
- **Fields dmidecode 3.7 decodes** (dmidecode.c L4964-5057, each gated by record length):
  - Total/Data Width, Size, Form Factor, Set, Locator, Bank Locator, Type, Type Detail
  - Speed (length ≥ 0x17)
  - Manufacturer, Serial Number, Asset Tag, Part Number (≥ 0x1B)
  - Rank (≥ 0x1C), Configured Memory Speed (≥ 0x22)
  - Minimum/Maximum/Configured Voltage (≥ 0x28)
  - Memory Technology, Operating Mode Capability, Firmware Version, Module Manufacturer ID, Module Product ID, Subsystem Controller IDs (≥ 0x34)
  - Non-Volatile / Volatile / Cache / Logical Size (≥ 0x3C / 0x44 / 0x4C / 0x54)
  - Extended speeds (≥ 0x5C), PMIC/RCD (≥ 0x64)
- **What this board reports** (from the udev capture of the same table):

| Field | Value |
|---|---|
| Locator / Bank Locator | "DIMM 0"/"DIMM 1" in "P0 CHANNEL A"/"P0 CHANNEL B" |
| Type / Type Detail | DDR4, Synchronous Unbuffered |
| Size / Rank | 8 GiB, rank 1 |
| Speed / Configured Memory Speed | both 3733 MT/s |
| Manufacturer (string) | "Unknown" |
| Module Manufacturer ID | Bank 6, Hex 0x02 |
| Part Number | "4400 C19 Series" |
| Serial Numbers | 101206FF, 00000000, 101206F0, 00000000 |
| Memory Technology | DRAM |
| Volatile Size | 8 GiB, so the record is at least 0x44 bytes long |

- **Caveats:**
  - **Voltage:** udev's "1" is a formatting artefact. systemd v259 prints whole-100 mV values with `%.1g` (`src/udev/dmi_memory_id/dmi_memory_id.c` L226-235), so 1.0 to 1.4 V all show as "1". dmidecode prints `%.1f V` (dmidecode.c L2864-2871). The real value is unknown until you run dmidecode. Whether this AMI BIOS fills Configured Voltage with your BIOS DRAM voltage setting or a nominal value is also unknown.
  - **Speed:** the BIOS puts the running speed in "Speed" as well, so DMI does not show the kit's rated/XMP speed. The SPD does.
  - **Manufacturer:** Bank 6/0x02 decodes to Patriot Memory (decode-dimms v4.4 L212 and L527-539; bank = continuation count + 1 per `dmi_memory_id.c` L401-403).
  - **Part number:** in DDR4 SPD the part number is bytes 329-348 (decode-dimms L2274). Whether the BIOS copies it verbatim is not verified.
  - **Missing fields:** Module Product ID, Cache Size and Logical Size are zero or absent (udev skips zeros). dmidecode will print Unknown or None.

## (4) Is reading SPD safe?
**What the historical warnings are about.** The kernel source explains it: quick-write probing "is known to corrupt the 24RF08 EEPROMs… and could also irreversibly write-protect some EEPROMs, so for address ranges 0x30-0x37 and 0x50-0x5f, we use a short byte read instead" (i2c-core-base.c L2431-2441).
- i2cdetect's default mode does the same for those two ranges (`tools/i2cdetect.c` v4.4 L63-69).
- Its man page: "This program can confuse your I2C bus, cause data loss and worse!". `-q` is "known to corrupt the Atmel AT24RF08"; `-r` is "known to lock SMBus on… clock chips at address 0x69" (`i2cdetect.8` L43-44, L68-76).
- i2cdump: its "c" mode "starts with WRITING a byte to the chip"; "Do not use i2cdump on random addresses" (`i2cdump.8` L77-85).

**DDR4 (EE1004) specifically**, from the ST M34E04 datasheet DocID023348 Rev 7 (mirror https://static.chipdip.ru/lib/320/DOC038320374.pdf; product page https://www.st.com/en/memories/m34e04.html):
- Write-protect commands live at 0x30-0x37: SWP0=0x31, SWP1=0x34, SWP2=0x35, SWP3=0x30, CWP=0x33. All of them need a high voltage (VHV) on the SA0 pin (Table 2; §5.1.1: "To issue the SWP(n) and CWP instructions, the signal applied on SA0 must be driven to VHV").
- 0x36/0x37 select page 0/1. The selection is not stored and resets to page 0 on power-on reset (§3.8.3). A read at 0x36 reports the current page (§3.8.4).

So from the host, a motherboard cannot set or clear DDR4 write protection. The real risk is writes to 0x50-0x57, which can overwrite SPD blocks that aren't protected. Whether your Patriot modules' blocks are protected is unknown.

**The ee1004 driver's only write** is a one-byte page-select command to 0x36/0x37 (ee1004.c L108). Everything else is a read, and the sysfs file has no write path. One caveat: after a full 512-byte read the bus is left on page 1. I found no documented harm from that, but the `head -c 1` line above puts it back on page 0 for free.

**Gigabyte-specific warnings:** the OpenRGB wiki says "There were reports of bricked Gigabyte Aorus Z390 motherboards caused by dumping SMBus address 0x68". Its README notes that hardware has been damaged or bricked before (README.md L31).

**Safe, read-only:**
- `lspci`, `i2cdetect -l`, `i2cdetect -F $BUS`
- `modprobe ee1004` / `jc42`, and `new_device` with ee1004 at 0x50-0x53
- `cat` / `hexdump -C` of `/sys/bus/i2c/drivers/ee1004/*/eeprom`
- `decode-dimms`, `dmidecode -t 17`, `acpidump`
- If you need a presence check, only `sudo i2cdetect -y $BUS 0x50 0x57` (byte reads in that range).

**Avoid:**
- `i2cset`, `i2ctransfer` writes, `eeprog -w`
- `i2cdump` anywhere outside 0x50-0x57, and i2cdump mode `c`
- `i2cdetect` without an address range (it quick-writes elsewhere, including 0x68/0x69), and `i2cdetect -q`/`-r`
- OpenRGB / liquidctl / coolercontrol SMBus scans while you capture
- The `at24` or `eeprom` drivers on DDR4 (decode-dimms L2812-2813 says to use ee1004)
- While running with `lax`, unloading gigabyte_wmi (`sudo modprobe -r gigabyte_wmi`) lowers the chance of firmware code using the SMBus at the same time. Whether that driver's firmware path touches the SMBus at all is unknown.

The kernel, ACPI, nvmem, i2c-tools, dmidecode and systemd sources I quoted are saved under `/tmp/spdresearch/{src,web,dmi,st}`. Nothing was written to your scratch directory.