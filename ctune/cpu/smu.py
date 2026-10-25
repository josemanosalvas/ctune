"""Curve Optimizer on Vermeer (Zen 3, family 19h model 21h) through the ryzen_smu driver, plus per-core pm_table telemetry.

Command IDs and argument encoding, checked against the sources (notes and pinned commits: docs/research/cpu-co-smu.md):
- MP1 0x35 SetDldoPsmMargin, one core: ZenStates-Core 869b7e8 Hardware/Smu/Settings/Zen3Settings.cs L22;
  UXTU eeb4304 RyzenSmu.cs L422 (Socket_AM4_V2 = Matisse/Vermeer); ruv.py c08815d L114 (5800X3D, Linux).
  0x36 is SetAllDldoPsmMargin (every core at once: Zen3Settings.cs L23, UXTU L420, ruv.py L147). Zen 2 uses 0x34/0x35
  (Zen2Settings.cs L69-70), so the IDs shift by one between generations. ctune never sends 0x36.
- MP1 0x48 GetDldoPsmMargin, one core: ruv.py L103-110 only. Response argument 0 is a sign-extended 32-bit margin.
  ZenStates-Core has no Zen 3 MP1 getter; its RSMU 0x7C is marked "Not sure" (Zen2Settings.cs L35). The root helper
  writes nothing until a probe in the same boot has read the BIOS values back for every core.
- Argument 0: [31:28] CCD, [27:24] CCX (0 on Zen 3), [23:20] core, [19:16] zero, [15:0] margin as 16-bit two's
  complement. ZenStates-Core SetPsmMarginSingleCore.cs L3-10 and L25, Utils.cs L180-184 (MakePsmMarginArg),
  Cpu.cs L932-937 (MakeCoreMask); ruv.py L114 builds the same word. The other five arguments are 0.
- Mailbox: ryzen_smu takes exactly 24 bytes in smu_args (drv.c L223-228) and a 4-byte ID in mp1_smu_cmd (drv.c
  L169-186); MP1 on Vermeer is cmd 0x3B10530 / rsp 0x3B1057C / args 0x3B109C4 (smu.c L528-531, Zen3Settings.cs L11-13).
  The driver returns OK when the SMU answers an error code quickly (smu.c L201-213 fails only after the retries run
  out), so a status of 1 does not prove a write landed. Every write is read back with 0x48.
"""
import fcntl
import os
import struct

SMU_DIR = "/sys/kernel/ryzen_smu_drv"
LOCK = "/run/ctune-smu.lock"

MP1_SET_CORE = 0x35   # SetDldoPsmMargin (one core)
MP1_GET_CORE = 0x48   # GetDldoPsmMargin (one core), single source: see above
ALLOWED_OPS = (MP1_SET_CORE, MP1_GET_CORE)
STATUS_OK = 0x01
STATUS_NAMES = {0x01: "OK", 0xFF: "Failed", 0xFE: "UnknownCmd", 0xFD: "CmdRejectedPrereq", 0xFC: "CmdRejectedBusy",
                0xFB: "CommandTimeout", 0xFA: "InvalidArgument", 0xF9: "Unsupported", 0xF6: "PCIFailed"}  # smu.h L41-60

CO_MIN, CO_MAX = -30, 30   # Zen 3 range (ZenStates-Core SetPsmMarginSingleCore.cs L4); the SMU clamps beyond it
MAX_STEP = 5               # ctune cap: largest change of one core's margin in one step
MAX_CORES = 8              # one CCD; the core index field is 4 bits but Zen 3 has 8 cores per CCD

CODENAME_VERMEER = 12      # ryzen_smu smu.h L66-79
MP1_IF_VERSION_11 = 2      # smu.h L100-103
CPU_FAMILY, CPU_MODEL = 0x19, 0x21

# pm_table 0x380905 (docs/research/pmtable.md). Per-core arrays of 8 float32; labels from ryzen_monitor, alignment
# confirmed by C0 + CC1 + CC6 = 100 %. CORE_POWER also matches ZenStates-Core PowerTable.cs.
PM_VERSION, PM_SIZE = 0x380905, 1488
PM_CORE = {"core_power_w": 0x2B0, "core_voltage_v": 0x2D0, "core_temp_c": 0x2F0,
           "core_freq_ghz": 0x350, "core_eff_ghz": 0x370, "core_c0_pct": 0x390}
PM_SCALAR = {"ppt_w": 0x004, "tdc_a": 0x00C, "thm_c": 0x014}


class SmuError(RuntimeError):
    pass


def _int(label, v):
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"{label} must be an integer, got {v!r}")
    return v


def core_mask(core):
    core = _int("core", core)
    if not 0 <= core < MAX_CORES:
        raise ValueError(f"core {core} outside 0..{MAX_CORES - 1}")
    return core << 20   # CCD 0, CCX 0


def set_arg(core, margin):
    margin = _int("margin", margin)
    if not CO_MIN <= margin <= CO_MAX:
        raise ValueError(f"margin {margin} outside {CO_MIN}..{CO_MAX}")
    return core_mask(core) | (margin & 0xFFFF)


def get_arg(core):
    return core_mask(core)


def decode_margin(word):
    """Response argument 0 of 0x48: a sign-extended 32-bit integer (ruv.py L108-109)."""
    return word - (1 << 32) if word >= (1 << 31) else word


def check_readback(word):
    m = decode_margin(word)
    if not CO_MIN <= m <= CO_MAX:
        raise SmuError(f"getter returned {word:#010x}, not a margin in {CO_MIN}..{CO_MAX}; the 0x48 getter is unproven here")
    return m


def validate_change(core, current, new):
    """Guardrail errors for moving one core from `current` to `new` (empty list = allowed)."""
    errs = []
    try:
        set_arg(core, new)
    except ValueError as e:
        errs.append(str(e))
    if not isinstance(current, int) or isinstance(current, bool) or not CO_MIN <= current <= CO_MAX:
        errs.append(f"current margin of core {core} unknown ({current!r})")
    elif isinstance(new, int) and abs(new - current) > MAX_STEP:
        errs.append(f"core {core}: {current} -> {new} changes by {abs(new - current)}, more than {MAX_STEP} per step")
    return errs


# ---------------------------------------------------------------- mailbox
class SysfsIO:
    def __init__(self, root=SMU_DIR):
        self.root = root

    def write(self, name, data):
        fd = os.open(os.path.join(self.root, name), os.O_WRONLY)
        try:
            return os.write(fd, data)
        finally:
            os.close(fd)

    def read(self, name, n):
        fd = os.open(os.path.join(self.root, name), os.O_RDONLY)
        try:
            return os.read(fd, n)
        finally:
            os.close(fd)


class Mailbox:
    """MP1 commands through ryzen_smu. Only the IDs in ALLOWED_OPS can be sent."""

    def __init__(self, io=None, lock=None):
        self.io = io or SysfsIO()
        self.lock = lock

    def send(self, op, arg0):
        if op not in ALLOWED_OPS:
            raise SmuError(f"MP1 command {op:#x} is not on ctune's verified list")
        if not 0 <= arg0 <= 0xFFFFFFFF:
            raise SmuError(f"argument {arg0!r} is not a u32")
        lk = None
        if self.lock:
            lk = open(self.lock, "w")
            fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            args = struct.pack("<6I", arg0, 0, 0, 0, 0, 0)
            if self.io.write("smu_args", args) != 24:
                raise SmuError("smu_args write was not accepted (the driver takes exactly 24 bytes)")
            if self.io.write("mp1_smu_cmd", struct.pack("<I", op)) != 4:
                raise SmuError("mp1_smu_cmd write was not accepted")
            st = struct.unpack("<I", self.io.read("mp1_smu_cmd", 4)[:4])[0]
            resp = struct.unpack("<6I", self.io.read("smu_args", 24)[:24])
        finally:
            if lk:
                lk.close()
        if st != STATUS_OK:
            raise SmuError(f"MP1 {op:#x} returned status {st:#x} ({STATUS_NAMES.get(st, 'unknown')})")
        return resp

    def read_margin(self, core):
        return check_readback(self.send(MP1_GET_CORE, get_arg(core))[0])

    def write_margin(self, core, margin):
        arg = set_arg(core, margin)   # validates before anything is sent
        self.send(MP1_SET_CORE, arg)
        return arg


# ---------------------------------------------------------------- platform
def _rd(p):
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def topology():
    """{core_id: [logical cpus]} for online CPUs, and the package ids seen."""
    cores, pkgs = {}, set()
    base = "/sys/devices/system/cpu"
    for n in sorted(os.listdir(base)):
        if not (n.startswith("cpu") and n[3:].isdigit()):
            continue
        t = os.path.join(base, n, "topology")
        cid, pkg = _rd(os.path.join(t, "core_id")), _rd(os.path.join(t, "physical_package_id"))
        if cid is None:
            continue
        cores.setdefault(int(cid), []).append(int(n[3:]))
        pkgs.add(pkg)
    return {c: sorted(v) for c, v in sorted(cores.items())}, pkgs


def platform():
    """What the helper needs before it sends anything: (info, problems)."""
    info, problems = {}, []
    if not os.path.isdir(SMU_DIR):
        return info, ["ryzen_smu is not loaded (/sys/kernel/ryzen_smu_drv missing)"]
    info["codename"] = _rd(f"{SMU_DIR}/codename")
    info["mp1_if_version"] = _rd(f"{SMU_DIR}/mp1_if_version")
    info["smu_fw"] = _rd(f"{SMU_DIR}/version")
    info["ryzen_smu"] = _rd(f"{SMU_DIR}/drv_version")
    fam = model = None
    for line in (_rd("/proc/cpuinfo") or "").splitlines():
        k, _, v = line.partition(":")
        if k.strip() == "cpu family" and fam is None:
            fam = int(v)
        elif k.strip() == "model" and model is None:
            model = int(v)
        elif k.strip() == "model name":
            info.setdefault("cpu", v.strip())
    info["family_model"] = f"{fam:#x}/{model:#x}" if fam is not None and model is not None else None
    if info["codename"] != str(CODENAME_VERMEER):
        problems.append(f"ryzen_smu codename {info['codename']} is not Vermeer ({CODENAME_VERMEER})")
    if info["mp1_if_version"] != str(MP1_IF_VERSION_11):
        problems.append(f"MP1 interface version {info['mp1_if_version']} is not v11 ({MP1_IF_VERSION_11})")
    if (fam, model) != (CPU_FAMILY, CPU_MODEL):
        problems.append(f"CPU family/model {info['family_model']} is not Vermeer (0x19/0x21)")
    cores, pkgs = topology()
    info["cores"] = {c: cpus for c, cpus in cores.items()}
    if len(pkgs) != 1:
        problems.append(f"{len(pkgs)} CPU packages")
    if list(cores) != list(range(len(cores))) or not 0 < len(cores) <= MAX_CORES:
        problems.append(f"core ids {list(cores)} are not 0..N-1 on one CCD; the physical core mapping is unknown")
    return info, problems


def first_cpu(core):
    """The first logical CPU of a physical core (where single-thread tests pin)."""
    cores, _ = topology()
    return cores[core][0]


# ---------------------------------------------------------------- telemetry
def parse_pm(raw):
    if len(raw) < PM_SIZE:
        raise SmuError(f"pm_table is {len(raw)} bytes, expected {PM_SIZE} for {PM_VERSION:#x}")
    out = {k: [round(v, 4) for v in struct.unpack_from("<8f", raw, off)] for k, off in PM_CORE.items()}
    out.update({k: round(struct.unpack_from("<f", raw, off)[0], 3) for k, off in PM_SCALAR.items()})
    return out


def pm_version():
    try:
        with open(f"{SMU_DIR}/pm_table_version", "rb") as f:
            return struct.unpack("<I", f.read(4)[:4])[0]
    except (OSError, struct.error):
        return None


def read_pm():
    """Per-core telemetry from the live pm_table (world-readable, no root needed)."""
    v = pm_version()
    if v != PM_VERSION:
        raise SmuError(f"pm_table version {v if v is None else hex(v)} has no known layout (need {PM_VERSION:#x})")
    with open(f"{SMU_DIR}/pm_table", "rb") as f:
        return parse_pm(f.read())
