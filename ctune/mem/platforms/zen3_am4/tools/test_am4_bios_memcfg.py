#!/usr/bin/env python3
"""Offline tests for am4_bios_memcfg.py (the APOB, SPD, Easy* and NVS sections). No root needed.

Usage: python3 -I test_am4_bios_memcfg.py OUTDIR [--work DIR] [--sibling-tables DIR]

Everything is synthetic, built from the documented layouts:
  * APOB: header with the config-offset list (ZenStates-Core 869b7e8 Apob.cs GetConfigOffsets), APOB_TYPE_HEADER
    entries (openSIL ApobCmn.h L101-110): MEM 1/2 (ApobMemGeneralConfig.cs), 1/16 (ApobBootInfo.cs), 1/17
    (ApobDimmSpd.cs / APOB_SPD_STRUCT), 1/18, 1/22, GEN 7/3 DDR4 timing blocks (ApobLayout.cs Zen3 DDR4 layout,
    ApobChannelTimings.cs, ApobDdr4ModeRegisters.cs), 7/6 (ApobEventLog.cs), SMBIOS 8/8 (ApobDmiInfo.cs),
    FABRIC 9/9 (ApobMemoryMap.cs), an "encrypted" entry, and a second entry chain reached only via the header list.
  * DDR4 SPDs with XMP 2.0 profiles (ZenStates-Core Ddr4SpdDecoder.cs offsets, JESD21-C Annex L).
  * An AOD SSDT (OperationRegion and Field AML, ACPI 6.5 sec. 20.2) and an AODT buffer with DSPD SPD copies.
  * EDK2 DXE pool headers and tails (MdeModulePkg/Core/Dxe/Mem/Pool.c) in front of the Easy* buffers and the AODT.
  * A sparse 'physical memory' image (--devmem), a fake /sys/firmware/memmap and the snapshot's real efivars.
The MR / SPD values and serials are invented test vectors, not a real machine's settings.
The offline run needs a ctune snapshot for its pm_table and efivars (CTUNE_SNAPSHOT=data/snapshots/<name>). The
byte-identity check of the first three sections also needs a copy of am4_bios_memcfg.py from before the further
sections (CTUNE_MEMCFG_BEFORE=FILE); without it that check is skipped.
"""
import argparse, contextlib, hashlib, importlib.util, io, json, os, random, shutil, struct, subprocess, sys, time

sys.dont_write_bytecode = True  # no __pycache__ next to the scripts or the reference copy

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "am4_bios_memcfg.py")
SNAP = os.environ.get("CTUNE_SNAPSHOT", "")
spec = importlib.util.spec_from_file_location("bm", SCRIPT)
bm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bm)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    return cond


# ------------------------------------------------------------------ SPD
def enc_ps(ps):
    mtb = -(-ps // 125)
    return mtb, (ps - mtb * 125) & 0xFF


def crc16(b):
    crc = 0
    for c in b:
        crc ^= c << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
    return crc


def cl_mask(cls):
    m = 0
    for c in cls:
        m |= 1 << (c - 7)
    return m.to_bytes(4, "little")


def spd_image(serial, part="4400 C19 Series", year=0x19, week=0x23):
    s = bytearray(512)
    s[0:18] = bytes([0x23, 0x11, 0x0C, 0x02, 0x85, 0x21, 0x00, 0x08, 0, 0x60, 0, 0x03, 0x01, 0x03, 0x00, 0, 0, 0])
    for mtb_at, fine_at, ps in ((18, 125, 938), (19, 124, 1600), (24, 123, 14060), (25, 122, 14060), (26, 121, 14060),
                                (38, 119, 3700), (39, 118, 5300), (40, 117, 5355)):
        s[mtb_at], s[fine_at] = enc_ps(ps)
    s[20:24] = cl_mask(range(9, 17))
    tras, (trc, trc_f) = 264, enc_ps(47060)
    s[27], s[28], s[29], s[120] = (trc >> 8) << 4 | (tras >> 8), tras & 0xFF, trc & 0xFF, trc_f
    struct.pack_into("<HHH", s, 30, 2800, 2080, 1280)        # tRFC1/2/4 350/260/160 ns
    s[36], s[37] = 0, 168                                       # tFAW 21 ns
    s[41], s[42], s[43], s[44], s[45] = 0, 120, 0, 20, 60      # tWR 15, tWTR_S 2.5, tWTR_L 7.5 ns
    struct.pack_into("<H", s, 126, crc16(s[0:126]))
    s[128], s[129], s[130], s[131] = 0x11, 0x01, 0x00, 0x00
    struct.pack_into("<H", s, 254, crc16(s[128:254]))
    s[320], s[321], s[322], s[323], s[324] = 0x85, 0x02, 0x01, year, week   # Patriot (bank 6, 0x02)
    s[325:329] = bytes.fromhex(serial)
    s[329:349] = part.encode().ljust(20)
    s[349], s[350], s[351], s[352] = 0x00, 0x80, 0xCE, 0x00                 # DRAM: Samsung (bank 1, 0xCE)
    s[384:388] = bytes([0x0C, 0x4A, 0x03, 0x20])                            # XMP 2.0, profiles 1 and 2, 1 DPC
    for o, vdd, tck, cls, taa, trcd, tras_m, trc_ps in ((393, 0xAD, 454, (16, 17, 18, 19), 8636, 8636, 141, 26363),
                                                        (440, 0xA3, 556, (15, 16, 17), 9444, 10556, 173, 32222)):
        s[o] = vdd
        s[o + 3], s[o + 38] = enc_ps(tck)
        s[o + 4:o + 8] = cl_mask(cls)
        s[o + 8], s[o + 37] = enc_ps(taa)
        s[o + 9], s[o + 36] = enc_ps(trcd)
        s[o + 10], s[o + 35] = enc_ps(trcd)
        m, f = enc_ps(trc_ps)
        s[o + 11], s[o + 12], s[o + 13], s[o + 34] = (m >> 8) << 4 | (tras_m >> 8), tras_m & 0xFF, m & 0xFF, f
        struct.pack_into("<HHH", s, o + 14, 2800, 2080, 1280)
        s[o + 20], s[o + 21] = 0, 168
        for k, ps in ((22, 3700), (23, 5300), (24, 5355)):
            s[o + k], s[o + 33 - (k - 22)] = enc_ps(ps)
    return bytes(s)


SERIALS = {"A1": "1A2B3C4F", "A2": "00000000", "B1": "1A2B3C40", "B2": "00000000"}


def spd_record(channel, dimm, spd, present=1):
    r = bytearray(532)
    r[0:8] = bytes([0, channel, dimm, 0, present, 0, 0, 0])
    struct.pack_into("<III", r, 8, 0xA0 + 2 * (channel * 2 + dimm), 0x12345678 + channel * 2 + dimm, 0x1)
    r[20:532] = spd
    return bytes(r)


def spd_records_blob(mutate_b1=False):
    out = bytearray()
    for ch in range(8):
        for d in range(2):
            slot = f"{chr(65 + ch)}{d + 1}"
            if slot in SERIALS:
                spd = bytearray(spd_image(SERIALS[slot]))
                if mutate_b1 and slot == "B1":
                    spd[400] ^= 0xFF
                out += spd_record(ch, d, bytes(spd))
            else:
                out += spd_record(ch, d, bytes(512), present=0)
    return bytes(out)


# ------------------------------------------------------------------ APOB
def entry(g, ty, inst, data):
    data = bytes(data) + b"\0" * (-len(data) % 4)
    return struct.pack("<4I", g, ty, inst, 48 + len(data)) + bytes(32) + data


def mr0(cl, wr):
    code = bm.MR_CL.index(cl)
    w = bm.MR_WR.index(wr)
    return ((code >> 4) & 1) << 12 | ((code >> 1) & 7) << 4 | (code & 1) << 2 | ((w >> 3) & 1) << 13 | (w & 7) << 9


def mr2(cwl, rttwr):
    return bm.MR_CWL.index(cwl) << 3 | rttwr << 9


def mr6(tccdl, vref, rng2=False):
    return (tccdl - 4) << 10 | (0x40 if rng2 else 0) | vref


MIN_TIMES = [550, 550, 300, 1280, 1830, 600, 148, 100, 840, 196, 300, 200]  # u16 SPD minima in front of MEMCLK
MR_A_P0 = [mr0(14, 26), 0x0701, mr2(14, 4), 0x0008, 0x1000, 0x0140, mr6(8, 0x1C)]
MR_A_P0_2ND = [mr0(14, 26), 0x0001, mr2(14, 4), 0x0008, 0x1000, 0x01C0, mr6(8, 0x1C)]
MR_P1 = [mr0(11, 12), 0x0701, mr2(9, 4), 0x0000, 0x0000, 0x0140, mr6(5, 0x1C)]
CLK_P0 = [14, 14, 15, 15, 14, 36, 51, 26, 4, 4, 16, 4, 12, 8]
CLK_P1 = [11, 9, 11, 11, 6, 28, 39, 12, 4, 2, 20, 5, 6, 5]


def put_block(buf, memclk_at, memclk, gap, clocks, rfc, mr_off, mrs, extra_mrs=(), filler=None):
    """memclk_at is the offset of MEMCLK inside buf (the data of the GEN 7/3 entry)."""
    for i, v in enumerate(MIN_TIMES):
        struct.pack_into("<H", buf, memclk_at - 24 + 2 * i, v)
    struct.pack_into("<H", buf, memclk_at, memclk)
    if filler is not None:
        struct.pack_into("<H", buf, memclk_at + 2, filler)
    c = memclk_at + gap
    buf[c:c + 14] = bytes(clocks)
    struct.pack_into("<HxxHxxH", buf, c + 0x10, *rfc)
    for k, s in enumerate([mrs] + list(extra_mrs)):
        struct.pack_into("<7H", buf, c + mr_off + 14 * k, *s)
    return c


def gen73_data():
    """4 blocks: A P0 / A P1 (0x108 apart), B P0 / B P1. Offsets relative to the entry start are recorded."""
    buf = bytearray(b"\xEE" * 0x40) + bytearray(0x600)
    pos = {}
    a0 = 0x40 + 24                                   # data-relative MEMCLK offsets (even relative to the entry)
    pos["A0"] = (a0, put_block(buf, a0, 1867, 6, CLK_P0, (350, 260, 160), 0x26, MR_A_P0, [MR_A_P0_2ND]))
    pos["A1"] = (a0 + 0x108, put_block(buf, a0 + 0x108, 800, 6, CLK_P1, (350, 260, 160), 0x26, MR_P1))
    b0 = a0 + 0x240
    clk_b = list(CLK_P0)
    clk_b[13] = 10                                   # block tCCD_L 10 > MR6 maximum 8: relaxed match expected
    pos["B0"] = (b0, put_block(buf, b0, 1867, 6, clk_b, (350, 260, 160), 0x62, MR_A_P0, filler=933))
    pos["B1"] = (b0 + 0x108, put_block(buf, b0 + 0x108, 800, 6, CLK_P1, (350, 260, 160), 0x62, MR_P1))
    return bytes(buf), pos


def gen12_data():
    d = bytearray(448)
    struct.pack_into("<HH", d, 4, 1867, 3733)
    d[24] = 1
    arr = 0xA4
    vals = [56, 7, 4, 5, 0, 0, 0, 15, 31, 15, 15]   # ProcODT 40 ohm, RTT 7/4/5, setups 0, DrvStr 24/20/24/24
    for k, v in enumerate(vals):
        for ch in (0, 1):
            d[arr + ch + 8 * k] = v
    struct.pack_into("<III", d, arr + 88, 0x3, 0x7, 0x100)
    return bytes(d)


def boot_info_data(dimm_like=False):
    d = bytearray(4 + 16 * 12 + 20)
    struct.pack_into("<I", d, 0, 0)
    for i in range(16):
        o = 4 + 12 * i
        if dimm_like:
            if i in (0, 1, 2, 3):
                struct.pack_into("<HHIB", d, o, 0xCE80, 0x0285, 0x1A2B3C40 + i, 1)
        else:
            struct.pack_into("<HHIB", d, o, 0x1234 + i, 0xBEEF, 0xA5A5A5A5, 0x5A)   # not DIMM-like (Zen2/3)
    t = 4 + 16 * 12
    d[t:t + 4] = bytes([0, 0, 0, 0])
    struct.pack_into("<IIII", d, t + 4, 0x20261024, 0x11, 0x22, 0x33)                 # BCD 2026-10-24
    return bytes(d)


def dmi_data():
    d = bytearray([0x1A, 4, 4, 0])
    for i, (ch, dm) in enumerate(((0, 0), (0, 1), (1, 0), (1, 1))):
        d += struct.pack("<BBHHH", ch << 2 | dm << 5 | 0x80, 0xA0 + 2 * i, 0x28 + i, 3733, 1200)
    for i, (ch, dm) in enumerate(((0, 0), (0, 1), (1, 0), (1, 1))):
        start = i * 8 * 1024 * 1024
        d += struct.pack("<BBHII4xQQ", ch << 2 | dm << 5 | 0x80, 1, 0x40 + i, start, start + 8 * 1024 * 1024 - 1, 0, 0)
    return bytes(d)


def events_data():
    d = bytearray(4 + 64 * 16)
    struct.pack_into("<H", d, 0, 2)
    struct.pack_into("<4I", d, 4, 6, 0x04090A00, 0, 1)
    struct.pack_into("<4I", d, 20, 5, 0x04120400, 2, 3)
    return bytes(d)


def memmap_data():
    d = bytearray(struct.pack("<QI4x", 0x840000000, 2))
    d += struct.pack("<QQI4x", 0xC0000000, 0x40000000, 1)
    d += struct.pack("<QQI4x", 0x9F00000, 0x100000, 9)
    return bytes(d)


def build_apob(dimm_like_boot=False):
    hsize = 0x60
    chain1 = (entry(1, 2, 0, gen12_data()) + entry(1, 16, 0, boot_info_data(dimm_like_boot)) +
              entry(1, 17, 0, bytes([2, 8, 0, 0]) + spd_records_blob()) + entry(8, 8, 0, dmi_data()))
    g73, pos = gen73_data()
    off73 = hsize + len(chain1)
    chain1 += entry(7, 3, 0, g73)
    chain1 += entry(0xFFFF0001, 0xFFFF0005, 0, bytes(range(64)))
    gap = bytes(64)
    chain2 = (entry(7, 6, 0, events_data()) + entry(1, 22, 0, bytes(64)) +
              entry(1, 18, 0, bytes([0, 1, 0, 2] + [0] * 28)) + entry(9, 9, 0, memmap_data()))
    c2 = hsize + len(chain1) + len(gap)
    total = c2 + len(chain2)
    hdr = bytearray(hsize)
    hdr[0:4] = b"APOB"
    struct.pack_into("<III", hdr, 4, 0x18, total, hsize)
    struct.pack_into("<I", hdr, 0x30, c2)              # second chain only reachable through the header list
    return bytes(hdr) + chain1 + gap + chain2, off73, pos


# ------------------------------------------------------------------ AML / AODT
def pkglen(v):
    if v < 0x40:
        return bytes([v])
    n = 1 if v < 0x1000 else 2 if v < 0x100000 else 3
    return bytes([n << 6 | (v & 0xF)] + [(v >> (4 + 8 * k)) & 0xFF for k in range(n)])


AOD_FIELDS = [(None, 20 * 8), ("MBMC", 16), ("TTCL", 8), ("TRCR", 8), ("TRCW", 8), ("TRAS", 8), ("TTRP", 8),
              ("VDIO", 16), ("VMTT", 16), ("VMPP", 16), ("PODT", 16), (None, 30 * 8), ("RTNM", 8), ("RTWR", 8),
              ("RTPK", 8), (None, 18 * 8), ("ACSP", 8), ("COSP", 8), ("CESP", 8), ("CBCK", 8), ("CBAC", 8),
              ("CBCO", 8), ("CBCE", 8), (None, 111 * 8), ("DSPD", 16 * 532 * 8)]


def acpi_table(sig, oem_tid, body):
    t = bytearray(sig + struct.pack("<IB", 36 + len(body), 2) + b"\0" + b"AMD   " + oem_tid.ljust(8)[:8] +
                  struct.pack("<I4sI", 1, b"INTL", 0x20190509)) + body
    t[9] = (-sum(t)) & 0xFF
    return bytes(t)


def aod_ssdt(base):
    body = b"\x5b\x80AODT\x00\x0c" + struct.pack("<I", base) + b"\x0b" + struct.pack("<H", 0x220C)
    fl = b"AODT\x00"
    for name, bits in AOD_FIELDS:
        fl += (b"\x00" + pkglen(bits)) if name is None else (name.encode() + pkglen(bits))
    n = len(fl)
    for k in (1, 2, 3, 4):
        if len(pkglen(n + k)) == k:
            n += k
            break
    return acpi_table(b"SSDT", b"AMD AOD", body + b"\x5b\x81" + pkglen(n) + fl)


def dsdt_regions():
    body = (b"\x5b\x80GNVS\x00\x0c" + struct.pack("<I", 0xBCBF5018) + b"\x0b" + struct.pack("<H", 0x0300) +
            b"\x5b\x80\\\x2e_SB_PMRG\x00\x0c" + struct.pack("<I", 0xFED80300) + b"\x0b\x00\x01" +
            b"\x5b\x80SMIO\x01\x0a\xb2\x0a\x02")
    return acpi_table(b"DSDT", b"TESTDSDT", body)


def aodt_buffer():
    b = bytearray(0x220C)
    struct.pack_into("<HBBBBBHHHH", b, 20, 1867, 14, 15, 15, 36, 15, 1540, 770, 2500, 56)
    b[65:68] = bytes([7, 4, 5])
    b[86:93] = bytes([0, 0, 0, 15, 31, 15, 15])
    b[0xCC:0xCC + 16 * 532] = spd_records_blob(mutate_b1=True)
    return bytes(b)


def pool(data, typ=10, tail=True, sig=b"phd0"):
    size = (len(data) + 7) // 8 * 8 + (0x28 if tail else 0x18)
    h = sig + struct.pack("<IIIQ", 0, typ, 0, size)
    body = data + bytes((len(data) + 7) // 8 * 8 - len(data))
    return h + body + (b"ptal" + struct.pack("<IQ", 0, size) if tail else b"")


# ------------------------------------------------------------------ fake system
LIVE_MEMMAP = [(0x0, 0x9FFFF, "System RAM"), (0x100000, 0x9C3EFFF, "System RAM"), (0x9C3F000, 0x9FFFFFF, "Reserved"),
               (0xA000000, 0xA1FFFFF, "System RAM"), (0xA200000, 0xA20DFFF, "ACPI Non-volatile Storage"),
               (0xA20E000, 0xAFFFFFF, "System RAM"), (0xBC9AA000, 0xBC9F1FFF, "ACPI Tables"),
               (0xBC9F2000, 0xBD0D8FFF, "ACPI Non-volatile Storage"), (0xBD0D9000, 0xBDDFEFFF, "Reserved"),
               (0xFED80000, 0xFED8FFFF, "Reserved")]
EASY = {"easytune": 0xBCB53018, "easyhealth": 0xBCB76018, "easyvoltage": 0xBCB77018}
AODT_BASE = 0xBD0BE018


def build_system(work):
    os.makedirs(work, exist_ok=True)
    mm = os.path.join(work, "memmap")
    shutil.rmtree(mm, ignore_errors=True)
    for i, (s, e, t) in enumerate(LIVE_MEMMAP):
        d = os.path.join(mm, str(i))
        os.makedirs(d)
        for k, v in (("start", f"0x{s:x}\n"), ("end", f"0x{e:x}\n"), ("type", t + "\n")):
            open(os.path.join(d, k), "w").write(v)
    acpi = os.path.join(work, "acpi")
    os.makedirs(acpi, exist_ok=True)
    open(os.path.join(acpi, "SSDT3"), "wb").write(aod_ssdt(AODT_BASE))
    open(os.path.join(acpi, "DSDT"), "wb").write(dsdt_regions())
    apob, off73, pos = build_apob()
    img = os.path.join(work, "phys.img")
    planted = {}
    with open(img, "wb") as f:
        f.truncate(0xBD0D9000)

        def put(addr, data):
            f.seek(addr)
            f.write(data)
            planted[addr] = data
        put(0xA200000, apob)
        put(AODT_BASE - 0x18, pool(aodt_buffer()))
        put(EASY["easytune"] - 0x18, pool(b"ETSETUP\0" + bytes(range(256)) * 7 + bytes(0x800 - 1804) + b"END!", tail=True))
        put(EASY["easyvoltage"] - 0x18, pool(b"EVOLT\0\0\0" + bytes(range(1, 249)), tail=False))
        put(EASY["easyhealth"] - 0x18, b"garbage!" * 3 + b"EHEALTH\0" + bytes(range(200)))
    return {"img": img, "memmap": mm, "acpi": acpi, "apob": apob, "off73": off73, "pos": pos, "planted": planted}


def run(args, timeout=120):
    t0 = time.time()
    p = subprocess.run([sys.executable, "-I", SCRIPT] + args, capture_output=True, text=True, timeout=timeout)
    return p, time.time() - t0


def quiet(fn, *a):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a)
    return buf.getvalue()


# ------------------------------------------------------------------ tests
def test_mr_tables():
    # coreboot ddr4.c ddr4_cas_to_mr0_map (L297-335) maps CL 9 to 32 to MR0 bits; the decode must give the CL back
    enc = [0x0000, 0x0004, 0x0010, 0x0014, 0x0020, 0x0024, 0x0030, 0x0034, 0x0064, 0x0040, 0x0070, 0x0044, 0x0074,
           0x0050, 0x0060, 0x0054, 0x1000, 0x1004, 0x1010, 0x1014, 0x1020, 0x1024, 0x1030, 0x1034]
    bad = [(cl, bm.mr_cl(e)) for cl, e in zip(range(9, 33), enc) if cl not in (29, 31) and bm.mr_cl(e) != cl]
    check("MR0 CL decode = coreboot encoder for CL 9..32", not bad, str(bad))
    # coreboot ddr4_wr_to_mr0_map (L287-295) for WR 10..24; WR 26/28 use A13 (JESD79-4B; ZSC WrCodes L37)
    wr_enc = {10: 0, 12: 1, 14: 2, 16: 3, 18: 4, 20: 5, 22: 7, 24: 6}
    bad = [w for w, c in wr_enc.items() if bm.mr_wr(c << 9) != w]
    check("MR0 WR decode = coreboot encoder for WR 10..24, A13 for 26/28",
          not bad and bm.mr_wr(1 << 13) == 26 and bm.mr_wr(1 << 13 | 1 << 9) == 28, str(bad))
    cw = {9: 0, 10: 1, 11: 2, 12: 3, 14: 4, 16: 5, 18: 6, 20: 7}       # coreboot ddr4_cwl_to_mr2_map L377-386
    check("MR2 CWL decode", all(bm.mr_cwl(c << 3) == v for v, c in cw.items()))
    check("MR6 tCCD_L decodes 4 to 8, and reserved codes to 0", [bm.mr_tccdl(c << 10) for c in range(8)] == [4, 5, 6, 7, 8, 0, 0, 0])
    d, lines = bm.decode_mrs(MR_A_P0, 1540)
    exp = {"CL": 14, "WR": 26, "RTP_autoprecharge": 13, "CWL": 14, "Ron_ohm": 34, "RttNom": "RZQ/7 (34 ohm)",
           "RttWr": "RZQ/3 (80 ohm)", "RttPark": "RZQ/5 (48 ohm)", "geardown": "1/4 rate (gear-down on)",
           "write_preamble_tCK": 2, "read_preamble_tCK": 1, "VrefDQ_pct": 78.2, "VrefDQ_range": 1, "tCCD_L": 8,
           "dll_enable": 1, "AL": "off", "BL": "BL8 fixed"}
    bad = {k: (d[k], v) for k, v in exp.items() if d[k] != v}
    check("decode_mrs on a hand-made DDR4-3733 CL14 GDM set", not bad, str(bad))
    check("VrefDQ absolute value uses the AOD VDDQ", any("= 1204 mV at VDDQ 1540 mV" in x for x in lines))
    d2, _ = bm.decode_mrs([0, 0x0005, 0x0E00, 0, 0x0800, 0x1C07, 0x0040 | 0x3F], None)
    check("reserved / range-2 encodings do not crash and are labelled",
          d2["Ron_ohm"] is None and d2["RttWr"] == "reserved (7)" and d2["VrefDQ_pct"] is None and
          d2["CA_parity_latency"] == "reserved" and d2["read_preamble_tCK"] == 2, str(d2))
    rnd = random.Random(1)
    try:
        for _ in range(20000):
            bm.decode_mrs([rnd.getrandbits(16) for _ in range(7)], rnd.choice([None, 1200, 1540]))
        ok = True
    except Exception as e:
        ok = repr(e)
    check("decode_mrs on 20000 random MR sets never raises", ok is True, str(ok))


def test_spd():
    d = bm.decode_spd(spd_image("1A2B3C4F"))
    j = d["jedec"]
    p1, p2 = d["xmp"]["profiles"]
    exp = [(d["part_number"], "4400 C19 Series"), (d["module_mfr"], "Patriot Memory (PDP Systems)"),
           (d["dram_mfr"], "Samsung"), (d["mfg_date"], "2019 week 23"), (d["serial"], "1A2B3C4F"),
           (d["capacity_MB"], 8192), (d["ranks"], 1), (d["device_width"], 8), (d["die_density_Gb"], 8.0),
           (d["bank_groups"], 4), (d["crc_base_ok"], True), (d["crc_module_ok"], True), (j["speed_MTs"], 2133),
           (j["timings"], [15, 15, 15, 36, 51]), (j["tRFC1_ns"], 350.0), (j["CL_supported"], list(range(9, 17))),
           (d["xmp"]["revision"], "2.0"), (p1["speed_MTs"], 4400), (p1["vdd_mV"], 1450),
           ([p1["clocks"][k] for k in ("tAA", "tRCD", "tRP", "tRAS", "tRC")], [19, 19, 19, 39, 58]),
           (p1["clocks"]["tRFC1"], 770), (p1["dimms_per_channel"], 1), (p2["speed_MTs"], 3600), (p2["vdd_mV"], 1350),
           ([p2["clocks"][k] for k in ("tAA", "tRCD", "tRP", "tRAS", "tRC")], [17, 19, 19, 39, 58]),
           (p2["clocks"]["tRFC1"], 630)]
    bad = [(i, a, b) for i, (a, b) in enumerate(exp) if a != b]
    check("SPD decode: part/mfr/DRAM/date/serial/capacity/CRC/JEDEC/XMP P1+P2", not bad, str(bad))
    lines = bm.spd_lines(d)
    check("SPD text has the XMP line", any("profile 1: DDR4-4400 19-19-19-39-58 (CL-RCD-RP-RAS-RC), tRFC1 770 (350 ns),"
                                           " 1.45 V" in x for x in lines), "\n".join(lines))
    bad_crc = bytearray(spd_image("1A2B3C4F"))
    bad_crc[5] ^= 1
    check("SPD CRC error detected", bm.decode_spd(bytes(bad_crc))["crc_base_ok"] is False)
    check("SPD of another type is not decoded", bm.decode_spd(bytes([0x23, 0x10, 0x12]) + bytes(509))["valid"] is False)
    check("JEP106 unknown / invalid parity", bm.jep106(0x7F, 0x13).startswith("bank 128") and
          bm.jep106(0x80, 0xCF).startswith("invalid"))
    rnd = random.Random(2)
    try:
        for _ in range(3000):
            s = bytearray(rnd.getrandbits(8) for _ in range(rnd.choice([0, 3, 100, 384, 512, 600])))
            if len(s) > 2 and rnd.random() < 0.8:
                s[2] = 0x0C
            if len(s) > 386 and rnd.random() < 0.5:
                s[384:386] = b"\x0c\x4a"
            bm.spd_lines(bm.decode_spd(bytes(s)))
        ok = True
    except Exception as e:
        ok = repr(e)
    check("SPD decode on 3000 random/short images never raises", ok is True, str(ok))


def test_timing_blocks():
    apob, off73, pos = build_apob()
    ents = bm.apob_list(apob, 0x60, len(apob))
    e = bm.apob_find(ents, 7, 3)
    blocks = bm.timing_blocks(apob, e[0], e[1], 1867)
    got = [(b["channel"], b["pstate"], b["memclk"], b["active"], b["clocks_rel"]) for b in blocks]
    want = [(0, 0, 1867, True, pos["A0"][1] + 48), (0, 1, 800, False, pos["A1"][1] + 48),
            (1, 0, 1867, True, pos["B0"][1] + 48), (1, 1, 800, False, pos["B1"][1] + 48)]
    check("GEN 7/3: 4 blocks, channel/P-state from the 0x108 stride, active by MEMCLK", got == want, f"{got} != {want}")
    check("GEN 7/3: clock bytes and tRFC ns", list(blocks[0]["clocks"].values()) == CLK_P0 and
          blocks[0]["tRFC_ns"] == [350, 260, 160])
    check("MR found at clocks+0x26 (A) and +0x62 (B)", [b.get("mr_from_clocks") for b in blocks] == [0x26, 0x26, 0x62, 0x62])
    check("MR words A0 / P1", blocks[0]["mr_words"] == MR_A_P0 and blocks[1]["mr_words"] == MR_P1)
    check("contiguous second MR set reported (tentative)", blocks[0]["mr_extra_contiguous"] == [MR_A_P0_2ND] and
          blocks[2]["mr_extra_contiguous"] == [])
    check("relaxed tCCD_L match flagged on B0", any("not ZenStates-Core's strict rule" in n for n in blocks[2]["notes"]))
    check("alternative MEMCLK candidate (933 filler) noted on B0", any("933" in n for n in blocks[2]["notes"]))
    # ZSC ambiguity: gap 2 puts tFAW (840 in 25 ps units) 10 bytes before the clock bytes
    buf = bytearray(0x200)
    put_block(buf, 0x40, 1867, 2, CLK_P0, (350, 260, 160), 0x26, MR_A_P0)
    t = entry(7, 3, 0, bytes(buf))
    b2 = bm.timing_blocks(t, 0, len(t), 1867)
    check("tFAW-before-MEMCLK trap: ZenStates-Core rule picks 840 and the misparse note fires",
          b2 and b2[0]["memclk"] == 840 and any("misparse" in n for n in b2[0]["notes"]), str(b2 and b2[0]["notes"]))


def fuzz_apob(n_iter=1500):
    apob, _, _ = build_apob()
    rnd = random.Random(3)
    args = argparse.Namespace(dump_dir=None)
    raised, guard_errs, cases = [], {}, 0

    def one(t):
        nonlocal cases
        cases += 1
        bm.STATE.clear()
        bm.OUT.clear()
        bm.OUT["errors"] = []
        hs = struct.unpack_from("<I", t, 12)[0] if len(t) >= 16 else 0
        ts = struct.unpack_from("<I", t, 8)[0] if len(t) >= 16 else 0
        bm.STATE["apob"] = {"t": t, "version": 0, "tsize": min(ts or len(t), len(t)), "hsize": hs}
        try:
            quiet(bm.apob_details, args)
        except Exception as e:
            raised.append(repr(e))
        for x in bm.OUT["errors"]:
            k = x.split(":")[0]
            guard_errs[k] = guard_errs.get(k, 0) + 1
    for cut in range(16, len(apob), 97):             # truncation (TableSize kept: entries cross the end)
        one(apob[:cut])
    for _ in range(n_iter):                          # random byte flips
        t = bytearray(apob)
        for _ in range(rnd.choice([1, 4, 32, 256])):
            t[rnd.randrange(len(t))] = rnd.getrandbits(8)
        one(bytes(t))
    for _ in range(200):                             # 32-bit field corruption at entry headers / hot spots
        t = bytearray(apob)
        o = rnd.randrange(0, len(t) - 4) & ~3
        struct.pack_into("<I", t, o, rnd.choice([0, 1, 16, 47, 48, 0xFFFF, 0x7FFFFFFF, 0xFFFFFFFF, len(t), o]))
        one(bytes(t))
    hdr = bytearray(apob)                            # adversarial headers
    for hs, ts in ((0, 0), (0xFFFFFFFF, 0xFFFFFFFF), (8, len(apob)), (len(apob) + 100, 16)):
        struct.pack_into("<II", hdr, 8, ts, hs)
        one(bytes(hdr))
    loop = bytearray(apob)
    struct.pack_into("<I", loop, 0x30, 0x60)         # header list points back into chain 1
    one(bytes(loop))
    for _ in range(200):                             # random garbage tables
        one(b"APOB" + bytes(rnd.getrandbits(8) for _ in range(rnd.choice([12, 60, 500, 4000]))))
    check(f"APOB fuzz: {cases} corrupt tables, no exception escapes apob_details", not raised, str(raised[:3]))
    return cases, guard_errs


def fuzz_aod():
    rnd = random.Random(4)
    args = argparse.Namespace(dump_dir=None)
    fields = bm.parse_fields(aod_ssdt(AODT_BASE), "AODT")
    raised, cases = [], 0
    base = aodt_buffer()
    for _ in range(600):
        b = bytearray(base[:rnd.choice([0, 10, 0xCC, 0xCC + 600, 0x220C, 0x220C])])
        for _ in range(rnd.choice([0, 3, 300])):
            if b:
                b[rnd.randrange(len(b))] = rnd.getrandbits(8)
        bm.STATE.clear()
        bm.STATE["aod"] = {"buf": bytes(b), "fields": rnd.choice([fields, {}]), "base": AODT_BASE, "length": 0x220C}
        bm.STATE["apob_spd"] = {}
        cases += 1
        try:
            quiet(bm.aod_spd, args)
        except Exception as e:
            raised.append(repr(e))
    check(f"AODT DSPD fuzz: {cases} buffers, no exception escapes aod_spd", not raised, str(raised[:3]))


def boot_info_variants():
    for dimm_like, want in ((False, "not DIMM fingerprints"), (True, "module Patriot Memory (PDP Systems), DRAM Samsung")):
        t = entry(1, 16, 0, boot_info_data(dimm_like))
        bi = bm.dec_boot_info(t, 0, len(t), 2026)
        ok = bi["last_training"] == "2026-10-24" and bi["dimm_records_valid"] == dimm_like
        check(f"boot info ({'DIMM-like' if dimm_like else 'Zen2/3-like'} slots), BCD date 2026-10-24", ok, str(bi)[:200])
    check("binary date (Zen4/5 style) preferred when plausible", bm.bcd_date(0x141A0A18, 2026) == "2026-10-24")
    check("BCD date with century byte 0", bm.bcd_date(0x00261024, 2026) == "2026-10-24")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--work", default="/tmp/taskc_test_run")
    ap.add_argument("--sibling-tables", default="/tmp/aod_dsl/linuxhw/tables")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    print(f"# am4_bios_memcfg.py tests, {time.strftime('%Y-%m-%d %H:%M:%S')}, python {sys.version.split()[0]}")
    print(f"# script sha256 {hashlib.sha256(open(SCRIPT, 'rb').read()).hexdigest()}")
    test_mr_tables()
    test_spd()
    test_timing_blocks()
    boot_info_variants()
    cases, guard = fuzz_apob()
    print(f"  (decode errors caught by the per-decoder guards during the fuzz: {guard or 'none'})")
    fuzz_aod()

    sysd = build_system(a.work)
    apob_file = os.path.join(a.work, "apob.bin")
    open(apob_file, "wb").write(sysd["apob"])
    region_file = os.path.join(a.work, "aodt.bin")
    open(region_file, "wb").write(aodt_buffer())
    ssdt = os.path.join(sysd["acpi"], "SSDT3")
    pmt = os.path.join(SNAP, "raw", "pm_table.bin")
    efiv = os.path.join(SNAP, "raw", "efivars")

    # 1. offline: the first three sections must be byte-identical to the script before the additions
    old = os.environ.get("CTUNE_MEMCFG_BEFORE")
    off_args = ["--ssdt", ssdt, "--region-file", region_file, "--apob-file", apob_file, "--pm-table", pmt]
    p_new, _ = run(off_args)
    if old:
        p_old = subprocess.run([sys.executable, "-I", old] + off_args, capture_output=True, text=True, timeout=60)
        head = p_new.stdout.split("== APOB entries")[0]
        check("offline: original sections byte-identical to the pre-change script",
              head == p_old.stdout and p_new.returncode == 0, f"old {len(p_old.stdout)} new-head {len(head)}")
    open(os.path.join(a.outdir, "offline_run.txt"), "w").write(p_new.stdout + p_new.stderr)
    for s in ("Blk0 channel A P0: MEMCLK 1867 MHz (DDR4-3734), active", "MR0 2024: CL 14, WR 26 / RTP 13",
              "MR3 0008: 1/4 rate (gear-down on)", "MR6 101C: VrefDQ 78.20% of VDDQ (range 1, code 0x1C) = 1204 mV",
              "Blk2 channel B P0", "= same as Blk0", "4 present of 16 records", "XMP 2.0 profile 1: DDR4-4400 19-19-19-39-58",
              "same SPD as A1 except manufacturing date/serial", "last full PMU training 2026-10-24",
              "event log (GEN 7/6): 2 event(s)", "Warning: 0x04090A00", "MBIST result info (MEM 1/18): 2 non-zero bytes",
              "PMU training failure info (MEM 1/22): no data (all zero)", "(encrypted)", "top of memory 0x840000000",
              "A1 (socket 0): SPD address 0xA0, handle 0x0028, configured speed 3733, voltage 1200 mV",
              "B1 (socket 0, channel 1, DIMM 0, SMBus 0xA4", "DIFFERS from the APOB copy", "offline mode"):
        check(f"offline output contains: {s}", s in p_new.stdout)
    check("offline: no decode errors", "decode error" not in p_new.stdout and "error:" not in p_new.stdout)
    if os.path.isdir(a.sibling_tables):
        sib = open(os.path.join(a.sibling_tables, "SSDT7.dat"), "rb").read()
        p_sib, _ = run(["--ssdt", os.path.join(a.sibling_tables, "SSDT7.dat"), "--region-file", region_file,
                        "--apob-file", apob_file, "--pm-table", pmt])
        check("sibling-board AOD SSDT (B550 AORUS PRO V2): DSPD field located, same SPD records",
              "DSPD at 0xCC: 4 present record(s)" in p_sib.stdout and b"DSPD" in sib)

    # 2. live path through --devmem (the code path the reboot uses)
    dump = os.path.join(a.work, "dump")
    shutil.rmtree(dump, ignore_errors=True)
    live = ["--ssdt", ssdt, "--devmem", sysd["img"], "--efivars", efiv, "--memmap", sysd["memmap"],
            "--acpi-dir", sysd["acpi"], "--pm-table", pmt, "--dump-dir", dump]
    p_live, dt = run(live)
    open(os.path.join(a.outdir, "devmem_run.txt"), "w").write(p_live.stdout + p_live.stderr)
    check(f"--devmem run exits 0 in {dt:.1f} s (ram_snapshot.py allows 60 s)", p_live.returncode == 0 and dt < 30, f"{dt}")
    out = p_live.stdout
    for s in ("APOB at 0xA200000", "raw 8716 bytes saved to aodt.bin",
              "EasyTuneSetupAddress = 0xBCB53018 (in ACPI Non-volatile Storage 0xBC9F2000-0xBD0D8FFF)",
              "pool header 'phd0' at 0xBCB53000: EfiACPIMemoryNVS, Size 0x828, tail 'ptal' matches",
              "2048 bytes", "no matching tail (heap-guard layout?)", "no EDK2 pool header at 0xBCB76000",
              "size unknown: reading 0x10000 bytes", "pool-header self-check on the AODT buffer",
              "expected 0x2210: consistent", "2 SystemMemory OperationRegion(s) in NVS: GNVS (TESTDSDT) 0xBCBF5018+0x300, AODT (AMD AOD) 0xBD0BE018+0x220C",
              "0xA200000-0xA20DFFF (56 KB): 0 refused page(s)", "0xBC9F2000-0xBD0D8FFF (7068 KB): 0 refused page(s)",
              "decoded data saved to bios_memcfg.json", "Blk0 channel A P0: MEMCLK 1867 MHz"):
        check(f"--devmem output contains: {s}", s in out)
    check("--devmem: no decode errors", "decode error" not in out and "error:" not in out)
    img = open(sysd["img"], "rb")

    def phys(addr, n):
        img.seek(addr)
        return img.read(n)
    files = sorted(os.listdir(dump))
    check("dump dir files", all(f in files for f in ("aodt.bin", "apob_a200000.bin", "apob_spd_A1.bin", "apob_spd_B2.bin",
                                                       "aod_spd_B1.bin", "bios_memcfg.json", "easytune_setup_bcb53018.bin",
                                                       "easyvoltage_bcb77018.bin", "easyhealth_bcb76018.bin",
                                                       "nvs_0a200000-0a20dfff.bin", "nvs_bc9f2000-bd0d8fff.bin")), str(files))
    rd = lambda f: open(os.path.join(dump, f), "rb").read()
    check("easytune dump = planted data (0x800 bytes)", rd("easytune_setup_bcb53018.bin") == phys(EASY["easytune"], 0x800))
    check("easyvoltage dump = Size-0x18 bytes", rd("easyvoltage_bcb77018.bin") == phys(EASY["easyvoltage"], 0x100))
    check("easyhealth fallback dump = 64 KB", rd("easyhealth_bcb76018.bin") == phys(EASY["easyhealth"], 0x10000))
    check("NVS dumps equal the image ranges", rd("nvs_0a200000-0a20dfff.bin") == phys(0xA200000, 0xE000) and
          rd("nvs_bc9f2000-bd0d8fff.bin") == phys(0xBC9F2000, 0xBD0D9000 - 0xBC9F2000))
    check("apob_a200000.bin = planted APOB", rd("apob_a200000.bin") == sysd["apob"])
    check("apob_spd_A1.bin = SPD image", rd("apob_spd_A1.bin") == spd_image("1A2B3C4F"))
    j = json.load(open(os.path.join(dump, "bios_memcfg.json")))
    check("JSON: timing blocks with MR decode, SPD, easy, nvs, no errors",
          j["apob"]["timing_blocks"][0]["mr"]["RttPark"] == "RZQ/5 (48 ohm)" and len(j["apob"]["spd"]) == 4 and
          j["apob"]["spd"][0]["decoded"]["xmp"]["profiles"][0]["speed_MTs"] == 4400 and len(j["gbt_easy"]) == 3 and
          len(j["nvs"]["ranges"]) == 2 and j["errors"] == [], str(j.get("errors")))
    shutil.copy(os.path.join(dump, "bios_memcfg.json"), os.path.join(a.outdir, "devmem_run_bios_memcfg.json"))

    # 3. --no-nvs-dump and a memmap without NVS
    p, _ = run(live[:-2] + ["--no-nvs-dump"])
    check("--no-nvs-dump skips the range reads", "--no-nvs-dump, not read" in p.stdout and p.returncode == 0)

    # 4. odd EFI variables: wrong length, address outside the map, address in MMIO 'Reserved'
    ev = os.path.join(a.work, "efivars_odd")
    shutil.rmtree(ev, ignore_errors=True)
    os.makedirs(ev)
    names = [n for n, _ in bm.EASY]
    open(os.path.join(ev, names[0]), "wb").write(b"\x07\0\0\0" + b"\x01\x02")
    open(os.path.join(ev, names[1]), "wb").write(b"\x06\0\0\0" + struct.pack("<I", 0xFED80300))
    open(os.path.join(ev, names[2]), "wb").write(b"\x06\0\0\0" + struct.pack("<Q", 0x1234567890))
    p, _ = run(["--ssdt", ssdt, "--devmem", sysd["img"], "--efivars", ev, "--memmap", sysd["memmap"],
                "--acpi-dir", sysd["acpi"], "--pm-table", pmt, "--no-nvs-dump"])
    check("odd EFI variables handled (bad length, MMIO address not read, outside map)",
          "unexpected 2-byte value" in p.stdout and p.stdout.count("not in a DRAM-backed firmware range") == 2 and
          p.returncode == 0 and "decode error" not in p.stdout)

    # 5. the real machine as this (non-root) user: /dev/mem and the ACPI tables are refused
    nr = os.path.join(a.work, "nonroot")
    shutil.rmtree(nr, ignore_errors=True)
    p, dt = run(["--dump-dir", nr], timeout=90)
    open(os.path.join(a.outdir, "nonroot_live_run.txt"), "w").write(p.stdout + p.stderr)
    check(f"real system, not root: exits 0 in {dt:.1f} s, explains the refusals, writes JSON",
          p.returncode == 0 and "EasyTuneSetupAddress = 0xBCB53018" in p.stdout and "Permission denied" in p.stdout
          and "EACCES: /dev/mem needs root" in p.stdout and os.path.exists(os.path.join(nr, "bios_memcfg.json")),
          p.stdout[-400:])

    # 5b. whole-program fuzz: corrupt APOB / AODT files through the CLI, every section header must still print
    rnd = random.Random(5)
    heads = ["== AOD buffer", "== APOB MEM general", "== PM table", "== APOB entries", "== APOB DDR4 channel timing",
             "== APOB DIMM SPD", "== APOB SMBIOS", "== APOB APCB boot info", "== APOB training status", "== AOD SPD copies",
             "== Gigabyte", "== ACPI NVS"]
    bad = []
    for i in range(40):
        t = bytearray(sysd["apob"])
        if i % 4 == 0:
            struct.pack_into("<II", t, 8, rnd.getrandbits(32), rnd.getrandbits(32))     # TableSize / HeaderSize
        for _ in range(rnd.choice([1, 16, 512])):
            t[rnd.randrange(len(t))] = rnd.getrandbits(8)
        t = t[:rnd.choice([len(t), len(t) // 2, 200, 17])]
        r = bytearray(aodt_buffer())
        for _ in range(rnd.choice([0, 64, 4096])):
            r[rnd.randrange(len(r))] = rnd.getrandbits(8)
        fa, fr = os.path.join(a.work, "fz_apob.bin"), os.path.join(a.work, "fz_aodt.bin")
        open(fa, "wb").write(t)
        open(fr, "wb").write(r[:rnd.choice([len(r), 0xCC + 700, 40])])
        p, _ = run(["--ssdt", ssdt, "--region-file", fr, "--apob-file", fa, "--pm-table", pmt,
                    "--dump-dir", os.path.join(a.work, "fz_dump")])
        missing = [h for h in heads if h not in p.stdout]
        if p.returncode or missing or "Traceback" in p.stderr:
            bad.append((i, p.returncode, missing, p.stderr[-200:]))
    check("CLI fuzz: 40 corrupt APOB/AODT file pairs, exit 0, all 12 section headers printed, no traceback",
          not bad, str(bad[:2]))

    # 6. root without iomem=relaxed: pread() gets EPERM on NVS pages (simulated in-process on the image)
    spec_old = importlib.util.spec_from_file_location("bm_old", old)
    bm_old = importlib.util.module_from_spec(spec_old)
    spec_old.loader.exec_module(bm_old)
    e1 = PermissionError(1, "Operation not permitted")
    check("EPERM hint text unchanged from the pre-change script",
          quiet(bm.explain, e1, 0xA200000) == quiet(bm_old.explain, e1, 0xA200000))
    real_pread = os.pread
    refused = (0xA200000, 0xA20E000), (0xBC9F2000, 0xBCB60000)   # APOB range and the first 1.4 MB of the big range

    def pread(fd, n, off):
        if any(lo <= off < hi for lo, hi in refused):
            raise PermissionError(1, "Operation not permitted")
        return real_pread(fd, n, off)
    eperm_dir = os.path.join(a.work, "eperm")
    shutil.rmtree(eperm_dir, ignore_errors=True)
    args = argparse.Namespace(apob_file=None, region_file=None, devmem=sysd["img"], efivars=efiv, memmap=sysd["memmap"],
                              acpi_dir=sysd["acpi"], no_nvs_dump=False, dump_dir=eperm_dir, ssdt=ssdt, pm_table=pmt)
    bm.DEVMEM = sysd["img"]
    bm.STATE.clear()
    bm.OUT.clear()
    bm.OUT["errors"] = []
    os.pread = pread
    try:
        txt = quiet(bm.apob, args) + quiet(bm.gbt_easy, args) + quiet(bm.nvs_dump, args)
    finally:
        os.pread = real_pread
        bm.DEVMEM = "/dev/mem"
    open(os.path.join(a.outdir, "eperm_simulation.txt"), "w").write(txt)
    big = open(os.path.join(eperm_dir, "nvs_bc9f2000-bd0d8fff.bin"), "rb").read() \
        if os.path.exists(os.path.join(eperm_dir, "nvs_bc9f2000-bd0d8fff.bin")) else b""
    check("EPERM simulation: a refused APOB gives the hint, a partly refused NVS range is saved zero-filled, "
          "EasyHealth is still dumped",
          "APOB not found/readable" in txt and "Boot once with kernel arg iomem=relaxed" in txt and
          txt.count("Boot once with kernel arg iomem=relaxed") <= 4 and
          "0xA200000-0xA20DFFF (56 KB): not readable" in txt and "(7068 KB): 366 refused page(s)" in txt and
          len(big) == 0xBD0D9000 - 0xBC9F2000 and big[:0x16E000] == bytes(0x16E000) and
          "EasyHealthAddress = 0xBCB76018" in txt and "easyhealth_bcb76018.bin" in txt and bm.OUT["errors"] == [],
          txt[-600:])

    n_ok = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{n_ok}/{len(RESULTS)} checks passed")
    with open(os.path.join(a.outdir, "test_results.txt"), "w") as f:
        for name, ok, detail in RESULTS:
            f.write(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else "") + "\n")
        f.write(f"\n{n_ok}/{len(RESULTS)} checks passed; APOB fuzz cases {cases}, guard-caught decode errors {guard}\n")
    return 0 if n_ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
