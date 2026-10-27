# DDR4 SMN register map used by ZenTimings / ZenStates-Core on Zen2/Zen3 (Matisse/Vermeer)

The ryzen_smu `smn` node is root-only and this research ran without root, so everything below comes from source code and none of it was checked against live hardware. I also couldn't get the AMD PPRs (56214, 55803): the AMD URL returns a 404, and the bugzilla mirror is behind a bot-block. The bit widths below are therefore not checked against AMD documents. I cross-checked them against CoreFreq instead.

**Sources** (pinned commits):
- **ZSC** = irusanov/ZenStates-Core @869b7e8 (2026-10-05)
- **ZT** = irusanov/ZenTimings @7532394 (2026-10-09)
- **CF** = cyring/CoreFreq @20de51c (`x86_64/amd_reg.h`, `corefreqk.c`)
- **RM** = hattedsquirrel/ryzen_monitor @7529c7c (`src/readinfo.c`)
- **RS** = amkillam/ryzen_smu @d298366
- **LX** = torvalds/linux master, fetched 2026-10-24 (`drivers/edac/amd64_edac.{c,h}`, `arch/x86/kernel/amd_node.c`)

## UMC base addresses (confirmed)
- **Base formula:** `offset = i << 20`, and every read is `offset | 0x50xxx`. So UMC0 starts at 0x050000 and UMC1 at 0x150000 (ZSC `Hardware/DRAM/MemoryConfig.cs:737`).
- **Independent confirmation:** LX `amd64_edac.h:405-409` (`get_umc_base = 0x50000 + (ch<<20)`) and CF `amd_reg.h:1624`.
- **Channels probed:** ZSC probes up to 12 UMCs (`MAX_CHANNELS`, `MemoryConfig.cs:24`). It stops once every SMBIOS module has been assigned to a slot.
- **Memory P-state:** ZSC only reads the P-state-0 timing set at 0x502xx. CF notes copies at 0x50300, 0x50400 and 0x50500 (`amd_reg.h` DTR comments). ZenTimings doesn't use those copies.
- **Bit helpers:** `Utils.BitSlice(v,hi,lo)` = `(v>>lo) & ((1<<(hi-lo+1))-1)` (ZSC `Utils.cs:64-73`). `GetBits(v,off,n)` = `(v>>off) & mask(n)` (`Utils.cs:54-60`).

## Register table
Addresses are absolute for UMC0; add 0x100000 for UMC1. Bit ranges are the current DDR4 map in ZSC `Dictionaries/DDR4Dictionary.cs:26-174`, applied by `BaseDramTimings.Read()` (`BaseDramTimings.cs:224-242`). The "Old width" column gives the width before commit fc250cb (2026-09-28), which older ZenTimings builds use. Those builds are the ones with the hard-coded `ReadTimings` in `WPF-no-themes/MainWindow.xaml.cs`, last at ZT ca4c163. "CF" in a cell means CoreFreq shows the same field.

| Item | SMN | Bits | Formula / notes | Old width |
|---|---|---|---|---|
| MEMCLK ratio | 0x50200 | [6:0] | `Ratio = raw/3`. MEMCLK (MHz) = Ratio × BCLK. MT/s = 2 × Ratio × BCLK (`Ddr4Timings.cs:67-73`). RM masks it the same way (0x7F, `readinfo.c:188-192`). **CF says [5:0]** (`amd_reg.h:1993`); see the discrepancies section. | same |
| BankGroupEn | 0x50200 | [8] | | |
| Cmd2T | 0x50200 | [10] | 1 = 2T, 0 = 1T. CF treats [10:9] as CMD_Rate, with 0b10 = 2T (`amd_reg.h:1997`), so the two agree. | |
| GearDownMode (GDM) | 0x50200 | [11] | 1 = Enabled | |
| Preamble2t | 0x50200 | [12] | | |
| PowerDown | 0x5012C | [28] | PowerDownMode is [29], AggrPwrDownEn is [30] (CF `amd_reg.h:1799-1808`) | |
| FGR refresh mode | 0x5012C | [18:16] | 0 = 1x, 1 = 2x, 2 = 4x, 5 = on-the-fly 1x/2x, 6 = on-the-fly 1x/4x (`Ddr4Timings.cs:90-96`, `21-33`) | |
| tCL / tRAS / tRCDRD / tRCDWR | 0x50204 | [5:0] / [14:8] / [21:16] / [29:24] | CF | |
| tRC / tRP | 0x50208 | [7:0] / [21:16] | CF | |
| tRRDS / tRRDL / tRTP | 0x5020C | [4:0] / [12:8] / [28:24] | CF | |
| tFAW | 0x50210 | [6:0] | CF | [7:0] |
| tCWL / tWTRS / tWTRL | 0x50214 | [5:0] / [12:8] / [22:16] | CF | |
| tWR | 0x50218 | [6:0] | CF | [7:0] |
| tRCPAGE | 0x5021C | [31:20] | Read, but not shown on the DDR4 panel | |
| tRDRDDD / SD / SC / SCL | 0x50220 | [3:0] / [11:8] / [19:16] / [27:24] | RDRDBan is [31:30]. CF | SCL was [29:24] |
| tWRWRDD / SD / SC / SCL | 0x50224 | [3:0] / [11:8] / [19:16] / [29:24] | WRWRBan is [31:30]. CF | |
| tWRRD / tRDWR | 0x50228 | [3:0] / [12:8] | CF | tRDWR was [13:8] |
| tZQCS / tZQOPER / ZqcsInterval / ShortInit | 0x5022C | [7:0] / [19:8] / [29:20] / [31] | Not displayed | |
| tREFI | 0x50230 | [15:0] | ns = REFI × 2000 / MT/s (`BaseDramTimings.cs:283-287`, `356`) | |
| tMRD / tMOD / tMRDPDA / tMODPDA | 0x50234 | [5:0] / [13:8] / [21:16] / [29:24] | CF | |
| tXS / tDLLK | 0x50238 | [10:0] / [26:16] | Not displayed | |
| tSTAG | 0x50250 | [23:16] | CF | [26:16] |
| tXP / tCPDED / tCKE | 0x50254 | [5:0] / [19:16] / [28:24] | tXP is not displayed for DDR4 | |
| tRDDATAEN / tPHYWRL / tPHYRDL / tPHYWRD / PARINL | 0x50258 | [6:0] / [12:8] / [21:16] / [26:24] / [29:28] | CF | PHYWRL was [15:8], PHYRDL was [23:16] |
| tRFC / tRFC2 / tRFC4 | 0x50260 and 0x50264 | [10:0] / [21:11] / [31:22] | Register selection is explained below (`Ddr4Timings.cs:79-88`) | same |
| tWRMPR | 0x5028C | [29:24] | Read, but not on the DDR4 panel. CmdStgCnt is [21:11], RcvrWait is [10:0] | |
| WRPRE / RDPRE | 0x502A4 | [10:8] / [2:0] | Shown as raw+1 (`BaseDramTimings.cs:334-352`). Not on the DDR4 panel | |
| DdrMaxRate | 0x50DF0 | [7:0] | Bit 19 = MemChannel_Dis (CF `amd_reg.h:1969`) | |
| DdrMaxRateEnf | 0x50DF4 | [7:0] | | |
| BGS | 0x50050 and 0x50058 | full 32-bit | Disabled only if **both** read 0x87654321, otherwise Enabled (`BaseDramTimings.cs:207-222`). Same in RM `readinfo.c:180-182` and CF `amd_reg.h:1626-1648`. | |
| BGS Alt | 0x500D0 and 0x500D4 | [10:4] | Enabled if either is non-zero (`BaseDramTimings.cs:221`). CF mask is 0x7F0 (`amd_reg.h:1673`) | |
| UMC_CFG | 0x50100 | DimmEccEn [12], BurstCtrl [11:10], BurstLength [9:8] | Type bits are covered in the population section | |
| VSOC (SVI2) | 0x5A00C | [23:16] VID | V = 1.55 − 0.00625 × VID. Same address on Matisse and Vermeer (ZSC `Common/Constants.cs:16-21`, `Cpu.cs:1450-1458`; ZT `WPF/Plugin/SVI2Plugin.cs:78-91`; `Utils.cs:109-112`) | |

**Displayed vs read-only.** The DDR4 panel shows these rows (ZT `WPF/Controls/DDR4TimingsPanel.xaml:28-117`):
- Speed, BGS, GDM, tCL, tRCDWR, tRCDRD, tRP, tRAS, tRC, tRRDS, tRRDL, tFAW, tWTRS, tWTRL, tWR
- tRFC(ns), tRFC, tRFC2, tRFC4, tMOD, tMODPDA, tPHYWRD, tPHYWRL
- Capacity (from SMBIOS), BGS Alt, Cmd2T, tRDRDSCL, tWRWRSCL, tCWL, tRTP, tRDWR, tWRRD
- tRDRDSC/SD/DD, tWRWRSC/SD/DD, tCKE, tREFI (with ns), Refresh, tSTAG, tMRD, tMRDPDA, tPHYRDL, PowerDown
- Advanced mode: MCLK, FCLK, UCLK, VSOC, VDDP, VDDG CCD, VDDG IOD, VDIMM, MEM VTT, ProcOdt, Rtt*, *DrvStr, *Setup

## How the memory speed is computed, and where BCLK comes in
- **Speed in MT/s** (ZSC `BaseDramTimings.cs:256-271`):
  - For DDR4, speed = PM-table MCLK × 2 whenever that value is > 0.
  - Otherwise speed = Ratio × BCLK × 2.
  - ZT shows `floor(Frequency)` in MT/s (`WPF/ViewModels/MainViewModel.cs:46`, `97-104`).
- **PM-table clocks are corrected for BCLK:** `MCLK/FCLK/UCLK = pm[off] × BCLK/100` (ZSC `Hardware/PowerTable.cs:627-645`).
- **BCLK source** (ZSC `Hardware/Mmio.cs:11-27`, `93-98`, `271-321`):
  - Read from FCH MMIO 0xFED80E10 (CGPLLConfig3); this is a physical address, not SMN.
  - `BCLK = f(index = bits[12:4]) + bits[28:25] × 0.0625`.
  - It returns null for an external clock generator in async eCLK mode. In that case the code falls back to 100.0.
- **Nanosecond values:** `ns = clk × 2 × 1000 / MT/s`.

## tRFC: which register, and how it chooses
- **Registers:** 0x50260 and 0x50264. CF labels these as DIMM[0] and DIMM[1] (`amd_reg.h:2281-2296`).
- **Selection rule** (ZSC `Ddr4Timings.cs:81`; same in RM `readinfo.c:233-235` and in old ZT):
  - If the two values are equal, use t0.
  - If they differ, use t0 unless t0 is 0x21060138; in that case use t1.
  - 0x21060138 is treated as "not programmed". It decodes to tRFC 312, tRFC2 192, tRFC4 132.
- **Which value tRFC(ns) uses:** ZT picks tRFC, tRFC2 or tRFC4 according to the FGR mode in 0x5012C[18:16] (`Ddr4Timings.cs:42-54`). The matching row is enabled in the UI (`MainViewModel.cs:130-132`).
- **No Zen3-specific logic:** there is no Zen2/Zen3 branch for this anywhere in `Hardware/DRAM`.
- **DDR5 only:** old ZT read 0x50268 and 0x5026C only for DDR5.

## Channel / slot population, DDR4 vs DDR5, dual rank (ZSC `MemoryConfig.cs`)
- **Channel enabled** (`MemoryConfig.cs:738-741`): 0x50DF0 bit 19 == 0, **and** at least one of the following is set:
  - 0x50000 bit 0: DIMM0 present, labelled `<A+ch>1`.
  - 0x50008 bit 0: DIMM1 present, labelled `<A+ch>2`.
- **Slot labels** (`MemoryConfig.cs:750-771`): SMBIOS modules are assigned to the DIMMs found, in order. Whether "A1/A2" here matches the B550 AORUS PRO silkscreen is unknown.
- **Rank count** (`GetRank`, `MemoryConfig.cs:675-689`): count bit 0 of the chip-select base registers at `0x50000 + 4×cs`, for cs = 2d and 2d+1 (0x50000/0x50004 for DIMM0, 0x50008/0x5000C for DIMM1). Two set means dual rank.
  - This matches the kernel: LX `amd64_edac.h:162,167`, `amd64_edac.c:1201-1213`, `1447-1470`.
  - Secondary chip-select bases at 0x50010 exist (LX `amd64_edac.h:256`), but ZSC doesn't use them.
- **DDR4 vs DDR5:** ZSC takes the type from the SMBIOS Type 17 memory type of the first module (`MemoryConfig.cs:319-334`, `636-663`).
  - ZSC defines the register constant 0x50100 & 0x3, but the line that used it is commented out (`MemoryConfig.cs:20-22`, `172-173`). Old ZT did use 0x50100[1:0].
  - The kernel trusts UMC_CFG[2:0] == 1 as DDR5 only on `zn_regs_v2` parts (LX `amd64_edac.c:1570`, `3845-3893`). Fam 19h model 21h isn't one of them, so on Zen2/Zen3 the memory is always handled as DDR4.
- **DIMM_CFG** at 0x50080/0x50084 (`MemoryModule.cs:88-115`; LX `amd64_edac.h:260`, `amd64_edac.c:1571-1582`):
  - bit 0 OnDimmMirror, bit 4 RDIMM, bit 5 LRDIMM, bit 6 x4, bit 7 x16.
  - ZSC does not use it to count ranks.
- **Alternative "UMC initialised" tests:**
  - 0x50104 (SDP_CTRL) bit 31 SdpInit (LX `amd64_edac.c:1561`; CF `corefreqk.c:7158-7160`).
  - RM switches to UMC1 if 0x50200 == 0x300 (`readinfo.c:177-178`).

## ProcODT / RTT / drive strengths
Neither ZSC nor CF reads these from any SMN register; I found no SMN path in either code base. Whether one exists in the DDR PHY address space is unknown, since I have no primary source for it.

ZT gets them from three places, in this order (ZT `WPF/MainWindow.xaml.cs:762-858`, `871-930`):
1. **APOB in physical memory.**
   - Found by the "APOB" signature at 0xA200000, 0x9F00000 or 0x4000000 (ZSC `Hardware/Apob/Apob.cs:95`, `417-426`).
   - Zen2/Zen3 layout: a 496-byte MemGeneralConfig with per-channel arrays at data+0xA4, stride 8 (ZSC `ApobMemGeneralConfig.cs:70-125`).
   - Order in the arrays: ProcOdt, RttNom, RttWr, RttPark, AddrCmdSetup, CsOdtSetup, CkeSetup, ClkDrvStren, AddrCmdDrvStren, CsOdtCmdDrvStren, CkeDrvStren.
2. **WMI AMD_ACPI "Get APCB Config"** (Windows only). Byte offsets are in ZT `Common/BiosMemController.cs:80-98`: ProcODT at 33, RttNom/Wr/Park at 65-67, Setup at 86-88, DrvStren at 89-92, VDDIO at 27, VTT at 29.
3. **AOD SSDT** (OEM table ID "AOD     ", region "AODE" or "AODT"; ZSC `Hardware/Aod/Aod.cs:40-41`, `148-157`).

The value encodings are in ZSC `Dictionaries/EncodedValueDictionaries.cs` and `Common/EncodedValues.cs`. I did not check whether Linux `/dev/mem` allows reading the APOB.

## Zen2 vs Zen3 differences
- **UMC map:** identical. ZSC has no family branch in `Hardware/DRAM` and uses the same DDR4 dictionary for all DDR4 parts (`MemoryConfig.cs:181-182`). CF uses identical constants for Matisse and Vermeer (`corefreqk.c:7238-7248`, `7334-7344`).
- **PM table:** this is where they differ.
  - Zen2 0x240903: FCLK 0xC0, UCLK 0xC8, MCLK 0xCC, VSOC 0xB4, VDDP 0x1F4, VDDG 0x1F8 (a single VDDG, no CCD value) (`PowerTable.cs:285`).
  - Zen3 **0x380905** (Vermeer, e.g. 5800X3D): FCLK 0xC0, UCLK 0xC8, MCLK 0xCC, VSOC 0xB4, VDDP 0x224, VDDG_IOD 0x228, VDDG_CCD 0x22C (`PowerTable.cs:300`, table size 0x5D0 = 1488, which matches a 5800X3D's table).
- **Example reading at those offsets** (5800X3D at DDR4-3733): FCLK = UCLK = MCLK = 1867.2683 (so 1:1:1), VSOC 1.1129, VDDP 0.8973, VDDG IOD and CCD 0.9976. With a BCLK correction of 1.0, ZT would show "3734 MT/s".

## Discrepancies and unknowns
- **MEMCLK field width:** ZSC and RM read [6:0]; CF reads [5:0]. Bit 6 is needed above DDR4-4200 (raw value > 63).
- **Address-config register:** ZSC reads 0x50040/0x50048 for all families (`MemoryConfig.cs:758,769`). CF says that location is the Rembrandt/Zen4 layout, and that Zen2/Zen3 use 0x50030/0x50034 (`amd_reg.h:1675-1708`; `corefreqk.c:7245,7341`). ZSC's address config may therefore be wrong on Zen2/Zen3. The DDR4 panel doesn't display it.
- **tRFC DIMM assignment:** that 0x50260 is DIMM0 and 0x50264 is DIMM1 comes only from a CF comment.
- **BGS register names:** the real names of 0x50050–0x5005C are unknown; CF just calls them "BankGroupSwap".
- **Other CF-only fields** (not used by ZT):
  - 0x50D6C [2:0] = current memory P-state.
  - 0x50D6C [3] = UCLK divisor (0 = UCLK:MEMCLK 1:2, 1 = 1:1) (`amd_reg.h:1919-1931`).

## Linux access notes
- **Never write 8 bytes to `smn`.** ryzen_smu treats a 4-byte write as a read request and an 8-byte write (address + value) as an SMN **write** (RS `drv.c:238-260`).
- **Index/data pair:** ryzen_smu uses config registers 0xC4/0xC8 on the root complex (RS `smu.h:29-32`, `smu.c:88-104`). The kernel's own SMN access uses 0x60/0x64 (LX `amd_node.c:42-43`), so the kernel's SMN traffic doesn't touch ryzen_smu's index register.
- **Concurrent users:** the read result in `smn` is a single global value, so two processes doing the write-then-read sequence at once can get each other's results.

## Python decode snippet
This is tested with mock register values only, not live hardware. It runs as root: `sudo python3 -I zen_ddr4_umc.py [bclk_mhz]`.
```python
import os, struct, sys
SMN = "/sys/kernel/ryzen_smu_drv/smn"
def make_reader(path=SMN):
    fd = os.open(path, os.O_RDWR)
    def rd(addr):
        os.lseek(fd, 0, os.SEEK_SET)
        if os.write(fd, struct.pack("<I", addr)) != 4:   # EXACTLY 4 bytes (8 = SMN WRITE)
            raise OSError("smn address write failed")
        os.lseek(fd, 0, os.SEEK_SET)
        b = os.read(fd, 4)
        if len(b) != 4: raise OSError("smn read failed")
        return struct.unpack("<I", b)[0]
    return rd
def bits(v, hi, lo): return (v >> lo) & ((1 << (hi - lo + 1)) - 1)
FIELDS = [(0x50200,"GDM",11,11),(0x50200,"Cmd2T",10,10),(0x50200,"BankGroupEn",8,8),(0x50200,"Preamble2t",12,12),
 (0x5012C,"PowerDown",28,28),(0x50204,"tCL",5,0),(0x50204,"tRAS",14,8),(0x50204,"tRCDRD",21,16),(0x50204,"tRCDWR",29,24),
 (0x50208,"tRC",7,0),(0x50208,"tRP",21,16),(0x5020C,"tRRDS",4,0),(0x5020C,"tRRDL",12,8),(0x5020C,"tRTP",28,24),
 (0x50210,"tFAW",6,0),(0x50214,"tCWL",5,0),(0x50214,"tWTRS",12,8),(0x50214,"tWTRL",22,16),(0x50218,"tWR",6,0),
 (0x5021C,"tRCPAGE",31,20),(0x50220,"tRDRDDD",3,0),(0x50220,"tRDRDSD",11,8),(0x50220,"tRDRDSC",19,16),(0x50220,"tRDRDSCL",27,24),
 (0x50224,"tWRWRDD",3,0),(0x50224,"tWRWRSD",11,8),(0x50224,"tWRWRSC",19,16),(0x50224,"tWRWRSCL",29,24),
 (0x50228,"tWRRD",3,0),(0x50228,"tRDWR",12,8),(0x50230,"tREFI",15,0),(0x50234,"tMRD",5,0),(0x50234,"tMOD",13,8),
 (0x50234,"tMRDPDA",21,16),(0x50234,"tMODPDA",29,24),(0x50250,"tSTAG",23,16),(0x50254,"tXP",5,0),(0x50254,"tCKE",28,24),
 (0x50258,"tPHYWRL",12,8),(0x50258,"tPHYRDL",21,16),(0x50258,"tPHYWRD",26,24),(0x5028C,"tWRMPR",29,24)]
TRFC_DEFAULT = 0x21060138
def decode_channel(rd, ch, bclk_mhz=100.0):
    off = ch << 20; r = lambda a: rd(off | a)
    out = {"umc": ch, "smn_base": hex(0x50000 | off)}
    out["channel_enabled_cap"] = bits(r(0x50DF0), 19, 19) == 0
    cs = [bits(r(0x50000 + 4*i), 0, 0) for i in range(4)]
    out["dimm0_present"], out["dimm1_present"] = bool(cs[0]), bool(cs[2])
    out["dimm0_ranks"], out["dimm1_ranks"] = cs[0] + cs[1], cs[2] + cs[3]
    if not (out["channel_enabled_cap"] and (cs[0] or cs[2])):
        out["enabled"] = False; return out
    out["enabled"] = True; cache = {}
    for reg, name, hi, lo in FIELDS:
        if reg not in cache: cache[reg] = r(reg)
        out[name] = bits(cache[reg], hi, lo)
    ratio = bits(r(0x50200), 6, 0) / 3.0
    out["ratio"], out["MEMCLK_MHz"] = ratio, ratio * bclk_mhz
    mts = out["MT_s"] = 2.0 * ratio * bclk_mhz
    t0, t1 = r(0x50260), r(0x50264)
    trfc = t0 if t0 == t1 else (t0 if t0 != TRFC_DEFAULT else t1)
    out["tRFC"], out["tRFC2"], out["tRFC4"] = bits(trfc,10,0), bits(trfc,21,11), bits(trfc,31,22)
    fgr = out["FGR"] = bits(cache[0x5012C], 18, 16)
    sel = {1: out["tRFC"], 2: out["tRFC2"], 4: out["tRFC4"]}.get({0:1,1:2,5:2,2:4,6:4}.get(fgr, 0))
    ns = lambda clk: clk * 2000.0 / mts if mts else 0.0
    out["tRFC_active_ns"] = ns(sel) if sel is not None else None
    out["tREFI_ns"] = ns(out["tREFI"])
    b0, b1 = r(0x50050), r(0x50058); out["BGS"] = not (b0 == 0x87654321 and b1 == 0x87654321)
    a0, a1 = r(0x500D0), r(0x500D4); out["BGSAlt"] = bits(a0,10,4) > 0 or bits(a1,10,4) > 0
    out["Cmd2T"] = "2T" if out["Cmd2T"] else "1T"
    return out
if __name__ == "__main__":
    rd = make_reader(); bclk = float(sys.argv[1]) if len(sys.argv) > 1 else 100.0
    for ch in (0, 1):
        for k, v in decode_channel(rd, ch, bclk).items(): print(f"UMC{ch} {k} = {v}")
```

ctune's maintained decoder, corrected by [umc_xcheck.md](umc_xcheck.md), is `ctune/mem/platforms/zen3_am4/zen3_ddr4_decode.py`.
