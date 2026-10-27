#!/usr/bin/env python3
"""AM4 / DDR4 "BIOS" memory parameters on Linux, read the way ZenTimings reads them. Run as root.

Source 1, the AOD buffer: the "AMD AOD" SSDT declares OperationRegion(AODT|AODE, SystemMemory, base, len)
in ACPI NVS. ZenTimings' BiosMemController.Resistances overlays the start of that buffer (WMI
RunCommand 0x00010001 "Get APCB Config" returns a copy of it).
Source 2, the APOB: the "APOB" table at a fixed physical address (0xA200000 on the B550 AORUS PRO's E820 map).
Its MEM/type-2 entry has the per-channel ProcODT, RTT, setup and drive strength values. ZenTimings
prefers this source.

Both live in ACPI NVS. When CONFIG_IO_STRICT_DEVMEM=y, /dev/mem refuses those pages unless the kernel
was booted with iomem=relaxed.
Offline use: --ssdt FILE  --region-file FILE  --apob-file FILE  --pm-table FILE

Further sections (printed after the PM table; the three sections above keep their output):
  * more of the APOB, following irusanov/ZenStates-Core 869b7e8 Hardware/Apob/*.cs: the entry list, the DDR4
    channel timing blocks with MR0-MR6 (GEN 7/3), the DIMM SPD copies (MEM 1/17) with a DDR4 SPD + XMP 2.0
    decode, the SMBIOS memory info (8/8), the APCB boot info (1/16), the ABL event log and training status,
    and the fabric memory map;
  * the SPD copies of the AODT buffer (field DSPD, 16 x 0x214-byte records read by WMI method AM07);
  * raw dumps of Gigabyte's EasyTuneSetup / EasyVoltage / EasyHealth buffers (EFI variables hold the address),
    sized with the EDK2 pool header in front of them;
  * raw dumps of every E820 "ACPI Non-volatile Storage" range, plus the SystemMemory OperationRegions that
    the ACPI tables declare in them;
  * --dump-dir also gets apob_spd_<slot>.bin files and bios_memcfg.json with everything decoded.
Every decoder checks bounds and runs inside its own guard: a bad buffer prints "decode error" and the rest
of the snapshot continues. Extra options for offline tests: --devmem IMAGE (sparse file, offset = physical
address), --efivars DIR, --memmap DIR, --acpi-dir DIR; --no-nvs-dump skips the NVS range dumps.
"""
import argparse, glob, hashlib, json, os, struct, sys, time

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
DEVMEM = "/dev/mem"  # --devmem replaces it with an image file for offline tests
STATE = {}           # buffers read by aod() / apob(), reused by the sections added below

def read_phys(addr, n):
    fd = os.open(DEVMEM, os.O_RDONLY | os.O_SYNC)
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

def read_phys_pages(addr, n):
    """Page-wise read that stops at the first refused page (devmem_is_allowed refuses System RAM even with
    iomem=relaxed, and read_mem fails the whole request if any page is refused)."""
    out = b""
    while len(out) < n:
        a = addr + len(out)
        step = min(n - len(out), 4096 - (a & 0xFFF))
        try:
            out += read_phys(a, step)
        except OSError:
            break
    return out

def dump(args, name, data):
    if args.dump_dir and data:
        try:  # a failed raw save must not drop the decode below it (and STATE for the later sections)
            with open(os.path.join(args.dump_dir, name), "wb") as f:
                f.write(data)
        except OSError as e:
            print(f"  raw {len(data)} bytes NOT saved to {name}: {e!r}")
            return
        print(f"  raw {len(data)} bytes saved to {name}")

def explain(e, addr):
    print(f"  /dev/mem read at 0x{addr:X} failed: {e}")
    if isinstance(e, PermissionError) and e.errno == 13:  # EACCES from open(): not root
        print("  -> EACCES: /dev/mem needs root (run via sudo/pkexec).")
    elif isinstance(e, PermissionError):
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
        dump(args, "aodt.bin", buf)
    STATE["aod"] = {"buf": buf, "fields": fields, "base": base, "length": length}
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
    # the offset list sits between 0x30 and the 32-byte hash at the end of the header (ZenStates-Core
    # Apob.cs GetConfigOffsets); min() keeps a corrupt HeaderSize from reading past the table
    starts = [hsize] + [struct.unpack_from("<I", t, o)[0] for o in range(0x30, min(hsize, len(t)) - 32, 4)]
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
                    t = read_phys_pages(a, tsize)
                    STATE["apob_addr"] = a
                    print(f"  APOB at 0x{a:X}, TableSize {tsize} bytes, {len(t)} readable")
                    dump(args, f"apob_{a:x}.bin", t); break
    if not t or t[:4] != b"APOB":
        print("  APOB not found/readable"); return
    _, ver, tsize, hsize = struct.unpack_from("<4I", t, 0)
    tsize = min(tsize or len(t), len(t))
    STATE["apob"] = {"t": t, "version": ver, "tsize": tsize, "hsize": hsize}
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

# =====================================================================================================
# APOB, SPD, Easy* and NVS sections. Sources (pinned): irusanov/ZenStates-Core 869b7e82 (ZSC), openSIL 9648171d
# xUSL/Include/ApobCmn.h, EDK2 0a503bc2 MdeModulePkg/Core/Dxe/Mem/Pool.c, coreboot 80dd4ace
# src/include/device/dram/ddr4.h + src/device/dram/ddr4.c (DDR4 MR fields), JEDEC JESD79-4 sec. 3.5 (MR0-MR6)
# and JESD21-C Annex L (DDR4 SPD). File:line references are to those commits.
# =====================================================================================================
OUT = {"errors": []}  # everything decoded below, written to <dump-dir>/bios_memcfg.json


class Guard:
    """One decoder: an exception is printed and recorded, the snapshot carries on."""
    def __init__(self, what):
        self.what = what

    def __enter__(self):
        return self

    def __exit__(self, et, e, tb):
        if et is not None and issubclass(et, Exception):
            print(f"  {self.what}: decode error: {e!r}")
            OUT["errors"].append(f"{self.what}: {e!r}")
            return True
        return False


def U(b, o, n):
    """Little-endian unsigned n-byte read with an explicit bounds check."""
    if o < 0 or o + n > len(b):
        raise ValueError(f"read of {n} bytes at 0x{o:X} past the end (0x{len(b):X})")
    return int.from_bytes(b[o:o + n], "little")


def nonzero(b):
    return len(b) - b.count(0)


def printable(b):
    return "".join(chr(c) if 0x20 <= c <= 0x7E else "." for c in b)


def pages_spanned(addr, n):
    return 0 if n <= 0 else (addr + n - 1) // 4096 - addr // 4096 + 1


def save(args, name, data):
    """Writes a file into --dump-dir; returns its name or None."""
    if not args.dump_dir or data is None:
        return None
    try:  # an unwritable --dump-dir must not abort the decode that calls this
        os.makedirs(args.dump_dir, exist_ok=True)
        with open(os.path.join(args.dump_dir, name), "wb") as f:
            f.write(data)
    except OSError as e:
        print(f"  {name} NOT saved: {e!r}")
        OUT["errors"].append(f"save {name}: {e!r}")
        return None
    return name


# ---------------- APOB entry list ----------------
# ZSC ApobEntry.cs L101-198 (ApobEntryNames, from AGESA APOBCMN.h / openSIL ApobCmn.h)
APOB_GROUPS = {1: "MEM", 2: "DF", 3: "CCX", 4: "GNB", 5: "FCH", 6: "PSP", 7: "GEN", 8: "SMBIOS", 9: "FABRIC"}
APOB_TYPES = {
    (1, 1): "General errors", (1, 2): "General configuration info", (1, 5): "PMU SMB (message block)",
    (1, 7): "DIMM SMBus info", (1, 15): "NVDIMM info", (1, 16): "APCB boot info", (1, 17): "DIMM SPD data",
    (1, 18): "MBIST result info", (1, 22): "PMU training failure info", (1, 25): "System configuration info",
    (1, 27): "SoC init config", (1, 28): "RMP info (memory profile)",
    (3, 1): "CCX logical to physical map", (3, 2): "CCX EDC throttle threshold", (3, 3): "CCD logical to physical map",
    (7, 3): "Configuration info", (7, 4): "S3 replay buffer info", (7, 6): "Event log", (7, 23): "Environment flags",
    (7, 26): "Configuration data", (8, 8): "Memory SMBIOS info", (9, 9): "System memory map", (9, 19): "NPS info",
    (9, 20): "S-Link info", (9, 21): "DXIO PHY FW override info", (9, 24): "CXL info"}
HDR = 48  # APOB_TYPE_HEADER: GroupID, DataTypeID, InstanceID, TypeSize, 32-byte HMAC (openSIL ApobCmn.h L101-110)


def apob_type_name(g, ty):
    if g == 1 and 30 <= ty < 40:
        return f"S3 DDR PHY replay phase {ty - 30}"
    if g == 1 and 40 <= ty < 48:
        return f"S3 MOP array replay channel {ty - 40}"
    return APOB_TYPES.get((g, ty), "Unknown")


def apob_list(t, hsize, tsize):
    """[(offset, group, type, instance, size, encrypted)], the same walk as ZSC ApobCoreMap.cs L207-252."""
    hsize = max(16, min(hsize, len(t)))
    out = []
    for off, (g, ty, inst, size) in apob_entries(t, hsize, min(tsize, len(t))):
        enc = (g >> 16) == 0xFFFF or (ty >> 16) == 0xFFFF  # ZSC ApobEntry.cs L35
        out.append((off, g & 0xFFFF, ty & 0xFFFF, inst, size, enc))
    return out


def apob_find(entries, g, ty):
    """First readable entry of the group/type (ZSC Apob.cs FindEntry): (offset, size) or None."""
    for off, eg, ety, inst, size, enc in entries:
        if not enc and (eg, ety) == (g, ty) and size >= HDR:
            return off, size
    return None


# ---------------- DDR4 mode registers (ZSC ApobDdr4ModeRegisters.cs; JESD79-4 sec. 3.5) ----------------
MR_CL = [9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24, 23, 17, 19, 21, 25, 26, 27, 28, 0, 30, 0, 32]  # ZSC L30-34
MR_WR = [10, 12, 14, 16, 18, 20, 24, 22, 26, 28]  # ZSC L37; MR0 A13,A11:A9, RTP = WR / 2
MR_CWL = [9, 10, 11, 12, 14, 16, 18, 20]          # ZSC L40; MR2 A5:A3
RZQ_DIV = {0: None, 1: 4, 2: 2, 3: 6, 4: 1, 5: 5, 6: 3, 7: 7}  # MR1 RTT_NOM / MR5 RTT_PARK (coreboot ddr4.h L155-163)
RZQ_WR = {0: "off (dynamic ODT off)", 1: 2, 2: 1, 3: "Hi-Z", 4: 3}  # MR2 RTT_WR (coreboot ddr4.h L196-201)


def rzq(code, table):
    d = table.get(code)
    if d is None:
        return "disabled" if code == 0 else f"reserved ({code})"
    if isinstance(d, str):
        return d
    return f"RZQ/{d} ({240 // d} ohm)"


def mr_cl(mr0):
    code = ((mr0 >> 12) & 1) << 4 | ((mr0 >> 4) & 7) << 1 | ((mr0 >> 2) & 1)  # ZSC L56-64
    return MR_CL[code] if code < len(MR_CL) else 0


def mr_wr(mr0):
    code = ((mr0 >> 13) & 1) << 3 | ((mr0 >> 9) & 7)  # ZSC L67-75
    return MR_WR[code] if code < len(MR_WR) else 0


def mr_cwl(mr2):
    return MR_CWL[(mr2 >> 3) & 7]


def mr_tccdl(mr6):
    code = (mr6 >> 10) & 7  # ZSC L81-88
    return code + 4 if code <= 4 else 0


def decode_mrs(w, vddq_mv=None):
    """MR0..MR6 -> (dict, text lines). Bit positions: coreboot ddr4.c L338-493 (MRS encoders), JESD79-4 3.5."""
    mr0, mr1, mr2, mr3, mr4, mr5, mr6 = w
    wr = mr_wr(mr0)
    vref_code, rng2 = mr6 & 0x3F, bool(mr6 & 0x40)
    vref = None if vref_code > 50 else round((45.0 if rng2 else 60.0) + vref_code * 0.65, 2)  # ZSC L109-121
    d = {
        "MR": [f"{x:04X}" for x in w],
        "CL": mr_cl(mr0), "WR": wr, "RTP_autoprecharge": wr // 2 if wr else 0,
        "BL": {0: "BL8 fixed", 1: "BC4/BL8 on the fly", 2: "BC4 fixed"}.get(mr0 & 3, "reserved"),
        "burst_type": "interleaved" if mr0 & 8 else "sequential", "test_mode": (mr0 >> 7) & 1,
        "dll_reset": (mr0 >> 8) & 1,
        "dll_enable": mr1 & 1, "Ron_ohm": {0: 34, 1: 48}.get((mr1 >> 1) & 3),  # ZSC L91-102: A2:1 RZQ/7, RZQ/5
        "AL": {0: "off", 1: "CL-1", 2: "CL-2"}.get((mr1 >> 3) & 3, "reserved"),
        "write_leveling": (mr1 >> 7) & 1, "RttNom": rzq((mr1 >> 8) & 7, RZQ_DIV),
        "TDQS": (mr1 >> 11) & 1, "Qoff": (mr1 >> 12) & 1,
        "CWL": mr_cwl(mr2), "LP_ASR": {0: "manual normal", 1: "manual reduced", 2: "manual extended",
                                       3: "auto"}[(mr2 >> 6) & 3],
        "RttWr": rzq((mr2 >> 9) & 7, RZQ_WR), "write_CRC": (mr2 >> 12) & 1,
        "MPR_page": mr3 & 3, "MPR_operation": (mr3 >> 2) & 1,
        "geardown": "1/4 rate (gear-down on)" if mr3 & 8 else "1/2 rate (gear-down off)",
        "PDA": (mr3 >> 4) & 1, "temp_sensor_readout": (mr3 >> 5) & 1,
        "fine_gran_refresh": {0: "normal 1x", 1: "fixed 2x", 2: "fixed 4x", 5: "on-the-fly 2x",
                              6: "on-the-fly 4x"}.get((mr3 >> 6) & 7, "reserved"),
        "wr_cmd_lat_crc_dm": {0: 4, 1: 5, 2: 6}.get((mr3 >> 9) & 3, "reserved"),
        "MPR_read_format": {0: "serial", 1: "parallel", 2: "staggered"}.get((mr3 >> 11) & 3, "reserved"),
        "max_powerdown": (mr4 >> 1) & 1, "temp_controlled_refresh": {0: "off", 1: "off", 2: "on, normal range",
                                                                      3: "on, extended range"}[(mr4 >> 2) & 3],
        "internal_vref_monitor": (mr4 >> 4) & 1, "sPPR": (mr4 >> 5) & 1,
        "CS_to_CMD_latency": {0: "off", 1: 3, 2: 4, 3: 5, 4: 6, 5: 8}.get((mr4 >> 6) & 7, "reserved"),
        "self_refresh_abort": (mr4 >> 9) & 1, "read_preamble_training": (mr4 >> 10) & 1,
        "read_preamble_tCK": 2 if mr4 & 0x800 else 1, "write_preamble_tCK": 2 if mr4 & 0x1000 else 1,
        "hPPR": (mr4 >> 13) & 1,
        "CA_parity_latency": {0: "off", 1: 4, 2: 5, 3: 6, 4: 8}.get(mr5 & 7, "reserved"),
        "ODT_buffer_in_powerdown": "deactivated" if mr5 & 0x20 else "activated",
        "RttPark": rzq((mr5 >> 6) & 7, RZQ_DIV), "data_mask": (mr5 >> 10) & 1,
        "write_DBI": (mr5 >> 11) & 1, "read_DBI": (mr5 >> 12) & 1,
        "VrefDQ_pct": vref, "VrefDQ_code": vref_code, "VrefDQ_range": 2 if rng2 else 1,
        "VrefDQ_training_mode": (mr6 >> 7) & 1, "tCCD_L": mr_tccdl(mr6),
    }
    vabs = f" = {vref * vddq_mv / 100:.0f} mV at VDDQ {vddq_mv} mV" if vref is not None and vddq_mv else ""
    L = [
        f"MR0 {mr0:04X}: CL {d['CL'] or 'reserved'}, WR {wr or 'reserved'} / RTP {d['RTP_autoprecharge']} "
        f"(auto-precharge), {d['BL']}, {d['burst_type']}, DLL reset {d['dll_reset']}, test mode {d['test_mode']}",
        f"MR1 {mr1:04X}: DLL {'on' if d['dll_enable'] else 'OFF'}, DRAM Ron {d['Ron_ohm'] or 'reserved'} ohm "
        f"(output driver), RttNom {d['RttNom']}, AL {d['AL']}, write leveling {d['write_leveling']}, "
        f"TDQS {d['TDQS']}, Qoff {d['Qoff']}",
        f"MR2 {mr2:04X}: CWL {d['CWL']}, RttWr {d['RttWr']}, LP ASR {d['LP_ASR']}, write CRC {d['write_CRC']}",
        f"MR3 {mr3:04X}: {d['geardown']}, MPR op {d['MPR_operation']} page {d['MPR_page']}, PDA {d['PDA']}, "
        f"temp readout {d['temp_sensor_readout']}, refresh {d['fine_gran_refresh']}, CRC/DM write latency "
        f"{d['wr_cmd_lat_crc_dm']}, MPR format {d['MPR_read_format']}",
        f"MR4 {mr4:04X}: read preamble {d['read_preamble_tCK']} tCK, write preamble {d['write_preamble_tCK']} tCK, "
        f"CS-to-CMD latency {d['CS_to_CMD_latency']}, read preamble training {d['read_preamble_training']}, "
        f"self-refresh abort {d['self_refresh_abort']}, max power-down {d['max_powerdown']}, TCR "
        f"{d['temp_controlled_refresh']}, Vref monitor {d['internal_vref_monitor']}, sPPR {d['sPPR']}, hPPR {d['hPPR']}",
        f"MR5 {mr5:04X}: RttPark {d['RttPark']}, CA parity latency {d['CA_parity_latency']}, ODT input buffer in "
        f"power-down {d['ODT_buffer_in_powerdown']}, DM {d['data_mask']}, write DBI {d['write_DBI']}, "
        f"read DBI {d['read_DBI']}",
        f"MR6 {mr6:04X}: VrefDQ {'%.2f%% of VDDQ' % vref if vref is not None else 'reserved'} (range "
        f"{d['VrefDQ_range']}, code 0x{vref_code:02X}){vabs} - the value training starts from (the PMU trains it "
        f"per device), VrefDQ training mode {d['VrefDQ_training_mode']}, tCCD_L {d['tCCD_L'] or 'reserved'}",
    ]
    return d, L


def mr_words(t, o):
    return [U(t, o + 2 * i, 2) for i in range(7)]


def mr_match(w, cl, cwl, wr, tccdl):
    """ZSC ApobDdr4ModeRegisters.Find L160-181: CL, tCCD_L, DLL enable, WR and CWL within +2 of the block."""
    return (mr_cl(w[0]) == cl and mr_tccdl(w[6]) == tccdl and (w[1] & 1) == 1 and
            wr <= mr_wr(w[0]) <= wr + 2 and cwl <= mr_cwl(w[2]) <= cwl + 2)


# ---------------- DDR4 channel timing blocks (ZSC ApobChannelTimings.cs Read L342-450, Zen3 DDR4 layout) -------------
# ApobLayout.cs L630-700: Zen3Ddr4ChannelTimingLayout = memClk u16 @0, clock bytes 2..10 bytes after MEMCLK in the
# order CL CWL RCD RP RTP RAS RC WR RRDS WTRS FAW [?] [?] CCDL (L637-659), tRFC1/2/4 ns u16 at clocks+0x10/+0x14/+0x18,
# MEMCLK 600..2400, CL 8..40, P-state blocks of a channel 0x108 apart, MR0-6 searched at clocks+0x10..+0x70.
CLOCK_NAMES = ["CL", "CWL", "RCD", "RP", "RTP", "RAS", "RC", "WR", "RRDS", "WTRS", "FAW", "RRDL?", "WTRL?", "CCDL"]
Z3 = {"min_clk": 600, "max_clk": 2400, "min_cl": 8, "max_cl": 40, "gap": (2, 10), "rfc": 0x10, "stride": 0x108,
      "mr": (0x10, 0x70), "span": 36}


def ddr4_clock_bytes_ok(t, o, end):
    """ZSC ApobChannelTimings.cs IsDdr4ClockBytes L208-223."""
    if o + 14 > end:
        return False
    cl, cwl, rcd, rp, rtp, ras, rc, wr, rrds, wtrs, faw, rrdl, wtrl = t[o:o + 13]
    return (Z3["min_cl"] <= cl <= Z3["max_cl"] and 5 <= cwl <= cl and 5 <= rcd <= Z3["max_cl"] and
            5 <= rp <= Z3["max_cl"] and 2 <= rtp <= 32 and ras > rcd and rc > ras and 5 <= wr <= 64 and
            2 <= rrds <= 16 and 1 <= wtrs <= 16 and rrds <= faw <= 96 and rrdl >= rrds and wtrl >= wtrs)


def find_mrs(t, start, last, end, cl, cwl, wr, tccdl):
    return [o for o in range(start, last + 1) if o + 14 <= end and mr_match(mr_words(t, o), cl, cwl, wr, tccdl)]


def timing_blocks(t, eoff, esize, memclk_hint=None):
    data, end = eoff + HDR, min(eoff + esize, len(t))
    blocks, o = [], data
    while o + Z3["span"] <= end and len(blocks) < 16:
        clk = U(t, o, 2)
        clocks = None
        if Z3["min_clk"] <= clk <= Z3["max_clk"]:
            for gap in range(Z3["gap"][0], Z3["gap"][1] + 1):
                if ddr4_clock_bytes_ok(t, o + gap, end):
                    clocks = o + gap
                    break
        if clocks is None:
            o += 2
            continue
        v = list(t[clocks:clocks + 14])
        b = {"index": len(blocks), "entry_rel": o - eoff, "clocks_rel": clocks - eoff, "memclk": clk,
             "data_rate": 2 * clk, "clocks": dict(zip(CLOCK_NAMES, v)),
             "tRFC_ns": [U(t, clocks + Z3["rfc"] + 4 * k, 2) for k in range(3)], "notes": []}
        # Other MEMCLK candidates the same clock bytes could belong to (ZSC takes the first one)
        alt = [p - eoff for p in range(o + 2, clocks - 1, 2)
               if Z3["min_clk"] <= U(t, p, 2) <= Z3["max_clk"] and 2 <= clocks - p <= 10]
        if alt:
            b["notes"].append("other MEMCLK candidates before the clock bytes: " +
                              ", ".join(f"{U(t, eoff + p, 2)} @+0x{p:X}" for p in alt))
            if memclk_hint and abs(clk - memclk_hint) > 1 and any(abs(U(t, eoff + p, 2) - memclk_hint) <= 1 for p in alt):
                b["notes"].append(f"MEMCLK {clk} is probably a misparse: MEM 1/2 MemClkFreq {memclk_hint} is a candidate")
        cl, cwl, wr, ccdl = v[0], v[1], v[7], v[13]
        lo, hi = clocks + Z3["mr"][0], clocks + Z3["mr"][1]
        hits = find_mrs(t, lo, hi, end, cl, cwl, wr, ccdl)
        relaxed = False
        if not hits and ccdl > 8:  # MR6 encodes tCCD_L 4..8 only (JESD79-4); an OC block may hold more
            hits, relaxed = find_mrs(t, lo, hi, end, cl, cwl, wr, 8), True
        if hits:
            m = hits[0]
            b["mr_rel"], b["mr_from_clocks"] = m - eoff, m - clocks
            b["mr_words"] = mr_words(t, m)
            if relaxed:
                b["notes"].append(f"MR set matched with tCCD_L 8 (block tCCD_L {ccdl} exceeds the MR6 maximum); "
                                  "not ZenStates-Core's strict rule")
            extra = []
            for k in (1, 2, 3):  # sets right behind the first (per DIMM / rank?) - layout not known, tentative
                p = m + 14 * k
                if p + 14 <= end:
                    w = mr_words(t, p)
                    if (w[1] & 1) and mr_cl(w[0]) == cl and cwl <= mr_cwl(w[2]) <= cwl + 2:
                        extra.append(w)
                        continue
                break
            b["mr_extra_contiguous"] = extra
            others = [h for h in hits[1:] if (h - m) % 14 or (h - m) // 14 > len(extra)]
            if others:
                b["notes"].append("further MR0-6 matches in the search window at clocks+" +
                                  ", ".join(f"0x{h - clocks:X}" for h in others))
        if blocks:
            prev = blocks[-1]
            same = b["entry_rel"] - prev["entry_rel"] == Z3["stride"]  # ZSC L430-437
            b["channel"] = prev["channel"] if same else prev["channel"] + 1
            b["pstate"] = prev["pstate"] + 1 if same else 0
        else:
            b["channel"], b["pstate"] = 0, 0
        b["active"] = bool(memclk_hint) and abs(clk - memclk_hint) <= 1  # ZSC Apob.cs L646-695 (NearlyEqual)
        blocks.append(b)
        o = clocks + 14
        if (o - eoff) & 1:
            o += 1
    return blocks


# ---------------- DDR4 SPD (ZSC Hardware/DRAM/DDR4/Spd/Ddr4SpdDecoder.cs; JESD21-C Annex L; XMP 2.0) ----------------
# JEP106 subset, copied from ZSC Hardware/DRAM/ManufacturerMapping.cs (JEP106BN): key (bank, code byte with parity)
JEP106 = {(1, 0x2C): "Micron Technology", (1, 0xAD): "SK Hynix", (1, 0xCE): "Samsung", (1, 0xDA): "Winbond",
          (2, 0x32): "Mushkin Enhanced Memory", (2, 0x4F): "Transcend Information", (2, 0x7A): "Apacer Technology",
          (2, 0x94): "Smart Modular", (2, 0x98): "Kingston", (2, 0xBA): "PNY Technologies", (3, 0x9E): "Corsair",
          (3, 0xFE): "Elpida", (4, 0x0B): "Nanya Technology", (4, 0x13): "Golden Empire (GeIL)",
          (4, 0x25): "Kingmax Semiconductor", (4, 0x94): "Mushkin", (5, 0x0D): "Patriot Scientific",
          (5, 0x43): "Ramaxel Technology", (5, 0xC8): "Powerchip Semiconductor", (5, 0xCB): "A-DATA Technology",
          (5, 0xCD): "G Skill Intl", (5, 0xEF): "Team Group Inc", (6, 0x02): "Patriot Memory (PDP Systems)",
          (6, 0x51): "Qimonda", (6, 0x9B): "Crucial Technology", (7, 0x16): "Avexir Technologies",
          (7, 0x5E): "Lexar Media", (7, 0x6D): "V-Color Technology", (7, 0xD3): "Silicon Power",
          (7, 0xF1): "InnoDisk", (8, 0x46): "Gloway International (HK)", (9, 0x13): "Gloway International",
          (9, 0x98): "Essencore Limited (KLEVV)", (9, 0xF1): "Asgard", (10, 0xAD): "Neo Forza",
          (10, 0xF7): "Netac Technology", (11, 0x6B): "Acer", (11, 0x76): "Lexar Co Limited", (11, 0x91): "CXMT",
          (11, 0xC2): "Thermaltake Technology", (12, 0x92): "Kingbank Technology", (13, 0x5D): "OLOy Technology",
          (17, 0x8F): "Essencore"}


def jep106(bank_byte, code_byte):
    """ZSC ManufacturerMapping.cs Lookup L2262-2282: bank = continuation count (bit 7 parity) + 1."""
    if bank_byte == 0 and code_byte == 0:
        return "none"
    bank = (bank_byte & 0x7F) + 1
    if bin(code_byte).count("1") % 2 != 1 or code_byte & 0x7F == 0:
        return f"invalid (0x{bank_byte:02X} 0x{code_byte:02X})"
    return JEP106.get((bank, code_byte), f"bank {bank} code 0x{code_byte:02X}")


def crc16(b):
    """CRC-16, polynomial 0x1021, initial 0 (ZSC Ddr4SpdDecoder.cs L604-614)."""
    crc = 0
    for c in b:
        crc ^= c << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
    return crc


def sbyte(x):
    return x - 256 if x > 127 else x


def spd_ps(s, mtb, fine):
    """Medium timebase (125 ps) + signed fine correction (1 ps), 0 when the MTB byte is 0 (ZSC L579-591)."""
    return 0 if s[mtb] == 0 else s[mtb] * 125 + sbyte(s[fine])


def speed_from_tck(ps):
    """Data rate snapped to the 33.3 MT/s grid (ZSC L388-395)."""
    return 0.0 if ps <= 0 else round(2000000.0 / ps * 3.0 / 100.0) * 100.0 / 3.0


def clocks_of(ps, tck):
    """JEDEC rounding with the 2.5 % guard band (ZSC L408-414)."""
    return 0 if ps <= 0 or tck <= 0 else int(ps / tck + 0.974)


def cas_list(s, first):
    base = 23 if s[first + 3] & 0x80 else 7  # ZSC L417-430
    return [base + i * 8 + bit for i in range(4) for bit in range(6 if i == 3 else 8) if s[first + i] & (1 << bit)]


def bcd(x):
    return ((x >> 4) & 0xF) * 10 + (x & 0xF)


def decode_spd(spd):
    """512-byte DDR4 SPD -> dict. Offsets: ZSC Ddr4SpdDecoder.cs L19-101 (JESD21-C Annex L, XMP 2.0)."""
    s = bytes(spd[:512]).ljust(512, b"\0")
    d = {"device_type": s[2]}
    if s[2] not in (0x0C, 0x0E):
        d["valid"] = False
        return d
    d["valid"] = True
    d["spd_revision"] = f"{s[1] >> 4}.{s[1] & 0xF}"
    d["bytes_used"] = (s[0] & 0xF) * 128 if 1 <= (s[0] & 0xF) <= 4 else 0
    mt = s[3] & 0xF
    d["module_type"] = {1: "RDIMM", 2: "UDIMM", 3: "SO-DIMM", 4: "LRDIMM", 5: "Mini-RDIMM", 6: "Mini-UDIMM",
                        8: "72b-SO-RDIMM", 9: "72b-SO-UDIMM", 0xC: "16b-SO-DIMM", 0xD: "32b-SO-DIMM"}.get(mt, f"0x{mt:X}")
    die_mbit = {0: 256, 1: 512, 2: 1024, 3: 2048, 4: 4096, 5: 8192, 6: 16384, 7: 32768, 8: 12288,
                9: 24576}.get(s[4] & 0xF, 0)  # ZSC L240-256
    d["die_density_Gb"] = die_mbit / 1024
    d["bank_groups"] = {0: 1, 1: 2}.get((s[4] >> 6) & 3, 4)
    d["banks_per_group"] = 4 if ((s[4] >> 4) & 3) == 0 else 8
    d["rows"], d["columns"] = 12 + ((s[5] >> 3) & 7), 9 + (s[5] & 7)
    d["package"] = ("non-monolithic" if s[6] & 0x80 else "monolithic") + f", {((s[6] >> 4) & 7) + 1} die"
    is3ds = (s[6] & 3) == 2
    width = 4 << (s[12] & 7) if (s[12] & 7) <= 3 else 0
    ranks = ((s[12] >> 3) & 7) + 1
    bus = 8 << (s[13] & 7) if (s[13] & 7) <= 3 else 0
    d["device_width"], d["ranks"], d["bus_width"] = width, ranks, bus
    d["ecc"] = ((s[13] >> 3) & 3) == 1
    d["asymmetric"] = bool(s[12] & 0x40)
    if die_mbit and width and bus:
        d["capacity_MB"] = die_mbit // 8 * (bus // width) * (((s[6] >> 4) & 7) + 1 if is3ds else 1) * ranks
    d["vdd"] = "1.2 V" + (" operable" if s[11] & 1 else "") + (" endurant" if s[11] & 2 else "")
    d["thermal_sensor"] = bool(s[14] & 0x80)
    tck = spd_ps(s, 18, 125)
    j = {"tCKmin_ps": tck, "tAA_ps": spd_ps(s, 24, 123), "tRCD_ps": spd_ps(s, 25, 122), "tRP_ps": spd_ps(s, 26, 121),
         "tRAS_ps": ((s[27] & 0xF) << 8 | s[28]) * 125,
         "tRC_ps": (((s[27] >> 4) & 0xF) << 8 | s[29]) * 125 + sbyte(s[120]),
         "tRFC1_ns": U(s, 30, 2) * 125 / 1000, "tRFC2_ns": U(s, 32, 2) * 125 / 1000,
         "tRFC4_ns": U(s, 34, 2) * 125 / 1000, "tFAW_ps": ((s[36] & 0xF) << 8 | s[37]) * 125,
         "tRRD_S_ps": spd_ps(s, 38, 119), "tRRD_L_ps": spd_ps(s, 39, 118), "tCCD_L_ps": spd_ps(s, 40, 117),
         "tWR_ps": ((s[41] & 0xF) << 8 | s[42]) * 125, "tWTR_S_ps": ((s[43] & 0xF) << 8 | s[44]) * 125,
         "tWTR_L_ps": (((s[43] >> 4) & 0xF) << 8 | s[45]) * 125, "CL_supported": cas_list(s, 20)}
    mts = speed_from_tck(tck)
    j["speed_MTs"] = int(mts)
    if mts:
        ck = 2000000.0 / mts
        j["timings"] = [clocks_of(j[k], ck) for k in ("tAA_ps", "tRCD_ps", "tRP_ps", "tRAS_ps", "tRC_ps")]
    d["jedec"] = j
    d["crc_base_ok"] = crc16(s[0:126]) == U(s, 126, 2)
    d["crc_module_ok"] = crc16(s[128:254]) == U(s, 254, 2)
    # JEDEC raw card letters skip I O Q S X Z (i2c-tools 24553db eeprom/decode-dimms L1561-1586, "ABCDEFGHJKLMNPRTUVWY");
    # two-letter cards (code >= 20) and the byte 130 bit 7 extension are printed as codes, not guessed
    card = s[130] & 0x1F
    rev = (s[130] >> 5) & 3
    rev += (s[128] >> 5) & 7 if rev == 3 else 0  # ZSC Ddr4SpdDecoder.cs L443-445
    d["raw_card"] = ("ZZ" if card == 31 else f"extended code {card}" if s[130] & 0x80 else
                     "ABCDEFGHJKLMNPRTUVWY"[card] if card < 20 else f"code {card}") + f" rev {rev}"
    d["address_mirrored"] = bool(s[131] & 1) if mt in (2, 3, 6, 9) else None
    # manufacturing (ZSC L466-498)
    d["module_mfr"] = jep106(s[320], s[321])
    d["module_mfr_id"] = f"{s[320]:02X}{s[321]:02X}"
    d["mfg_location"] = s[322]
    d["mfg_date"] = "n/a" if s[323] == 0 and s[324] == 0 else f"20{bcd(s[323]):02d} week {bcd(s[324]):02d}"
    d["serial"] = s[325:329].hex().upper()
    d["part_number"] = "".join(chr(c) for c in s[329:349] if 0x20 <= c <= 0x7E).strip()
    d["module_revision"] = s[349]
    d["dram_mfr"] = jep106(s[350], s[351])
    d["dram_mfr_id"] = f"{s[350]:02X}{s[351]:02X}"
    d["dram_stepping"] = s[352]
    # Intel XMP 2.0 (ZSC L500-576): 0x0C 0x4A, enables, revision, profiles of 47 bytes at 393 and 440
    d["xmp"] = None
    if s[384] == 0x0C and s[385] == 0x4A:
        x = {"revision": f"{s[387] >> 4}.{s[387] & 0xF}", "profiles": []}
        for p in range(2):
            if not s[386] & (1 << p):
                continue
            o = 393 + 47 * p
            ptck = spd_ps(s, o + 3, o + 38)
            if ptck <= 0:
                continue
            pm = speed_from_tck(ptck)
            ck = 2000000.0 / pm
            pr = {"profile": p + 1, "dimms_per_channel": ((s[386] >> (2 + 2 * p)) & 3) + 1,
                  "vdd_mV": ((s[o] >> 7) & 1) * 1000 + (s[o] & 0x7F) * 10, "tCK_ps": ptck, "speed_MTs": int(pm),
                  "tAA_ps": spd_ps(s, o + 8, o + 37), "tRCD_ps": spd_ps(s, o + 9, o + 36),
                  "tRP_ps": spd_ps(s, o + 10, o + 35), "tRAS_ps": ((s[o + 11] & 0xF) << 8 | s[o + 12]) * 125,
                  "tRC_ps": (((s[o + 11] >> 4) & 0xF) << 8 | s[o + 13]) * 125 + sbyte(s[o + 34]),
                  "tRFC1_ps": U(s, o + 14, 2) * 125, "tRFC2_ps": U(s, o + 16, 2) * 125,
                  "tRFC4_ps": U(s, o + 18, 2) * 125, "tFAW_ps": ((s[o + 20] & 0xF) << 8 | s[o + 21]) * 125,
                  "tRRD_S_ps": spd_ps(s, o + 22, o + 33), "tRRD_L_ps": spd_ps(s, o + 23, o + 32),
                  "tCCD_L_ps": spd_ps(s, o + 24, o + 31), "CL_supported": cas_list(s, o + 4)}
            pr["clocks"] = {k[:-3]: clocks_of(pr[k], ck) for k in
                            ("tAA_ps", "tRCD_ps", "tRP_ps", "tRAS_ps", "tRC_ps", "tRFC1_ps", "tRFC2_ps", "tRFC4_ps",
                             "tFAW_ps", "tRRD_S_ps", "tRRD_L_ps", "tCCD_L_ps")}
            x["profiles"].append(pr)
        d["xmp"] = x
    return d


def spd_lines(d):
    if not d.get("valid"):
        return [f"SPD key byte 0x{d.get('device_type', 0):02X}: not DDR4, not decoded"]
    j = d["jedec"]
    t = "-".join(str(x) for x in j.get("timings", [])) or "?"
    cls = j["CL_supported"]
    L = [f"module \"{d['part_number']}\" by {d['module_mfr']} (0x{d['module_mfr_id']}), DRAM {d['dram_mfr']} "
         f"(0x{d['dram_mfr_id']}) stepping 0x{d['dram_stepping']:02X}, made {d['mfg_date']}, serial {d['serial']}, "
         f"module rev 0x{d['module_revision']:02X}",
         f"DDR4 {d['module_type']}, {d.get('capacity_MB', '?')} MB, {d['ranks']} rank(s) x{d['device_width']}, "
         f"{d['die_density_Gb']:g} Gb dies ({d['bank_groups']} BG x {d['banks_per_group']} banks, "
         f"{d['rows']} row / {d['columns']} col bits), {d['package']}, {d['vdd']}, SPD rev {d['spd_revision']}, "
         f"raw card {d['raw_card']}, thermal sensor {'yes' if d['thermal_sensor'] else 'no'}, "
         f"CRC base {'ok' if d['crc_base_ok'] else 'BAD'} / module {'ok' if d['crc_module_ok'] else 'BAD'}",
         f"JEDEC: DDR4-{j['speed_MTs']} {t} (CL-RCD-RP-RAS-RC), tRFC1/2/4 {j['tRFC1_ns']:g}/{j['tRFC2_ns']:g}/"
         f"{j['tRFC4_ns']:g} ns, CL {cls[0] if cls else '?'}-{cls[-1] if cls else '?'}"]
    x = d.get("xmp")
    if x is None:
        L.append("XMP: none")
    else:
        if not x["profiles"]:
            L.append(f"XMP {x['revision']}: header present, no enabled profile")
        for p in x["profiles"]:
            c = p["clocks"]
            L.append(f"XMP {x['revision']} profile {p['profile']}: DDR4-{p['speed_MTs']} "
                     f"{c['tAA']}-{c['tRCD']}-{c['tRP']}-{c['tRAS']}-{c['tRC']} (CL-RCD-RP-RAS-RC), tRFC1 {c['tRFC1']} "
                     f"({p['tRFC1_ps'] / 1000:g} ns), {p['vdd_mV'] / 1000:.2f} V, RRDS/RRDL/FAW/CCDL "
                     f"{c['tRRD_S']}/{c['tRRD_L']}/{c['tFAW']}/{c['tCCD_L']}, {p['dimms_per_channel']} DPC")
    return L


# ---------------- APOB_SPD_STRUCT records (openSIL ApobCmn.h L115-133; ZSC ApobDimmSpd.cs L55-118) ----------------
SPD_REC = 532  # 8 bytes, Address u32, SerialNumber u32, flags u32, Data[512]; the AODT DSPD records are 0x214 too


def spd_records(buf, rec0, nrec, limit):
    """Present records -> list of dicts; None when the bytes do not look like this layout (ZSC L77-112)."""
    out = []
    for i in range(nrec):
        r = rec0 + i * SPD_REC
        if r + SPD_REC > min(limit, len(buf)):
            break
        present = buf[r + 4]
        if present > 1:
            return None
        if present == 0:
            continue
        spd = bytes(buf[r + 20:r + SPD_REC])
        if not (spd[2] == 0x0C or 0x10 <= spd[2] <= 0x15):  # ZSC IsKnownDeviceType L115-118
            return None
        out.append({"record": i, "socket": buf[r], "channel": buf[r + 1], "dimm": buf[r + 2], "page": buf[r + 3],
                    "smbus": U(buf, r + 8, 4), "record_serial": U(buf, r + 12, 4), "flags": U(buf, r + 16, 4),
                    "slot": f"{chr(65 + buf[r + 1]) if buf[r + 1] < 26 else '?'}{buf[r + 2] + 1}", "spd": spd})
    return out


def print_spd_records(args, recs, prefix, other=None):
    """Prints and saves the records; 'other' = {slot: spd bytes} of another source to compare against."""
    first = {}
    res = []
    for r in recs:
        name = save(args, f"{prefix}_spd_{r['slot']}.bin", r["spd"])
        d = decode_spd(r["spd"])
        print(f"  {r['slot']} (socket {r['socket']}, channel {r['channel']}, DIMM {r['dimm']}, SMBus 0x{r['smbus']:X}, "
              f"record serial {r['record_serial']:08X}, flags 0x{r['flags']:X})" + (f": saved {name}" if name else ""))
        if other is not None:
            print("    " + ("same 512 bytes as the APOB copy" if other.get(r["slot"]) == r["spd"] else
                            "DIFFERS from the APOB copy" if r["slot"] in other else "no APOB copy of this slot"))
        # bytes 323-324 = manufacturing date, 325-328 = serial (JESD21-C Annex L); 320-322 (module maker, location) must match
        twin = next((k for k, v in first.items() if v[:323] == r["spd"][:323] and v[329:] == r["spd"][329:]), None)
        if twin:
            print(f"    same SPD as {twin} except manufacturing date/serial: made {d.get('mfg_date')}, "
                  f"serial {d.get('serial')}")
        else:
            for line in spd_lines(d):
                print("    " + line)
        first.setdefault(r["slot"], r["spd"])
        res.append({k: v for k, v in r.items() if k != "spd"} | {"decoded": d, "file": name})
    return res


# ---------------- the other APOB entries ----------------
EVENT_CLASS = ["Success", "Unsupported", "Bounds check", "Sync more data", "Sync slave assert", "Alert", "Warning",
               "Error", "Critical", "Fatal"]  # AGESA_STATUS, ZSC ApobEventLog.cs L37-48
HOLE_TYPES = ["UMA", "MMIO", "Privileged DRAM", "Reserved 1TB remap", "Reserved S-Link", "Reserved S-Link alignment",
              "Reserved DRTM", "Reserved CVIP", "Reserved SMU features", "Reserved fTPM", "Reserved MPIO C20",
              "Reserved NBIF", "Reserved CXL", "Reserved CXL alignment", "Reserved CPU TMR",
              "Reserved RAS EINJ"]  # openSIL MEMORY_HOLE_TYPES via ZSC ApobMemoryMap.cs L36-54
DMI_TYPES = {0x18: "DDR3", 0x1A: "DDR4", 0x1D: "LPDDR3", 0x1E: "LPDDR4", 0x22: "DDR5", 0x23: "LPDDR5"}


def bcd_date(raw, now_year):
    """ZSC ApobBootInfo.cs L85-121: binary on Zen4/5, BCD on Zen1-3 (day, month, year, century bytes)."""
    def to(bcdmode):
        p = []
        for i in range(4):
            x = (raw >> (8 * i)) & 0xFF
            if bcdmode:
                if (x & 0xF) > 9 or (x >> 4) > 9:
                    return None
                x = (x >> 4) * 10 + (x & 0xF)
            p.append(x)
        day, month, year = p[0], p[1], (p[3] or 20) * 100 + p[2]
        if not (2000 <= year <= 2200 and 1 <= month <= 12 and 1 <= day <= 31):
            return None
        try:
            time.strptime(f"{year}-{month}-{day}", "%Y-%m-%d")
        except ValueError:
            return None
        return f"{year:04d}-{month:02d}-{day:02d}"
    b = to(False)
    if b and int(b[:4]) <= now_year + 1:
        return b
    return to(True) or b


def dec_general(t, off, size):
    """MEM 1/2 fields the section above does not print (ZSC ApobMemGeneralConfig.cs L70-137)."""
    data, n = off + HDR, size - HDR
    if n == 496 - HDR:
        nch, arr = 8, data + 0xA4
    elif n == 256 - HDR:
        nch, arr = 4, data + 0x98
    else:
        return None
    r = {"MemClkFreq": U(t, data + 4, 2), "DdrMaxRate": U(t, data + 6, 2),
         "ecc_enable": [t[data + 8 + i] != 0 for i in range(nch)],
         "channel_interleave": t[data + 24] != 0 if nch == 8 else None,
         "interleave_mode": U(t, arr + 11 * nch, 4), "interleave_capability": U(t, arr + 11 * nch + 4, 4),
         "interleave_size": U(t, arr + 11 * nch + 8, 4)}
    return r


def dec_boot_info(t, off, size, now_year):
    """ZSC ApobBootInfo.cs Decode L129-181; openSIL APOB_APCB_BOOT_INFO_STRUCT L296-310."""
    data, n = off + HDR, size - HDR
    if n < 4 + 12 + 20 or (n - 24) % 12:
        return None
    slots = (n - 24) // 12
    dimms = []
    for i in range(slots):
        o = data + 4 + 12 * i
        dimms.append({"slot": i, "dram_id": U(t, o, 2), "module_id": U(t, o + 2, 2), "serial": U(t, o + 4, 4),
                      "present": t[o + 8]})
    valid = all(x["present"] <= 1 and (not x["present"] or (x["dram_id"] and x["module_id"])) for x in dimms)
    tr = data + 4 + 12 * slots
    raw = U(t, tr + 4, 4)
    return {"ApcbActiveInstance": U(t, data, 4), "dimm_records_valid": valid, "dimms": dimms if valid else [],
            "DimmConfigurationUpdated": t[tr], "ApcbRecoveryFlag": t[tr + 1], "ActionOnBistFailure": t[tr + 2],
            "WorkloadProfile": t[tr + 3], "LastPmuTrainTime_raw": raw, "last_training": bcd_date(raw, now_year),
            "unknown": [U(t, tr + 8 + 4 * k, 4) for k in range(3)]}


def dec_dmi(t, off, size):
    """ZSC ApobDmiInfo.cs Decode L105-163 (layout verified there on Granite Ridge only)."""
    data, n = off + HDR, size - HDR
    if n < 4:
        return None
    phys, logi = t[data + 1], t[data + 2]
    if 4 + phys * 8 + logi * 32 > n:
        return None
    r = {"memory_type": DMI_TYPES.get(t[data] & 0x7F, f"0x{t[data] & 0x7F:02X}"), "ecc_capable": bool(t[data] & 0x80),
         "physical": [], "logical": []}
    o = data + 4
    for _ in range(phys):
        bits = t[o]
        r["physical"].append({"slot": f"{chr(65 + ((bits >> 2) & 7))}{((bits >> 5) & 3) + 1}", "socket": bits & 3,
                              "present": bool(bits & 0x80), "spd_address": t[o + 1], "handle": U(t, o + 2, 2),
                              "configured_speed": U(t, o + 4, 2), "configured_voltage_mV": U(t, o + 6, 2)})
        o += 8
    for _ in range(logi):
        bits = t[o]
        s_kb, e_kb = U(t, o + 4, 4), U(t, o + 8, 4)
        xs, xe = U(t, o + 16, 8), U(t, o + 24, 8)
        if 0xFFFFFFFF in (s_kb, e_kb):
            mb = (xe - xs + 1) >> 20 if xe > xs else 0
        else:
            mb = (e_kb - s_kb + 1) >> 10 if e_kb > s_kb else 0
        r["logical"].append({"slot": f"{chr(65 + ((bits >> 2) & 7))}{((bits >> 5) & 3) + 1}", "socket": bits & 3,
                             "present": bool(bits & 0x80), "interleaved": bool(t[o + 1] & 1), "handle": U(t, o + 2, 2),
                             "start_kb": s_kb, "end_kb": e_kb, "size_MB": mb})
        o += 32
    return r


def dec_events(t, off, size):
    """ZSC ApobEventLog.cs Decode L61-90; openSIL APOB_ERROR_LOG / EVENT_LOG_STRUCT L142-158 (Count u16, pad, 16-byte events)."""
    data, n = off + HDR, size - HDR
    if n < 4:
        return None
    count = U(t, data, 2)
    ev = []
    for i in range(min(count, (n - 4) // 16)):
        o = data + 4 + 16 * i
        c = U(t, o, 4)
        ev.append({"class": EVENT_CLASS[c] if c < len(EVENT_CLASS) else f"class {c}", "info": U(t, o + 4, 4),
                   "data_a": U(t, o + 8, 4), "data_b": U(t, o + 12, 4)})
    return {"count": count, "events": ev}


def dec_memmap(t, off, size):
    """ZSC ApobMemoryMap.cs Decode L77-106 (layout verified there on Granite Ridge only)."""
    data, n = off + HDR, size - HDR
    if n < 16:
        return None
    holes = U(t, data + 8, 4)
    if holes > (n - 16) // 24:
        return None
    r = {"top_of_memory": U(t, data, 8), "holes": []}
    for i in range(holes):
        o = data + 16 + 24 * i
        ty = U(t, o + 16, 4)
        r["holes"].append({"base": U(t, o, 8), "size": U(t, o + 8, 8),
                           "type": HOLE_TYPES[ty] if ty < len(HOLE_TYPES) else f"unknown ({ty})"})
    return r


def apob_details(args):
    st = STATE.get("apob")
    if not st:
        print("== APOB details: no APOB table read above, skipped")
        return
    t, ver, tsize, hsize = st["t"], st["version"], st["tsize"], st["hsize"]
    R = OUT.setdefault("apob", {"address": STATE.get("apob_addr"), "version": ver, "table_size": tsize,
                                "header_size": hsize, "bytes": len(t)})
    print(f"== APOB entries (ZenStates-Core 869b7e8 ApobEntry.cs naming): version 0x{ver:X}, TableSize {tsize}, "
          f"HeaderSize 0x{hsize:X}")
    entries = []
    with Guard("APOB entry list"):
        entries = apob_list(t, hsize, tsize)
        R["entries"] = []
        for i, (off, g, ty, inst, size, enc) in enumerate(entries):
            nz = nonzero(t[off + HDR:off + size]) if size > HDR else 0
            name = f"{APOB_GROUPS.get(g, f'group {g}')} {apob_type_name(g, ty)}"
            # the non-zero count goes to the JSON only: trained data changes it from boot to boot, and
            # ram_compare.py diffs this text
            print(f"  [{i:2}] +0x{off:05X} {g:>2}/{ty:<3} inst 0x{inst:X} {size:6} bytes  {name}"
                  + (" (encrypted)" if enc else "") + (" (data all zero)" if size > HDR and nz == 0 else ""))
            R["entries"].append({"offset": off, "group": g, "type": ty, "instance": inst, "size": size,
                                 "encrypted": enc, "nonzero": nz, "name": name})
    gen = dmi = None
    e = apob_find(entries, 1, 2)
    if e:
        with Guard("APOB MEM 1/2 extra fields"):
            gen = dec_general(t, *e)
            if gen:
                print(f"  MEM 1/2 more: MemClkFreq {gen['MemClkFreq']} MHz, DdrMaxRate {gen['DdrMaxRate']}, ECC enable "
                      f"per channel slot {''.join('1' if x else '0' for x in gen['ecc_enable'])}, channel interleave "
                      f"{gen['channel_interleave']}, "
                      f"interleave mode 0x{gen['interleave_mode']:X} capability 0x{gen['interleave_capability']:X} "
                      f"size 0x{gen['interleave_size']:X}")
                R["general_config"] = gen
    e = apob_find(entries, 8, 8)
    if e:
        with Guard("APOB SMBIOS 8/8"):
            dmi = dec_dmi(t, *e)
    # ---- timing blocks + mode registers
    print("== APOB DDR4 channel timing blocks + mode registers MR0-MR6 (GEN 7/3, ZenStates-Core Zen3 DDR4 layout)")
    e = apob_find(entries, 7, 3)
    if not e:
        print("  no readable GEN 7/3 entry")
    else:
        with Guard("APOB GEN 7/3 timing blocks"):
            hint = gen["MemClkFreq"] if gen else None
            blocks = timing_blocks(t, e[0], e[1], hint)
            if dmi:  # ZSC Apob.cs MapChannelsToDimms L695-723: number the channels after the populated ones
                pop = sorted({ord(p["slot"][0]) - 65 for p in dmi["physical"] if p["present"]})
                if blocks and len(pop) == max(b["channel"] for b in blocks) + 1:
                    for b in blocks:
                        b["channel"] = pop[b["channel"]]
            vddq = None
            aodst = STATE.get("aod")
            if aodst:
                v = get(aodst["buf"], aodst["fields"], "VDIO")
                vddq = v if v and 1000 <= v <= 2000 else None
            print(f"  entry +0x{e[0]:X} ({e[1]} bytes): {len(blocks)} block(s); active = MEMCLK of MEM 1/2 "
                  f"({hint if hint else 'unknown'} MHz)")
            seen = []
            for b in blocks:
                c = b["clocks"]
                print(f"  Blk{b['index']} channel {chr(65 + b['channel'])} P{b['pstate']}: MEMCLK {b['memclk']} MHz "
                      f"(DDR4-{b['data_rate']}){', active' if b['active'] else ''}, block +0x{b['entry_rel']:X}, "
                      f"clock bytes +0x{b['clocks_rel']:X}")
                print("    ABL clocks: " + " ".join(f"{k} {x}" for k, x in c.items()) +
                      f"; tRFC1/2/4 {b['tRFC_ns'][0]}/{b['tRFC_ns'][1]}/{b['tRFC_ns'][2]} ns  (? = placement tentative)")
                for n in b["notes"]:
                    print("    note: " + n)
                if "mr_words" not in b:
                    print("    MR0-MR6: not found (no set in clocks+0x10..0x70 matches CL/CWL/WR/tCCD_L)")
                    continue
                key = tuple(b["mr_words"])
                d, lines = decode_mrs(b["mr_words"], vddq)
                b["mr"] = d
                twin = next((s for s, k in seen if k == key), None)
                print(f"    MR0-MR6 at clocks+0x{b['mr_from_clocks']:X} (block +0x{b['mr_rel']:X}): "
                      + " ".join(f"{w:04X}" for w in key) + (f"  = same as Blk{twin}" if twin is not None else ""))
                if twin is None:
                    for line in lines:
                        print("      " + line)
                    seen.append((b["index"], key))
                for k, w in enumerate(b.get("mr_extra_contiguous", []), 1):
                    print(f"    tentative further set {k} right after it (per DIMM/rank? layout unconfirmed): "
                          + " ".join(f"{x:04X}" for x in w) + f" -> RttNom {rzq((w[1] >> 8) & 7, RZQ_DIV)}, "
                          f"RttWr {rzq((w[2] >> 9) & 7, RZQ_WR)}, RttPark {rzq((w[5] >> 6) & 7, RZQ_DIV)}, "
                          f"Ron {({0: 34, 1: 48}.get((w[1] >> 1) & 3, '?'))} ohm, VrefDQ code 0x{w[6] & 0x3F:02X}")
            R["timing_blocks"] = blocks
    # ---- SPD
    print("== APOB DIMM SPD data (MEM 1/17, APOB_SPD_STRUCT records; DDR4 SPD + XMP 2.0 decode after ZenStates-Core)")
    STATE["apob_spd"] = {}
    e = apob_find(entries, 1, 17)
    if not e:
        print("  no readable MEM 1/17 entry")
    else:
        with Guard("APOB MEM 1/17 SPD"):
            data, n = e[0] + HDR, e[1] - HDR
            if n < 4:
                raise ValueError(f"entry data too short ({n} bytes)")
            mdpc, mcps = t[data], t[data + 1]
            recs = spd_records(t, data + 4, mdpc * mcps, data + n)
            if recs is None:
                print(f"  records do not match the 532-byte DDR4 layout (MaxDimmsPerChannel {mdpc}, "
                      f"MaxChannelsPerSocket {mcps})")
            else:
                print(f"  {len(recs)} present of {mdpc * mcps} records (MaxDimmsPerChannel {mdpc}, MaxChannelsPerSocket {mcps})")
                R["spd"] = print_spd_records(args, recs, "apob")
                STATE["apob_spd"] = {r["slot"]: r["spd"] for r in recs}
    # ---- SMBIOS info
    print("== APOB SMBIOS memory info (SMBIOS 8/8; ZenStates-Core verified the layout on Zen5 only)")
    if not dmi:
        print("  no readable/decodable SMBIOS 8/8 entry")
    else:
        print(f"  memory type {dmi['memory_type']}, ECC capable {dmi['ecc_capable']}, {len(dmi['physical'])} physical / "
              f"{len(dmi['logical'])} logical records")
        for p in dmi["physical"]:
            if p["present"]:
                print(f"  {p['slot']} (socket {p['socket']}): SPD address 0x{p['spd_address']:02X}, handle "
                      f"0x{p['handle']:04X}, configured speed {p['configured_speed']}, voltage {p['configured_voltage_mV']} mV")
        for p in dmi["logical"]:
            if p["present"]:
                print(f"  logical {p['slot']}: 0x{p['start_kb']:X}-0x{p['end_kb']:X} KB ({p['size_MB']} MB)"
                      + (", interleaved" if p["interleaved"] else ""))
        R["smbios"] = dmi
    # ---- boot info
    print("== APOB APCB boot info (MEM 1/16)")
    e = apob_find(entries, 1, 16)
    if not e:
        print("  no readable MEM 1/16 entry")
    else:
        with Guard("APOB MEM 1/16 boot info"):
            bi = dec_boot_info(t, e[0], e[1], time.localtime().tm_year)
            if not bi:
                print(f"  size {e[1]} does not fit the layout")
            else:
                print(f"  ApcbActiveInstance {bi['ApcbActiveInstance']}, last full PMU training {bi['last_training'] or 'n/a'} "
                      f"(raw 0x{bi['LastPmuTrainTime_raw']:08X}), DimmConfigurationUpdated {bi['DimmConfigurationUpdated']}, "
                      f"ApcbRecoveryFlag {bi['ApcbRecoveryFlag']}, ActionOnBistFailure {bi['ActionOnBistFailure']}, "
                      f"WorkloadProfile {bi['WorkloadProfile']}, unknown " + " ".join(f"0x{x:08X}" for x in bi["unknown"]))
                if bi["dimm_records_valid"]:
                    for x in bi["dimms"]:
                        if x["present"]:
                            print(f"  slot {x['slot']} ({chr(65 + x['slot'] // 2)}{x['slot'] % 2 + 1}): module "
                                  f"{jep106(x['module_id'] & 0xFF, x['module_id'] >> 8)}, DRAM "
                                  f"{jep106(x['dram_id'] & 0xFF, x['dram_id'] >> 8)}, serial {x['serial']:08X}")
                else:
                    print("  slot records are not DIMM fingerprints (expected on Zen2/Zen3 per ZenStates-Core)")
                R["boot_info"] = bi
    # ---- training status and event log
    print("== APOB training status and ABL event log")
    for (g, ty), what in (((1, 22), "PMU training failure info (MEM 1/22)"), ((1, 18), "MBIST result info (MEM 1/18)")):
        e = apob_find(entries, g, ty)
        nz = nonzero(t[e[0] + HDR:e[0] + e[1]]) if e else None
        print(f"  {what}: " + ("not present" if e is None else "no data (all zero)" if nz == 0 else f"{nz} non-zero bytes"))
        R[f"nonzero_{g}_{ty}"] = nz
    e = apob_find(entries, 7, 6)
    if not e:
        print("  event log (GEN 7/6): not present")
    else:
        with Guard("APOB GEN 7/6 event log"):
            ev = dec_events(t, *e)
            if ev is None:
                print("  event log entry too short")
            else:
                print(f"  event log (GEN 7/6): {ev['count']} event(s)")
                for i, x in enumerate(ev["events"]):
                    print(f"   [{i:2}] {x['class']}: 0x{x['info']:08X} (0x{x['data_a']:08X}, 0x{x['data_b']:08X})")
                R["event_log"] = ev
    # ---- memory map
    e = apob_find(entries, 9, 9)
    if e:
        with Guard("APOB FABRIC 9/9 memory map"):
            mm = dec_memmap(t, *e)
            if mm:
                print(f"== APOB system memory map (FABRIC 9/9): top of memory 0x{mm['top_of_memory']:X} "
                      f"({mm['top_of_memory'] >> 20} MB), {len(mm['holes'])} hole(s)")
                for h in mm["holes"]:
                    print(f"  0x{h['base']:012X} size 0x{h['size']:X} ({h['size'] >> 10} KB) {h['type']}")
                R["memory_map"] = mm


# ---------------- AODT SPD copies ----------------
def aod_spd(args):
    st = STATE.get("aod")
    print("== AOD SPD copies (AODT field DSPD, 16 x 0x214-byte records returned by WMI method AM07; "
          "read as APOB_SPD_STRUCT, tentative)")
    if not st:
        print("  no AOD buffer read above, skipped")
        return
    buf, fields = st["buf"], st["fields"]
    if "DSPD" in fields and fields["DSPD"][0] % 8 == 0:
        start, size = fields["DSPD"][0] // 8, fields["DSPD"][1] // 8
    else:
        start, size = 0xCC, 16 * SPD_REC  # B550 AORUS PRO V2 AOD SSDT layout (research/aod.md)
    with Guard("AOD DSPD"):
        if start + SPD_REC > len(buf):
            print(f"  buffer too short ({len(buf)} bytes) for DSPD at 0x{start:X}")
            return
        recs = spd_records(buf, start, size // SPD_REC, start + size)
        if recs is None:
            print(f"  DSPD at 0x{start:X} does not hold APOB_SPD_STRUCT records (first bytes "
                  f"{bytes(buf[start:start + 24]).hex()})")
        elif not recs:
            print(f"  DSPD at 0x{start:X}: no present records ({nonzero(buf[start:start + size])} non-zero bytes)")
        else:
            print(f"  DSPD at 0x{start:X}: {len(recs)} present record(s)")
            OUT["aod_spd"] = print_spd_records(args, recs, "aod", STATE.get("apob_spd"))


# ---------------- E820 map, Gigabyte Easy* buffers, ACPI NVS dumps ----------------
NVS = "ACPI Non-volatile Storage"


def memmap_ranges(args):
    out = []
    try:
        names = os.listdir(args.memmap)
    except OSError:
        return out
    for e in names:
        try:
            d = os.path.join(args.memmap, e)
            s, t = (int(open(os.path.join(d, k)).read(), 16) for k in ("start", "end"))
            out.append((s, t, open(os.path.join(d, "type")).read().strip()))
        except (OSError, ValueError):
            continue
    return sorted(out)


def range_of(ranges, addr):
    return next((r for r in ranges if r[0] <= addr <= r[1]), None)


def readable_type(rng):
    """Only DRAM-backed firmware ranges are read: NVS / ACPI tables, or 'Reserved' below 0xC0000000 (the DRAM
    below the 32-bit MMIO hole on the B550 AORUS PRO; Reserved ranges above it include MMIO such as 0xFED80000)."""
    return rng is not None and (rng[2] in (NVS, "ACPI Tables") or (rng[2] == "Reserved" and rng[1] < 0xC0000000))


def read_span(addr, n):
    """Page-wise read with one fd; refused pages are zero-filled. -> (bytes, refused pages, first error)."""
    out, refused, err = bytearray(), 0, None
    fd = os.open(DEVMEM, os.O_RDONLY | os.O_SYNC)
    try:
        while len(out) < n:
            a = addr + len(out)
            step = min(n - len(out), 4096 - (a & 0xFFF))
            try:
                chunk = os.pread(fd, step, a)
            except OSError as e:
                chunk, refused, err = b"", refused + 1, err or e
            out += chunk.ljust(step, b"\0")
    finally:
        os.close(fd)
    return bytes(out), refused, err


EFI_MEMTYPE = ["EfiReservedMemoryType", "EfiLoaderCode", "EfiLoaderData", "EfiBootServicesCode",
               "EfiBootServicesData", "EfiRuntimeServicesCode", "EfiRuntimeServicesData", "EfiConventionalMemory",
               "EfiUnusableMemory", "EfiACPIReclaimMemory", "EfiACPIMemoryNVS", "EfiMemoryMappedIO",
               "EfiMemoryMappedIOPortSpace", "EfiPalCode", "EfiPersistentMemory", "EfiUnacceptedMemoryType"]  # UEFI 2.10 7.2


def pool_size(addr, rng):
    """EDK2 DXE pool header in front of an AllocatePool() buffer (Pool.c L21-41, L407-409, L520-535):
    {u32 'phd0'|'phd1', u32 reserved, u32 EFI_MEMORY_TYPE, pad, u64 Size} = 0x18 bytes, then the data, then the
    tail {u32 'ptal', u32 reserved, u64 Size}; Size = ALIGN8(request) + 0x28. -> (data length or None, text)."""
    head = addr - 0x18
    if head < rng[0]:
        return None, "no room for a pool header"
    h, ref, err = read_span(head, 0x18)
    if ref:
        return None, f"pool header unreadable ({err})"
    sig, typ, size = h[:4], U(h, 8, 4), U(h, 0x10, 8)
    if sig not in (b"phd0", b"phd1") or typ >= len(EFI_MEMTYPE) or not 0x28 <= size <= 0x1000000 or size % 8:
        return None, f"no EDK2 pool header at 0x{head:X} (bytes {h[:8].hex()}, type {typ}, size 0x{size:X})"
    txt = f"pool header '{sig.decode()}' at 0x{head:X}: {EFI_MEMTYPE[typ]}, Size 0x{size:X}"
    tail = head + size - 0x10
    if tail + 0x10 <= rng[1] + 1:
        tb, ref, _ = read_span(tail, 0x10)
        if not ref and tb[:4] == b"ptal" and U(tb, 8, 8) == size:
            return size - 0x28, txt + ", tail 'ptal' matches"
    return size - 0x18, txt + ", no matching tail (heap-guard layout?), data length = Size - 0x18"


EASY = [("EasyTuneSetupAddress-2a64d079-aceb-4ad9-afd5-252e35ba994a", "easytune_setup"),
        ("EasyVoltageAddress-dd41adf5-4368-4654-b1ca-46a6b98d9e84", "easyvoltage"),
        ("EasyHealthAddress-dd41adf5-4368-4654-b1ca-46a6b98d9e84", "easyhealth")]


def offline(args):
    return bool(args.apob_file or args.region_file) and not args.devmem


def gbt_easy(args):
    print("== Gigabyte EasyTuneSetup / EasyVoltage / EasyHealth buffers (EFI variable = u32 phys address; raw dumps)")
    if offline(args):
        print("  offline mode (--apob-file/--region-file without --devmem): skipped")
        return
    ranges = memmap_ranges(args)
    res = OUT.setdefault("gbt_easy", [])
    explained = False
    for var, tag in EASY:
        with Guard(var.split("-")[0]):
            try:
                raw = open(os.path.join(args.efivars, var), "rb").read()
            except OSError as e:
                print(f"  {var.split('-')[0]}: EFI variable not readable ({e.strerror})")
                continue
            val = raw[4:]  # 4-byte attributes first (efivarfs)
            if len(val) not in (4, 8):
                print(f"  {var.split('-')[0]}: unexpected {len(val)}-byte value {val.hex()}")
                continue
            addr = U(val, 0, len(val))
            rng = range_of(ranges, addr)
            where = f"in {rng[2]} 0x{rng[0]:X}-0x{rng[1]:X}" if rng else "outside the E820 map"
            print(f"  {var.split('-')[0]} = 0x{addr:X} ({where})")
            r = {"variable": var, "address": addr, "range": list(rng) if rng else None}
            res.append(r)
            if not readable_type(rng):
                print("    not in a DRAM-backed firmware range: not read (safety)")
                continue
            try:
                n, how = pool_size(addr, rng)
                print("    " + how)
                r["pool"] = how
                if n is None:
                    n = min(0x10000, rng[1] + 1 - addr)
                    print(f"    size unknown: reading 0x{n:X} bytes (to 64 KB or the end of the range)")
                n = min(n, rng[1] + 1 - addr)
                data, ref, err = read_span(addr, n)
            except OSError as e:  # /dev/mem not openable (not root, lockdown)
                ref, err = None, e
            if ref is None or ref == pages_spanned(addr, n):
                if not explained:
                    explain(err, addr)
                    explained = True
                else:
                    print(f"    not readable ({err!r})")
                r["error"] = repr(err)
                continue
            name = save(args, f"{tag}_{addr:08x}.bin", data)
            # sha256 / non-zero counts only in the JSON: they change every boot and ram_compare.py diffs this text
            r.update({"length": n, "refused_pages": ref, "nonzero": nonzero(data), "file": name,
                      "sha256": hashlib.sha256(data).hexdigest()})
            print(f"    {n} bytes, {ref} refused page(s)" + (f", saved {name}" if name else "")
                  + f"; first bytes as text: {printable(data[:40])}")
    st = STATE.get("aod")
    if st and not args.region_file:
        with Guard("pool header self-check"):
            rng = range_of(ranges, st["base"])
            if readable_type(rng):
                try:
                    n, how = pool_size(st["base"], rng)
                except OSError as e:
                    n, how = None, f"not readable ({e!r})"
                exp = (st["length"] + 7) // 8 * 8
                print(f"  pool-header self-check on the AODT buffer (SSDT length 0x{st['length']:X}): {how}"
                      + (f" -> data 0x{n:X}, expected 0x{exp:X}: {'consistent' if n == exp else 'MISMATCH'}"
                         if n is not None else ""))


def aml_name(b, i):
    s = ""
    while b[i] in (0x5C, 0x5E):
        s += chr(b[i]); i += 1
    if b[i] == 0x2E:
        n, i = 2, i + 1
    elif b[i] == 0x2F:
        n, i = b[i + 1], i + 2
    elif b[i] == 0x00:
        return s, i + 1
    else:
        n = 1
    segs = []
    for _ in range(n):
        if not name_seg_ok(b[i:i + 4]):
            raise ValueError("bad NameSeg")
        segs.append(b[i:i + 4].decode()); i += 4
    return s + ".".join(segs), i


def op_regions(acpi_dir):
    """SystemMemory OperationRegions with constant base/length in DSDT/SSDT* (ACPI 6.5 sec. 19.6.100 / 20.2.5.2)."""
    out, unreadable = [], 0
    for p in sorted(glob.glob(os.path.join(acpi_dir, "DSDT*")) + glob.glob(os.path.join(acpi_dir, "SSDT*"))):
        try:
            b = open(p, "rb").read()
        except OSError:
            unreadable += 1
            continue
        if b[:4] not in (b"DSDT", b"SSDT"):
            continue
        i = b.find(b"\x5b\x80")
        while i >= 0:
            try:
                nm, j = aml_name(b, i + 2)
                if b[j] == 0:
                    base, j = aml_int(b, j + 1)
                    ln, _ = aml_int(b, j)
                    out.append({"table": os.path.basename(p), "oem_table_id": b[16:24].rstrip(b"\0 ").decode("latin1"),
                                "name": nm, "base": base, "length": ln})
            except (ValueError, IndexError):
                pass
            i = b.find(b"\x5b\x80", i + 2)
    return out, unreadable


def nvs_dump(args):
    print("== ACPI NVS ranges (E820 'ACPI Non-volatile Storage'): raw dumps + OperationRegions declared in them")
    if offline(args):
        print("  offline mode (--apob-file/--region-file without --devmem): skipped")
        return
    ranges = memmap_ranges(args)
    nvs = [r for r in ranges if r[2] == NVS]
    if not nvs:
        print(f"  no NVS range in {args.memmap}")
        return
    res = OUT.setdefault("nvs", {"ranges": [], "op_regions": []})
    with Guard("OperationRegion scan"):
        regs, unreadable = op_regions(args.acpi_dir)
        inside = [x for x in regs if range_of(nvs, x["base"])]
        res["op_regions"] = inside
        print(f"  {len(inside)} SystemMemory OperationRegion(s) in NVS" +
              (f" ({unreadable} ACPI table(s) unreadable: need root)" if unreadable else "") +
              (": " + ", ".join(f"{x['name']} ({x['oem_table_id'] or x['table']}) 0x{x['base']:X}+0x{x['length']:X}"
                                for x in inside) if inside else ""))
    for s, e, _ in nvs:
        with Guard(f"NVS 0x{s:X}"):
            n = e + 1 - s
            r = {"start": s, "end": e}
            res["ranges"].append(r)
            if args.no_nvs_dump:
                print(f"  0x{s:X}-0x{e:X} ({n // 1024} KB): --no-nvs-dump, not read")
                continue
            if n > 0x4000000:
                print(f"  0x{s:X}-0x{e:X}: {n} bytes, larger than 64 MB, not read")
                continue
            try:
                data, ref, err = read_span(s, n)
            except OSError as ex:  # /dev/mem not openable
                ref, err = None, ex
            if ref is None or ref == pages_spanned(s, n):
                print(f"  0x{s:X}-0x{e:X} ({n // 1024} KB): not readable ({err!r})")
                if s == nvs[0][0]:
                    explain(err, s)
                r["error"] = repr(err)
                continue
            name = save(args, f"nvs_{s:08x}-{e:08x}.bin", data)
            # sha256 / non-zero counts only in the JSON (they change every boot; ram_compare.py diffs this text)
            r.update({"refused_pages": ref, "nonzero": nonzero(data), "file": name,
                      "sha256": hashlib.sha256(data).hexdigest()})
            print(f"  0x{s:X}-0x{e:X} ({n // 1024} KB): {ref} refused page(s)"
                  + (f", saved {name}" if name else " (not saved)" if args.dump_dir else " (no --dump-dir: not saved)"))


def write_json(args):
    if not args.dump_dir:
        return
    OUT["generated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    OUT["sources"] = {"ZenStates-Core": "869b7e82572337e5a673e92230edc92bddf0e44b",
                      "openSIL": "9648171d730771cc3574d7d4d13581d22d163319",
                      "edk2": "0a503bc2f5bd37c7a5d39d0ba31008f446f0298d",
                      "coreboot": "80dd4ace74fbb5c409eeedd2f139ad24022949fb"}
    try:
        os.makedirs(args.dump_dir, exist_ok=True)
        with open(os.path.join(args.dump_dir, "bios_memcfg.json"), "w") as f:
            json.dump(OUT, f, indent=1, default=lambda o: o.hex() if isinstance(o, (bytes, bytearray)) else str(o))
        print("  decoded data saved to bios_memcfg.json")
    except (OSError, TypeError, ValueError) as e:
        print(f"  bios_memcfg.json not written: {e!r}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    for opt in ("--ssdt", "--region-file", "--apob-file", "--pm-table", "--dump-dir"):
        ap.add_argument(opt)
    ap.add_argument("--devmem", help="read 'physical memory' from this file instead of /dev/mem (offline tests)")
    ap.add_argument("--efivars", default="/sys/firmware/efi/efivars")
    ap.add_argument("--memmap", default="/sys/firmware/memmap")
    ap.add_argument("--acpi-dir", default="/sys/firmware/acpi/tables")
    ap.add_argument("--no-nvs-dump", action="store_true", help="do not dump the ACPI NVS ranges")
    a = ap.parse_args()
    if a.devmem:
        DEVMEM = a.devmem
    if a.dump_dir:  # dump() expects it (ram_snapshot.py creates it; a manual run may not)
        try:
            os.makedirs(a.dump_dir, exist_ok=True)
        except OSError as e:
            print(f"note: --dump-dir {a.dump_dir}: {e}", file=sys.stderr)
    if os.geteuid() != 0 and not (a.ssdt and a.region_file):
        print("note: not root; /sys/firmware/acpi/tables/* and /dev/mem need root", file=sys.stderr)
    for fn in (aod, apob, pm, apob_details, aod_spd, gbt_easy, nvs_dump, write_json):
        try:
            fn(a)
        except Exception as e:  # keep going with the other sources
            print(f"  error: {e!r}")
