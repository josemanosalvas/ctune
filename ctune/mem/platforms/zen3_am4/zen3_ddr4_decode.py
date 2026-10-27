#!/usr/bin/env python3
"""Decode Zen2 and Zen3 (Matisse, Vermeer, for example the Ryzen 7 5800X3D) DDR4 UMC registers read over SMN.

Input : dict {absolute_smn_address: u32}. UMC n base = 0x50000 + (n << 20)
        (ZenStates-Core MemoryConfig.cs:737; Linux amd64_edac.h:405-409).
Source of the bit map: irusanov/ZenStates-Core @869b7e8 Dictionaries/DDR4Dictionary.cs:26-174,
Hardware/DRAM/Ddr4Timings.cs, BaseDramTimings.cs, MemoryConfig.cs; cross-checked field by field with
cyring/CoreFreq @20de51c x86_64/amd_reg.h:1786-2319. AMD's public PPRs (56214 rev 3.05, 55803 rev 0.91)
do not document UMC::CH registers, so no width below is AMD-verified.
It decodes only the P-state-0 timing set (0x502xx), as ZenTimings does.
"""

UMC_STRIDE = 1 << 20
TRFC_UNPROGRAMMED = 0x21060138      # Ddr4Timings.cs:81 (decodes to 312/192/132)
BGS_OFF_PATTERN = 0x87654321        # BaseDramTimings.cs:220
SVI2_SOC_PLANE = 0x5A00C            # Constants.cs:21 and Cpu.cs:1455-1459 (Vermeer); the same on Matisse (1430-1433)


def bits(v, hi, lo):
    """ZenStates-Core Utils.BitSlice(v, hi, lo) (Utils.cs:64-73)."""
    return (v >> lo) & ((1 << (hi - lo + 1)) - 1)


# (offset, key, hi, lo). Order and widths follow DDR4Dictionary.cs @869b7e8 (after fc250cb). Keys use the ZenTimings
# DDR4 panel label when the field is shown there (DDR4TimingsPanel.xaml:28-86), else the ZSC property name.
DDR4_FIELDS = (
    (0x50100, "DimmEccEn", 12, 12), (0x50100, "BurstCtrl", 11, 10), (0x50100, "BurstLength", 9, 8),
    (0x5012C, "AggrPwrDownEn", 30, 30), (0x5012C, "PowerDownMode", 29, 29), (0x5012C, "PowerDown", 28, 28),
    (0x50130, "OdtsIncRefEn", 28, 28), (0x50130, "OdtsEn", 27, 27), (0x50130, "ForcePwrDownThrotEn", 26, 26),
    (0x50130, "OdtsCmdThrotEn", 25, 25), (0x50130, "SwCmdThrotEn", 24, 24), (0x50130, "OdtsCmdThrotCyc", 23, 16),
    (0x50130, "SwCmdThrotCyc", 15, 8), (0x50130, "RollWindowDepth", 7, 0),
    (0x50200, "Preamble2t", 12, 12), (0x50200, "GDM", 11, 11), (0x50200, "Cmd2T", 10, 10),
    (0x50200, "BankGroupEn", 8, 8),
    (0x50204, "tRCDWR", 29, 24), (0x50204, "tRCDRD", 21, 16), (0x50204, "tRAS", 14, 8), (0x50204, "tCL", 5, 0),
    (0x50208, "tRC", 7, 0), (0x50208, "tRP", 21, 16),
    (0x5020C, "tRTP", 28, 24), (0x5020C, "tRRDL", 12, 8), (0x5020C, "tRRDS", 4, 0),
    (0x50210, "tFAW", 6, 0),
    (0x50214, "tWTRL", 22, 16), (0x50214, "tWTRS", 12, 8), (0x50214, "tCWL", 5, 0),
    (0x50218, "tWR", 6, 0),
    (0x5021C, "TRCPAGE", 31, 20),
    (0x50220, "RDRDBan", 31, 30), (0x50220, "tRDRDSCL", 27, 24), (0x50220, "tRDRDSC", 19, 16),
    (0x50220, "tRDRDSD", 11, 8), (0x50220, "tRDRDDD", 3, 0),
    (0x50224, "WRWRBan", 31, 30), (0x50224, "tWRWRSCL", 29, 24), (0x50224, "tWRWRSC", 19, 16),
    (0x50224, "tWRWRSD", 11, 8), (0x50224, "tWRWRDD", 3, 0),
    (0x50228, "tRDWR", 12, 8), (0x50228, "tWRRD", 3, 0),
    (0x5022C, "ShortInit", 31, 31), (0x5022C, "ZqcsInterval", 29, 20), (0x5022C, "TzqOperCal", 19, 8),
    (0x5022C, "Tzqcs", 7, 0),
    (0x50230, "tREFI", 15, 0),
    (0x50234, "tMODPDA", 29, 24), (0x50234, "tMRDPDA", 21, 16), (0x50234, "tMOD", 13, 8), (0x50234, "tMRD", 5, 0),
    (0x50238, "DLLK", 26, 16), (0x50238, "XS", 10, 0),
    (0x5023C, "RankBusyDly", 30, 24), (0x5023C, "CmdParLatency", 19, 16), (0x5023C, "AlertParDly", 14, 8),
    (0x5023C, "AlertCrcDly", 6, 0),
    (0x50244, "AggrPwrDownDly", 30, 25), (0x50244, "PwrDownDly", 24, 17), (0x50244, "PD", 4, 0),
    (0x50250, "tSTAG", 23, 16),
    (0x50254, "tCKE", 28, 24), (0x50254, "CPDED", 19, 16), (0x50254, "XP", 5, 0),
    (0x50258, "PARINL", 29, 28), (0x50258, "tPHYWRD", 26, 24), (0x50258, "tPHYRDL", 21, 16),
    (0x50258, "tPHYWRL", 12, 8), (0x50258, "RDDATAEN", 6, 0),
    (0x5025C, "TgearHold", 30, 28), (0x5025C, "TgearSetup", 26, 24), (0x5025C, "LpExitDly", 13, 8),
    (0x5025C, "LpDly", 5, 0),
    (0x5028C, "WRMPR", 29, 24), (0x5028C, "CmdStgCnt", 21, 11), (0x5028C, "RcvrWait", 10, 0),
    (0x502A4, "WRPRE_raw", 10, 8), (0x502A4, "RDPRE_raw", 2, 0),
    (0x50DF0, "DdrMaxRate", 7, 0),
    (0x50DF4, "DdrMaxRateEnf", 7, 0),
)

# Other per-UMC offsets the decoder uses (MemoryConfig.cs:738-740, 675-689; BaseDramTimings.cs:210-213;
# Ddr4Timings.cs:79-80; DIMM_CFG: MemoryConfig.cs:753/764, MemoryModule.cs:102-115, amd64_edac.h:260).
EXTRA_OFFSETS = (0x50000, 0x50004, 0x50008, 0x5000C, 0x50050, 0x50058, 0x500D0, 0x500D4,
                 0x50080, 0x50084, 0x50260, 0x50264)


def required_smn_addresses(channels=(0, 1), include_svi2=True):
    """Absolute SMN addresses to read (4-byte write of the address to ryzen_smu 'smn', then 4-byte read)."""
    offs = sorted({o for o, *_ in DDR4_FIELDS} | set(EXTRA_OFFSETS))
    addrs = [(ch * UMC_STRIDE) | o for ch in channels for o in offs]
    return addrs + ([SVI2_SOC_PLANE] if include_svi2 else [])


def _dimm_cfg(v):
    """DIMM_CFG bits: MemoryModule.cs:104-114 (the same as CoreFreq AMD_17_UMC_DIMM_CFG, amd_reg.h:1710-1732)."""
    names = ("OnDimmMirror", "OutputInvert", "Dram3DS", "CIsCS", "RDIMM", "LRDIMM", "X4Dram", "X16Dram",
             "DqMapSwapDis", "DimmRefDis", "PkgRnkTimingAlign")
    return {n: bool((v >> i) & 1) for i, n in enumerate(names)}


def decode_umc(regs, ch, pm_mclk_mhz=None, bclk_mhz=100.0):
    """Decode one UMC. regs: {abs_smn_addr: u32}. pm_mclk_mhz: PM-table MCLK (0xCC on table 0x380905,
    PowerTable.cs:300); if given, speed = 2*MCLK*bclk/100 like ZenTimings (BaseDramTimings.cs:262-265 and
    PowerTable.cs:632-643), else 2*Ratio*BCLK (BaseDramTimings.cs:268-269)."""
    base = ch * UMC_STRIDE

    def r(off):
        return regs.get(base | off)

    out = {"umc": ch, "smn_base": hex(base | 0x50000), "missing": []}

    # Channel/DIMM population: MemoryConfig.cs:738-741 (DIMM n present = CS 2n base bit 0)
    cap, cs = r(0x50DF0), [r(0x50000 + 4 * i) for i in range(4)]
    if cap is None or cs[0] is None or cs[2] is None:
        out["enabled"] = None
        out["missing"] = [hex(base | o) for o in (0x50DF0, 0x50000, 0x50008) if r(o) is None]
        return out
    cs_en = [None if v is None else v & 1 for v in cs]
    out["MemChannel_Dis"] = bits(cap, 19, 19)                     # CoreFreq amd_reg.h:1969
    out["enabled"] = out["MemChannel_Dis"] == 0 and bool(cs_en[0] or cs_en[2])
    dimms = []
    for d in (0, 1):
        present = bool(cs_en[2 * d])
        e = {"slot_zsc_label": f"{chr(65 + ch)}{d + 1}", "present": present}
        if present:
            # Rank count: CS 2d and 2d+1 bit 0 (MemoryConfig.cs:682-688; amd64_edac.c:1206-1210)
            odd = cs_en[2 * d + 1]
            e["ranks"] = None if odd is None else 1 + odd
            dc = r(0x50080 + 4 * d)
            e["dimm_cfg"] = None if dc is None else _dimm_cfg(dc)
        dimms.append(e)
    out["dimms"] = dimms
    if not out["enabled"]:
        return out

    for off, key, hi, lo in DDR4_FIELDS:
        v = r(off)
        if v is None:
            out[key] = None
            if hex(base | off) not in out["missing"]:
                out["missing"].append(hex(base | off))
        else:
            out[key] = bits(v, hi, lo)

    # Display conversions as ZenStates-Core does them
    if out["Cmd2T"] is not None:
        out["Cmd2T"] = "2T" if out["Cmd2T"] else "1T"             # BaseDramTimings.cs:39-44
    for k in ("GDM", "PowerDown"):
        if out[k] is not None:
            out[k] = "Enabled" if out[k] else "Disabled"           # BaseDramTimings.cs:18-23
    if out["WRPRE_raw"] is not None:
        out["WRPRE"] = out["WRPRE_raw"] + 1                        # BaseDramTimings.cs:336-340
    if out["RDPRE_raw"] is not None:
        rp = out["RDPRE_raw"]
        out["RDPRE"] = rp + 1 if rp < 2 else rp                    # BaseDramTimings.cs:342-351

    # Ratio and speed: Ddr4Timings.cs:67-73 (bits [6:0] / 3). CoreFreq declares only [5:0] (amd_reg.h:1993);
    # the two agree for raw values up to 63 (up to DDR4-4200 at 100 MHz BCLK).
    misc = r(0x50200)
    if misc is not None:
        raw = bits(misc, 6, 0)
        out["ratio_raw"] = raw
        out["Ratio"] = raw / 3.0
        out["MEMCLK_MHz_from_ratio"] = raw / 3.0 * bclk_mhz
    if pm_mclk_mhz:
        mts = 2.0 * pm_mclk_mhz * (bclk_mhz / 100.0)
        out["speed_source"] = "pm_table_mclk"
    elif misc is not None:
        mts = 2.0 * out["Ratio"] * bclk_mhz
        out["speed_source"] = "ratio_x_bclk"
    else:
        mts = 0.0
        out["speed_source"] = None
    out["MT_s"] = mts
    out["MT_s_zentimings_display"] = int(mts // 1)                 # MainViewModel.cs:103 (Math.Floor)

    def ns(clk):                                                   # Utils.cs:475-480 / BaseDramTimings.cs:283-287
        return clk * 2000.0 / mts if (mts > 0 and clk is not None) else None

    # tRFC: Ddr4Timings.cs:79-88
    t0, t1 = r(0x50260), r(0x50264)
    out["tRFC_reg_dimm0"] = None if t0 is None else hex(t0)        # DIMM0/DIMM1 naming: CoreFreq amd_reg.h:2280-2283 only
    out["tRFC_reg_dimm1"] = None if t1 is None else hex(t1)
    if t0 is None or t1 is None:
        out["tRFC"] = out["tRFC2"] = out["tRFC4"] = None
    else:
        sel = (t0 if t0 != TRFC_UNPROGRAMMED else t1) if t0 != t1 else t0
        if sel != 0:
            out["tRFC"], out["tRFC2"], out["tRFC4"] = bits(sel, 10, 0), bits(sel, 21, 11), bits(sel, 31, 22)
        else:
            out["tRFC"] = out["tRFC2"] = out["tRFC4"] = 0           # ZSC leaves them at their default 0

    # Fine granularity refresh: 0x5012C[18:16] (Ddr4Timings.cs:90-96, 21-33)
    spaz = r(0x5012C)
    if spaz is not None:
        fgr = bits(spaz, 18, 16)
        mult = {0: 1, 1: 2, 5: 2, 2: 4, 6: 4}.get(fgr, 0)
        out["FGR"], out["FgrMultiplier"], out["FgrOnTheFly"] = fgr, mult, fgr in (5, 6)
        out["Refresh"] = "NORMAL" if fgr == 0 else "FGR"
        active = {1: out.get("tRFC"), 2: out.get("tRFC2"), 4: out.get("tRFC4")}.get(mult)
        out["tRFC_ns"] = ns(active) if mult else 0.0                # Ddr4Timings.cs:42-54
    out["tREFI_ns"] = ns(out.get("tREFI"))                          # BaseDramTimings.cs:356

    # Bank group swap: BaseDramTimings.cs:207-222
    b0, b1, a0, a1 = r(0x50050), r(0x50058), r(0x500D0), r(0x500D4)
    if None in (b0, b1, a0, a1):
        out["BGS"] = out["BGSAlt"] = None                          # ZSC also skips both if any read fails
    else:
        out["BGS"] = "Disabled" if (b0 == BGS_OFF_PATTERN and b1 == BGS_OFF_PATTERN) else "Enabled"
        out["BGSAlt"] = "Enabled" if (bits(a0, 10, 4) or bits(a1, 10, 4)) else "Disabled"
    return out


def decode_vsoc(regs):
    """VSOC from SVI2 telemetry 0x5A00C, VID [23:16], V = 1.55 - 0.00625*VID
    (ZenTimings WPF/Plugin/SVI2Plugin.cs:78,85-86; ZSC Utils.cs:109-112). ZenTimings re-reads while
    bits [15:8] of both planes are non-zero (SVI2Plugin.cs:57); this only reports that flag."""
    v = regs.get(SVI2_SOC_PLANE)
    if v is None:
        return None
    vid = bits(v, 23, 16)
    return {"raw": hex(v), "vid": vid, "VSOC_V": round(1.55 - 0.00625 * vid, 5),
            "bits15_8_nonzero": bits(v, 15, 8) != 0}


def decode_all(regs, channels=(0, 1), pm_mclk_mhz=None, bclk_mhz=100.0):
    return {"umcs": [decode_umc(regs, ch, pm_mclk_mhz, bclk_mhz) for ch in channels],
            "vsoc_svi2": decode_vsoc(regs)}


if __name__ == "__main__":
    # Live use (root): python3 -I zen3_ddr4_decode.py [pm_mclk_mhz] [bclk_mhz]
    import os, struct, sys, json

    def read_all(addrs, path="/sys/kernel/ryzen_smu_drv/smn"):
        # Same sequence as ryzen_smu lib/libsmu.c:255-277. Exactly 4 bytes: 8 bytes is an SMN WRITE
        # (drv.c:250-261). A failed read leaves the previous result in place (drv.c:247-248).
        fd = os.open(path, os.O_RDWR)
        try:
            out = {}
            for a in addrs:
                os.lseek(fd, 0, os.SEEK_SET)
                if os.write(fd, struct.pack("<I", a)) != 4:
                    raise OSError(f"smn address write failed for {a:#x}")
                os.lseek(fd, 0, os.SEEK_SET)
                b = os.read(fd, 4)
                if len(b) != 4:
                    raise OSError(f"smn read failed for {a:#x}")
                out[a] = struct.unpack("<I", b)[0]
            return out
        finally:
            os.close(fd)

    pm = float(sys.argv[1]) if len(sys.argv) > 1 else None
    bclk = float(sys.argv[2]) if len(sys.argv) > 2 else 100.0
    regs = read_all(required_smn_addresses())
    print(json.dumps({"raw": {hex(k): hex(v) for k, v in sorted(regs.items())},
                      "decoded": decode_all(regs, pm_mclk_mhz=pm, bclk_mhz=bclk)}, indent=1))
