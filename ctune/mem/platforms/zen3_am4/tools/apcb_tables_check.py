#!/usr/bin/env python3
"""Re-check the APCB DDR4 CAD-bus, data-bus and ODT-pattern rows that the termination research cites.
Struct layouts: oxidecomputer/amd-apcb src/ondisk.rs (UdimmDdr4CadBusElement 36 B, Ddr4DataBusElement 52 B,
Ddr4OdtPatElement 20 B; DdrRates bit n = DDR(n*133.33), that is, bit12=1600 ... bit24=3200; drive strength
codes 0=120,1=60,3=40,7=30,15=24,31=20 ohm; RttNom/Park JEDEC MR codes 0 off,1 60,2 120,3 40,4 240,5 48,6 80,7 34;
RttWr 0 off,1 120,2 240,3 HiZ,4 80).  Read-only; usage: python3 -I apcb_tables_check.py <bios image>"""
import struct, sys

RATES = {10: 1333, 12: 1600, 14: 1866, 16: 2133, 18: 2400, 20: 2667, 22: 2933, 24: 3200}
DS = {0: 120, 1: 60, 3: 40, 7: 30, 15: 24, 31: 20, 0xFF: 'Auto'}
NOM = {0: 'off', 1: 60, 2: 120, 3: 40, 4: 240, 5: 48, 6: 80, 7: 34}
WR = {0: 'off', 1: 120, 2: 240, 3: 'HiZ', 4: 80}
RK = lambda v: '|'.join(n for b, n in ((0, 'none'), (1, 'SR'), (2, 'DR'), (3, 'QR')) if v >> b & 1) or hex(v)
rates = lambda v: [r for b, r in RATES.items() if v >> b & 1]
PROCODT = {0x01: 480, 0x02: 240, 0x03: 160, 0x08: 120, 0x09: 96, 0x0A: 80, 0x0B: 68.6, 0x18: 60, 0x19: 53.3,
           0x1A: 48, 0x1B: 43.6, 0x38: 40, 0x39: 36.9, 0x3A: 34.3, 0x3B: 32, 0x3E: 30, 0x3F: 28.2}

def cad(d, off, n):
    print(f'-- CAD bus @ {off:#x}')
    for k in range(n):
        e = d[off + 36 * k: off + 36 * (k + 1)]
        slots, rt, vdd, r0, r1 = struct.unpack_from('<5I', e)
        gdm, _, slow, _, acc = struct.unpack_from('<4HI', e, 20)
        cke, csodt, addr, clk = e[32:36]
        print(f'  slots={slots} rates={rates(rt)} vdd={vdd:#x} dimm0={RK(r0)} dimm1={RK(r1)} GDM={gdm} slow(2T)={slow} '
              f'setup={acc:#08x} drv Clk/AddrCmd/CsOdt/Cke={DS.get(clk, clk)}/{DS.get(addr, addr)}/{DS.get(csodt, csodt)}/{DS.get(cke, cke)}')

def databus(d, off, n):
    print(f'-- data bus @ {off:#x}')
    for k in range(n):
        f = struct.unpack_from('<13I', d, off + 52 * k)
        if f[0] not in (1, 2) or f[1] == 0 or f[1] >> 25:
            print('  (end)'); break
        print(f'  slots={f[0]} rates={rates(f[1])} dimm0={RK(f[3])} dimm1={RK(f[4])} RttNom={NOM.get(f[5], f[5])} '
              f'RttWr={WR.get(f[6], f[6])} RttPark={NOM.get(f[7], f[7])} dq={f[8]:#x} dqs={f[9]:#x} '
              f'odt={f[10]:#x}({PROCODT.get(f[10], "?")} ohm if ProcODT code) phyvref={f[11]:#x} vrefdq={f[12]:#x}')

if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read()
    # offsets cited by the research are APCB ENTRY_HEADERs (16 B: group u16, entry u16, size u16, instance u16, ...);
    # elements follow the header.
    def hdr(o):
        g, e, sz, inst = struct.unpack_from('<4H', d, o)
        print(f'== entry @ {o:#x}: group={g:#x} id={e:#x} size={sz} instance={inst:#x}')
        return o + 16, sz - 16
    o, n = hdr(0x19D620); cad(d, o, n // 36)
    for oo in (0x19D8D8, 0x19DD78, 0x19E358):
        o, n = hdr(oo); databus(d, o, n // 52)
    o, n = hdr(0x19D828)
    for k in range(n // 20):
        print('  ', ' '.join(f'{x:#010x}' for x in struct.unpack_from('<5I', d, o + 20 * k)))
