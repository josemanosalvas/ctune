#!/usr/bin/env python3
"""Decode the live AMI Aptio V (Gigabyte) BIOS setup configuration from EFI variables and the HII database.

Inputs (the defaults come from the snapshot in $CTUNE_SNAPSHOT; ram_snapshot.py passes them explicitly):
  --hiidb    raw/fwmem/hiidb.bin           HII database exported by AMI (EFI var 'HiiDB' holds its physical address)
  --efivars  raw/efivars/                  copies of /sys/firmware/efi/efivars/* (4-byte attribute header + data)
  --live     /sys/firmware/efi/efivars     read-only fallback for varstores that were not captured
Outputs (--out, default <snapshot>/decoded/):
  bios_settings.json, bios_settings.md, efivars_extra/ (copies of the live fallback vars that were used)

Formats:
  * efivarfs files: the first 4 bytes are UINT32 attributes, then the data (Linux Documentation/filesystems/efivarfs.rst).
  * IFR / HII: see hii_ifr.py (UEFI 2.10 ch. 33).
  * AMI NVAR (StdDefaults): UEFITool common/nvram.h (https://github.com/LongSoft/UEFITool/blob/new_engine/common/nvram.h)
    NVAR_ENTRY_HEADER {UINT32 Signature 'NVAR'; UINT16 Size; UINT32 Next:24; UINT32 Attributes:8}
    attributes RUNTIME 0x01, ASCII_NAME 0x02, GUID 0x04, DATA_ONLY 0x08, EXT_HEADER 0x10, HW_ERROR_RECORD 0x20,
    AUTH_WRITE 0x40, VALID 0x80; without the GUID attribute a 1-byte index into the GUID store follows the header;
    the GUID store is at the end of the store and grows downwards (index 0 is the last 16 bytes)
    (UEFITool common/ffsparser.cpp parseNvarStore).
  * Gigabyte ProfileN (GUID b05e6b5f-6ed8-4015-b5c5-b1049faf3e5b): reverse-engineered for ctune, with no public spec:
    header 0x38 bytes {u16 magic 0x1964; u16 payload_len; u16 0x3811; u8 flag; u8 sum8(payload);
    u32 record_count; char[12] BIOS version and board id ('F16b8ARNR003'); CHAR16 name[16]}
    followed by records {u32 tse_var_index; u16 len; u8 data[len]}. On the tested board the lengths add up exactly,
    and the checksum byte equals sum(payload) & 0xFF in all 9 slots.
"""
import argparse
import glob
import importlib.util
import json
import os
import re
import struct
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.environ.get("CTUNE_SNAPSHOT", "")   # a ctune snapshot directory, for the argument defaults


def load_hii_module():
    spec = importlib.util.spec_from_file_location("hii_ifr", os.path.join(HERE, "hii_ifr.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


H = load_hii_module()
clean = H.clean_prompt

GIGABYTE_PROFILE_GUID = "b05e6b5f-6ed8-4015-b5c5-b1049faf3e5b"
# Variable-length UEFI globals that AMI TSE declares as 2-byte placeholder varstores (handled by TSE code,
# not by offset): PlatformLang/PlatformLangCodes (UEFI 2.10 3.3 Globally Defined Variables), BootOrder,
# LegacyDevOrder.
VARLEN_STORES = {"PlatformLang", "PlatformLangCodes", "BootOrder", "LegacyDevOrder", "DriverOrder", "Timeout"}


# ----------------------------------------------------------------------------- efivars
def read_efivar_file(path):
    raw = open(path, "rb").read()
    return struct.unpack_from("<I", raw, 0)[0], raw[4:]


def split_name(fn):
    m = re.match(r"^(.*)-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", fn)
    return (m.group(1), m.group(2)) if m else (fn, None)


def load_efivars(d):
    out = {}
    for p in sorted(glob.glob(os.path.join(d, "*"))):
        name, g = split_name(os.path.basename(p))
        if g:
            attr, data = read_efivar_file(p)
            out[(name, g)] = dict(attr=attr, data=data, source=p)
    return out


# ----------------------------------------------------------------------------- AMI NVAR
def parse_nvar_store(d):
    """Returns (entries, guid_store). entries: dict(name, guid, attr, data, offset)."""
    entries = []
    p = 0
    while p + 10 <= len(d) and d[p:p + 4] == b"NVAR":
        size = struct.unpack_from("<H", d, p + 4)[0]
        nxt = int.from_bytes(d[p + 6:p + 9], "little")
        attr = d[p + 9]
        q = p + 10
        e = dict(offset=p, size=size, next=nxt, attr=attr)
        if not attr & 0x08:
            if attr & 0x04:
                e["guid"] = str(uuid.UUID(bytes_le=d[q:q + 16])); q += 16
            else:
                e["guid_index"] = d[q]; q += 1
            if attr & 0x02:
                z = d.index(b"\0", q); e["name"] = d[q:z].decode("ascii"); q = z + 1
            else:
                z = q
                while d[z:z + 2] != b"\0\0":
                    z += 2
                e["name"] = d[q:z].decode("utf-16le"); q = z + 2
        end = p + size
        if attr & 0x10:  # extended header at the end: ... ExtAttr ... [checksum] u16 ExtSize
            ext = struct.unpack_from("<H", d, end - 2)[0]
            end -= ext
        e["data"] = d[q:end]
        entries.append(e)
        if size < 10:
            break
        p += size
    # GUID store: 16-byte GUIDs from the end of the buffer downwards
    guids = []
    i = len(d) - 16
    while i >= p:
        guids.append(str(uuid.UUID(bytes_le=d[i:i + 16])))
        i -= 16
    for e in entries:
        if "guid_index" in e and e["guid_index"] < len(guids):
            e["guid"] = guids[e["guid_index"]]
    return entries, guids


# ----------------------------------------------------------------------------- Gigabyte profiles
def parse_gbt_profile(d):
    magic, plen, ver, flag, csum, count = struct.unpack_from("<HHHBBI", d, 0)
    bios = d[12:24].decode("ascii", "replace")
    name = d[24:56].decode("utf-16le", "replace").split("\0")[0]
    recs = {}
    order = []
    p = 0x38
    ok = True
    while p + 6 <= len(d):
        idx, n = struct.unpack_from("<IH", d, p)
        if p + 6 + n > len(d):
            ok = False
            break
        recs[idx] = d[p + 6:p + 6 + n]
        order.append(idx)
        p += 6 + n
    return dict(magic=magic, payload_len=plen, version=ver, flag=flag, checksum=csum,
                checksum_ok=(sum(d[0x38:]) & 0xFF) == csum, payload_len_ok=(len(d) - 0x38 == plen),
                record_count=count, record_count_ok=(count == len(recs)), parsed_to_end=ok and p == len(d),
                bios=bios, name=name, records=recs, order=order)


# ----------------------------------------------------------------------------- value helpers
def as_signed(v, w):
    bits = 8 * w
    return v - (1 << bits) if v >> (bits - 1) else v


def read_value(q, data):
    off, w = q.get("var_offset"), q.get("width")
    if off is None or not w or data is None or off + w > len(data):
        return None
    raw = data[off:off + w]
    op = q["op"]
    if op == "STRING" or op == "PASSWORD":
        return raw.decode("utf-16le", "replace").split("\0")[0]
    if op == "ORDERED_LIST":
        es = q.get("elem_size") or 1
        return [int.from_bytes(raw[i:i + es], "little") for i in range(0, len(raw), es)]
    if op == "DATE" or op == "TIME":
        return raw.hex()
    v = int.from_bytes(raw, "little")
    return v


def value_text(q, v):
    if v is None:
        return None
    if q["op"] in ("ONE_OF", "CHECKBOX"):
        if q["op"] == "CHECKBOX":
            return "Enabled" if v else "Disabled"
        for o in q["options"]:
            if o["value"] == v:
                return clean(o["text"])
        return None
    if q["op"] == "ORDERED_LIST":
        m = {o["value"]: clean(o["text"]) for o in q["options"]}
        return ", ".join(m.get(x, str(x)) for x in v)
    if q["op"] == "NUMERIC":
        if q.get("display") == "int_dec":
            return str(as_signed(v, q["width"]))
        if q.get("display") == "uint_hex":
            return hex(v)
        return str(v)
    return str(v)


def ifr_default(q, default_id=0):
    """Order used by the EDK2 browser (SetupBrowserDxe Setup.c GetQuestionDefault): DEFAULT opcode,
    then ONE_OF option flags, then CHECKBOX flags."""
    ds = [d for d in q["defaults"] if d.get("default_id") == default_id]
    for src in ("default_op", "default_op_buffer", "default_op_string", "default_expr", "option_flag",
                "checkbox_flag", "checkbox_flag_clear"):
        for d in ds:
            if d["src"] == src and d.get("value") is not None:
                return d["value"], src
    for d in ds:
        if d["src"] == "default_expr":
            return None, "expr:" + d["expr"].render()
    return None, None


def is_valid(q, v):
    if v is None:
        return None
    if q["op"] == "ONE_OF":
        return any(o["value"] == v for o in q["options"])
    if q["op"] == "CHECKBOX":
        return v in (0, 1)
    if q["op"] == "NUMERIC":
        mn, mx, st = q.get("min"), q.get("max"), q.get("step")
        if mn is None:
            return None
        if q.get("display") == "int_dec":
            v, mn, mx = as_signed(v, q["width"]), as_signed(mn, q["width"]), as_signed(mx, q["width"])
        ok = mn <= v <= mx
        if ok and st and st > 1:
            ok = (v - mn) % st == 0
        return ok
    if q["op"] == "ORDERED_LIST":
        vals = {o["value"] for o in q["options"]}
        return all(x in vals or x == 0 for x in v)
    return None


AUTO_RX = re.compile(r"\bauto\b", re.I)
# Gigabyte's LLC questions are numerics holding a list code. An untouched one holds the all-ones sentinel (F16b); on
# F19c both read 0 while the BIOS menu shows "Auto", so code 0 is Auto too.
GBT_AUTO_ZERO = {"CPU Vcore Loadline Calibration", "Vcore SOC Loadline Calibration"}


def auto_state(q, v, txt, dflt):
    """'Auto' / 'default' / 'manual' classification for the focused table."""
    if v is None:
        return "n/a"
    if txt and AUTO_RX.search(txt):
        return "Auto"
    w = q.get("width") or 0
    if q["op"] == "NUMERIC" and w and v == (1 << (8 * w)) - 1:
        return "Auto (all-ones sentinel)"
    if q["op"] == "NUMERIC" and v == 0 and clean(q.get("prompt") or "") in GBT_AUTO_ZERO:
        return "Auto (code 0)"
    if dflt is not None and v == dflt:
        return "default"
    if dflt is None:
        return "n/a (no IFR default)"
    return "MANUAL"


# ----------------------------------------------------------------------------- main decode
def decode(args):
    db = H.HiiDb(open(args.hiidb, "rb").read())
    qs = db.questions()
    efv = load_efivars(args.efivars)
    extra_used = {}

    def get_var(name, guid):
        k = (name, guid)
        if k in efv:
            return efv[k]
        if args.live and name:
            p = os.path.join(args.live, f"{name}-{guid}")
            if os.path.exists(p):
                try:
                    attr, data = read_efivar_file(p)
                except OSError:
                    return None
                efv[k] = dict(attr=attr, data=data, source=p + " (live fallback)")
                extra_used[k] = p
                return efv[k]
        return None

    # StdDefaults (AMI NVAR)
    std = {}
    std_entries = []
    sd = efv.get(("StdDefaults", "4599d26f-1a11-49b8-b91f-858745cff824"))
    if sd:
        std_entries, std_guids = parse_nvar_store(sd["data"])
        for e in std_entries:
            std[(e.get("name"), e.get("guid"))] = e["data"]

    # varstore check
    vs_report = []
    seen_vs = set()
    for fs in db.formsets:
        for vid, vs in sorted(fs.varstores.items()):
            k = (vs.get("name"), vs["guid"])
            if k in seen_vs:
                continue
            seen_vs.add(k)
            ev = get_var(*k) if vs.get("name") else None
            vs_report.append(dict(formset=fs.title, name=vs.get("name"), guid=vs["guid"], kind=vs["kind"],
                                  ifr_size=vs.get("size"), efivar_size=len(ev["data"]) if ev else None,
                                  efivar_source=ev["source"] if ev else None,
                                  size_match=(ev is not None and len(ev["data"]) == vs.get("size")),
                                  variable_length=k[0] in VARLEN_STORES,
                                  in_StdDefaults=k in std,
                                  StdDefaults_size=len(std[k]) if k in std else None))

    # per-formset value lookup for condition evaluation
    def make_qval(fs, qid_map, data_override=None):
        def qval(qid):
            q = qid_map.get(qid)
            if not q or not q.get("varstore_id"):
                return None
            vs = fs.varstores.get(q["varstore_id"])
            if not vs or not vs.get("name"):
                return None
            k = (vs["name"], vs["guid"])
            if data_override and k in data_override:
                data = data_override[k]
            else:
                ev = get_var(*k)
                data = ev["data"] if ev else None
            if data is None and (vs["name"], q["var_offset"]) in assumed:
                return assumed[(vs["name"], q["var_offset"])]
            v = read_value(q, data)
            return v if isinstance(v, int) else None
        return qval

    # Assumed values for boot-time-only pseudo varstores used in conditions (no runtime efivar exists).
    # SystemAccess (AMI TSE): 0 = administrator, 1 = user. With no setup passwords the user is always
    # administrator. AMITSESetup holds the user and admin password buffers (2 x 40 bytes), all zero on the
    # tested board.
    assumed = {}
    tse = get_var("AMITSESetup", "c811fa38-42c8-4579-a9bb-60e94eddfb34")
    if tse and not any(tse["data"][:80]):
        assumed[("SystemAccess", 0)] = 0
    for a in (args.assume or []):
        m = re.match(r"^(\w+)@(\w+)=(\w+)$", a)
        if m:
            assumed[(m.group(1), int(m.group(2), 0))] = int(m.group(3), 0)

    qval_cache = {}
    rows = []
    for q in qs:
        fs = q["_fs"]
        if id(fs) not in qval_cache:
            qval_cache[id(fs)] = make_qval(fs, q["_qid_map"])
        qval = qval_cache[id(fs)]
        qn = H.qname_factory(q["_qid_map"], fs.varstores)
        vs = q["varstore"]
        r = dict(formset=fs.title, formset_guid=fs.guid, form_path=" > ".join(q["form_path"]),
                 prompt=clean(q["prompt"]), prompt_raw=q["prompt"], help=q["help"], op=q["op"], qid=q["qid"],
                 keyword=q.get("keyword"))
        if q["op"] == "REF":
            r["ref"] = q.get("ref")
        if vs:
            r.update(varstore=vs.get("name"), varstore_guid=vs["guid"], varstore_size=vs.get("size"),
                     offset=q["var_offset"], width=q.get("width"))
        for k in ("min", "max", "step", "display", "max_containers", "max_size"):
            if k in q:
                r[k] = q[k]
        if q["options"]:
            r["options"] = {str(o["value"]): clean(o["text"]) for o in q["options"]}
            oc = [o for o in q["options"] if o.get("conds")]
            if oc:
                r["option_conds"] = {str(o["value"]): [dict(kind=c["kind"], expr=c["expr"].render(qn),
                                                             now=c["expr"].evaluate(qval)) for c in o["conds"]]
                                     for o in oc}
        d0, dsrc = ifr_default(q, 0)
        d1, _ = ifr_default(q, 1)
        r["ifr_default"] = d0
        r["ifr_default_src"] = dsrc
        if d1 is not None and d1 != d0:
            r["ifr_default_mfg"] = d1
        r["conds"] = []
        hidden = False
        unknown = False
        grayed = False
        for c in q["conds"]:
            ev = c["expr"].evaluate(qval)
            r["conds"].append(dict(kind=c["kind"], expr=c["expr"].render(qn), now=ev))
            if c["kind"] in ("suppressif", "disableif"):
                if ev is True:
                    hidden = True
                elif ev is None:
                    unknown = True
            elif c["kind"] == "grayoutif" and ev is True:
                grayed = True
        r["under_suppressif"] = any(c["kind"] == "suppressif" for c in q["conds"])
        r["visible_now"] = False if hidden else (None if unknown else True)
        r["grayed_now"] = grayed
        if vs and vs.get("name"):
            ev = get_var(vs["name"], vs["guid"])
            data = ev["data"] if ev else None
            v = read_value(q, data)
            r["value"] = v
            r["value_text"] = value_text(q, v)
            r["valid"] = is_valid(q, v)
            r["default_text"] = value_text(q, d0) if isinstance(d0, int) else (d0 if d0 is not None else None)
            r["non_default"] = (v is not None and d0 is not None and isinstance(d0, int) and v != d0)
            sk = (vs["name"], vs["guid"])
            if sk in std and q.get("width"):
                sv = read_value(q, std[sk])
                r["stddefaults_value"] = sv
                r["stddefaults_text"] = value_text(q, sv)
                r["non_stddefault"] = (v is not None and sv is not None and v != sv)
                r["ifr_vs_stddefaults_agree"] = (d0 == sv) if isinstance(d0, int) and isinstance(sv, int) else None
            r["auto_state"] = auto_state(q, v, r["value_text"], d0 if isinstance(d0, int) else None)
        r["_q"] = q
        rows.append(r)

    # dedupe by storage location into settings
    settings = {}
    for r in rows:
        if r.get("offset") is None or r["op"] in ("REF", "ACTION"):
            continue
        k = (r["varstore"], r["varstore_guid"], r["offset"], r["width"])
        s = settings.get(k)
        if not s:
            s = dict(varstore=r["varstore"], varstore_guid=r["varstore_guid"], offset=r["offset"],
                     width=r["width"], prompts=[], locations=[], value=r.get("value"),
                     value_text=r.get("value_text"), ifr_default=r.get("ifr_default"),
                     default_text=r.get("default_text"), non_default=r.get("non_default"),
                     stddefaults_value=r.get("stddefaults_value"), non_stddefault=r.get("non_stddefault"),
                     valid=r.get("valid"), auto_state=r.get("auto_state"), visible_now=r.get("visible_now"),
                     op=r["op"], options=r.get("options"), min=r.get("min"), max=r.get("max"),
                     step=r.get("step"))
            settings[k] = s
        if (r["prompt"] or "?") not in s["prompts"]:
            s["prompts"].append(r["prompt"] or "?")
        s["locations"].append(f"{r['form_path']} :: {r['prompt']}")
        # visible anywhere?
        if r.get("visible_now") is True:
            s["visible_now"] = True
        elif r.get("visible_now") is None and s["visible_now"] is False:
            s["visible_now"] = None
        # value text: option sets can differ per instance (alternative pages under different suppressif);
        # prefer the text of an instance that is visible under the current values
        if r.get("value_text") and (s["value_text"] is None or (r.get("visible_now") is True and not s.get("_vt_vis"))):
            s["value_text"] = r["value_text"]
            s["_vt_vis"] = r.get("visible_now") is True
            s["default_text"] = r.get("default_text")
        if r.get("valid") is True:
            s["valid"] = True
        if s.get("ifr_default") is None and r.get("ifr_default") is not None:
            s["ifr_default"], s["default_text"], s["non_default"] = r["ifr_default"], r.get("default_text"), r.get("non_default")

    # Gigabyte profiles
    profiles = []
    big = {("Setup", "ec87d643-eba4-4bb5-a1e5-3f3e36b20da9"), ("AmdSetup", "3a997502-647a-4c82-998e-52ef9486a247"),
           ("AOD_SETUP", "5ed15dc0-edef-4161-9151-6014c4cc630c")}
    for (name, g), ev in sorted(efv.items()):
        if g != GIGABYTE_PROFILE_GUID or not name.startswith("Profile"):
            continue
        pr = parse_gbt_profile(ev["data"])
        # map each record index to a variable: a record maps to a variable when exactly one non-profile efivar has
        # the same length, and the length is unique inside the profile and at least 40 bytes (small stores stay
        # unmapped). Records are AMI TSE variable-list indices, which are not published, so smaller records are
        # only guessed.
        recmap = {}
        guesses = {}
        sizes = [len(b) for b in pr["records"].values()]
        for idx, blob in pr["records"].items():
            if not blob:
                continue
            cands = [k for k, v in efv.items() if len(v["data"]) == len(blob) and k[1] != GIGABYTE_PROFILE_GUID]
            same = [k for k in cands if efv[k]["data"] == blob]
            if len(blob) >= 40 and len(cands) == 1 and sizes.count(len(blob)) == 1:
                recmap[idx] = cands[0]
            elif len(cands) == 1 or len(same) == 1:
                kk = (same or cands)[0]
                guesses[idx] = f"{kk[0]}-{kk[1]}" + (" (identical to current)" if same else " (size match)")
        override = {recmap[i]: pr["records"][i] for i in recmap}
        diffs = []
        if override:
            for k, s in settings.items():
                vk = (k[0], k[1])
                if vk not in override:
                    continue
                cand = [r for r in rows if r.get("varstore") == k[0] and r.get("offset") == k[2]
                        and r.get("width") == k[3] and r["varstore_guid"] == k[1]]
                r0 = next((r for r in cand if r.get("visible_now") is True), cand[0])
                pv = read_value(r0["_q"], override[vk])
                if pv != s["value"]:
                    diffs.append(dict(setting=" / ".join(p_ or "?" for p_ in s["prompts"]), where=s["locations"][0],
                                      varstore=k[0], offset=k[2], current=s["value"], current_text=s["value_text"],
                                      profile=pv, profile_text=value_text(r0["_q"], pv)))
        profiles.append(dict(slot=name, name=pr["name"], bios=pr["bios"], flag=pr["flag"],
                             checksum_ok=pr["checksum_ok"], payload_len_ok=pr["payload_len_ok"],
                             record_count=pr["record_count"], record_count_ok=pr["record_count_ok"],
                             parsed_to_end=pr["parsed_to_end"],
                             records={str(i): len(b) for i, b in pr["records"].items()},
                             mapped={str(i): f"{k[0]}-{k[1]}" for i, k in recmap.items()},
                             guessed=guesses,
                             identical_to_current={f"{k[0]}": pr["records"][i] == get_var(*k)["data"]
                                                   for i, k in recmap.items()},
                             diffs_vs_current=diffs))

    return dict(db=db, rows=rows, settings=settings, vs_report=vs_report, std_entries=std_entries,
                assumed={f"{k[0]}@{k[1]}": v for k, v in assumed.items()},
                profiles=profiles, extra_used=extra_used, efv=efv)


# ----------------------------------------------------------------------------- reporting
def fmt(v):
    if v is None:
        return ""
    if isinstance(v, int):
        return f"{v} (0x{v:X})" if v > 9 else str(v)
    return str(v)


def md_escape(s):
    return (s or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def write_outputs(res, out_dir, focus_md, problems):
    os.makedirs(out_dir, exist_ok=True)
    rows = res["rows"]
    js = dict(
        source=dict(hiidb=os.path.abspath(ARGS.hiidb), efivars=os.path.abspath(ARGS.efivars),
                    live_fallback=sorted(f"{k[0]}-{k[1]}" for k in res["extra_used"])),
        formsets=[dict(guid=fs.guid, title=fs.title, package_list=fs.pl_guid, forms=len(fs.forms),
                       questions=len(fs.questions),
                       varstores=[dict(v) for k, v in sorted(fs.varstores.items())])
                  for fs in res["db"].formsets],
        varstore_check=res["vs_report"],
        stddefaults=[dict(name=e.get("name"), guid=e.get("guid"), attr=e["attr"], size=len(e["data"]))
                     for e in res["std_entries"]],
        key_problems=problems,
        questions=[{k: v for k, v in r.items() if not k.startswith("_")} for r in rows],
        settings=[{**{kk: vv for kk, vv in s.items() if not kk.startswith("_")}, "key": f"{k[0]}@{k[2]}/{k[3]}"}
                  for k, s in res["settings"].items()],
        profiles=res["profiles"],
    )
    with open(os.path.join(out_dir, "bios_settings.json"), "w") as f:
        json.dump(js, f, indent=1, default=lambda o: o.hex() if isinstance(o, bytes) else str(o))

    L = []
    A = L.append
    A("# Live BIOS setup configuration (decoded from EFI variables + HII database)\n")
    A(f"- HII database: `{os.path.abspath(ARGS.hiidb)}`")
    A(f"- EFI variables: `{os.path.abspath(ARGS.efivars)}`"
      + (f" (+ live read-only fallback for: {', '.join(sorted(k[0] for k in res['extra_used']))})" if res["extra_used"] else ""))
    A("- Generated by `scripts/bios_settings_decode.py` (IFR parser `scripts/hii_ifr.py`).")
    A("- Columns: **cur** = current value (raw, then option text); **IFR def** = standard default from the IFR "
      "(DEFAULT opcode > option DEFAULT flag > checkbox flag); **Std** = AMI StdDefaults NVAR value (only Setup & a few small stores have one); "
      "**ND** = NON-DEFAULT against the IFR default; **vis** = visible under current values (suppressif evaluated: Y / hidden / ? unknown).\n")
    # statistics
    nq = len(rows)
    nstore = sum(1 for r in rows if r.get("varstore") and r["op"] not in ("REF", "ACTION"))
    nval = sum(1 for r in rows if r.get("valid") is True)
    ninv = [r for r in rows if r.get("valid") is False]
    nchk = sum(1 for r in rows if r.get("valid") is not None)
    agree = sum(1 for r in rows if r.get("ifr_vs_stddefaults_agree") is True)
    disagree = [r for r in rows if r.get("ifr_vs_stddefaults_agree") is False]
    nd = sum(1 for s in res["settings"].values() if s.get("non_default"))
    A("## Summary\n")
    A(f"- Formsets: " + "; ".join(f"'{fs.title}' ({fs.guid}, {len(fs.forms)} forms, {len(fs.questions)} questions)"
                                  for fs in res["db"].formsets if fs.questions))
    A(f"- Question instances: {nq}; with storage: {nstore}; unique storage locations: {len(res['settings'])}; "
      f"non-default (against the IFR default): {nd}.")
    A(f"- Value validity: {nval}/{nchk} checkable instances hold a legal value (ONE_OF option / NUMERIC range); invalid: "
      + (", ".join(f"{r['prompt']} ({r['varstore']}@{r['offset']}={r.get('value')})" for r in ninv) or "none") + ".")
    A(f"- IFR default against the AMI StdDefaults NVAR store: {agree} agree, {len(disagree)} disagree"
      + (" (" + ", ".join(f"{r['prompt']} {r['varstore']}@{r['offset']}" for r in disagree) + ")" if disagree else "")
      + ". StdDefaults holds: " + ", ".join(f"{e.get('name')}({len(e['data'])} B)" for e in res["std_entries"])
      + ", and no AmdSetup, AOD_SETUP or AMD_PBS_SETUP (AMD CBS and AOD defaults come from the IFR only).")
    A("- Where things live: Gigabyte Tweaker (shown as 'M.I.T.' in the HII strings) is in `Setup` (ec87d643...) "
      "offsets 288-520 (frequencies 288-351, memory 345-487, voltages 490-517); AMD CBS in `AmdSetup` (3a997502...); "
      "AMD Overclocking in `AOD_SETUP` (5ed15dc0...); AMD PBS in `AMD_PBS_SETUP`; fans in `GcSensorVarName`. "
      "The CBS UMC timing fields currently mirror the Tweaker values (Gigabyte copies them; see Profile3, where CBS was "
      "still Auto while Tweaker held values).")
    if res.get("assumed"):
        A("- Condition evaluation assumptions (only for suppressif/grayoutif): "
          + ", ".join(f"{k}={v}" for k, v in res["assumed"].items())
          + " (AMI TSE SystemAccess: 0 = administrator; AMITSESetup password buffers are all zero, so no password is set). "
          "Conditions on boot-time-only stores (AmiAmdSbSuppressIfSetupData, AMICallback) stay unknown ('?').")
    A("- Gigabyte voltage fields store a UI code, not mV: see the note at GBT_VOLT in the script; the "
      "volt values shown are inferred from Gigabyte's EasyTune ACPI table 'GSAT' (F19c image) and are marked as such.\n")
    A("## Key table check\n")
    A("Every key setting was found. Compare them with the running hardware with `ctune mem verify`.\n" if not problems
      else "These key settings match no question, or several; fix KEY in bios_settings_decode.py:\n\n"
      + "\n".join(f"- {md_escape(p)}" for p in problems) + "\n")
    A("### Varstore size check (IFR VARSTORE size against efivar data size)\n")
    A("| Varstore | GUID | IFR size | efivar size | match | in StdDefaults |\n|---|---|---|---|---|---|")
    for v in res["vs_report"]:
        if v["efivar_size"] is None and not v["in_StdDefaults"]:
            continue
        A(f"| {v['name']} | {v['guid']} | {v['ifr_size']} | {v['efivar_size']} | {'OK' if v['size_match'] else ('no runtime variable' if v['efivar_size'] is None else ('variable-length (TSE placeholder)' if v.get('variable_length') else 'MISMATCH'))} | "
          f"{v['StdDefaults_size'] if v['in_StdDefaults'] else ''} |")
    nov = [v for v in res["vs_report"] if v["efivar_size"] is None and not v["in_StdDefaults"]]
    A(f"\nVarstores without a readable runtime variable (boot-time-only / volatile; values unknown): "
      + ", ".join(sorted({str(v['name']) for v in nov})) + "\n")
    A(focus_md)
    # profiles
    A("## Gigabyte saved profiles (ProfileN-b05e6b5f-...)\n")
    A("Format (reverse-engineered, checked on all 9 slots): 0x38-byte header {u16 0x1964, u16 payload length, u16 0x3811, "
      "u8 flag, u8 sum8(payload), u32 record count, 'F16b8ARNR003' (BIOS + board id), CHAR16 name[16]} then records "
      "{u32 AMI-TSE variable index, u16 length, raw variable bytes}. Raw copies of the variables, not NVAR. "
      "Record 0 = Setup (646 B), 110 = AOD_SETUP (1020 B), 112 = AmdSetup (1553 B), 114 = RtkNvData (2014 B, NIC).\n")
    A("| Slot | Name | BIOS | flag | checksum | records | Setup/AOD/CBS identical to current | # setting diffs against current |\n|---|---|---|---|---|---|---|---|")
    for p in res["profiles"]:
        ident = ", ".join(f"{k}:{'=' if v else 'diff'}" for k, v in sorted(p["identical_to_current"].items()))
        A(f"| {p['slot']} | {md_escape(p['name']) or '(none)'} | {p['bios']} | {p['flag']} | {'OK' if p['checksum_ok'] else 'BAD'} | "
          f"{p['record_count']} | {ident} | {len(p['diffs_vs_current'])} |")
    A("")
    for p in res["profiles"]:
        if not p["diffs_vs_current"]:
            continue
        A(f"### {p['slot']} '{md_escape(p['name'])}': settings that differ from the current configuration\n")
        A("| Setting | Store@off | Current | Profile |\n|---|---|---|---|")
        for d in p["diffs_vs_current"]:
            A(f"| {md_escape(d['where'])} | {d['varstore']}@{d['offset']} | {fmt(d['current'])} {md_escape(d['current_text'] or '')} | "
              f"{fmt(d['profile'])} {md_escape(d['profile_text'] or '')} |")
        A("")
    # all questions
    A("## All questions (every formset / form)\n")
    cur_form = None
    for r in rows:
        if r["op"] in ("REF",):
            continue
        key = (r["formset"], r["form_path"])
        if key != cur_form:
            cur_form = key
            A(f"\n### {md_escape(r['formset'])} :: {md_escape(r['form_path'])}\n")
            A("| Prompt | Type | Store@off/w | cur | IFR def | Std | ND | vis |\n|---|---|---|---|---|---|---|---|")
        st = f"{r.get('varstore')}@{r.get('offset')}/{r.get('width')}" if r.get("varstore") else "-"
        cur = f"{fmt(r.get('value'))} {md_escape(r.get('value_text') or '')}" if r.get("varstore") else ""
        if r.get("valid") is False:
            cur += " (INVALID)"
        dft = f"{fmt(r.get('ifr_default'))} {md_escape(r.get('default_text') or '')}" if r.get("ifr_default") is not None else ""
        std = fmt(r.get("stddefaults_value")) if "stddefaults_value" in r else ""
        nd = "**ND**" if r.get("non_default") else ""
        vis = {True: "Y", False: "hidden", None: "?"}[r.get("visible_now")]
        if r.get("grayed_now"):
            vis += " (grayed)"
        A(f"| {md_escape(r['prompt'])} | {r['op']} | {st} | {cur} | {dft} | {std} | {nd} | {vis} |")
    with open(os.path.join(out_dir, "bios_settings.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    # copies of live fallback vars, for reproducibility
    if res["extra_used"]:
        xd = os.path.join(out_dir, "efivars_extra")
        os.makedirs(xd, exist_ok=True)
        for k, p in res["extra_used"].items():
            with open(p, "rb") as fi, open(os.path.join(xd, os.path.basename(p)), "wb") as fo:
                fo.write(fi.read())


# ----------------------------------------------------------------------------- focused memory section
# Settings are found by menu location ("form path :: prompt") and varstore, never by offset alone: a BIOS update moves
# offsets. F19c's Setup store grows from 646 to 650 bytes and 418 Tweaker questions shift by +4 or +5 bytes against
# F16b (CAS Latency moves from Setup@368 to 373), so an offset table would show another setting's value without any
# error.
TW_FREQ = "Setup > M.I.T. > Advanced Frequency Settings"
TW_CPU = TW_FREQ + " > Advanced CPU Settings"
TW_MEM = "Setup > M.I.T. > Advanced Memory Settings"
TW_SUB_A = TW_MEM + " > Channel A Memory Sub Timings"
TW_SUB_B = TW_MEM + " > Channel B Memory Sub Timings"
TW_VOLT = "Setup > M.I.T. > Advanced Voltage Settings"
CBS_TIM = "AMD CBS > UMC Common Options > DDR4 Common Options > DRAM Timing Configuration"
CBS_CTL = "AMD CBS > UMC Common Options > DDR4 Common Options > DRAM Controller Configuration"
CBS_PWR = CBS_CTL + " > DRAM Power Options"
CBS_XFR = "AMD CBS > XFR Enhancement"
CBS_SMU = "AMD CBS > NBIO Common Options > SMU Common Options"
AOD = "AMD Overclocking Option"
AOD_DDR = AOD + " > DDR and Infinity Fabric Frequency/Timings"
AOD_CO = AOD + " > Curve Optimizer"


def find_row(res, store, path, prompt, op=None, width=None):
    """(row, None) for the question at path :: prompt in varstore store, or (None, reason). Several storage
    locations at one menu location count as a failure, never a guess: op or width tell them apart (AOD's
    'VDDP Voltage Control' is both the Auto/Manual switch and the mV value). Prefers a row visible now."""
    cands = [r for r in res["rows"] if r.get("varstore") == store and r.get("form_path") == path
             and r.get("prompt") == prompt and r["op"] not in ("REF", "ACTION")
             and op in (None, r["op"]) and width in (None, r.get("width"))]
    where = {(r["offset"], r.get("width")) for r in cands}
    if len(where) != 1:
        return None, ("not found" if not where else
                      "ambiguous: " + ", ".join(f"{store}@{o}/{w}" for o, w in sorted(where)))
    return next(r for want in (True, None, False) for r in cands if r.get("visible_now") is want), None


# Gigabyte "Advanced Voltage Settings" (Setup varstore) store a UI code, not millivolts. 0xFFFF = Auto.
# Ranges below come from Gigabyte's EasyTune ACPI voltage table "GSAT" (Name(GSAT, Package(){...}) in an
# SSDT of the F19c image, decompressed from B550AORUSPRO.F19c; the F16b SSDT could not be read without root).
# entry = {id, name, {type, chip, i2c addr, loop, reg}, {f1, f2, {min, max, step, divisor}}, ..., default}.
# The code is assumed to map to volts as (min + code*step)/divisor ("index from min"). Evidence: DRAM code 55 gives
# 1.550 V, which agrees with the DDR VTT proxy (IT8792E in1 0.763 V, about 1.53 V); the "from max" reading (1.45 V)
# does not. For CPU Vcore and VCORE SOC the IFR greys out DVID unless code == 1 (help text: DVID only with 'Normal'),
# so codes 0 and 1 are special (Auto and Normal), and the first voltage may be code 2, with the alternative value
# min + (code-2)*step.
# Keyed by prompt on the TW_VOLT page (Setup varstore); each prompt maps to (min V, step V, codes 0/1 reserved).
GBT_VOLT = {
    "CPU Vcore": (0.750, 0.00625, True),
    "VCORE SOC": (0.750, 0.00625, True),
    "CPU VDDP": (-0.200, 0.020, False),   # maybe an offset, not an absolute voltage
    "CPU VDD18": (1.600, 0.040, False),
    "A_VDD18S5": (1.500, 0.020, False),
    "DRAM Voltage      (CH A/B)": (1.000, 0.010, False),
    "DDRVPP Voltage    (CH A/B)": (1.980, 0.040, False),
    "DRAM Termination  (CH A/B)": (0.450, 0.005, False),
}


def gbt_volt_text(r):
    """Inferred volts for a Gigabyte voltage code, or None when r is not one of GBT_VOLT's questions."""
    v = r.get("value")
    if r.get("varstore") != "Setup" or r.get("form_path") != TW_VOLT or r.get("prompt") not in GBT_VOLT or v is None:
        return None
    mn, st, special = GBT_VOLT[r["prompt"]]
    if v == 0xFFFF:
        return "Auto"
    if special and v in (0, 1):
        return {0: "code 0 (Auto?)", 1: "code 1 (Normal)"}[v]
    t = f"~{mn + v * st:.4f} V (inferred: {mn:.3f} V + {v} x {st * 1000:g} mV)"
    if special:
        t += f"; {mn + (v - 2) * st:.4f} V if codes 0/1 are reserved"
    return t


# (label, varstore, form path, prompt[, {op or width}]): the compact "what is actually configured" table
KEY = [
    ("XMP", "Setup", TW_FREQ, "Extreme Memory Profile(X.M.P.)"),
    ("Memory speed (System Memory Multiplier, x100)", "Setup", TW_FREQ, "System Memory Multiplier"),
    ("Memory Timing Mode", "Setup", TW_SUB_A, "Memory Timing Mode"),
    ("CBS Overclock (UMC)", "AmdSetup", CBS_TIM, "Overclock"),
    ("CBS Memory Clock Speed", "AmdSetup", CBS_TIM, "Memory Clock Speed"),
    ("CBS FCLK Frequency", "AmdSetup", CBS_XFR, "FCLK Frequency"),
    ("CBS UCLK DIV1 MODE", "AmdSetup", CBS_XFR, "UCLK DIV1 MODE"),
    ("AOD Memory Clock Speed", "AOD_SETUP", AOD_DDR + " > DRAM Timing Configuration", "Memory Clock Speed"),
    ("tCL (Tweaker)", "Setup", TW_SUB_A, "CAS Latency"),
    ("tRCDRD", "Setup", TW_MEM, "tRCDRD"),
    ("tRCDWR", "Setup", TW_MEM, "tRCDWR"),
    ("tRP", "Setup", TW_SUB_A, "tRP"),
    ("tRAS", "Setup", TW_SUB_A, "tRAS"),
    ("tRC", "Setup", TW_MEM, "tRC"),
    ("tWR", "Setup", TW_MEM, "tWR"),
    ("tCWL", "Setup", TW_MEM, "tCWL"),
    ("tRRD_S", "Setup", TW_MEM, "tRRD_S"),
    ("tRRD_L", "Setup", TW_MEM, "tRRD_L"),
    ("tWTR_S", "Setup", TW_MEM, "tWTR_S"),
    ("tWTR_L", "Setup", TW_MEM, "tWTR_L"),
    ("tRFC", "Setup", TW_MEM, "tRFC"),
    ("tRFC2", "Setup", TW_MEM, "tRFC2"),
    ("tRFC4", "Setup", TW_MEM, "tRFC4"),
    ("tRTP", "Setup", TW_MEM, "tRTP"),
    ("tFAW", "Setup", TW_MEM, "tFAW"),
    ("tRDWR", "Setup", TW_MEM, "tRDWR"),
    ("tWRRD", "Setup", TW_MEM, "tWRRD"),
    ("tRDRD SC/SD/DD", "Setup", TW_MEM, "tRDRDSC"),
    ("tRDRDSD", "Setup", TW_MEM, "tRDRDSD"),
    ("tRDRDDD", "Setup", TW_MEM, "tRDRDDD"),
    ("tRDRD_SCL", "Setup", TW_MEM, "tRDRD_SCL"),
    ("tWRWRSC", "Setup", TW_MEM, "tWRWRSC"),
    ("tWRWRSD", "Setup", TW_MEM, "tWRWRSD"),
    ("tWRWRDD", "Setup", TW_MEM, "tWRWRDD"),
    ("tWRWR_SCL", "Setup", TW_MEM, "tWRWR_SCL"),
    ("tCKE", "Setup", TW_MEM, "tCKE"),
    ("Channel B timings (Advanced Manual only)", "Setup", TW_SUB_B, "CAS Latency"),
    ("ProcODT (Tweaker)", "Setup", TW_MEM, "ProcODT"),
    ("ProcODT (CBS)", "AmdSetup", CBS_TIM, "ProcODT"),
    ("Command Rate", "Setup", TW_MEM, "Command Rate(tCMD)"),
    ("Gear Down Mode (Tweaker)", "Setup", TW_MEM, "Gear Down Mode"),
    ("Gear Down Mode (CBS)", "AmdSetup", CBS_CTL, "Gear Down Mode"),
    ("Power Down Enable (CBS)", "AmdSetup", CBS_PWR, "Power Down Enable"),
    ("Power Down Enable (AOD)", "AOD_SETUP", AOD_DDR, "Power Down Enable"),
    ("RttNom (Tweaker)", "Setup", TW_MEM, "RttNom"),
    ("RttWr (Tweaker)", "Setup", TW_MEM, "RttWr"),
    ("RttPark (Tweaker)", "Setup", TW_MEM, "RttPark"),
    ("AddrCmdSetup (Tweaker)", "Setup", TW_MEM, "AddrCmdSetup"),
    ("CsOdtSetup", "Setup", TW_MEM, "CsOdtSetup"),
    ("CkeSetup", "Setup", TW_MEM, "CkeSetup"),
    ("ClkDrvStren", "Setup", TW_MEM, "ClkDrvStren"),
    ("AddrCmdDrvStren", "Setup", TW_MEM, "AddrCmdDrvStren"),
    ("CsOdtDrvStren", "Setup", TW_MEM, "CsOdtDrvStren"),
    ("CkeDrvStren", "Setup", TW_MEM, "CkeDrvStren"),
    ("AOD Data Bus Configuration User Controls", "AOD_SETUP", AOD_DDR + " > Data Bus Configuration",
     "Data Bus Configuration User Controls"),
    ("AOD CAD Bus Timing User Controls", "AOD_SETUP", AOD_DDR + " > CAD Bus Configuration",
     "CAD Bus Timing User Controls"),
    ("AOD CAD Bus Drive Strength User Controls", "AOD_SETUP", AOD_DDR + " > CAD Bus Configuration",
     "CAD Bus Drive Strength User Controls"),
    ("BankGroupSwap (CBS)", "AmdSetup", "AMD CBS > UMC Common Options > DRAM Memory Mapping", "BankGroupSwap"),
    ("BankGroupSwapAlt (CBS)", "AmdSetup", "AMD CBS > UMC Common Options > DRAM Memory Mapping", "BankGroupSwapAlt"),
    ("Memory interleaving (CBS)", "AmdSetup", "AMD CBS", "Memory interleaving"),
    ("Channel Interleaving (Tweaker)", "Setup", TW_MEM, "Channel Interleaving"),
    ("Rank Interleaving (Tweaker)", "Setup", TW_MEM, "Rank Interleaving"),
    ("TSME (CBS)", "AmdSetup", "AMD CBS > UMC Common Options > DDR4 Common Options > Security", "TSME"),
    ("DRAM Voltage (CH A/B)", "Setup", TW_VOLT, "DRAM Voltage      (CH A/B)"),
    ("DDRVPP Voltage", "Setup", TW_VOLT, "DDRVPP Voltage    (CH A/B)"),
    ("DRAM Termination", "Setup", TW_VOLT, "DRAM Termination  (CH A/B)"),
    ("VCORE SOC", "Setup", TW_VOLT, "VCORE SOC"),
    ("Dynamic VCORE SOC (DVID)", "Setup", TW_VOLT, "Dynamic VCORE SOC(DVID)"),
    ("Vcore SOC LLC", "Setup", TW_VOLT, "Vcore SOC Loadline Calibration"),
    ("CPU VDDP (Tweaker)", "Setup", TW_VOLT, "CPU VDDP"),
    ("CPU VDD18", "Setup", TW_VOLT, "CPU VDD18"),
    ("AOD VDDP Voltage Control", "AOD_SETUP", AOD, "VDDP Voltage Control", {"op": "ONE_OF"}),
    ("AOD VDDP [mV]", "AOD_SETUP", AOD, "VDDP Voltage Control", {"op": "NUMERIC"}),
    ("AOD VDDG Voltage Control", "AOD_SETUP", AOD, "VDDG Voltage Control"),
    ("AOD Global VDDG CCD [mV]", "AOD_SETUP", AOD, "Global VDDG CCD Voltage Control"),
    ("AOD Global VDDG IOD [mV]", "AOD_SETUP", AOD, "Global VDDG IOD Voltage Control"),
    ("AOD SoC/Uncore OC Mode", "AOD_SETUP", AOD, "SoC/Uncore OC Mode"),
    ("AOD SoC Voltage [mV]", "AOD_SETUP", AOD, "SoC Voltage"),
    ("CBS SOC OVERCLOCK VID", "AmdSetup", CBS_XFR, "SOC OVERCLOCK VID"),
    ("CBS VDDP Voltage Control", "AmdSetup", CBS_XFR, "VDDP Voltage Control"),
    ("CBS VDDG Voltage Control", "AmdSetup", CBS_XFR, "VDDG Voltage Control"),
    ("DF Cstates (CBS)", "AmdSetup", CBS_SMU, "DF Cstates"),
    ("APBDIS (CBS)", "AmdSetup", CBS_SMU, "APBDIS"),
    ("Fixed SOC Pstate (CBS)", "AmdSetup", CBS_SMU, "Fixed SOC Pstate"),
    ("Global C-state Control (Tweaker)", "Setup", TW_CPU, "Global C-state Control"),
    ("Power Supply Idle Control (Tweaker)", "Setup", TW_CPU, "Power Supply Idle Control"),
    ("Global C-state Control (CBS)", "AmdSetup", "AMD CBS > CPU Common Options", "Global C-state Control"),
    ("Power Supply Idle Control (CBS)", "AmdSetup", "AMD CBS > CPU Common Options", "Power Supply Idle Control"),
    ("CPPC / Preferred Cores (Tweaker)", "Setup", TW_CPU, "CPPC"),
    ("IOMMU (CBS)", "AmdSetup", "AMD CBS > NBIO Common Options", "IOMMU"),
    ("Precision Boost Overdrive (AOD)", "AOD_SETUP", AOD, "Precision Boost Overdrive"),
    ("PBO Limits (AOD)", "AOD_SETUP", AOD, "PBO Limits"),
    ("PBO Scalar (AOD)", "AOD_SETUP", AOD, "Precision Boost Overdrive Scalar", {"width": 1}),
    ("CPU Boost Clock Override (AOD)", "AOD_SETUP", AOD, "CPU Boost Clock Override"),
    ("Curve Optimizer (AOD)", "AOD_SETUP", AOD_CO, "Curve Optimizer"),
    ("CO core 0 sign", "AOD_SETUP", AOD_CO, "Core 0 Curve Optimizer Sign"),
    ("CO core 0 magnitude", "AOD_SETUP", AOD_CO, "Core 0 Curve Optimizer Magnitude"),
    ("CO core 1 sign", "AOD_SETUP", AOD_CO, "Core 1 Curve Optimizer Sign"),
    ("CO core 1 magnitude", "AOD_SETUP", AOD_CO, "Core 1 Curve Optimizer Magnitude"),
    ("CO core 2 sign", "AOD_SETUP", AOD_CO, "Core 2 Curve Optimizer Sign"),
    ("CO core 2 magnitude", "AOD_SETUP", AOD_CO, "Core 2 Curve Optimizer Magnitude"),
    ("CO core 3 sign", "AOD_SETUP", AOD_CO, "Core 3 Curve Optimizer Sign"),
    ("CO core 3 magnitude", "AOD_SETUP", AOD_CO, "Core 3 Curve Optimizer Magnitude"),
    ("CO core 4 sign", "AOD_SETUP", AOD_CO, "Core 4 Curve Optimizer Sign"),
    ("CO core 4 magnitude", "AOD_SETUP", AOD_CO, "Core 4 Curve Optimizer Magnitude"),
    ("CO core 5 sign", "AOD_SETUP", AOD_CO, "Core 5 Curve Optimizer Sign"),
    ("CO core 5 magnitude", "AOD_SETUP", AOD_CO, "Core 5 Curve Optimizer Magnitude"),
    ("CO core 6 sign", "AOD_SETUP", AOD_CO, "Core 6 Curve Optimizer Sign"),
    ("CO core 6 magnitude", "AOD_SETUP", AOD_CO, "Core 6 Curve Optimizer Magnitude"),
    ("CO core 7 sign", "AOD_SETUP", AOD_CO, "Core 7 Curve Optimizer Sign"),
    ("CO core 7 magnitude", "AOD_SETUP", AOD_CO, "Core 7 Curve Optimizer Magnitude"),
    ("CBS Precision Boost Overdrive (XFR)", "AmdSetup", CBS_XFR, "Precision Boost Overdrive"),
    ("CBS OC Mode", "AmdSetup", "AMD CBS > CPU Common Options > Performance", "OC Mode"),
    ("Spread Spectrum (Tweaker)", "Setup", TW_FREQ, "Spread Spectrum Control"),
    ("SVM Mode (Tweaker)", "Setup", TW_CPU, "SVM Mode"),
    ("Above 4G Decoding", "Setup", "Setup > Peripherals", "Above 4G Decoding"),
    ("Re-Size BAR", "Setup", "Setup > Peripherals", "Re-Size BAR Support"),
    ("CSM Support", "Setup", "Setup > BIOS", "CSM Support"),
]

FOCUS = [
    # (title, formset regex, include regex on "form path :: prompt", exclude regex)
    ("Tweaker / M.I.T.: frequency, CPU and memory (Gigabyte, Setup varstore)", r"^Setup$",
     r"Advanced Memory Settings|Memory Sub Timings|Advanced Frequency Settings|Advanced CPU Settings", None),
    ("Tweaker / M.I.T.: voltages (Gigabyte, Setup varstore)", r"^Setup$", r"Advanced Voltage Settings", None),
    ("AMD Overclocking (AOD_SETUP): memory, fabric, voltages, PBO and CO", r"AMD Overclocking",
     r"DDR|DRAM|Data Bus|CAD Bus|Fabric|FCLK|UCLK|Voltage|VDD|SoC|Precision Boost|PBO|Curve|Boost|Thermal|NUMA|Gaming|LCLK|Power Down|Gear|Cmd2T",
     None),
    ("AMD CBS: UMC and DDR4", r"AMD CBS", r"UMC Common Options|DDR4 Common", r"MBIST|ECC Configuration|Common RAS"),
    ("AMD CBS: DF", r"AMD CBS", r"DF Common Options|Memory interleaving|NUMA", r"Link"),
    ("AMD CBS: NBIO, SMU and XFR", r"AMD CBS", r"SMU Common Options|XFR Enhancement|NBIO Common Options :: IOMMU",
     r"Fan Control|NBIO LCLK DPM|xGMI"),
    ("AMD CBS: CPU common (C-states, PSI, prefetch)", r"AMD CBS", r"CPU Common Options", r"Thread Enablement|SMU and PSP"),
]


def build_focus(res):
    rows = res["rows"]
    out = ["## Memory / Infinity Fabric / voltage related settings (focused)\n",
           "**state**: Auto = option text contains 'Auto' or a Gigabyte all-ones (0xFFFF/0xFFFFFFFF) Auto sentinel; "
           "default = equals the IFR default; MANUAL = anything else. **vis**: shown under the current values "
           "(suppressif evaluated; ? = depends on a boot-time-only variable).\n",
           "### Key settings (what is actually configured)\n",
           "| Item | Store@off | Current | state | IFR default | vis |\n|---|---|---|---|---|---|"]
    for label, store, path, prompt, *want in KEY:
        r, why = find_row(res, store, path, prompt, **(want[0] if want else {}))
        if not r:
            out.append(f"| {label} | {store} | ({why}: {md_escape(path)} :: {md_escape(prompt)}) | | | |")
            continue
        s = res["settings"].get((r["varstore"], r["varstore_guid"], r["offset"], r["width"]), {})
        vt = gbt_volt_text(r) or s.get("value_text") or r.get("value_text") or ""
        vis = {True: "Y", False: "hidden", None: "?"}[s.get("visible_now", r.get("visible_now"))]
        out.append(f"| {label} | {store}@{r['offset']} | {fmt(r.get('value'))} {md_escape(vt)} | {r.get('auto_state')} | "
                   f"{fmt(r.get('ifr_default'))} {md_escape(r.get('default_text') or '')} | {vis} |")
    out.append("")
    used = set()
    for title, fsrx, rx, ex in FOCUS:
        fr, rr = re.compile(fsrx), re.compile(rx, re.I)
        xr = re.compile(ex, re.I) if ex else None
        sel = []
        for r in rows:
            if not r.get("varstore") or r["op"] in ("REF", "ACTION") or not r.get("prompt"):
                continue
            if not fr.search(r["formset"] or ""):
                continue
            hay = f"{r['form_path']} :: {r['prompt']}"
            if not rr.search(hay) or (xr and xr.search(hay)):
                continue
            k = (r["varstore"], r["varstore_guid"], r["offset"], r["width"])
            if k in used:
                continue
            used.add(k)
            sel.append(r)
        if not sel:
            continue
        out.append(f"### {title}\n")
        out.append("| Page | Setting | Store@off/w | Current | state | IFR default | vis |\n|---|---|---|---|---|---|---|")
        for r in sel:
            s = res["settings"].get((r["varstore"], r["varstore_guid"], r["offset"], r["width"]), {})
            vt = gbt_volt_text(r) or s.get("value_text") or r.get("value_text") or ""
            vis = {True: "Y", False: "hidden", None: "?"}[s.get("visible_now", r.get("visible_now"))]
            page = r["form_path"].split(" > ")[-1]
            out.append(f"| {md_escape(page)} | {md_escape(r['prompt'])} | {r['varstore']}@{r['offset']}/{r['width']} | "
                       f"{fmt(r.get('value'))} {md_escape(vt)} | {r.get('auto_state')} | "
                       f"{fmt(r.get('ifr_default'))} {md_escape(r.get('default_text') or '')} | {vis} |")
        out.append("")
    return "\n".join(out) + "\n"


def key_problems(res):
    """KEY entries that resolve to no question or to several: after a BIOS update, a renamed menu or prompt."""
    out = []
    for label, store, path, prompt, *want in KEY:
        r, why = find_row(res, store, path, prompt, **(want[0] if want else {}))
        if why:
            out.append(f"{label}: {why} ({store} {path} :: {prompt})")
    return out


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--hiidb", default=os.path.join(SNAP, "raw", "fwmem", "hiidb.bin"))
    ap.add_argument("--efivars", default=os.path.join(SNAP, "raw", "efivars"))
    ap.add_argument("--live", default="/sys/firmware/efi/efivars", help="read-only fallback dir ('' to disable)")
    ap.add_argument("--out", default=os.path.join(SNAP, "decoded"))
    ap.add_argument("--assume", action="append", help="NAME@OFFSET=VALUE for a varstore with no runtime "
                    "variable, used only when evaluating suppressif/grayoutif (repeatable)")
    ARGS = ap.parse_args()
    res = decode(ARGS)
    problems = key_problems(res)
    focus = build_focus(res)
    write_outputs(res, ARGS.out, focus, problems)
    # console summary
    n = sum(1 for r in res["rows"] if r.get("varstore"))
    nv = sum(1 for r in res["rows"] if r.get("valid") is True)
    ninv = [r for r in res["rows"] if r.get("valid") is False]
    print(f"questions: {len(res['rows'])}, with storage: {n}, valid values: {nv}, invalid: {len(ninv)}")
    for r in ninv[:40]:
        print(f"   INVALID {r['formset']} > {r['form_path']} :: {r['prompt']} {r['varstore']}@{r['offset']} = {r.get('value')}")
    for v in res["vs_report"]:
        if v["efivar_size"] is not None and not v["size_match"] and not v.get("variable_length"):
            print("   SIZE MISMATCH", v)
    for p in problems:
        print(f"   KEY {p}")
    print("wrote", os.path.join(ARGS.out, "bios_settings.json"), os.path.join(ARGS.out, "bios_settings.md"))


ARGS = None
if __name__ == "__main__":
    main()
