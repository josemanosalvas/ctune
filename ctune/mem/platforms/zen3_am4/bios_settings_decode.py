#!/usr/bin/env python3
"""Decode the live AMI Aptio V / Gigabyte BIOS setup configuration from EFI variables + the HII database.

Inputs (defaults = the F16b snapshot):
  --hiidb    raw/fwmem/hiidb.bin           HII database exported by AMI (EFI var 'HiiDB' -> phys address)
  --efivars  raw/efivars/                  copies of /sys/firmware/efi/efivars/* (4-byte attribute header + data)
  --live     /sys/firmware/efi/efivars     read-only fallback for varstores that were not captured
Outputs (--out, default <snapshot>/decoded/):
  bios_settings.json, bios_settings.md, efivars_extra/ (copies of the live fallback vars that were used)

Formats:
  * efivarfs files: first 4 bytes = UINT32 attributes, then the data (Linux Documentation/filesystems/efivarfs.rst).
  * IFR / HII: see hii_ifr.py (UEFI 2.10 ch. 33).
  * AMI NVAR (StdDefaults): UEFITool common/nvram.h (https://github.com/LongSoft/UEFITool/blob/new_engine/common/nvram.h)
    NVAR_ENTRY_HEADER {UINT32 Signature 'NVAR'; UINT16 Size; UINT32 Next:24; UINT32 Attributes:8}
    attributes RUNTIME 0x01, ASCII_NAME 0x02, GUID 0x04, DATA_ONLY 0x08, EXT_HEADER 0x10, HW_ERROR_RECORD 0x20,
    AUTH_WRITE 0x40, VALID 0x80; without GUID attribute a 1-byte index into the GUID store follows the header;
    the GUID store sits at the end of the store and grows downwards (index 0 = last 16 bytes)
    (UEFITool common/ffsparser.cpp parseNvarStore).
  * Gigabyte ProfileN (GUID b05e6b5f-6ed8-4015-b5c5-b1049faf3e5b): reverse-engineered here, no public spec:
    header 0x38 bytes {u16 magic 0x1964; u16 payload_len; u16 0x3811; u8 flag; u8 sum8(payload);
    u32 record_count; char[12] BIOS-version+board-id ('F16b8ARNR003'); CHAR16 name[16]}
    followed by records {u32 tse_var_index; u16 len; u8 data[len]} (verified: lengths add up exactly, the
    checksum byte equals sum(payload) & 0xFF for all 9 slots).
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
SNAP = os.path.normpath(os.path.join(HERE, "..", "snapshots", "20261024-181828_F16b"))


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


def auto_state(q, v, txt, dflt):
    """'Auto' / 'default' / 'manual' classification for the focused table."""
    if v is None:
        return "n/a"
    if txt and AUTO_RX.search(txt):
        return "Auto"
    w = q.get("width") or 0
    if q["op"] == "NUMERIC" and w and v == (1 << (8 * w)) - 1:
        return "Auto (all-ones sentinel)"
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
    # administrator; AMITSESetup holds the user+admin password buffers (2 x 40 bytes) - all zero here.
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

    # dedupe by storage location -> settings
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
        # map record index -> variable: a record maps to a variable when exactly one non-profile efivar has the
        # same length, the length is unique inside the profile and >= 40 bytes (small stores stay unmapped). Records are AMI TSE variable-list
        # indices, which are not published, so smaller records are only guessed.
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


def write_outputs(res, out_dir, focus_md, checks):
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
        sanity_checks=checks,
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
      "**ND** = NON-DEFAULT vs IFR default; **vis** = visible under current values (suppressif evaluated: Y / hidden / ? unknown).\n")
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
      f"non-default (vs IFR default): {nd}.")
    A(f"- Value validity: {nval}/{nchk} checkable instances hold a legal value (ONE_OF option / NUMERIC range); invalid: "
      + (", ".join(f"{r['prompt']} ({r['varstore']}@{r['offset']}={r.get('value')})" for r in ninv) or "none") + ".")
    A(f"- IFR default vs AMI StdDefaults NVAR store: {agree} agree, {len(disagree)} disagree"
      + (" (" + ", ".join(f"{r['prompt']} {r['varstore']}@{r['offset']}" for r in disagree) + ")" if disagree else "")
      + ". StdDefaults holds: " + ", ".join(f"{e.get('name')}({len(e['data'])} B)" for e in res["std_entries"])
      + " - no AmdSetup / AOD_SETUP / AMD_PBS_SETUP (AMD CBS/AOD defaults come from the IFR only).")
    A("- Where things live: Gigabyte Tweaker (shown as 'M.I.T.' in the HII strings) -> `Setup` (ec87d643...) "
      "offsets 288-520 (frequencies 288-351, memory 345-487, voltages 490-517); AMD CBS -> `AmdSetup` (3a997502...); "
      "AMD Overclocking -> `AOD_SETUP` (5ed15dc0...); AMD PBS -> `AMD_PBS_SETUP`; fans -> `GcSensorVarName`. "
      "The CBS UMC timing fields currently mirror the Tweaker values (Gigabyte copies them; see Profile3, where CBS was "
      "still Auto while Tweaker held values).")
    if res.get("assumed"):
        A("- Condition evaluation assumptions (only for suppressif/grayoutif): "
          + ", ".join(f"{k}={v}" for k, v in res["assumed"].items())
          + " (AMI TSE SystemAccess: 0 = administrator; AMITSESetup password buffers are all zero, so no password is set). "
          "Conditions on boot-time-only stores (AmiAmdSbSuppressIfSetupData, AMICallback) stay unknown ('?').")
    A("- Gigabyte voltage fields store a UI code, not mV: see the note at GBT_VOLT in the script; the "
      "volt values shown are inferred from Gigabyte's EasyTune ACPI table 'GSAT' (F19c image) and are marked as such.\n")
    A("## Sanity checks\n")
    A("| Check | Expected | Decoded | Result |\n|---|---|---|---|")
    for c in checks:
        A(f"| {md_escape(c['check'])} | {md_escape(str(c['expected']))} | {md_escape(str(c['decoded']))} | {c['result']} |")
    A("")
    A("### Varstore size check (IFR VARSTORE size vs efivar data size)\n")
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
    A("| Slot | Name | BIOS | flag | checksum | records | Setup/AOD/CBS identical to current | # setting diffs vs current |\n|---|---|---|---|---|---|---|---|")
    for p in res["profiles"]:
        ident = ", ".join(f"{k}:{'=' if v else 'diff'}" for k, v in sorted(p["identical_to_current"].items()))
        A(f"| {p['slot']} | {md_escape(p['name']) or '(none)'} | {p['bios']} | {p['flag']} | {'OK' if p['checksum_ok'] else 'BAD'} | "
          f"{p['record_count']} | {ident} | {len(p['diffs_vs_current'])} |")
    A("")
    for p in res["profiles"]:
        if not p["diffs_vs_current"]:
            continue
        A(f"### {p['slot']} '{md_escape(p['name'])}' — settings that differ from the current configuration\n")
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


# ----------------------------------------------------------------------------- focused memory section + checks
# Gigabyte "Advanced Voltage Settings" (Setup varstore) store a UI code, not millivolts. 0xFFFF = Auto.
# Ranges below come from Gigabyte's EasyTune ACPI voltage table "GSAT" (Name(GSAT, Package(){...}) in an
# SSDT of the F19c image, decompressed from /tmp/gbzips/x_f19c/B550AORUSPRO.F19c; the F16b SSDT could not be
# read without root): entry = {id, name, {type, chip, i2c addr, loop, reg}, {f1, f2, {min, max, step, divisor}}, ..., default}.
# code -> volts assumed = (min + code*step)/divisor ("index from min"). Evidence: DRAM code 55 -> 1.550 V agrees with the
# DDR VTT proxy (IT8792E in1 0.763 V -> ~1.53 V); the "from max" reading (1.45 V) does not. For CPU Vcore / VCORE SOC
# the IFR greys out DVID unless code == 1 (help text: DVID only with 'Normal'), so codes 0/1 are special (Auto/Normal)
# and the first voltage may be code 2 -> alternative value (min + (code-2)*step).
GBT_VOLT = {
    490: ("CPU Vcore", 0.750, 0.00625, True),
    494: ("VCORE SOC", 0.750, 0.00625, True),
    502: ("CPU VDDP (offset?)", -0.200, 0.020, False),
    504: ("CPU VDD18", 1.600, 0.040, False),
    506: ("A_VDD18S5", 1.500, 0.020, False),
    508: ("DRAM Voltage (CH A/B)", 1.000, 0.010, False),
    510: ("DDRVPP (CH A/B)", 1.980, 0.040, False),
    512: ("DRAM Termination (CH A/B)", 0.450, 0.005, False),
}


def gbt_volt_text(off, v):
    if off not in GBT_VOLT or v is None:
        return None
    name, mn, st, special = GBT_VOLT[off]
    if v == 0xFFFF:
        return "Auto"
    if special and v in (0, 1):
        return {0: "code 0 (Auto?)", 1: "code 1 (Normal)"}[v]
    t = f"~{mn + v * st:.4f} V (inferred: {mn:.3f} V + {v} x {st * 1000:g} mV)"
    if special:
        t += f"; {mn + (v - 2) * st:.4f} V if codes 0/1 are reserved"
    return t


# (label, varstore, offset) - the compact "what is actually configured" table
KEY = [
    ("XMP", "Setup", 338), ("Memory speed (System Memory Multiplier, x100)", "Setup", 341),
    ("Memory Timing Mode", "Setup", 345), ("CBS Overclock (UMC)", "AmdSetup", 212),
    ("CBS Memory Clock Speed", "AmdSetup", 213), ("CBS FCLK Frequency", "AmdSetup", 338),
    ("CBS UCLK DIV1 MODE", "AmdSetup", 341), ("AOD Memory Clock Speed", "AOD_SETUP", 73),
    ("tCL (Tweaker)", "Setup", 368), ("tRCDRD", "Setup", 372), ("tRCDWR", "Setup", 374), ("tRP", "Setup", 376),
    ("tRAS", "Setup", 378), ("tRC", "Setup", 380), ("tWR", "Setup", 382), ("tCWL", "Setup", 384),
    ("tRRD_S", "Setup", 386), ("tRRD_L", "Setup", 388), ("tWTR_S", "Setup", 390), ("tWTR_L", "Setup", 392),
    ("tRFC", "Setup", 394), ("tRFC2", "Setup", 396), ("tRFC4", "Setup", 398), ("tRTP", "Setup", 400),
    ("tFAW", "Setup", 402), ("tRDWR", "Setup", 406), ("tWRRD", "Setup", 416), ("tRDRD SC/SD/DD", "Setup", 408),
    ("tRDRDSD", "Setup", 410), ("tRDRDDD", "Setup", 412), ("tRDRD_SCL", "Setup", 414), ("tWRWRSC", "Setup", 418),
    ("tWRWRSD", "Setup", 420), ("tWRWRDD", "Setup", 422), ("tWRWR_SCL", "Setup", 424), ("tCKE", "Setup", 426),
    ("Channel B timings (Advanced Manual only)", "Setup", 428),
    ("ProcODT (Tweaker)", "Setup", 352), ("ProcODT (CBS)", "AmdSetup", 256), ("Command Rate", "Setup", 353),
    ("Gear Down Mode (Tweaker)", "Setup", 354), ("Gear Down Mode (CBS)", "AmdSetup", 258),
    ("Power Down Enable (CBS)", "AmdSetup", 259), ("Power Down Enable (AOD)", "AOD_SETUP", 121),
    ("RttNom (Tweaker)", "Setup", 365), ("RttWr (Tweaker)", "Setup", 366), ("RttPark (Tweaker)", "Setup", 367),
    ("AddrCmdSetup (Tweaker)", "Setup", 355), ("CsOdtSetup", "Setup", 357), ("CkeSetup", "Setup", 359),
    ("ClkDrvStren", "Setup", 361), ("AddrCmdDrvStren", "Setup", 362), ("CsOdtDrvStren", "Setup", 363),
    ("CkeDrvStren", "Setup", 364), ("AOD Data Bus Configuration User Controls", "AOD_SETUP", 131),
    ("AOD CAD Bus Timing User Controls", "AOD_SETUP", 122), ("AOD CAD Bus Drive Strength User Controls", "AOD_SETUP", 126),
    ("BankGroupSwap (CBS)", "AmdSetup", 288), ("BankGroupSwapAlt (CBS)", "AmdSetup", 289),
    ("Memory interleaving (CBS)", "AmdSetup", 187), ("Channel Interleaving (Tweaker)", "Setup", 350),
    ("Rank Interleaving (Tweaker)", "Setup", 351), ("TSME (CBS)", "AmdSetup", 285),
    ("DRAM Voltage (CH A/B)", "Setup", 508), ("DDRVPP Voltage", "Setup", 510), ("DRAM Termination", "Setup", 512),
    ("VCORE SOC", "Setup", 494), ("Dynamic VCORE SOC (DVID)", "Setup", 496), ("Vcore SOC LLC", "Setup", 516),
    ("CPU VDDP (Tweaker)", "Setup", 502), ("CPU VDD18", "Setup", 504),
    ("AOD VDDP Voltage Control", "AOD_SETUP", 184), ("AOD VDDP [mV]", "AOD_SETUP", 185),
    ("AOD VDDG Voltage Control", "AOD_SETUP", 189), ("AOD Global VDDG CCD [mV]", "AOD_SETUP", 579),
    ("AOD Global VDDG IOD [mV]", "AOD_SETUP", 577), ("AOD SoC/Uncore OC Mode", "AOD_SETUP", 138),
    ("AOD SoC Voltage [mV]", "AOD_SETUP", 335), ("CBS SOC OVERCLOCK VID", "AmdSetup", 340),
    ("CBS VDDP Voltage Control", "AmdSetup", 366), ("CBS VDDG Voltage Control", "AmdSetup", 558),
    ("DF Cstates (CBS)", "AmdSetup", 383), ("APBDIS (CBS)", "AmdSetup", 382), ("Fixed SOC Pstate (CBS)", "AmdSetup", 384),
    ("Global C-state Control (Tweaker)", "Setup", 329), ("Power Supply Idle Control (Tweaker)", "Setup", 330),
    ("Global C-state Control (CBS)", "AmdSetup", 36), ("Power Supply Idle Control (CBS)", "AmdSetup", 37),
    ("CPPC / Preferred Cores (Tweaker)", "Setup", 336), ("IOMMU (CBS)", "AmdSetup", 329),
    ("Precision Boost Overdrive (AOD)", "AOD_SETUP", 142), ("PBO Limits (AOD)", "AOD_SETUP", 143),
    ("PBO Scalar (AOD)", "AOD_SETUP", 164), ("CPU Boost Clock Override (AOD)", "AOD_SETUP", 182),
    ("Curve Optimizer (AOD)", "AOD_SETUP", 368),
    ("CO core 0 sign", "AOD_SETUP", 372), ("CO core 0 magnitude", "AOD_SETUP", 436),
    ("CO core 1 sign", "AOD_SETUP", 373), ("CO core 1 magnitude", "AOD_SETUP", 438),
    ("CO core 2 sign", "AOD_SETUP", 374), ("CO core 2 magnitude", "AOD_SETUP", 440),
    ("CO core 3 sign", "AOD_SETUP", 375), ("CO core 3 magnitude", "AOD_SETUP", 442),
    ("CO core 4 sign", "AOD_SETUP", 376), ("CO core 4 magnitude", "AOD_SETUP", 444),
    ("CO core 5 sign", "AOD_SETUP", 377), ("CO core 5 magnitude", "AOD_SETUP", 446),
    ("CO core 6 sign", "AOD_SETUP", 378), ("CO core 6 magnitude", "AOD_SETUP", 448),
    ("CO core 7 sign", "AOD_SETUP", 379), ("CO core 7 magnitude", "AOD_SETUP", 450),
    ("CBS Precision Boost Overdrive (XFR)", "AmdSetup", 342), ("CBS OC Mode", "AmdSetup", 55),
    ("Spread Spectrum (Tweaker)", "Setup", 307), ("SVM Mode (Tweaker)", "Setup", 327),
    ("Above 4G Decoding", "Setup", 642), ("Re-Size BAR", "Setup", 643), ("CSM Support", "Setup", 532),
]

FOCUS = [
    # (title, formset regex, include regex on "form path :: prompt", exclude regex)
    ("Tweaker / M.I.T. - frequency, CPU & memory (Gigabyte, Setup varstore)", r"^Setup$",
     r"Advanced Memory Settings|Memory Sub Timings|Advanced Frequency Settings|Advanced CPU Settings", None),
    ("Tweaker / M.I.T. - voltages (Gigabyte, Setup varstore)", r"^Setup$", r"Advanced Voltage Settings", None),
    ("AMD Overclocking (AOD_SETUP) - memory, fabric, voltages, PBO/CO", r"AMD Overclocking",
     r"DDR|DRAM|Data Bus|CAD Bus|Fabric|FCLK|UCLK|Voltage|VDD|SoC|Precision Boost|PBO|Curve|Boost|Thermal|NUMA|Gaming|LCLK|Power Down|Gear|Cmd2T",
     None),
    ("AMD CBS - UMC / DDR4", r"AMD CBS", r"UMC Common Options|DDR4 Common", r"MBIST|ECC Configuration|Common RAS"),
    ("AMD CBS - DF", r"AMD CBS", r"DF Common Options|Memory interleaving|NUMA", r"Link"),
    ("AMD CBS - NBIO / SMU / XFR", r"AMD CBS", r"SMU Common Options|XFR Enhancement|NBIO Common Options :: IOMMU",
     r"Fan Control|NBIO LCLK DPM|xGMI"),
    ("AMD CBS - CPU common (C-states, PSI, prefetch)", r"AMD CBS", r"CPU Common Options", r"Thread Enablement|SMU and PSP"),
]


def row_for(res, store, off):
    """First question instance at store@off, preferring one that is visible under the current values."""
    cands = [r for r in res["rows"]
             if r.get("varstore") == store and r.get("offset") == off and r["op"] not in ("REF", "ACTION")]
    for want in (True, None, False):
        for r in cands:
            if r.get("visible_now") is want:
                return r
    return None


def build_focus(res):
    rows = res["rows"]
    out = ["## Memory / Infinity Fabric / voltage related settings (focused)\n",
           "**state**: Auto = option text contains 'Auto' or a Gigabyte all-ones (0xFFFF/0xFFFFFFFF) Auto sentinel; "
           "default = equals the IFR default; MANUAL = anything else. **vis**: shown under the current values "
           "(suppressif evaluated; ? = depends on a boot-time-only variable).\n",
           "### Key settings (what is actually configured)\n",
           "| Item | Store@off | Current | state | IFR default | vis |\n|---|---|---|---|---|---|"]
    for label, store, off in KEY:
        r = row_for(res, store, off)
        if not r:
            out.append(f"| {label} | {store}@{off} | (not found) | | | |")
            continue
        s = res["settings"].get((r["varstore"], r["varstore_guid"], r["offset"], r["width"]), {})
        vt = s.get("value_text") or r.get("value_text") or ""
        if store == "Setup" and off in GBT_VOLT:
            vt = gbt_volt_text(off, r.get("value")) or vt
        vis = {True: "Y", False: "hidden", None: "?"}[s.get("visible_now", r.get("visible_now"))]
        out.append(f"| {label} | {store}@{off} | {fmt(r.get('value'))} {md_escape(vt)} | {r.get('auto_state')} | "
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
            vt = s.get("value_text") or r.get("value_text") or ""
            if r["varstore"] == "Setup" and r["offset"] in GBT_VOLT:
                vt = gbt_volt_text(r["offset"], r.get("value")) or vt
            vis = {True: "Y", False: "hidden", None: "?"}[s.get("visible_now", r.get("visible_now"))]
            page = r["form_path"].split(" > ")[-1]
            out.append(f"| {md_escape(page)} | {md_escape(r['prompt'])} | {r['varstore']}@{r['offset']}/{r['width']} | "
                       f"{fmt(r.get('value'))} {md_escape(vt)} | {r.get('auto_state')} | "
                       f"{fmt(r.get('ifr_default'))} {md_escape(r.get('default_text') or '')} | {vis} |")
        out.append("")
    return "\n".join(out) + "\n"


def sanity_checks(res, spec):
    out = []
    for c in spec:
        r = row_for(res, c["store"], c["offset"])
        if not r:
            out.append(dict(check=c["name"], expected=c["expect"], decoded="(question not found)", result="NOT FOUND"))
            continue
        v, t = r.get("value"), r.get("value_text")
        dec = c["conv"](v, t) if "conv" in c else (t if t and not t.isdigit() else v)
        res_ = c["test"](v, t)
        if res_ is True:
            res_ = "MATCH"
        elif res_ is False:
            res_ = "MISMATCH"
        out.append(dict(check=c["name"], where=f"{r['form_path']} :: {r['prompt']} ({r['varstore']}@{r['offset']}/{r['width']})",
                        expected=c["expect"], decoded=f"{dec} (raw {v})", result=res_, note=c.get("note", "")))
    return out


def _t(name, store, off, expect, test, **kw):
    return dict(name=name, store=store, offset=off, expect=expect, test=test, **kw)


CHECKS = [
    _t("XMP (Tweaker)", "Setup", 338, "Disabled (manual DDR4-3733)", lambda v, t: t == "Disabled"),
    _t("Memory speed (Tweaker System Memory Multiplier)", "Setup", 341, "DDR4-3733 (stored x100: 3733)", lambda v, t: v == 3733),
    _t("Memory Clock Speed (CBS UMC)", "AmdSetup", 213, "1867MHz (= DDR4-3733; code 56 = live MEMCLK ratio 56)",
       lambda v, t: t == "1867MHz"),
    _t("FCLK (CBS XFR Enhancement > FCLK Frequency)", "AmdSetup", 338, "1867MHz", lambda v, t: t == "1867MHz"),
    _t("Memory Timing Mode", "Setup", 345, "Manual (both channels)", lambda v, t: t == "Manual"),
    _t("tCL (Tweaker)", "Setup", 368, 14, lambda v, t: v == 14),
    _t("tCL (CBS)", "AmdSetup", 214, 14, lambda v, t: v == 14),
    _t("tRCDRD (Tweaker)", "Setup", 372, 15, lambda v, t: v == 15),
    _t("tRCDWR (Tweaker)", "Setup", 374, 15, lambda v, t: v == 15),
    _t("tRP (Tweaker)", "Setup", 376, 15, lambda v, t: v == 15),
    _t("tRAS (Tweaker)", "Setup", 378, 36, lambda v, t: v == 36),
    _t("tRC (Tweaker)", "Setup", 380, 51, lambda v, t: v == 51),
    _t("tRFC (Tweaker)", "Setup", 394, 288, lambda v, t: v == 288),
    _t("tRFC (CBS)", "AmdSetup", 237, 288, lambda v, t: v == 288),
    _t("tRRD_S (Tweaker)", "Setup", 386, 4, lambda v, t: v == 4),
    _t("tRRD_L (Tweaker)", "Setup", 388, 4, lambda v, t: v == 4),
    _t("tFAW (Tweaker)", "Setup", 402, 16, lambda v, t: v == 16),
    _t("tWTR_S (Tweaker)", "Setup", 390, 4, lambda v, t: v == 4),
    _t("tWTR_L (Tweaker)", "Setup", 392, 12, lambda v, t: v == 12),
    _t("tWR (Tweaker)", "Setup", 382, 26, lambda v, t: v == 26),
    _t("tRTP (Tweaker)", "Setup", 400, 14, lambda v, t: v == 14),
    _t("tCWL (Tweaker)", "Setup", 384, 14, lambda v, t: v == 14),
    _t("tRDWR (Tweaker)", "Setup", 406, 10, lambda v, t: v == 10),
    _t("tWRRD (Tweaker)", "Setup", 416, 3, lambda v, t: v == 3),
    _t("tRDRD_SCL / tWRWR_SCL (Tweaker)", "Setup", 414, "5 / 5", lambda v, t: v == 5),
    _t("tRDRDDD (Tweaker)", "Setup", 412, 6, lambda v, t: v == 6),
    _t("tWRWRDD (Tweaker)", "Setup", 422, 7, lambda v, t: v == 7),
    _t("Gear Down Mode (Tweaker)", "Setup", 354, "Enabled", lambda v, t: t == "Enabled"),
    _t("Gear Down Mode (CBS)", "AmdSetup", 258, "Enabled", lambda v, t: t == "Enabled"),
    _t("Command Rate (Tweaker)", "Setup", 353, "1T", lambda v, t: t == "1T"),
    _t("Power Down (CBS > DRAM Power Options)", "AmdSetup", 259, "Disabled (live UMC register)",
       lambda v, t: "MATCH" if t == "Disabled" else ("AUTO - resolves to Disabled (live UMC)" if t == "Auto" else False)),
    _t("VSOC (Tweaker VCORE SOC code)", "Setup", 494, "1.20 V-ish (pm_table 0xB0 SoC set-point)",
       lambda v, t: "MANUAL, but ~1.14-1.15 V, not 1.20 V (see note)" if v == 64 else False,
       conv=lambda v, t: gbt_volt_text(494, v),
       note="pm_table 0xB0 (1.20 V) is the SMU set-point; the board VRM (RAA229004 loop B) is driven by this code"),
    _t("DRAM Voltage (Tweaker code)", "Setup", 508, "~1.54 V (VTT proxy)",
       lambda v, t: "MATCH (1.550 V, inferred encoding)" if v == 55 else False, conv=lambda v, t: gbt_volt_text(508, v)),
    _t("VDDP (AOD, mV)", "AOD_SETUP", 185, "0.90 V (pm_table CLDO_VDDP 0.897)", lambda v, t: v == 900),
    _t("VDDG CCD (AOD Global, mV)", "AOD_SETUP", 579, "1.00 V (pm_table 0.998)", lambda v, t: v == 1000),
    _t("VDDG IOD (AOD Global, mV)", "AOD_SETUP", 577, "1.00 V (pm_table 0.998)", lambda v, t: v == 1000),
    _t("ProcODT (Tweaker)", "Setup", 352, "not known before (Auto or manual?)", lambda v, t: f"decoded: {t}"),
]


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
    checks = sanity_checks(res, CHECKS)
    focus = build_focus(res)
    write_outputs(res, ARGS.out, focus, checks)
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
    for c in checks:
        print(f"   check {c['check']}: {c['result']} ({c['decoded']})")
    print("wrote", os.path.join(ARGS.out, "bios_settings.json"), os.path.join(ARGS.out, "bios_settings.md"))


ARGS = None
if __name__ == "__main__":
    main()
