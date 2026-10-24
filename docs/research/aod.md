# How ZenTimings reads the AM4 DDR4 "BIOS" memory parameters, and how to do it on Linux

I couldn't verify any of this on your machine itself: the ACPI table files and `/dev/mem` both need root, and I didn't escalate. The AML evidence comes from public dumps of a sibling board and a Zen3 laptop. Your own board's identity, kernel restrictions and memory map were read without root.

## 1. How ZenTimings gets each value (ZenTimings `7532394`, ZenStates-Core `869b7e8`)

**DDR4 setup.** `WPF/MainWindow.xaml.cs` L241-249 creates `BiosMemController` only for DDR4/LPDDR4. `ReadDDR4MemoryConfig` (L871-944) then fills it:
1. It calls the `AMD_ACPI` WMI class in `root\wmi`. `AOD.GetWmiFunctions` (`Hardware/Aod/Aod.cs` L529-620) runs `GetObjectID` and `GetObjectID2` to get the command name→ID list.
2. It runs `WMI.RunCommand(cmd.ID)` for "Get APCB Config" (ID `0x00010001`, comment at L921). `Hardware/WMI.cs` L205-234 sends `Inbuf[8]` = cmdID (u32 LE) + arg, and returns `Outbuf.Result`.
3. It then runs "Get memory voltages" and copies the non-zero bytes 27..30 (VDDIO, VTT) over the first buffer (L922-935).
4. If "Get APCB Config" is missing, it uses `cpu.info.aod.Table.RawAodTable` instead, i.e. the raw AOD memory region (L910-917).

**Which source wins.** `ApplyDdr4MemoryConfig` (L762-863):
- ProcODT, RTT, setup times and drive strengths come from the **APOB** (`apob.MemGeneralConfig`, first populated channel, L750-754 and L810-828) when it has them. The WMI/BMC buffer is only the fallback (L830-862).
- VDIMM and VTT always come from the BMC buffer (mV / 1000, L770 and L799). Fallbacks are ASUS WMI, then SuperIO.

**VDDP / VDDG / VSOC.** These come from the PM table (`WPF/Controls/DDR4TimingsPanel.xaml` L96-98). For `0x380905`, `Hardware/PowerTable.cs` L300 gives VDDCR_SOC `0xB4`, CLDO_VDDP `0x224`, VDDG_IOD `0x228`, VDDG_CCD `0x22C`. Your dump reads 1.1129 / 0.8973 / 0.9976 / 0.9976 V, so these already work on Linux through `ryzen_smu`.

**VPP.** It is in the struct (offset 31) and in snapshot exports, but there is no VPP row in the DDR4 panel.

**Correction on the SMU path.** `BiosMemController` never uses `GetDramBaseAddress` or `TransferTableToDram`. Those are PM-table-only:
- ZenStates-Core: Vermeer uses `Zen3Settings` (`Hardware/SMU.cs` L298), which inherits from `Zen2Settings`. `Zen2Settings.cs` L38-40 sets RSMU GetDramBaseAddress = `0x6`, GetTableVersion = `0x8`, TransferTableToDram = `0x5`. `GetDramAddress.cs` L39-48 sends args {1,1}.
- `ryzen_smu` uses the same messages: `smu.c` L665-688, L773-801, L875-911.

**ZenStates-Core `AOD.cs` (the AM5-oriented path).**
- It reads SSDTs from the Windows registry `HKLM\HARDWARE\ACPI` (L102-147); the RSDT walk is commented out (L58-100).
- It finds the region by NameSeg `"AODE"`, then `"AODT"` (L149-158). It parses a fixed 16-byte OperationRegion: name, space, `0x0C`+u32 base, `0x0B`+u16 length (`ACPI.cs` L165-179, `Aod.cs` L207-236).
- It reads physical memory at `base` for `length`.
- Its `AodData` dictionaries are DDR5 layouts at offsets ≥8916. Vermeer falls to `AodDataDictionaryV1` (L466-467), but those offsets lie past a 0x220C-byte AM4 buffer and are skipped (`AodData.cs` L178). So only `RawAodTable` matters on AM4.

## 2. The AOD SSDT / WMI mechanism

**On your machine (read without root):**
- `/sys/bus/acpi/devices/PNP0C14:01/path` = `\AOD_`, `_UID` "AOD".
- It exposes WMI GUID `ABBC0F6A-8EA1-11D1-00A0-C90629100000` (object "AA", so method `\AOD_.WMAA`), with no driver bound, plus a BMOF `05901221-…-5` (object "BA").
- That GUID is the `AMD_ACPI` class GUID listed at https://wutils.com/wmi/root/wmi/amd_acpi/.

**Signature.** The table is an `SSDT` with OEMID `AMD`, OEM table ID `"AMD AOD"`. ZenStates-Core also knows `"AOD     "` and `"CB-01   "` (`ACPI.cs` L16-18). `AOD_` is the ACPI device name, not a table signature. On Gigabyte B550 the region is named **AODT**.

**AML evidence** (sibling board B550 AORUS PRO V2, linuxhw/ACPI `43a4745`, file `Desktop/Gigabyte Technology/B550/B550 AORUS PRO V2/25E5EF251D0E`):
- L26: table #16 is SSDT "AMD AOD", 0x3D74 bytes.
- L23605: `OperationRegion (AODT, SystemMemory, 0xBAC80018, 0x220C)`. The address is a boot-time physical address (irusanov confirms this in https://github.com/irusanov/ZenTimings/issues/18).
- L21570: SW-SMI port `OperationRegion (PSMI, SystemIO, 0xB2, 2)`.
- L24784-24830: `_WDG` holds the same ABBC0F6A GUID. `WMAA` dispatches method IDs 1–8 to AM01..AM08: 3 = `OBID` (GetObjectID), 5 = AM05 (RunCommand), 7 = AM07 (SPD), 8 = `OBIE` (GetObjectID2).
- L21714: the OBID list includes 0x00010001 "Get APCB Config" and 0x00010002 "Get memory voltages".
- L23715-23727:
  - `R101` (Get APCB Config): `MBVS=5`, `MBCB=0x80000000`, `ASMI(0x9D)` (SW SMI).
  - `R102` (Get memory voltages): `MBCB=0x40000000`, SMI `0x9E`.
- L23028-23555: AM05 then copies the AODT fields one-to-one into a 200-byte `OBUF` and returns it.
- **So the "Get APCB Config" WMI result is a byte copy of the start of the AODT NVS buffer.**
- The same layout and logic appear in a Zen3 Cezanne AOD SSDT: hhd-dev/hwinfo `cb716b4`, `devices/Legion_5_15ACH6H/decoded/ssdt3.dsl` L1868, L1974.

**Locating it on Linux:** it is one of `/sys/firmware/acpi/tables/SSDT*`. These files are mode 0400, so root is needed. By size it is almost certainly your `SSDT3`: 15740 bytes, against 15732 on the PRO V2, and your SSDT6/8/9 sizes match that dump's ArticDGP/ArticC/AmdTable exactly. That is an inference, not a check. The script finds it by the `5B 80 "AODT"/"AODE"` bytes.

## 3. Layout of the AODT buffer (byte offsets, little-endian)

Offsets were computed from `Field (AODT, AnyAcc…)` (L23606-23714); the field list totals exactly 0x220C bytes.

| Offset | Field | ZenTimings name | Notes |
|---|---|---|---|
| 0x00 | ASCD u16, ASTS u32 | — | status |
| 0x0C / 0x10 | MBVS / MBCB u32 | — | mailbox |
| 0x14 | MBMC u16 | — | memclk |
| 0x16-0x1A | TTCL, TRCR, TRCW, TRAS, TTRP | — | timings |
| **27 (0x1B)** | VDIO u16 | MemVddio | mV |
| **29 (0x1D)** | VMTT u16 | MemVtt | mV |
| **31 (0x1F)** | VMPP u16 | MemVpp | mV |
| **33 (0x21)** | PODT u16 | ProcODT | ZenTimings reads the low byte |
| **65 / 66 / 67** | RTNM / RTWR / RTPK | RttNom / RttWr / RttPark | |
| **86 / 87 / 88** | ACSP / COSP / CESP | AddrCmd / CsOdt / Cke Setup | |
| **89-92** | CBCK / CBAC / CBCO / CBCE | Clk / AddrCmd / CsOdt / Cke DrvStren | |
| 0x5D | CDVP u16 | — | "Set CLDO_VDDP" (R230) |
| 0x7A | FCLK | — | |
| 0x82 | CVDG u16 | — | "Set CLDO_VDDG" (R238) |
| 0xA5 | IVDG u16 | — | "Set IOD VDDG" (R309) |
| 0xCC | DSPD | — | 16 × 0x214 bytes of SPD copies (WMI method 7) |

- The bold rows match ZenTimings `Resistances` exactly (`Common/BiosMemController.cs` L80-98).
- The units of CDVP, CVDG and IVDG are unverified.
- The "Set" handlers confirm the mapping: R207→PODT, R222-224→RTT, R229-22F→setup/drive strength, R301-303→VDIO/VMTT/VMPP (SMI 0x9E).
- This is a sibling board on an unknown BIOS. The script therefore parses the Field list from your own SSDT and checks it against these offsets.

## 4. Value encodings

Sources: ZenStates-Core `Dictionaries/EncodedValueDictionaries.cs` L101-158 and `Common/EncodedValues.cs` L58-129.

- **ProcODT:**
  - 0 Hi-Z, 1 480, 2 240, 3 160
  - 8 120, 9 96, 10 80, 11 68.6
  - 24 60, 25 53.3, 26 48, 27 43.6
  - 56 40, 57 36.9, 58 34.3, 59 32, 62 30, 63 28.2 Ω
- **Drive strength (Clk/AddrCmd/CsOdt/Cke):** 0 120, 1 60, 3 40, 7 30, 15 24, 31 20 Ω.
- **RttNom / RttPark (MR1/MR5):** 0 off, 1 RZQ/4, 2 RZQ/2, 3 RZQ/6, 4 RZQ/1, 5 RZQ/5, 6 RZQ/3, 7 RZQ/7. RZQ = 240 Ω.
- **RttWr (MR2):** 0 off, 1 RZQ/2, 2 RZQ/1, 3 Hi-Z, 4 RZQ/3.
- **Setup:** raw value shown as `v (v/32 / v%32)`.
- **Voltages:** u16 mV.

## 5. The APOB alternative (ZenTimings' preferred source)

`Hardware/Apob/Apob.cs`:
- Signature "APOB" (L41) at known physical addresses `0xA200000, 0x9F00000, 0x4000000` (L95, L417-426).
- Header: u32 signature, version, TableSize, HeaderSize.
- Entries: u32 group, type, instance, size (including a 48-byte header), then the next entry at +size (`ApobEntry.cs` L14-15). Entries whose group or type ID has 0xFFFF in the upper half are encrypted and skipped (L35).

The MEM group 1 / type 2 entry (`ApobMemGeneralConfig.cs` L46-136) is 496 bytes on Zen2/3:
- MemClkFreq at data+4.
- 11 arrays of 8 per-channel bytes at data+0xA4, in this order: ProcOdt, RttNom, RttWr, RttPark, AddrCmdSetup, CsOdtSetup, CkeSetup, ClkDrvStren, AddrCmdDrvStren, CsOdtCmdDrvStren, CkeDrvStren.
- Same encodings as above. No voltages.

On your E820 map, `0xa200000-0xa20dfff` is "ACPI Non-volatile Storage", the expected APOB spot. The signature is unverified.

## 6. Can it be done reliably on Linux?

**Partly.**

- **The ACPI table file only gives the address, length and layout, not the values.** The values live in physical memory: the AODT buffer and the APOB, both ACPI NVS.

- **Your kernel blocks `/dev/mem` reads of those pages.** It has `CONFIG_STRICT_DEVMEM=y` and `CONFIG_IO_STRICT_DEVMEM=y`; lockdown is `none` and there is no `iomem=` on the cmdline. The kernel code that does this:
  - e820 marks NVS as `IORESOURCE_BUSY`: `arch/x86/kernel/e820.c` L1084-1106 and L1142-1143 (master `5b0764a`).
  - `resource_is_exclusive` treats busy regions as exclusive under IO_STRICT_DEVMEM: `kernel/resource.c` L1884-1929.
  - `devmem_is_allowed` then returns 0: `arch/x86/mm/init.c` L867-895.
  - `read_mem` returns `-EPERM`: `drivers/char/mem.c` v6.12 L139-143.
  - The fix is `iomem=relaxed` (`kernel/resource.c` L2153-2160, L2206). On Bazzite: `rpm-ostree kargs --append=iomem=relaxed`, then reboot.
  - Others hit the same wall: https://github.com/delfianto/zentools/blob/4b1547dd9542975a8f0713d61f7efa0992361056/tools/aod-probe/README.md and ZenTimings issue #18.

- **The AOD buffer may not be current without the SMI.** The firmware writes it at boot or on the WMI SMI 0x9D. Whether it is filled before any WMI call on F16b is unknown; the script warns if it is all zero. Compare its Tcl/Trcd/Tras against your SMN UMC timings. APOB values are what the AGESA boot loader (ABL) actually applied, so they are more trustworthy.

- **Linux has no supported way to call `\AOD_.WMAA`** (that is, to trigger the SMI the way ZenTimings does). There is no ACPI_CUSTOM_METHOD, no ACPI debugger, and acpi_call is out of tree. AMD's proposed `amd-wmi-aod` driver binds GUID `95F20B61-3D44-41F8-87E1-03CBF94C4E8F`, not this board's ABBC0F6A device (https://www.spinics.net/lists/platform-driver-x86/msg67374.html, msg67369). Writing SMI 0x9D by hand is not recommended: MBCB selects which setting the SMM handler applies.

## 7. Script

Saved as `/var/home/josemanosalvas/.t3/scratch/2026-10-24-snapshot-the-current-ram-settings-01633672/scripts/am4_bios_memcfg.py`; run it as root with `python3 -I`.

What I tested:
- **Offline only.** It ran on the B550 AORUS PRO V2 SSDT pulled from linuxhw, on hand-made AOD and APOB buffers I built for the test, and on your `pm_table.bin`.
- **AML parse:** it found AODT at base 0xBAC80018, length 0x220C, and 105 fields whose offsets match ZenTimings.
- **PM table:** it printed SOC 1.1129, VDDP 0.8973, VDDG 0.9976 / 0.9976 V.

It has not run against your real SSDT, `/dev/mem` or APOB.

```python
#!/usr/bin/env python3
"""AM4 / DDR4 "BIOS" memory parameters on Linux, read the way ZenTimings reads them. Run as root.

Source 1, the AOD buffer: the "AMD AOD" SSDT declares OperationRegion(AODT|AODE, SystemMemory, base, len)
in ACPI NVS. ZenTimings' BiosMemController.Resistances overlays the start of that buffer (WMI
RunCommand 0x00010001 "Get APCB Config" returns a copy of it).
Source 2, the APOB: the "APOB" table at a fixed physical address (0xA200000 on this board's E820 map).
Its MEM/type-2 entry has the per-channel ProcODT, RTT, setup and drive strength values. ZenTimings
prefers this source.

Both live in ACPI NVS. When CONFIG_IO_STRICT_DEVMEM=y, /dev/mem refuses those pages unless the kernel
was booted with iomem=relaxed.
Offline use: --ssdt FILE  --region-file FILE  --apob-file FILE  --pm-table FILE
"""
import argparse, glob, os, struct, sys

# ZenStates-Core Dictionaries/EncodedValueDictionaries.cs (DDR4 = APCB / BIOS table encoding)
PROC_ODT = {0: "Hi-Z", 1: "480.0", 2: "240.0", 3: "160.0", 8: "120.0", 9: "96.0", 10: "80.0", 11: "68.6",
            24: "60.0", 25: "53.3", 26: "48.0", 27: "43.6", 56: "40.0", 57: "36.9", 58: "34.3", 59: "32.0",
            62: "30.0", 63: "28.2"}
DRV_STREN = {0: "120.0", 1: "60.0", 3: "40.0", 7: "30.0", 15: "24.0", 31: "20.0"}
RTT = {0: "Disabled", 1: "RZQ/4", 2: "RZQ/2", 3: "RZQ/6", 4: "RZQ/1", 5: "RZQ/5", 6: "RZQ/3", 7: "RZQ/7"}
RTT_WR = {0: "Off", 1: "RZQ/2", 2: "RZQ/1", 3: "Hi-Z", 4: "RZQ/3"}

def ohm(d, v):
    s = d.get(v)
    return f"{s} ohm" if s and s[0].isdigit() else (s or f"unknown({v})")

def rtt(d, v):
    s = d.get(v)
    if s and s.startswith("RZQ/"):
        return f"{s} ({240 // int(s[4:])} ohm)"
    return s or f"unknown({v})"

def setup(v):  # ZenStates-Core Common/EncodedValues.cs Ddr4Setup
    return "0" if v == 0 else f"{v} ({v // 32}/{v % 32})"

# ---------------- physical memory ----------------
def read_phys(addr, n):
    fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
    try:
        out = b""
        while len(out) < n:
            chunk = os.pread(fd, n - len(out), addr + len(out))
            if not chunk:
                break
            out += chunk
        return out
    finally:
        os.close(fd)

def explain(e, addr):
    print(f"  /dev/mem read at 0x{addr:X} failed: {e}")
    if isinstance(e, PermissionError):
        print("  -> EPERM: page is ACPI NVS (IORESOURCE_BUSY) and CONFIG_IO_STRICT_DEVMEM=y, or lockdown is on.\n"
              "     Boot once with kernel arg iomem=relaxed (Bazzite: rpm-ostree kargs --append=iomem=relaxed).")

# ---------------- AML helpers ----------------
def aml_int(b, i):
    op = b[i]
    if op in (0x00, 0x01):
        return op, i + 1
    if op == 0xFF:
        return 0xFFFFFFFFFFFFFFFF, i + 1
    size = {0x0A: 1, 0x0B: 2, 0x0C: 4, 0x0E: 8}.get(op)
    if size is None:
        raise ValueError(f"non-constant TermArg (opcode 0x{op:02X}) at 0x{i:X}")
    return int.from_bytes(b[i + 1:i + 1 + size], "little"), i + 1 + size

def pkg_len(b, i):
    lead = b[i]
    n = lead >> 6
    if n == 0:
        return lead & 0x3F, i + 1
    v = lead & 0x0F
    for k in range(n):
        v |= b[i + 1 + k] << (4 + 8 * k)
    return v, i + 1 + n

def name_seg_ok(s):
    return len(s) == 4 and all(c in b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in s) and s[:1] not in b"0123456789"

def find_region(ssdt):
    for name in (b"AODE", b"AODT"):  # same order as ZenStates-Core Aod.cs GetAodRegionIndex
        for prefix in (b"", b"\\"):
            i = ssdt.find(b"\x5b\x80" + prefix + name)
            if i >= 0:
                break
        if i >= 0:
            j = i + 6 + len(prefix)
            space = ssdt[j]
            base, j = aml_int(ssdt, j + 1)
            length, _ = aml_int(ssdt, j)
            return name.decode(), space, base, length
    return None

def parse_fields(ssdt, region):
    """{field name: (bit offset, bit width)} from every Field(region, ...) in the table."""
    fields, pos = {}, 0
    while True:
        i = ssdt.find(b"\x5b\x81", pos)
        if i < 0:
            return fields
        pos = i + 2
        try:
            plen, j = pkg_len(ssdt, i + 2)
            end = i + 2 + plen
            if ssdt[j:j + 4] != region.encode():
                continue
            j += 5  # NameSeg + FieldFlags
            bit = 0
            while j < end:
                t = ssdt[j]
                if t == 0x00:                      # ReservedField
                    w, j = pkg_len(ssdt, j + 1); bit += w
                elif t == 0x01:                    # AccessField
                    j += 3
                elif t == 0x03:                    # ExtendedAccessField
                    j += 4
                elif name_seg_ok(ssdt[j:j + 4]):   # NamedField
                    nm = ssdt[j:j + 4].decode()
                    w, j = pkg_len(ssdt, j + 4)
                    fields[nm] = (bit, w); bit += w
                else:
                    break                          # ConnectField or unexpected byte: stop this list
        except IndexError:
            continue

def find_aod_ssdt(path=None):
    paths = [path] if path else sorted(glob.glob("/sys/firmware/acpi/tables/SSDT*"))
    for p in paths:
        with open(p, "rb") as f:
            data = f.read()
        if data[:4] == b"SSDT" and find_region(data):
            return p, data
    return None, None

# ZenTimings Common/BiosMemController.cs Resistances (fallback when the AML field names differ)
FALLBACK = {"VDIO": (27, 16), "VMTT": (29, 16), "VMPP": (31, 16), "PODT": (33, 8), "RTNM": (65, 8),
            "RTWR": (66, 8), "RTPK": (67, 8), "ACSP": (86, 8), "COSP": (87, 8), "CESP": (88, 8),
            "CBCK": (89, 8), "CBAC": (90, 8), "CBCO": (91, 8), "CBCE": (92, 8)}

def get(buf, fields, name):
    if name in fields:
        bit, w = fields[name]
    elif name in FALLBACK:
        byte, w = FALLBACK[name]; bit = byte * 8
    else:
        return None
    if bit % 8 or w % 8 or bit // 8 + w // 8 > len(buf):
        return None
    return int.from_bytes(buf[bit // 8: bit // 8 + w // 8], "little")

def aod(args):
    print("== AOD buffer (ZenTimings BiosMemController / WMI 'Get APCB Config' layout)")
    p, ssdt = find_aod_ssdt(args.ssdt)
    if not ssdt:
        print("  no SSDT with OperationRegion AODE/AODT found"); return
    region, space, base, length = find_region(ssdt)
    oem_tid = ssdt[16:24].rstrip(b"\0 ").decode(errors="replace")
    print(f"  table {p} (OEM table id '{oem_tid}', {len(ssdt)} bytes)")
    print(f"  OperationRegion({region}, space={space}, base=0x{base:X}, len=0x{length:X})")
    fields = parse_fields(ssdt, region)
    print(f"  {len(fields)} named fields parsed from Field({region}); "
          f"ZenTimings offsets agree: {all(fields.get(k, (v[0]*8,))[0] == v[0]*8 for k, v in FALLBACK.items())}")
    if args.region_file:
        buf = open(args.region_file, "rb").read()
    else:
        try:
            buf = read_phys(base, length)
        except OSError as e:
            explain(e, base); return
    g = lambda n: get(buf, fields, n)
    if all((g(n) or 0) == 0 for n in ("VDIO", "PODT", "CBCK", "TTCL")):
        print("  WARNING: buffer looks unpopulated (all zero); firmware may only fill it on SMI 0x9D")
    print(f"  MemVddio (VDIMM) : {g('VDIO')} mV    MemVtt : {g('VMTT')} mV    MemVpp : {g('VMPP')} mV")
    po = g("PODT")
    print(f"  ProcODT          : {ohm(PROC_ODT, po & 0xFF) if po is not None else None}  (raw 0x{po or 0:X})")
    print(f"  RttNom / RttWr / RttPark : {rtt(RTT, g('RTNM'))} / {rtt(RTT_WR, g('RTWR'))} / {rtt(RTT, g('RTPK'))}")
    print(f"  ClkDrvStr {ohm(DRV_STREN, g('CBCK'))}, AddrCmdDrvStr {ohm(DRV_STREN, g('CBAC'))}, "
          f"CsOdtDrvStr {ohm(DRV_STREN, g('CBCO'))}, CkeDrvStr {ohm(DRV_STREN, g('CBCE'))}")
    print(f"  AddrCmdSetup {setup(g('ACSP'))}, CsOdtSetup {setup(g('COSP'))}, CkeSetup {setup(g('CESP'))}")
    print(f"  raw BIOS-set fields (units unverified): CLDO_VDDP(CDVP)={g('CDVP')} CCD VDDG(CVDG)={g('CVDG')} "
          f"IOD VDDG(IVDG)={g('IVDG')} FCLK={g('FCLK')} MEMCLK(MBMC)={g('MBMC')}")
    print(f"  plausibility (compare with UMC timings): Tcl={g('TTCL')} TrcdRd={g('TRCR')} TrcdWr={g('TRCW')} "
          f"Tras={g('TRAS')} Trp={g('TTRP')} Trfc={g('TRFC')} Cmd2T={g('CM2T')} GDM={g('GRDN')}")
    print("  first 0xCC bytes:", buf[:0xCC].hex())

# ---------------- APOB ----------------
APOB_ADDRS = (0xA200000, 0x9F00000, 0x4000000)  # ZenStates-Core Hardware/Apob/Apob.cs KnownAddresses

def apob_entries(t, hsize, tsize):
    starts = [hsize] + [struct.unpack_from("<I", t, o)[0] for o in range(0x30, hsize - 32, 4)]
    seen = {}
    for off in starts:
        for _ in range(4096):
            if not off or off + 16 > tsize or off in seen:
                break
            g, ty, inst, size = struct.unpack_from("<4I", t, off)
            if size < 16 or off + size > tsize:
                break
            seen[off] = (g, ty, inst, size)
            off += size
    return sorted(seen.items())

def apob(args):
    print("== APOB MEM general configuration info (group 1, type 2), ZenTimings' primary source")
    t = None
    if args.apob_file:
        t = open(args.apob_file, "rb").read()
    else:
        for a in APOB_ADDRS:
            try:
                hdr = read_phys(a, 16)
            except OSError as e:
                explain(e, a); continue
            if hdr[:4] == b"APOB":
                tsize = struct.unpack_from("<I", hdr, 8)[0]
                if 16 <= tsize <= 0x1000000:
                    t = read_phys(a, tsize); print(f"  APOB at 0x{a:X}, {tsize} bytes"); break
    if not t or t[:4] != b"APOB":
        print("  APOB not found/readable"); return
    _, ver, tsize, hsize = struct.unpack_from("<4I", t, 0)
    tsize = min(tsize or len(t), len(t))
    for off, (g, ty, inst, size) in apob_entries(t, hsize, tsize):
        if (g >> 16) == 0xFFFF or (ty >> 16) == 0xFFFF or (g & 0xFFFF, ty & 0xFFFF) != (1, 2):
            continue
        data, dsize = off + 48, size - 48
        if dsize == 496 - 48:
            nch, arr = 8, data + 0xA4
        elif dsize == 256 - 48:
            nch, arr = 4, data + 0x98
        else:
            print(f"  entry at 0x{off:X} has unknown size {size}"); continue
        memclk, maxrate = struct.unpack_from("<HH", t, data + 4)
        print(f"  entry @0x{off:X} inst {inst}: MemClkFreq {memclk} MHz, DdrMaxRate {maxrate}")
        for c in range(nch):
            v = [t[arr + c + k * nch] for k in range(11)]
            if not (v[0] or any(v[7:11])):
                continue
            print(f"  channel {chr(65 + c)}: ProcODT {ohm(PROC_ODT, v[0])}, RttNom {rtt(RTT, v[1])}, "
                  f"RttWr {rtt(RTT_WR, v[2])}, RttPark {rtt(RTT, v[3])}")
            print(f"             Setup AddrCmd {setup(v[4])}, CsOdt {setup(v[5])}, Cke {setup(v[6])}; DrvStr Clk "
                  f"{ohm(DRV_STREN, v[7])}, AddrCmd {ohm(DRV_STREN, v[8])}, CsOdt {ohm(DRV_STREN, v[9])}, "
                  f"Cke {ohm(DRV_STREN, v[10])}")

# ---------------- PM table (VDDP / VDDG / VSOC, as ZenTimings shows them) ----------------
PM_OFFS = {0x380905: {"VDDCR_SOC": 0xB4, "CLDO_VDDP": 0x224, "CLDO_VDDG_IOD": 0x228, "CLDO_VDDG_CCD": 0x22C,
                      "FCLK": 0xC0, "UCLK": 0xC8, "MCLK": 0xCC}}  # ZenStates-Core Hardware/PowerTable.cs

def pm(args):
    print("== PM table (ryzen_smu)")
    try:
        if args.pm_table:
            ver, pt = 0x380905, open(args.pm_table, "rb").read()
        else:
            ver = struct.unpack("<I", open("/sys/kernel/ryzen_smu_drv/pm_table_version", "rb").read(4))[0]
            pt = open("/sys/kernel/ryzen_smu_drv/pm_table", "rb").read()
    except OSError as e:
        print(f"  {e}"); return
    offs = PM_OFFS.get(ver)
    if not offs:
        print(f"  unknown pm_table version 0x{ver:X}"); return
    print("  " + ", ".join(f"{k} {struct.unpack_from('<f', pt, o)[0]:.4f}" for k, o in offs.items()))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    for opt in ("--ssdt", "--region-file", "--apob-file", "--pm-table"):
        ap.add_argument(opt)
    a = ap.parse_args()
    if os.geteuid() != 0 and not (a.ssdt and a.region_file):
        print("note: not root; /sys/firmware/acpi/tables/* and /dev/mem need root", file=sys.stderr)
    for fn in (aod, apob, pm):
        try:
            fn(a)
        except Exception as e:  # keep going with the other sources
            print(f"  error: {e!r}")
```

## Still unknown

- Your own AODT base address and the exact `SSDT#`: needs root to read.
- Whether F16b fills the AODT buffer at boot without SMI 0x9D.
- The units of CDVP, CVDG and IVDG.
- The `AMD_ACPI` method IDs on this BIOS: decode `/sys/bus/wmi/devices/05901221-D566-11D1-B2F0-00A0C9062910-5/bmof` with bmfdec. The sibling board's AML uses 1–8, matching AMD's documented IDs.
- That the APOB signature is really at 0xA200000 on this board.