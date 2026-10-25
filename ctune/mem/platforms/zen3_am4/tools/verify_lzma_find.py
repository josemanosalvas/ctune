#!/usr/bin/env python3
"""Find LZMA GUID-defined sections (PI spec vol3 s3.2.5: EFI_GUID_DEFINED_SECTION, LZMA GUID
EE4E5898-3914-4259-9D6E-DC7BD79403CF) in a BIOS image, decompress, search a needle.
usage: python3 -I verify_lzma_find.py IMAGE NEEDLE OUTDIR"""
import sys, lzma, uuid, struct, os
img = open(sys.argv[1], "rb").read()
needle = sys.argv[2].encode()
out = sys.argv[3]
g = uuid.UUID("EE4E5898-3914-4259-9D6E-DC7BD79403CF").bytes_le
p = 0
n = 0
while True:
    p = img.find(g, p)
    if p < 0: break
    s = p - 4
    size = int.from_bytes(img[s:s + 3], "little"); typ = img[s + 3]
    doff, attr = struct.unpack_from("<HH", img, p + 16)
    if typ == 0x02 and 0 < size <= len(img) - s:
        try:
            dec = lzma.LZMADecompressor(lzma.FORMAT_ALONE).decompress(img[s + doff:s + size])
        except Exception as e:
            dec = b""
        if needle in dec:
            fn = os.path.join(out, "sec_%08x.bin" % s)
            open(fn, "wb").write(dec)
            print("hit sec@0x%x size %d dec %d -> %s at %s" % (s, size, len(dec), fn,
                  [hex(i) for i in range(len(dec)) if dec.startswith(needle, i)][:5]))
        n += 1
    p += 16
print("lzma sections:", n)
