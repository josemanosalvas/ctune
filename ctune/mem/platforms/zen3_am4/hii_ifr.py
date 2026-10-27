#!/usr/bin/env python3
"""HII database / IFR parser (Python 3 stdlib only).

Parses an exported HII database (a concatenation of EFI_HII_PACKAGE_LIST_HEADERs, as AMI Aptio V
exports it through the "HiiDB" EFI variable) and builds a question table:
formset, form path, prompt, help, varstore (GUID, name and size), VarOffset, width, type,
options, min/max/step, defaults, and the suppressif/grayoutif/disableif conditions.

Format references (UEFI Specification 2.10, Aug 2022):
  - 33.3.1   EFI_HII_PACKAGE_LIST_HEADER, EFI_HII_PACKAGE_HEADER (Length:24, Type:8),
             package types FORMS 0x02, STRINGS 0x04, END 0xDF.
  - 33.3.6   String package: EFI_HII_STRING_PACKAGE_HDR (HdrSize, StringInfoOffset, LanguageWindow[16],
             LanguageName, Language[]) and string blocks SIBT_* (END 0x00, STRING_SCSU 0x10,
             STRING_SCSU_FONT 0x11, STRINGS_SCSU 0x12, STRINGS_SCSU_FONT 0x13, STRING_UCS2 0x14,
             STRING_UCS2_FONT 0x15, STRINGS_UCS2 0x16, STRINGS_UCS2_FONT 0x17, DUPLICATE 0x20,
             SKIP2 0x21, SKIP1 0x22, EXT1 0x30, EXT2 0x31, EXT4 0x32). String IDs start at 1.
  - 33.3.8   Forms package; 33.3.8.2 opcode table; 33.3.8.3 opcode structures
             (EFI_IFR_OP_HEADER {OpCode u8; Length:7, Scope:1}; EFI_IFR_QUESTION_HEADER 11 bytes).
  Same definitions in EDK2: MdePkg/Include/Uefi/UefiInternalFormRepresentation.h
  (https://github.com/tianocore/edk2/blob/master/MdePkg/Include/Uefi/UefiInternalFormRepresentation.h)
  and the string-block walker in MdeModulePkg/Universal/HiiDatabaseDxe/String.c (FindStringBlock).

Usage:
  python3 -I hii_ifr.py hiidb.bin [--json out.json] [--grep REGEX]
"""
import json
import re
import struct
import sys
import uuid

# ---------------------------------------------------------------------------- constants
PKG_NAMES = {0x00: "ALL", 0x01: "GUID", 0x02: "FORMS", 0x04: "STRINGS", 0x05: "FONTS", 0x06: "IMAGES",
             0x07: "SIMPLE_FONTS", 0x08: "DEVICE_PATH", 0x09: "KEYBOARD_LAYOUT", 0x0A: "ANIMATIONS",
             0xDF: "END"}

OP = dict(
    FORM=0x01, SUBTITLE=0x02, TEXT=0x03, IMAGE=0x04, ONE_OF=0x05, CHECKBOX=0x06, NUMERIC=0x07,
    PASSWORD=0x08, ONE_OF_OPTION=0x09, SUPPRESS_IF=0x0A, LOCKED=0x0B, ACTION=0x0C, RESET_BUTTON=0x0D,
    FORM_SET=0x0E, REF=0x0F, NO_SUBMIT_IF=0x10, INCONSISTENT_IF=0x11, EQ_ID_VAL=0x12, EQ_ID_ID=0x13,
    EQ_ID_VAL_LIST=0x14, AND=0x15, OR=0x16, NOT=0x17, RULE=0x18, GRAY_OUT_IF=0x19, DATE=0x1A, TIME=0x1B,
    STRING=0x1C, REFRESH=0x1D, DISABLE_IF=0x1E, ANIMATION=0x1F, TO_LOWER=0x20, TO_UPPER=0x21, MAP=0x22,
    ORDERED_LIST=0x23, VARSTORE=0x24, VARSTORE_NAME_VALUE=0x25, VARSTORE_EFI=0x26, VARSTORE_DEVICE=0x27,
    VERSION=0x28, END=0x29, MATCH=0x2A, GET=0x2B, SET=0x2C, READ=0x2D, WRITE=0x2E, EQUAL=0x2F,
    NOT_EQUAL=0x30, GREATER_THAN=0x31, GREATER_EQUAL=0x32, LESS_THAN=0x33, LESS_EQUAL=0x34,
    BITWISE_AND=0x35, BITWISE_OR=0x36, BITWISE_NOT=0x37, SHIFT_LEFT=0x38, SHIFT_RIGHT=0x39, ADD=0x3A,
    SUBTRACT=0x3B, MULTIPLY=0x3C, DIVIDE=0x3D, MODULO=0x3E, RULE_REF=0x3F, QUESTION_REF1=0x40,
    QUESTION_REF2=0x41, UINT8=0x42, UINT16=0x43, UINT32=0x44, UINT64=0x45, TRUE=0x46, FALSE=0x47,
    TO_UINT=0x48, TO_STRING=0x49, TO_BOOLEAN=0x4A, MID=0x4B, FIND=0x4C, TOKEN=0x4D, STRING_REF1=0x4E,
    STRING_REF2=0x4F, CONDITIONAL=0x50, QUESTION_REF3=0x51, ZERO=0x52, ONE=0x53, ONES=0x54,
    UNDEFINED=0x55, LENGTH=0x56, DUP=0x57, THIS=0x58, SPAN=0x59, VALUE=0x5A, DEFAULT=0x5B,
    DEFAULTSTORE=0x5C, FORM_MAP=0x5D, CATENATE=0x5E, GUID=0x5F, SECURITY=0x60, MODAL_TAG=0x61,
    REFRESH_ID=0x62, WARNING_IF=0x63, MATCH2=0x64)
OPNAME = {v: k for k, v in OP.items()}

# expression opcodes (UEFI 2.10 33.3.8.2, "Expression" rows)
EXPR_OPS = {OP[k] for k in (
    "EQ_ID_VAL", "EQ_ID_ID", "EQ_ID_VAL_LIST", "AND", "OR", "NOT", "RULE_REF", "QUESTION_REF1",
    "QUESTION_REF2", "QUESTION_REF3", "UINT8", "UINT16", "UINT32", "UINT64", "TRUE", "FALSE", "TO_UINT",
    "TO_STRING", "TO_BOOLEAN", "MID", "FIND", "TOKEN", "STRING_REF1", "STRING_REF2", "CONDITIONAL",
    "ZERO", "ONE", "ONES", "UNDEFINED", "LENGTH", "DUP", "THIS", "SPAN", "TO_LOWER", "TO_UPPER", "MAP",
    "MATCH", "MATCH2", "GET", "SET", "EQUAL", "NOT_EQUAL", "GREATER_THAN", "GREATER_EQUAL", "LESS_THAN",
    "LESS_EQUAL", "BITWISE_AND", "BITWISE_OR", "BITWISE_NOT", "SHIFT_LEFT", "SHIFT_RIGHT", "ADD",
    "SUBTRACT", "MULTIPLY", "DIVIDE", "MODULO", "CATENATE", "VERSION", "SECURITY")}

QUESTION_OPS = {OP[k] for k in ("ONE_OF", "CHECKBOX", "NUMERIC", "PASSWORD", "ACTION", "REF", "DATE", "TIME",
                                "STRING", "ORDERED_LIST")}
COND_OPS = {OP["SUPPRESS_IF"]: "suppressif", OP["GRAY_OUT_IF"]: "grayoutif", OP["DISABLE_IF"]: "disableif"}

# EFI_IFR_TYPE_* (33.3.8.3.x EFI_IFR_ONE_OF_OPTION)
IFR_TYPE = {0: ("u8", 1), 1: ("u16", 2), 2: ("u32", 4), 3: ("u64", 8), 4: ("bool", 1), 5: ("time", 3),
            6: ("date", 4), 7: ("string", 2), 8: ("other", 0), 9: ("undefined", 0), 10: ("action", 2),
            11: ("buffer", 0), 12: ("ref", 0)}


def guid_str(b, off=0):
    return str(uuid.UUID(bytes_le=bytes(b[off:off + 16])))


def u8(b, o): return b[o]
def u16(b, o): return struct.unpack_from("<H", b, o)[0]
def u32(b, o): return struct.unpack_from("<I", b, o)[0]
def u64(b, o): return struct.unpack_from("<Q", b, o)[0]


def read_uint(b, o, n):
    return int.from_bytes(bytes(b[o:o + n]), "little")


def cstr(b, o):
    e = b.find(b"\0", o)
    return bytes(b[o:e if e >= 0 else len(b)]).decode("ascii", "replace")


# ---------------------------------------------------------------------------- strings
def parse_string_package(pkg):
    """pkg is the bytes of the whole package, including the 4-byte header. Returns (language, {id: str})."""
    hdr_size, info_off = struct.unpack_from("<II", pkg, 4)
    lang = cstr(pkg, 4 + 4 + 4 + 32 + 2)  # Header, HdrSize, StringInfoOffset, LanguageWindow[16], LanguageName
    strings = {}
    sid = 1
    p = info_off
    n = len(pkg)

    def ucs2(p):
        e = p
        while e + 1 < n and (pkg[e] or pkg[e + 1]):
            e += 2
        return pkg[p:e].decode("utf-16le", "replace"), e + 2

    def scsu(p):  # treat as 8-bit text (SCSU single-byte mode, ASCII subset)
        e = pkg.find(b"\0", p)
        return pkg[p:e].decode("latin-1"), e + 1

    while p < n:
        bt = pkg[p]
        if bt == 0x00:  # SIBT_END
            break
        elif bt == 0x14:  # SIBT_STRING_UCS2
            s, p = ucs2(p + 1); strings[sid] = s; sid += 1
        elif bt == 0x15:  # SIBT_STRING_UCS2_FONT
            s, p = ucs2(p + 2); strings[sid] = s; sid += 1
        elif bt in (0x16, 0x17):  # SIBT_STRINGS_UCS2(_FONT)
            q = p + 1 + (1 if bt == 0x17 else 0)
            cnt = u16(pkg, q); q += 2
            for _ in range(cnt):
                s, q = ucs2(q); strings[sid] = s; sid += 1
            p = q
        elif bt == 0x10:  # SIBT_STRING_SCSU
            s, p = scsu(p + 1); strings[sid] = s; sid += 1
        elif bt == 0x11:
            s, p = scsu(p + 2); strings[sid] = s; sid += 1
        elif bt in (0x12, 0x13):
            q = p + 1 + (1 if bt == 0x13 else 0)
            cnt = u16(pkg, q); q += 2
            for _ in range(cnt):
                s, q = scsu(q); strings[sid] = s; sid += 1
            p = q
        elif bt == 0x20:  # SIBT_DUPLICATE
            ref = u16(pkg, p + 1)
            strings[sid] = strings.get(ref, ""); sid += 1; p += 3
        elif bt == 0x21:  # SIBT_SKIP2
            sid += u16(pkg, p + 1); p += 3
        elif bt == 0x22:  # SIBT_SKIP1
            sid += pkg[p + 1]; p += 2
        elif bt == 0x30:  # SIBT_EXT1: BlockType2, Length(u8) = whole block length
            p += pkg[p + 2]
        elif bt == 0x31:  # SIBT_EXT2: Length(u16)
            p += u16(pkg, p + 2)
        elif bt == 0x32:  # SIBT_EXT4: Length(u32)
            p += u32(pkg, p + 2)
        else:
            raise ValueError(f"unknown SIBT 0x{bt:02x} at +0x{p:x} ({lang})")
    return lang, strings


# ---------------------------------------------------------------------------- package lists
def parse_package_lists(db):
    """Returns a list of dicts {guid, offset, length, packages: [(type, abs_offset, bytes)]}."""
    out = []
    off = 0
    while off + 20 <= len(db):
        g = guid_str(db, off)
        L = u32(db, off + 16)
        if L < 20 or off + L > len(db):
            break
        pkgs = []
        p = off + 20
        while p + 4 <= off + L:
            h = u32(db, p)
            plen, ptype = h & 0xFFFFFF, h >> 24
            if plen < 4:
                break
            pkgs.append((ptype, p, db[p:p + plen]))
            if ptype == 0xDF:
                break
            p += plen
        out.append(dict(guid=g, offset=off, length=L, packages=pkgs))
        off += L
    return out


# ---------------------------------------------------------------------------- IFR tree
class Node:
    __slots__ = ("op", "data", "off", "children", "scope")

    def __init__(self, op, data, off, scope):
        self.op, self.data, self.off, self.scope = op, data, off, scope
        self.children = []


def build_tree(form_pkg, base_off):
    """form_pkg is the whole package, including the header. Returns the root Node with the top-level opcodes as
    children."""
    root = Node(-1, b"", base_off, True)
    stack = [root]
    p = 4
    n = len(form_pkg)
    while p + 2 <= n:
        op = form_pkg[p]
        ln = form_pkg[p + 1] & 0x7F
        scope = bool(form_pkg[p + 1] & 0x80)
        if ln < 2:
            raise ValueError(f"bad IFR op length at +0x{p:x}")
        node = Node(op, form_pkg[p:p + ln], base_off + p, scope)
        if op == OP["END"]:
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].children.append(node)
            if scope:
                stack.append(node)
        p += ln
    return root


# ---------------------------------------------------------------------------- expressions
class Expr:
    """A postfix IFR expression as readable text, with an evaluator over question values."""

    def __init__(self, nodes):
        self.nodes = nodes  # list of Node (expression opcodes, possibly with scoped children)

    def flat(self):
        """Pre-order flatten. Some compilers (seen in this AMI/AMD IFR) set the Scope bit on the first
        expression opcode (for example EQ_ID_VAL), so the rest of the postfix expression (NOT, OR, ...) sits
        inside its scope until the matching END; in binary order the opcode precedes its nested ops."""
        out = []

        def rec(n):
            out.append(n)
            for c in n.children:
                rec(c)
        for n in self.nodes:
            rec(n)
        return out

    def tokens(self):
        out = []
        for nd in self.flat():
            d = nd.data
            op = nd.op
            if op == OP["EQ_ID_VAL"]:
                out.append(("eqidval", u16(d, 2), u16(d, 4)))
            elif op == OP["EQ_ID_ID"]:
                out.append(("eqidid", u16(d, 2), u16(d, 4)))
            elif op == OP["EQ_ID_VAL_LIST"]:
                qid, cnt = u16(d, 2), u16(d, 4)
                out.append(("eqidlist", qid, [u16(d, 6 + 2 * i) for i in range(cnt)]))
            elif op == OP["QUESTION_REF1"]:
                out.append(("qref", u16(d, 2)))
            elif op in (OP["UINT8"], OP["UINT16"], OP["UINT32"], OP["UINT64"]):
                out.append(("const", read_uint(d, 2, len(d) - 2)))
            elif op in (OP["TRUE"], OP["ONE"]):
                out.append(("const", 1))
            elif op in (OP["FALSE"], OP["ZERO"]):
                out.append(("const", 0))
            elif op == OP["ONES"]:
                out.append(("const", 0xFFFFFFFFFFFFFFFF))
            elif op == OP["GET"]:
                out.append(("get", u16(d, 2), u16(d, 4), d[6] if len(d) > 6 else 0))
            elif op in (OP["NOT"], OP["BITWISE_NOT"], OP["TO_BOOLEAN"], OP["TO_UINT"], OP["LENGTH"]):
                out.append(("un", OPNAME[op]))
            elif op in (OP["AND"], OP["OR"], OP["EQUAL"], OP["NOT_EQUAL"], OP["GREATER_THAN"],
                        OP["GREATER_EQUAL"], OP["LESS_THAN"], OP["LESS_EQUAL"], OP["BITWISE_AND"],
                        OP["BITWISE_OR"], OP["SHIFT_LEFT"], OP["SHIFT_RIGHT"], OP["ADD"], OP["SUBTRACT"],
                        OP["MULTIPLY"], OP["DIVIDE"], OP["MODULO"], OP["CATENATE"], OP["MATCH"]):
                out.append(("bin", OPNAME[op]))
            elif op == OP["CONDITIONAL"]:
                out.append(("tern",))
            else:
                out.append(("unk", OPNAME.get(op, hex(op))))
        return out

    def render(self, qname=lambda q: f"Q{q:#x}"):
        st = []
        sym = dict(AND="and", OR="or", EQUAL="==", NOT_EQUAL="!=", GREATER_THAN=">", GREATER_EQUAL=">=",
                   LESS_THAN="<", LESS_EQUAL="<=", BITWISE_AND="&", BITWISE_OR="|", SHIFT_LEFT="<<",
                   SHIFT_RIGHT=">>", ADD="+", SUBTRACT="-", MULTIPLY="*", DIVIDE="/", MODULO="%")
        for t in self.tokens():
            k = t[0]
            if k == "eqidval":
                st.append(f"{qname(t[1])} == {t[2]}")
            elif k == "eqidid":
                st.append(f"{qname(t[1])} == {qname(t[2])}")
            elif k == "eqidlist":
                st.append(f"{qname(t[1])} in {t[2]}")
            elif k == "qref":
                st.append(qname(t[1]))
            elif k == "const":
                st.append(str(t[1]))
            elif k == "get":
                st.append(f"get(vs{t[1]}@{t[2]:#x})")
            elif k == "un":
                a = st.pop() if st else "?"
                st.append(f"not ({a})" if t[1] == "NOT" else f"{t[1].lower()}({a})")
            elif k == "bin":
                b = st.pop() if st else "?"
                a = st.pop() if st else "?"
                st.append(f"({a} {sym.get(t[1], t[1])} {b})")
            elif k == "tern":
                c = st.pop() if st else "?"; b = st.pop() if st else "?"; a = st.pop() if st else "?"
                st.append(f"({a} ? {b} : {c})")
            else:
                st.append(f"<{t[1]}>")
        return " ; ".join(st) if st else "<empty>"

    def evaluate(self, qval):
        """qval(qid) returns an int or None. Returns True, False or None (None means it cannot evaluate)."""
        st = []
        try:
            for t in self.tokens():
                k = t[0]
                if k == "eqidval":
                    v = qval(t[1]); st.append(None if v is None else int(v == t[2]))
                elif k == "eqidid":
                    a, b = qval(t[1]), qval(t[2]); st.append(None if a is None or b is None else int(a == b))
                elif k == "eqidlist":
                    v = qval(t[1]); st.append(None if v is None else int(v in t[2]))
                elif k == "qref":
                    st.append(qval(t[1]))
                elif k == "const":
                    st.append(t[1])
                elif k == "un":
                    a = st.pop()
                    if a is None:
                        st.append(None)
                    elif t[1] in ("NOT",):
                        st.append(int(not a))
                    elif t[1] == "BITWISE_NOT":
                        st.append(~a & 0xFFFFFFFFFFFFFFFF)
                    elif t[1] == "TO_BOOLEAN":
                        st.append(int(bool(a)))
                    elif t[1] == "TO_UINT":
                        st.append(a)
                    else:
                        st.append(None)
                elif k == "bin":
                    b = st.pop(); a = st.pop()
                    o = t[1]
                    if o == "AND":
                        # three-valued logic
                        if a == 0 or b == 0:
                            st.append(0)
                        elif a is None or b is None:
                            st.append(None)
                        else:
                            st.append(1)
                    elif o == "OR":
                        if (a not in (None, 0)) or (b not in (None, 0)):
                            st.append(1)
                        elif a is None or b is None:
                            st.append(None)
                        else:
                            st.append(0)
                    elif a is None or b is None:
                        st.append(None)
                    else:
                        f = dict(EQUAL=lambda: int(a == b), NOT_EQUAL=lambda: int(a != b),
                                 GREATER_THAN=lambda: int(a > b), GREATER_EQUAL=lambda: int(a >= b),
                                 LESS_THAN=lambda: int(a < b), LESS_EQUAL=lambda: int(a <= b),
                                 BITWISE_AND=lambda: a & b, BITWISE_OR=lambda: a | b,
                                 SHIFT_LEFT=lambda: a << b, SHIFT_RIGHT=lambda: a >> b, ADD=lambda: a + b,
                                 SUBTRACT=lambda: a - b, MULTIPLY=lambda: a * b,
                                 DIVIDE=lambda: a // b if b else None, MODULO=lambda: a % b if b else None)
                        st.append(f[o]() if o in f else None)
                elif k == "tern":
                    c = st.pop(); b = st.pop(); a = st.pop()
                    st.append(None if a is None else (b if a else c))
                else:
                    st.append(None)
        except IndexError:
            return None
        if len(st) != 1 or st[0] is None:
            return None
        return bool(st[0])


# ---------------------------------------------------------------------------- formset walker
def _strip(s):
    return s.strip() if isinstance(s, str) else s


class FormSet:
    def __init__(self, pl_guid, strings, xami):
        self.pl_guid = pl_guid
        self.strings = strings  # en-US {id: str}
        self.xami = xami        # x-UEFI-AMI keywords {id: str}
        self.guid = None
        self.title = None
        self.help = None
        self.class_guids = []
        self.varstores = {}     # {id: dict}
        self.defaultstores = {}
        self.forms = {}         # {form id: dict(title, refs=[...])}
        self.questions = []     # list of dicts
        self.statements = []    # subtitles / text (context only)
        self.exprs = []         # (question index, kind, Expr)

    def s(self, sid):
        return self.strings.get(sid)


def _question_header(d):
    prompt, help_, qid, vsid, vsinfo, qflags = struct.unpack_from("<HHHHHB", d, 2)
    return prompt, help_, qid, vsid, vsinfo, qflags


def parse_formsets(db, pl, strings_by_lang):
    en = strings_by_lang.get("en-US") or (next(iter(strings_by_lang.values())) if strings_by_lang else {})
    xami = strings_by_lang.get("x-UEFI-AMI", {})
    formsets = []
    for ptype, poff, pkg in pl["packages"]:
        if ptype != 0x02:
            continue
        root = build_tree(pkg, poff)
        for fsnode in root.children:
            if fsnode.op != OP["FORM_SET"]:
                continue
            fs = FormSet(pl["guid"], en, xami)
            d = fsnode.data
            fs.guid = guid_str(d, 2)
            fs.title = _strip(fs.s(u16(d, 18)))
            fs.help = _strip(fs.s(u16(d, 20)))
            if len(d) > 22:
                ncls = d[22] & 0x3
                fs.class_guids = [guid_str(d, 23 + 16 * i) for i in range(ncls) if 23 + 16 * i + 16 <= len(d)]
            _walk(fs, fsnode.children, ctx=dict(form=None, conds=[], question=None))
            formsets.append(fs)
    return formsets


def _walk(fs, nodes, ctx):
    i = 0
    nodes = list(nodes)
    while i < len(nodes):
        nd = nodes[i]
        op, d = nd.op, nd.data
        if op == OP["VARSTORE"]:
            vid = u16(d, 18)
            fs.varstores[vid] = dict(id=vid, kind="buffer", guid=guid_str(d, 2), size=u16(d, 20),
                                     name=cstr(d, 22))
        elif op == OP["VARSTORE_EFI"]:
            vid = u16(d, 2)
            vs = dict(id=vid, kind="efi", guid=guid_str(d, 4), attributes=u32(d, 20))
            if len(d) >= 26:
                vs["size"] = u16(d, 24)
                vs["name"] = cstr(d, 26)
            fs.varstores[vid] = vs
        elif op == OP["VARSTORE_NAME_VALUE"]:
            vid = u16(d, 2)
            fs.varstores[vid] = dict(id=vid, kind="name_value", guid=guid_str(d, 4), name=None, size=None)
        elif op == OP["DEFAULTSTORE"]:
            fs.defaultstores[u16(d, 4)] = _strip(fs.s(u16(d, 2)))
        elif op in (OP["FORM"], OP["FORM_MAP"]):
            fid = u16(d, 2)
            title = _strip(fs.s(u16(d, 4))) if op == OP["FORM"] else (
                _strip(fs.s(u16(d, 4))) if len(d) >= 6 else None)
            fs.forms.setdefault(fid, dict(id=fid, title=title, refs=[], conds=list(ctx["conds"])))
            _walk(fs, nd.children, dict(ctx, form=fid, question=None))
        elif op in COND_OPS:
            # leading expression children form the condition; the rest are the guarded statements
            ch = nd.children
            k = 0
            while k < len(ch) and ch[k].op in EXPR_OPS:
                k += 1
            ex = Expr(ch[:k])
            cond = dict(kind=COND_OPS[op], expr=ex)
            _walk(fs, ch[k:], dict(ctx, conds=ctx["conds"] + [cond]))
        elif op in QUESTION_OPS:
            q = _question(fs, nd, ctx)
            if q is not None and op != OP["REF"]:
                pass
            # children: options, defaults, nested conditions on options and so on
            _walk_question_children(fs, q, nd.children, ctx)
        elif op in (OP["SUBTITLE"], OP["TEXT"]):
            prompt = u16(d, 2)
            fs.statements.append(dict(form=ctx["form"], op=OPNAME[op], text=_strip(fs.s(prompt)),
                                      text2=_strip(fs.s(u16(d, 6))) if op == OP["TEXT"] and len(d) >= 8 else None,
                                      conds=list(ctx["conds"])))
            if nd.children:
                _walk(fs, nd.children, ctx)
        elif op == OP["GUID"]:
            pass  # vendor extensions (EDK2 label, class and subclass, AMI), ignored
        elif nd.children:
            # any other scoped opcode: descend (statements inside, for example LOCKED or MODAL)
            if op not in EXPR_OPS:
                _walk(fs, nd.children, ctx)
        i += 1


def _question(fs, nd, ctx):
    d, op = nd.data, nd.op
    prompt, help_, qid, vsid, vsinfo, qflags = _question_header(d)
    q = dict(formset_guid=fs.guid, formset=fs.title, form_id=ctx["form"], qid=qid,
             op=OPNAME[op], prompt=_strip(fs.s(prompt)), help=_strip(fs.s(help_)),
             keyword=fs.xami.get(prompt), varstore_id=vsid, var_offset=vsinfo if vsid else None,
             qflags=qflags, conds=list(ctx["conds"]), options=[], defaults=[], src_off=nd.off)
    if op in (OP["ONE_OF"], OP["NUMERIC"]):
        flags = d[13]
        size = 1 << (flags & 0x03)
        q["width"] = size
        q["display"] = {0x00: "int_dec", 0x10: "uint_dec", 0x20: "uint_hex"}.get(flags & 0x30, hex(flags & 0x30))
        q["flags"] = flags
        if len(d) >= 14 + 3 * size:
            mn, mx, st = (read_uint(d, 14 + i * size, size) for i in range(3))
            q["min"], q["max"], q["step"] = mn, mx, st
    elif op == OP["CHECKBOX"]:
        q["width"] = 1
        f = d[13]
        q["flags"] = f
        if f & 0x01:
            q["defaults"].append(dict(default_id=0, value=1, src="checkbox_flag"))
        else:
            q["defaults"].append(dict(default_id=0, value=0, src="checkbox_flag_clear"))
        if f & 0x02:
            q["defaults"].append(dict(default_id=1, value=1, src="checkbox_flag"))
    elif op == OP["STRING"]:
        q["min_size"], q["max_size"], q["flags"] = d[13], d[14], d[15]
        q["width"] = d[14] * 2
    elif op == OP["PASSWORD"]:
        q["min_size"], q["max_size"] = u16(d, 13), u16(d, 15)
        q["width"] = q["max_size"] * 2
    elif op == OP["ORDERED_LIST"]:
        q["max_containers"], q["flags"] = d[13], d[14]
        q["width"] = None  # = max_containers * option size, fixed after options are known
    elif op == OP["DATE"]:
        q["width"] = 4
    elif op == OP["TIME"]:
        q["width"] = 3
    elif op == OP["REF"]:
        tgt = dict(form_id=u16(d, 13) if len(d) >= 15 else None)
        if len(d) >= 17:
            tgt["question_id"] = u16(d, 15)
        if len(d) >= 33:
            tgt["formset_guid"] = guid_str(d, 17)
        if ctx["form"] is not None and ctx["form"] in fs.forms:
            fs.forms[ctx["form"]]["refs"].append(dict(tgt, prompt=q["prompt"], conds=list(ctx["conds"])))
        q["ref"] = tgt
        q["width"] = 0
    elif op == OP["ACTION"]:
        q["width"] = 0
    fs.questions.append(q)
    return q


def _walk_question_children(fs, q, children, ctx, conds=None):
    conds = conds or []
    i = 0
    ch = list(children)
    while i < len(ch):
        nd = ch[i]
        op, d = nd.op, nd.data
        if op == OP["ONE_OF_OPTION"]:
            sid, fl, ty = u16(d, 2), d[4], d[5]
            tname, tsize = IFR_TYPE.get(ty, ("?", 0))
            val = read_uint(d, 6, tsize) if tsize and ty <= 4 else None
            opt = dict(value=val, text=_strip(fs.s(sid)), type=tname)
            if fl & 0x10:
                opt["default"] = True
                q["defaults"].append(dict(default_id=0, value=val, src="option_flag"))
            if fl & 0x20:
                opt["default_mfg"] = True
                q["defaults"].append(dict(default_id=1, value=val, src="option_flag"))
            if conds:
                opt["conds"] = list(conds)
            q["options"].append(opt)
            if q["op"] == "ORDERED_LIST" and q.get("width") is None and tsize:
                q["width"] = q["max_containers"] * tsize
                q["elem_size"] = tsize
        elif op == OP["DEFAULT"]:
            did, ty = u16(d, 2), d[4]
            tname, tsize = IFR_TYPE.get(ty, ("?", 0))
            if ty <= 4 and not nd.scope:
                val = read_uint(d, 5, tsize)
                q["defaults"].append(dict(default_id=did, value=val, src="default_op"))
            elif ty == 11 and not nd.scope:  # buffer
                q["defaults"].append(dict(default_id=did, value=bytes(d[5:]).hex(), src="default_op_buffer"))
            elif ty == 7 and not nd.scope:
                q["defaults"].append(dict(default_id=did, value=_strip(fs.s(u16(d, 5))), src="default_op_string"))
            else:
                ex = Expr([c for c in nd.children if c.op in EXPR_OPS])
                q["defaults"].append(dict(default_id=did, value=None, expr=ex, src="default_expr"))
        elif op in COND_OPS:
            k = 0
            cc = nd.children
            while k < len(cc) and cc[k].op in EXPR_OPS:
                k += 1
            _walk_question_children(fs, q, cc[k:], ctx, conds + [dict(kind=COND_OPS[op], expr=Expr(cc[:k]))])
        elif op in (OP["INCONSISTENT_IF"], OP["NO_SUBMIT_IF"], OP["WARNING_IF"]):
            q.setdefault("checks", []).append(dict(kind=OPNAME[op], msg=_strip(fs.s(u16(d, 2))),
                                                   expr=Expr([c for c in nd.children if c.op in EXPR_OPS])))
        elif op == OP["VALUE"]:
            q["value_expr"] = Expr([c for c in nd.children if c.op in EXPR_OPS])
        elif op == OP["REFRESH"] or op == OP["REFRESH_ID"]:
            q["refresh"] = True
        i += 1


# ---------------------------------------------------------------------------- top-level API
class HiiDb:
    def __init__(self, data):
        self.data = data
        self.package_lists = parse_package_lists(data)
        self.strings = {}       # {pl guid: {lang: {id: str}}}
        self.formsets = []
        for pl in self.package_lists:
            sl = {}
            for ptype, poff, pkg in pl["packages"]:
                if ptype == 0x04:
                    lang, st = parse_string_package(pkg)
                    sl.setdefault(lang, {}).update(st)
            self.strings[pl["guid"]] = sl
            self.formsets.extend(parse_formsets(data, pl, sl))
        self._build_paths()

    # form path via REF graph ------------------------------------------------
    def _build_paths(self):
        parent = {}  # {(fs_guid, form_id): (fs_guid, form_id, prompt)}
        fs_by_guid = {}
        for fs in self.formsets:
            fs_by_guid.setdefault(fs.guid, fs)
        for fs in self.formsets:
            for fid, f in fs.forms.items():
                for r in f["refs"]:
                    tfs = r.get("formset_guid") or fs.guid
                    if tfs == "00000000-0000-0000-0000-000000000000":
                        tfs = fs.guid
                    tfid = r.get("form_id")
                    if tfid is None:
                        continue
                    if tfs != fs.guid and tfid == 0 and tfs in fs_by_guid:
                        # REF3 to the first form of another formset
                        tf = next(iter(fs_by_guid[tfs].forms), None)
                        tfid = tf if tf is not None else tfid
                    key = (tfs, tfid)
                    if key not in parent and key != (fs.guid, fid):
                        parent[key] = (fs.guid, fid, r.get("prompt"))
        self.parent = parent
        self.fs_by_guid = fs_by_guid

    def form_path(self, fs_guid, form_id):
        path = []
        seen = set()
        key = (fs_guid, form_id)
        while key and key not in seen:
            seen.add(key)
            fs = self.fs_by_guid.get(key[0])
            title = fs.forms.get(key[1], {}).get("title") if fs else None
            path.append(title or f"form{key[1]:#x}")
            p = self.parent.get(key)
            key = (p[0], p[1]) if p else None
        return list(reversed(path))

    def questions(self):
        """Flat list of question dicts with varstore info and form path resolved."""
        out = []
        for fs in self.formsets:
            qid_map = {}
            for q in fs.questions:
                qid_map.setdefault(q["qid"], q)
            for q in fs.questions:
                vs = fs.varstores.get(q["varstore_id"]) if q["varstore_id"] else None
                q2 = dict(q)
                q2["varstore"] = dict(vs) if vs else None
                q2["form_title"] = fs.forms.get(q["form_id"], {}).get("title") if q["form_id"] is not None else None
                q2["form_path"] = self.form_path(fs.guid, q["form_id"]) if q["form_id"] is not None else []
                q2["_fs"] = fs
                q2["_qid_map"] = qid_map
                out.append(q2)
        return out


def clean_prompt(s):
    """Gigabyte/AMI prompts carry a right-hand 'current value' column after 0x7F; keep the label only."""
    return s.split("\x7f")[0].strip() if isinstance(s, str) else s


def qname_factory(qid_map, varstores=None):
    def qn(qid):
        q = qid_map.get(qid)
        if not q:
            return f"Q{qid:#x}"
        vs = (varstores or {}).get(q.get("varstore_id")) if q.get("varstore_id") else None
        loc = ""
        if q.get("var_offset") is not None:
            loc = f"{(vs or {}).get('name') or 'vs%d' % q['varstore_id']}@{q['var_offset']:#x}"
        p = clean_prompt(q.get("prompt"))
        return f"[{p}]({loc})" if p else f"[{loc or 'Q%#x' % qid}]"
    return qn


def question_to_json(q):
    qn = qname_factory(q["_qid_map"], q["_fs"].varstores)
    j = {k: v for k, v in q.items() if not k.startswith("_") and k not in ("conds", "defaults", "checks",
                                                                            "value_expr", "options")}
    j["options"] = [{k: v for k, v in o.items() if k != "conds"} | (
        {"conds": [dict(kind=c["kind"], expr=c["expr"].render(qn)) for c in o["conds"]]} if o.get("conds") else {})
        for o in q["options"]]
    j["defaults"] = [{k: (v.render(qn) if isinstance(v, Expr) else v) for k, v in dd.items()} for dd in q["defaults"]]
    j["conds"] = [dict(kind=c["kind"], expr=c["expr"].render(qn)) for c in q["conds"]]
    return j


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("hiidb")
    ap.add_argument("--json", help="write all questions as JSON")
    ap.add_argument("--grep", help="print questions whose prompt/form matches REGEX")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args(argv)
    db = HiiDb(open(a.hiidb, "rb").read())
    qs = db.questions()
    if a.summary or not (a.json or a.grep):
        for pl in db.package_lists:
            print(f"package list {pl['guid']} @0x{pl['offset']:x} len 0x{pl['length']:x}: "
                  + ", ".join(f"{PKG_NAMES.get(t, hex(t))}" for t, _, _ in pl["packages"][:6])
                  + (" ..." if len(pl["packages"]) > 6 else ""))
        for fs in db.formsets:
            nq = sum(1 for q in fs.questions if q["op"] != "REF")
            print(f"formset {fs.guid} '{fs.title}': {len(fs.forms)} forms, {nq} questions")
            for vid, vs in sorted(fs.varstores.items()):
                print(f"   varstore {vid:#06x} {vs['kind']:<10} {vs.get('name')!s:<24} {vs['guid']} size={vs.get('size')}")
    if a.grep:
        rx = re.compile(a.grep, re.I)
        for q in qs:
            hay = " / ".join([q["formset"] or "", *q["form_path"], q["prompt"] or ""])
            if rx.search(hay):
                vs = q["varstore"]
                print(f"{' > '.join(q['form_path'])} :: {q['prompt']!r} {q['op']} "
                      f"{(vs or {}).get('name')}@{q['var_offset']} w={q.get('width')} "
                      f"opts={len(q['options'])} def={[d['value'] for d in q['defaults'] if d['default_id'] == 0]}"
                      f"{' [suppressif]' if any(c['kind'] == 'suppressif' for c in q['conds']) else ''}")
    if a.json:
        with open(a.json, "w") as f:
            json.dump([question_to_json(q) for q in qs], f, indent=1, default=str)
        print(f"wrote {a.json}: {len(qs)} questions")


if __name__ == "__main__":
    main(sys.argv[1:])
