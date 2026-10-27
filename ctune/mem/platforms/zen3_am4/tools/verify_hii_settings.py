#!/usr/bin/env python3
"""Independent verifier for the HII/efivar decode (does not import hii_ifr.py / bios_settings_decode.py).

Format sources (UEFI Spec 2.10 + EDK2 headers):
  * EFI_HII_PACKAGE_LIST_HEADER / EFI_HII_PACKAGE_HEADER  -- UEFI 2.10 s33.3.1
      (EDK2 MdePkg/Include/Uefi/UefiInternalFormRepresentation.h: EFI_HII_PACKAGE_HEADER is
       UINT32 Length:24, Type:8; EFI_HII_PACKAGE_FORMS=0x02, EFI_HII_PACKAGE_STRINGS=0x04, END=0xDF)
  * String package + SIBT_* blocks                       -- UEFI 2.10 s33.3.6
      (same header: SIBT_END 0x00, STRING_SCSU 0x10.._FONT 0x11, STRINGS_SCSU 0x12.._FONT 0x13,
       STRING_UCS2 0x14, .._FONT 0x15, STRINGS_UCS2 0x16, .._FONT 0x17, DUPLICATE 0x20,
       SKIP2 0x21, SKIP1 0x22, EXT1 0x30, EXT2 0x31, EXT4 0x32)
  * IFR opcodes / EFI_IFR_OP_HEADER (Length:7, Scope:1)   -- UEFI 2.10 s33.3.8
      EFI_IFR_QUESTION_HEADER = Prompt,Help,QuestionId,VarStoreId,VarOffset (UINT16 each) + Flags(UINT8)
      ONE_OF 0x05 / NUMERIC 0x07: + Flags(UINT8; EFI_IFR_NUMERIC_SIZE=0x03) + MinMaxData
      ONE_OF_OPTION 0x09: Option(STRING_ID) Flags(UINT8; DEFAULT=0x10, DEFAULT_MFG=0x20) Type(UINT8) Value
      VARSTORE 0x24: Guid, VarStoreId(UINT16), Size(UINT16), Name(CHAR8[])
      VARSTORE_EFI 0x26: VarStoreId(UINT16), Guid, Attributes(UINT32), Size(UINT16), Name(CHAR8[])
  * efivarfs file = UINT32 attributes + data  (Linux Documentation/filesystems/efivarfs.rst)

Usage: CTUNE_SNAPSHOT=data/snapshots/<name> python3 -I verify_hii_settings.py [OUT.json]. The other verify_* tools
import this module and read the same snapshot.
"""
import json
import os
import struct
import sys
import uuid

SNAP = os.environ.get("CTUNE_SNAPSHOT", "")   # a ctune snapshot directory
HII = os.path.join(SNAP, "raw", "fwmem", "hiidb.bin")
EFIV = os.path.join(SNAP, "raw", "efivars")

OPN = {0x01: "FORM", 0x02: "SUBTITLE", 0x03: "TEXT", 0x05: "ONE_OF", 0x06: "CHECKBOX", 0x07: "NUMERIC",
       0x08: "PASSWORD", 0x09: "OPTION", 0x0A: "SUPPRESS_IF", 0x0E: "FORM_SET", 0x0F: "REF",
       0x12: "EQ_ID_VAL", 0x14: "EQ_ID_VAL_LIST", 0x15: "AND", 0x16: "OR", 0x17: "NOT", 0x19: "GRAY_OUT_IF",
       0x1C: "STRING", 0x1E: "DISABLE_IF", 0x23: "ORDERED_LIST", 0x24: "VARSTORE", 0x26: "VARSTORE_EFI",
       0x29: "END", 0x5B: "DEFAULT", 0x5F: "GUID", 0x46: "TRUE", 0x47: "FALSE", 0x5A: "VALUE"}


def guid(b):
    return str(uuid.UUID(bytes_le=bytes(b)))


def parse_strings(pkg):
    """pkg = whole string package incl. 4-byte header. Returns (lang, {id: str})."""
    hdr_size, info_off = struct.unpack_from("<II", pkg, 4)
    lang_end = pkg.index(b"\0", 4 + 4 + 4 + 32 + 2)
    lang = pkg[4 + 4 + 4 + 32 + 2:lang_end].decode("ascii")
    p = info_off
    sid = 1
    out = {}

    def ucs2(p):
        e = p
        while pkg[e:e + 2] != b"\0\0":
            e += 2
        return pkg[p:e].decode("utf-16-le", "replace"), e + 2

    def scsu(p):
        e = pkg.index(b"\0", p)
        return pkg[p:e].decode("latin-1"), e + 1

    while p < len(pkg):
        t = pkg[p]
        p += 1
        if t == 0x00:
            break
        elif t in (0x14, 0x15):
            if t == 0x15:
                p += 1
            s, p = ucs2(p)
            out[sid] = s
            sid += 1
        elif t in (0x16, 0x17):
            if t == 0x17:
                p += 1
            n = struct.unpack_from("<H", pkg, p)[0]
            p += 2
            for _ in range(n):
                s, p = ucs2(p)
                out[sid] = s
                sid += 1
        elif t in (0x10, 0x11):
            if t == 0x11:
                p += 1
            s, p = scsu(p)
            out[sid] = s
            sid += 1
        elif t in (0x12, 0x13):
            if t == 0x13:
                p += 1
            n = struct.unpack_from("<H", pkg, p)[0]
            p += 2
            for _ in range(n):
                s, p = scsu(p)
                out[sid] = s
                sid += 1
        elif t == 0x20:  # DUPLICATE
            dup = struct.unpack_from("<H", pkg, p)[0]
            p += 2
            out[sid] = out.get(dup, "<dup %d>" % dup)
            sid += 1
        elif t == 0x21:  # SKIP2
            sid += struct.unpack_from("<H", pkg, p)[0]
            p += 2
        elif t == 0x22:  # SKIP1
            sid += pkg[p]
            p += 1
        elif t == 0x30:  # EXT1: BlockType2, Length(UINT8) -- length covers whole block
            ln = pkg[p + 1]
            p = p - 1 + ln
        elif t == 0x31:
            ln = struct.unpack_from("<H", pkg, p + 1)[0]
            p = p - 1 + ln
        elif t == 0x32:
            ln = struct.unpack_from("<I", pkg, p + 1)[0]
            p = p - 1 + ln
        else:
            raise ValueError("unknown SIBT 0x%02x at %d" % (t, p - 1))
    return lang, out


def walk_db(data):
    """Yield package lists: dict(guid, strings(en-US), forms=[bytes])."""
    off = 0
    lists = []
    while off + 20 <= len(data):
        g = data[off:off + 16]
        ln = struct.unpack_from("<I", data, off + 16)[0]
        if ln < 20 or off + ln > len(data):
            break
        pl = {"guid": guid(g), "off": off, "strings": {}, "langs": [], "forms": []}
        p = off + 20
        while p < off + ln:
            h = struct.unpack_from("<I", data, p)[0]
            plen, ptype = h & 0xFFFFFF, h >> 24
            pkg = data[p:p + plen]
            if ptype == 0x04:
                lang, strs = parse_strings(pkg)
                pl["langs"].append(lang)
                if lang.lower().startswith("en") and not pl["strings"]:
                    pl["strings"] = strs
            elif ptype == 0x02:
                pl["forms"].append((p, pkg))
            elif ptype == 0xDF:
                break
            if plen == 0:
                break
            p += plen
        lists.append(pl)
        off += ln
    return lists


def parse_forms(pkg, strs, base_off):
    """Return (varstores{id:(name,guid,size)}, questions[list], ops[list])."""
    p = 4
    ops = []
    depth = 0
    while p < len(pkg):
        op = pkg[p]
        lb = pkg[p + 1]
        ln, sc = lb & 0x7F, lb >> 7
        if ln < 2:
            raise ValueError("bad op len at 0x%x" % (base_off + p))
        ops.append((base_off + p, op, sc, depth, pkg[p:p + ln]))
        if sc:
            depth += 1
        if op == 0x29:
            depth -= 1
        p += ln
    return ops


def main():
    if not SNAP:
        sys.exit("set CTUNE_SNAPSHOT to a ctune snapshot directory (data/snapshots/<name>)")
    data = open(HII, "rb").read()
    lists = walk_db(data)
    allq = []
    stores_all = {}
    report = {"package_lists": [], "questions": []}
    for li, pl in enumerate(lists):
        strs = pl["strings"]
        for (foff, fpkg) in pl["forms"]:
            ops = parse_forms(fpkg, strs, foff)
            stores = {}
            fs_title = None
            cur_q = None
            form_title = None
            # scope stack of enclosing conditions: each entry = list of raw expr ops
            stack = []
            cond_expr_pending = None
            i = 0
            while i < len(ops):
                off, op, sc, depth, raw = ops[i]
                if op == 0x0E:
                    fs_title = strs.get(struct.unpack_from("<H", raw, 18)[0])
                if op == 0x24:
                    sid, size = struct.unpack_from("<HH", raw, 18)
                    name = raw[22:raw.index(b"\0", 22)].decode()
                    stores[sid] = (name, guid(raw[2:18]), size)
                elif op == 0x26:
                    sid = struct.unpack_from("<H", raw, 2)[0]
                    g = guid(raw[4:20])
                    attr, size = struct.unpack_from("<IH", raw, 20)
                    name = raw[26:raw.index(b"\0", 26)].decode()
                    stores[sid] = (name, g, size)
                elif op == 0x01:
                    form_title = strs.get(struct.unpack_from("<H", raw, 4)[0])
                if op in (0x05, 0x07, 0x06):
                    prompt, hlp, qid, vsid, voff, qfl = struct.unpack_from("<HHHHHB", raw, 2)
                    fl = raw[13]
                    if op == 0x06:
                        width = 1
                        mm = None
                    else:
                        width = 1 << (fl & 3)
                        fmt = {1: "B", 2: "H", 4: "I", 8: "Q"}[width]
                        mm = struct.unpack_from("<" + fmt * 3, raw, 14)
                    q = {"pl": li, "off": off, "op": OPN[op], "prompt": strs.get(prompt, "<sid %d>" % prompt),
                         "help": strs.get(hlp, ""), "qid": qid, "vsid": vsid, "voff": voff, "width": width,
                         "minmaxstep": mm, "flags": fl, "formset": fs_title, "form": form_title,
                         "store": stores.get(vsid), "options": [], "defaults": []}
                    q["_d"] = depth
                    allq.append(q)
                    cur_q = q if sc else None
                elif op in (0x08, 0x0C, 0x1A, 0x1B, 0x1C, 0x23):
                    cur_q = None
                elif op == 0x09 and cur_q is not None:
                    osid, ofl, oty = struct.unpack_from("<HBB", raw, 2)
                    vb = raw[6:]
                    vw = {0: 1, 1: 2, 2: 4, 3: 8, 4: 1}.get(oty)
                    val = int.from_bytes(vb[:vw], "little") if vw else None
                    cur_q["options"].append((val, strs.get(osid, "<sid %d>" % osid), ofl, oty))
                elif op == 0x5B and cur_q is not None:
                    did, ty = struct.unpack_from("<HB", raw, 2)
                    vw = {0: 1, 1: 2, 2: 4, 3: 8, 4: 1}.get(ty)
                    cur_q["defaults"].append((did, int.from_bytes(raw[5:5 + vw], "little") if vw else raw[5:].hex()))
                elif op == 0x29 and cur_q is not None and depth == cur_q["_d"] + 1:
                    cur_q = None
                i += 1
            for k, v in stores.items():
                stores_all.setdefault((li, k), v)
    return lists, allq, stores_all


def efivar(name, g):
    fn = os.path.join(EFIV, "%s-%s" % (name, g))
    if not os.path.exists(fn):
        fn2 = "/sys/firmware/efi/efivars/%s-%s" % (name, g)
        if os.path.exists(fn2):
            fn = fn2
        else:
            return None
    return open(fn, "rb").read()[4:]


if __name__ == "__main__":
    lists, allq, stores = main()
    print("package lists:", len(lists), "questions:", len(allq))
    for li, pl in enumerate(lists):
        print(li, pl["guid"], pl["langs"], len(pl["strings"]), "forms", len(pl["forms"]))
    json.dump([{k: v for k, v in q.items() if not k.startswith("_")} for q in allq],
              open(sys.argv[1] if len(sys.argv) > 1 else "/dev/null", "w"), indent=0, default=str)
