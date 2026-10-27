I checked every item in the earlier report ([umc.md](umc.md)) against ZenStates-Core, ZenTimings, CoreFreq, ryzen_monitor, ryzen_smu and the Linux kernel, all cloned or fetched fresh. All the main bit ranges, offsets and formulas are confirmed. I found five things that are wrong, all in side details rather than the timing bit ranges:
- the "old width" attribution for tFAW and tWR
- which ZenTimings file held the old hard-coded reader
- how RDPRE is displayed
- the claim that the kernel never uses ryzen_smu's index register
- the line cited for the Vermeer voltage address

The bit widths still can't be checked against AMD documents. I downloaded both public PPRs (56214 rev 3.05 for Vermeer and 55803 rev 0.91 for Rome, via the Wayback Machine). Neither describes the UMC channel registers; they only cover the UMC machine-check registers. Nothing was read from live hardware.

Sources: ZenStates-Core (ZSC) @869b7e8, ZenTimings (ZT) @7532394, CoreFreq (CF) @20de51c, ryzen_monitor (RM) @7529c7c, ryzen_smu (RS) @d298366 (version 0.1.7), Linux (LX) master fetched 2026-10-24. "DDR4Dict" means `ZSC Dictionaries/DDR4Dictionary.cs`; "CF" means `CF x86_64/amd_reg.h` unless a file is named.

## WRONG

1. **Old tFAW / tWR width [7:0].** This width came only from the old ZSC dictionary (before fc250cb, for example at d08e0ac). The hard-coded ZenTimings reader always used [6:0], i.e. `GetBits(x,0,7)`:
   - `ZT WPF-no-themes/MainWindow.xaml.cs:569,575`
   - `ZT WPF/MainWindow.xaml.cs` at ca4c163, lines 584 and 590
   
   The other old widths are correct in both places: RDRDSCL [29:24], RDWR [13:8], STAG [26:16], PHYWRL [15:8], PHYRDL [23:16] (no-themes lines 582, 589, 599, 604-605).
2. **"WPF-no-themes/MainWindow.xaml.cs, last at ca4c163".** The no-themes file still has `ReadTimings` at HEAD (lines 484-638). It is the themed `WPF/MainWindow.xaml.cs` that last had it at ca4c163; it was removed in e3bdf6b (2025-09-20).
3. **"RDPRE shown as raw+1".** RDPRE is shown as raw+1 only when raw < 2, otherwise as raw (`BaseDramTimings.cs:342-351`). WRPRE is always raw+1 (`:336-340`), as stated.
4. **"The kernel's SMN traffic doesn't touch ryzen_smu's index register".** This is incomplete:
   - `LX amd_node.c:45-46,138-140` defines `HSMP_INDEX/DATA` as 0xC4/0xC8, the same pair ryzen_smu uses (`RS smu.h:31-32`).
   - The kernel's `amd_smn_hsmp_rdwr()` uses that pair under the kernel's own mutex, not ryzen_smu's.
   - `amd_smn_read`/`amd_smn_write` do use 0x60/0x64 (`amd_node.c:42-43,121,134`).
   - A collision needs the hsmp module loaded (check `lsmod`); it was not loaded on the 5800X3D system checked.
5. **Line cite `Cpu.cs:1450-1458`.** Lines 1450-1451 are the APU case. The Vermeer case is 1455-1459 and Matisse is 1430-1433. Both give the SoC plane 0x5A00C (`Constants.cs:17,21`), so the address itself is correct.

## CONFIRMED

**UMC addressing and helpers**
- UMC base `i<<20 | 0x50xxx`: `ZSC MemoryConfig.cs:737`, `LX amd64_edac.h:405-409`, `CF:1624`.
- Up to 12 UMCs probed, stopping once all SMBIOS modules are assigned: `MemoryConfig.cs:24,730-735`.
- CF lists timing copies at 0x50{200,300,400,500} (`CF:1988` etc.). CF never says these are memory P-states; that reading is an inference.
- `GetBits` (`Utils.cs:54-60`) and `BitSlice` (`Utils.cs:64-73`) work as described.

**Register table.** Every offset and bit range matches DDR4Dict and agrees with CF:

| Register(s) | DDR4Dict lines | CF lines |
|---|---|---|
| 0x50100 | 26-30 | 1740-1744 |
| 0x5012C PowerDown / Mode / Aggr | 31-35 | 1803-1805 |
| 0x50200 Preamble2t / GDM / Cmd2T / BankGroupEn | 46-52 | 1996-1999; CF Cmd2T is [10:9] with 0b10 = 2T |
| 0x50204 | 53-58 | 2018-2024 |
| 0x50208 | 59-62 | 2035-2037 |
| 0x5020C | 63-67 | 2050-2056 |
| 0x50210 tFAW [6:0] | 68-70 | 2066 |
| 0x50214 | 71-75 | 2080-2084 |
| 0x50218 tWR [6:0] | 76-78 | 2094 |
| 0x5021C | 79-81 | 2105 |
| 0x50220 | 82-88 | 2114-2122 |
| 0x50224 | 89-95 | 2131-2138 |
| 0x50228 | 96-99 | 2147-2149 |
| 0x5022C | 100-105 | 2161-2165 |
| 0x50230 | 106-108 | 2174 |
| 0x50234 | 109-114 | 2184-2190 |
| 0x50238 | 115-118 | 2200-2202 |
| 0x50250 tSTAG [23:16] | 130-132 | 2242 |
| 0x50254 | 133-137 | 2252-2256 |
| 0x50258 | 138-144 | 2266-2274 |
| 0x5028C | 151-155 | 2313-2316 |
| 0x502A4 | 165-168 | — |
| 0x50DF0 / 0x50DF4 | 169-174 | bit 19 MemChannel_Dis at 1969 |

The earlier script's FIELDS list matches all of these.

**Ratio, refresh and tRFC**
- Ratio `GetBits(reg,0,7)/3` (`Ddr4Timings.cs:71`) and RM masks with 0x7f (`readinfo.c:190`).
- CF uses MEMCLK [5:0] (`CF:1993`). This discrepancy is real but doesn't matter at DDR4-3733 (raw value 56).
- FGR is [18:16] (`Ddr4Timings.cs:92-93`), mapped to 1x/2x/4x at `:21-33`, and the active tRFC(ns) is chosen at `:42-54` (CF field at `:1799`).
- tRFC selection and the [10:0]/[21:11]/[31:22] fields: `Ddr4Timings.cs:79-88`, CF `2286-2289`.
- 0x21060138 decodes to 312/192/132 (I computed it).
- RM uses the same selection rule (`readinfo.c:233-235`), but masks tRFC and tRFC2 with 0x3ff (10 bits, `:238-240`). RM's tFAW, tWR and tWTRL masks also differ (`:208,211,215`); RM is the outlier.
- The DIMM0/DIMM1 naming of 0x50260/0x50264 comes only from a CF comment (`CF:2280-2283`).
- Old ZenTimings read 0x50268/0x5026C only for DDR5 (no-themes `:513-514,522-533`).

**Bank group swap**
- BGS logic (`BaseDramTimings.cs:220`; RM `:180-182`; CF `:1627-1648`). CF's sample dumps show only 0x50058 changing between BGS on and off.
- BGS Alt [10:4] (`BaseDramTimings.cs:221`; CF mask 0x7F0 at `:1673`).

**Panel list** (`ZT DDR4TimingsPanel.xaml:28-117`): correct. The panel also has ProcOdt Pu/Pd rows (`:103-104`).

**Speed and BCLK**
- Speed calculation: `BaseDramTimings.cs:249,256-271`; ZT displays the floor (`MainViewModel.cs:46,97-104`).
- PM-table clocks scaled by BCLK/100: `PowerTable.cs:632-645`.
- BCLK is read from 0xFED80E10 with index = bits [12:4] and fraction = bits [28:25] × 0.0625 (`Mmio.cs:11-27,314-318`).
- Correction: the index-to-MHz function is an XOR — `index<32 ? index^100 : index^164` (`Mmio.cs:93-98`).
- It returns null when an external clock generator runs in async eCLK mode (`:311-312`).
- ns = clk × 2000 / MT/s (`Utils.cs:475-480`, `BaseDramTimings.cs:283-287,355-356`).

**Population and memory type**
- Channel enabled: 0x50DF0 bit 19 = 0 and (0x50000 bit 0 or 0x50008 bit 0) (`MemoryConfig.cs:738-741`).
- Slot labels: `:750-771`.
- Rank count from chip selects 2d and 2d+1 (`:675-689`; `LX amd64_edac.h:162,167` and `amd64_edac.c:1201-1213,1447-1470`).
- Secondary chip-select base at 0x10 (`LX .h:256`); the kernel uses it in its CS-mode logic (`.c:1212-1215`), ZSC doesn't.
- Memory type comes from SMBIOS (`MemoryConfig.cs:319-334,662`). The 0x50100 &3 line is commented out (`:20-22,172-173`). Old ZT used [1:0] (no-themes `:488`).
- The kernel treats UMC_CFG[2:0]==1 as DDR5 only on zn_regs_v2 parts (`LX .c:1570`). Family 19h model 21h falls in no case of `:3846-3887`, so it stays DDR4.
- DIMM_CFG bits: `MemoryModule.cs:104-114`, `LX .c:1571-1582`, `CF:1719-1726`.
- SdpInit: `LX .c:1561`, `CF corefreqk.c:7158-7160`.
- RM switches to UMC1 when 0x50200 == 0x300 (`readinfo.c:177-178`; the offset is applied in the macro at `:31-32`).

**Address config discrepancy**
- ZSC reads 0x50040/0x50048 (`MemoryConfig.cs:758,769`).
- CF uses 0x50030/0x50034 on Zen2/Zen3 (`CF:1675-1679`; `corefreqk.c:7245,7341`, which passes 0x30 as `UMC_DAC`, used at `:7000`).
- Which one is right is unverified.

**Voltages and PM table**
- VSOC = 1.55 − 0.00625 × VID, VID at [23:16] (`ZT SVI2Plugin.cs:78,85-86`; `Utils.cs:109-112`; `CF:2394,2401`). ZT re-reads while bits [15:8] are non-zero (`SVI2Plugin.cs:57`).
- PM table rows: `PowerTable.cs:285` (0x240903) and `:300` (0x380905, size 0x5D0).
- An example pm_table dump (5800X3D at DDR4-3733) reads, at these offsets: 0xCC = 1867.2683, 0xC0 = 0xC8 = 1867.2683, 0xB4 = 1.11294, 0x224 = 0.89728, 0x228 = 0x22C = 0.99759. That gives floor(2 × 1867.27) = 3734 MT/s at a BCLK correction of 1.0.

**ryzen_smu access**
- A 4-byte write means read, an 8-byte write means SMN write (`RS drv.c:238-261`).
- The access is index/data under the driver's mutex (`smu.c:88-108`).
- The result is one global value (`drv.c:231-236`).
- The earlier reader's sequence is the same as the reference `lib/libsmu.c:255-277`.

**ProcODT / RTT / drive strengths**
- No SMN path exists in ZSC (only APOB/AOD files mention ProcOdt) or in CF (no hits).
- ZT order: APOB first, then WMI "Get APCB Config" (`ZT WPF/MainWindow.xaml.cs:810-835,909-920`). The raw AOD table is used only when that WMI function is missing (`:913-916`); it is a fallback inside the WMI path, not a separate third step.
- APOB addresses: `Apob.cs:95`. Layout: `ApobMemGeneralConfig.cs:52,77-81,107-127`, with one 8-byte (one byte per channel) array per field at data+0xA4.
- WMI byte offsets: `BiosMemController.cs:84-97`.

## UNVERIFIED

- Every UMC bit width against AMD. Only ZSC and CF agree with each other; the public PPRs have no UMC channel registers.
- MEMCLK bit 6.
- Whether address config lives at 0x30 or 0x40 on Zen3.
- The actual BCLK on a running system (Linux has no Mmio path; 100 MHz is assumed).
- How ZSC slot labels map to the B550 AORUS PRO silkscreen.
- Whether Linux `/dev/mem` allows reading the APOB.

## Other findings

- **Two DIMMs per channel.** With 4 single-rank modules (Channel A and B, DIMM 0 and DIMM 1 in SMBIOS), both 0x50260 and 0x50264 should be programmed.
- **Failed reads are silent.** If an SMN read fails, ryzen_smu keeps the previous value and still accepts the write (`drv.c:247-248`), so a failure can't be detected from userspace.
- **`smn` is world-readable.** Its mode is 0644 (`drv.c:57-59,283`): readable by anyone, writable only by root.

## Corrected decode function

It passed round-trip tests on synthetic register values, not on live hardware. ctune's maintained copy is `ctune/mem/platforms/zen3_am4/zen3_ddr4_decode.py`. Run it as root with `sudo python3 -I zen3_ddr4_decode.py [pm_mclk_mhz] [bclk_mhz]`; pass 1867.2683 as the PM-table MCLK to reproduce ZenTimings' 3734 MT/s.

```python
#!/usr/bin/env python3
"""Decode Zen2/Zen3 (Matisse/Vermeer, e.g. Ryzen 7 5800X3D) DDR4 UMC registers read over SMN.

Input : dict {absolute_smn_address: u32}. UMC n base = 0x50000 + (n << 20)
        (ZenStates-Core MemoryConfig.cs:737; Linux amd64_edac.h:405-409).
Source of the bit map: irusanov/ZenStates-Core @869b7e8 Dictionaries/DDR4Dictionary.cs:26-174,
Hardware/DRAM/Ddr4Timings.cs, BaseDramTimings.cs, MemoryConfig.cs; cross-checked field by field with
cyring/CoreFreq @20de51c x86_64/amd_reg.h:1786-2319. AMD's public PPRs (56214 rev 3.05, 55803 rev 0.91)
do not document UMC::CH registers, so no width below is AMD-verified.
Only P-state-0 timing set (0x502xx) is decoded, as ZenTimings does.
"""

UMC_STRIDE = 1 << 20
TRFC_UNPROGRAMMED = 0x21060138      # Ddr4Timings.cs:81 (decodes to 312/192/132)
BGS_OFF_PATTERN = 0x87654321        # BaseDramTimings.cs:220
SVI2_SOC_PLANE = 0x5A00C            # Constants.cs:21 + Cpu.cs:1455-1459 (Vermeer); Matisse same (1430-1433)


def bits(v, hi, lo):
    """ZenStates-Core Utils.BitSlice(v, hi, lo) (Utils.cs:64-73)."""
    return (v >> lo) & ((1 << (hi - lo + 1)) - 1)


# (offset, key, hi, lo). Order/widths = DDR4Dictionary.cs @869b7e8 (post-fc250cb). Keys use the ZenTimings
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
    """DIMM_CFG bits: MemoryModule.cs:104-114 (= CoreFreq AMD_17_UMC_DIMM_CFG, amd_reg.h:1710-1732)."""
    names = ("OnDimmMirror", "OutputInvert", "Dram3DS", "CIsCS", "RDIMM", "LRDIMM", "X4Dram", "X16Dram",
             "DqMapSwapDis", "DimmRefDis", "PkgRnkTimingAlign")
    return {n: bool((v >> i) & 1) for i, n in enumerate(names)}


def decode_umc(regs, ch, pm_mclk_mhz=None, bclk_mhz=100.0):
    """Decode one UMC. regs: {abs_smn_addr: u32}. pm_mclk_mhz: PM-table MCLK (0xCC on table 0x380905,
    PowerTable.cs:300); if given, speed = 2*MCLK*bclk/100 like ZenTimings (BaseDramTimings.cs:262-265 +
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

    # Ratio / speed: Ddr4Timings.cs:67-73 (bits [6:0] / 3). CoreFreq declares only [5:0] (amd_reg.h:1993);
    # the two agree for raw <= 63 (<= DDR4-4200 at 100 MHz BCLK).
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
```

Web sources used:
- [AMD 56214 PPR zip](https://www.amd.com/content/dam/amd/en/documents/processor-tech-docs/programmer-references/56214-B0-PUB.zip)
- [AMD 55803 PPR (Wayback)](https://web.archive.org/web/2024id_/https://www.amd.com/content/dam/amd/en/documents/processor-tech-docs/programmer-references/55803-ppr-family-17h-model-31h-b0-processors.pdf)
- [55803 listing](https://www.amd.com/content/dam/amd/en/documents/processor-tech-docs/programmer-references/55803-ppr-family-17h-model-31h-b0-processors.pdf)
- [lore.kernel.org EDAC thread](https://lore.kernel.org/all/YfviHFrzzdapbwDt@zn.tnic/t/)
- [Milan PPR cite in QEMU commit](https://git.cs.tu-dortmund.de/SYS-OSS/FRET-qemu/commit/27f03be6f59d04bd5673ba1e1628b2b490f9a9ff)
- [kernel bugzilla 206537](https://bugzilla.kernel.org/show_bug.cgi?id=206537)