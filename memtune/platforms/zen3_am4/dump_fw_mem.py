#!/usr/bin/env python3
"""Dump firmware buffers that sit in e820 'Reserved' memory (readable via /dev/mem without iomem=relaxed).

Run as root:  python3 -I dump_fw_mem.py OUTDIR [--owner UID:GID]
 * HiiDB: AMI exports the whole HII database (IFR forms + strings of every BIOS setup page) and publishes
   {UINT32 size, UINT32 phys_addr} in the EFI variable HiiDB-1b838190-.... Needed to decode the Setup /
   AmdSetup / AOD_SETUP / Profile variables exactly for the running BIOS version.
 * AmiNbMemAddrData: AMI north-bridge memory info buffer (layout unknown; saved raw).
 * A signature scan (APOB / AODT) of the Reserved range that holds those buffers.
Read-only; reads page by page and skips pages the kernel refuses.
"""
import os, struct, sys

EFIVARS = "/sys/firmware/efi/efivars"


def efivar(name):
    with open(os.path.join(EFIVARS, name), "rb") as f:
        return f.read()[4:]  # strip the 4-byte attribute header


def read_pages(addr, n):
    out, refused = bytearray(), 0
    fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
    try:
        while len(out) < n:
            a = addr + len(out)
            step = min(n - len(out), 4096 - (a & 0xFFF))
            try:
                chunk = os.pread(fd, step, a)
            except OSError:
                chunk, refused = b"\0" * step, refused + 1
            out += chunk or b"\0" * step
    finally:
        os.close(fd)
    return bytes(out), refused


def memmap_range(addr):
    base = "/sys/firmware/memmap"
    for e in os.listdir(base):
        s, t = (int(open(f"{base}/{e}/{k}").read(), 16) for k in ("start", "end"))
        if s <= addr <= t:
            return s, t, open(f"{base}/{e}/type").read().strip()
    return None


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    log = []
    size, addr = struct.unpack("<II", efivar("HiiDB-1b838190-4625-4ead-abc9-cd5e6af18fe0")[:8])
    log.append(f"HiiDB: addr 0x{addr:X} size 0x{size:X} in {memmap_range(addr)}")
    data, ref = read_pages(addr, size)
    open(os.path.join(out, "hiidb.bin"), "wb").write(data)
    log.append(f"  saved hiidb.bin ({len(data)} bytes, {ref} refused pages)")

    nb = struct.unpack("<I", efivar("AmiNbMemAddrData-5ef8f5aa-b95e-4f12-bd40-1ebaf38be19c")[:4])[0]
    log.append(f"AmiNbMemAddrData: addr 0x{nb:X} in {memmap_range(nb)}")
    data, ref = read_pages(nb, 0x1000)
    open(os.path.join(out, "aminbmem.bin"), "wb").write(data)
    log.append(f"  saved aminbmem.bin (4096 bytes, {ref} refused pages)")

    rng = memmap_range(addr)
    if rng and rng[2] == "Reserved":
        s, t, _ = rng
        data, ref = read_pages(s, t - s + 1)
        for sig in (b"APOB", b"AODT", b"AODE"):
            hits, i = [], data.find(sig)
            while i >= 0 and len(hits) < 20:
                hits.append(hex(s + i)); i = data.find(sig, i + 1)
            log.append(f"scan 0x{s:X}-0x{t:X} for {sig!r}: {hits or 'none'} ({ref} refused pages)")

    open(os.path.join(out, "dump_fw_mem.log"), "w").write("\n".join(log) + "\n")
    print("\n".join(log))
    if len(sys.argv) > 3 and sys.argv[2] == "--owner":
        uid, gid = (int(x) for x in sys.argv[3].split(":"))
        for n in [out] + [os.path.join(out, x) for x in os.listdir(out)]:
            os.chown(n, uid, gid)


if __name__ == "__main__":
    main()
