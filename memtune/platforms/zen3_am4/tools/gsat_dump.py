#!/usr/bin/env python3
"""Dump Gigabyte EasyTune 'GSAT' AML package (Name(GSAT, Package(){...})) from a decompressed SSDT blob.
AML encodings per ACPI 6.5 spec section 20.2 (PackageOp 0x12, PkgLength 20.2.4, data object prefixes 20.2.3).
usage: python3 -I gsat_dump.py <blob> [offset_of_'GSAT']"""
import sys, json

def pkglen(d, i):
    b0 = d[i]; n = b0 >> 6
    if n == 0:
        return b0 & 0x3F, i + 1
    v = b0 & 0x0F
    for k in range(n):
        v |= d[i + 1 + k] << (4 + 8 * k)
    return v, i + 1 + n

def obj(d, i):
    op = d[i]
    if op == 0x00: return 0, i + 1
    if op == 0x01: return 1, i + 1
    if op == 0xFF: return -1, i + 1
    if op == 0x0A: return d[i + 1], i + 2
    if op == 0x0B: return int.from_bytes(d[i + 1:i + 3], 'little'), i + 3
    if op == 0x0C: return int.from_bytes(d[i + 1:i + 5], 'little'), i + 5
    if op == 0x0E: return int.from_bytes(d[i + 1:i + 9], 'little'), i + 9
    if op == 0x0D:
        j = d.index(b'\x00', i + 1); return d[i + 1:j].decode('latin1'), j + 1
    if op in (0x12, 0x13):
        start = i + 1
        L, j = pkglen(d, start)
        end = start + L
        if op == 0x12:
            n = d[j]; j += 1
        else:
            n, j = obj(d, j)
        out = []
        while j < end and len(out) < n:
            v, j = obj(d, j); out.append(v)
        return out, end
    if op == 0x11:
        start = i + 1
        L, j = pkglen(d, start)
        return ('buf', d[j:start + L].hex()), start + L
    raise ValueError(f'op {op:#x} at {i:#x}')

def s32(v):
    return v - (1 << 32) if isinstance(v, int) and v >= 0x80000000 else v

if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read()
    off = int(sys.argv[2], 0) if len(sys.argv) > 2 else d.index(b'GSAT\x12')
    pkg, end = obj(d, off + 4)
    for e in pkg:
        print(json.dumps(e, default=str))
