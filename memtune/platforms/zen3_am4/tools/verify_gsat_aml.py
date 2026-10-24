#!/usr/bin/env python3
"""Parse an AML `Name(GSAT, Package(){...})` object out of a decompressed firmware blob.
AML encodings per ACPI 6.5 s20.2.3 (Data Objects: BytePrefix 0x0A, WordPrefix 0x0B, DWordPrefix 0x0C,
StringPrefix 0x0D, QWordPrefix 0x0E, ZeroOp 0x00, OneOp 0x01, OnesOp 0xFF, BufferOp 0x11,
PackageOp 0x12, VarPackageOp 0x13) and s20.2.4 (PkgLength).
usage: python3 -I verify_gsat_aml.py BLOB [offset_of_08_GSAT]"""
import sys, struct

def pkglen(d, p):
    b0 = d[p]
    n = b0 >> 6
    if n == 0:
        return b0 & 0x3F, p + 1
    v = b0 & 0x0F
    for i in range(n):
        v |= d[p + 1 + i] << (4 + 8 * i)
    return v, p + 1 + n

def obj(d, p):
    op = d[p]
    if op == 0x0A: return d[p + 1], p + 2
    if op == 0x0B: return struct.unpack_from("<H", d, p + 1)[0], p + 3
    if op == 0x0C: return struct.unpack_from("<I", d, p + 1)[0], p + 5
    if op == 0x0E: return struct.unpack_from("<Q", d, p + 1)[0], p + 9
    if op == 0x0D:
        e = d.index(b"\0", p + 1); return d[p + 1:e].decode("latin-1"), e + 1
    if op == 0x00: return 0, p + 1
    if op == 0x01: return 1, p + 1
    if op == 0xFF: return 0xFFFFFFFFFFFFFFFF, p + 1
    if op in (0x12, 0x13):
        ln, q = pkglen(d, p + 1)
        end = p + 1 + ln
        if op == 0x12:
            n = d[q]; q += 1
        else:
            n, q = obj(d, q)
        out = []
        while q < end:
            v, q = obj(d, q)
            out.append(v)
        return out, end
    if op == 0x11:
        ln, q = pkglen(d, p + 1)
        end = p + 1 + ln
        sz, q = obj(d, q)
        return ("buf", d[q:end].hex()), end
    raise ValueError("op 0x%02x at 0x%x" % (op, p))

d = open(sys.argv[1], "rb").read()
off = int(sys.argv[2], 0) if len(sys.argv) > 2 else d.index(b"\x08GSAT\x12")
assert d[off:off + 5] == b"\x08GSAT"
pkg, end = obj(d, off + 5)
print("GSAT @0x%x, %d top-level entries, %d bytes" % (off, len(pkg), end - off))

def show(v, ind=0):
    if isinstance(v, list):
        flat = all(not isinstance(x, list) for x in v)
        if flat:
            print(" " * ind + "[" + ", ".join(repr(x) if isinstance(x, str) else (hex(x) if isinstance(x, int) and x > 9 else repr(x)) for x in v) + "]")
        else:
            print(" " * ind + "[")
            for x in v: show(x, ind + 2)
            print(" " * ind + "]")
    else:
        print(" " * ind + repr(v))
want = sys.argv[3] if len(sys.argv) > 3 else None
for e in pkg:
    if isinstance(e, list) and e and isinstance(e[0], str):
        if want is None or want.lower() in e[0].lower():
            show(e)
    elif want is None:
        show(e)
