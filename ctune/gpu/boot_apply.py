#!/usr/bin/python3 -I -B
"""Re-apply the signed-off GPU settings at boot and after resume, with nvidia_oc.

`ctune gpu persist` installs a root-owned copy as /usr/local/libexec/ctune/gpu-boot-apply, beside a pinned
nvidia_oc build, and writes /etc/ctune/gpu.json. ctune-gpu-persist.service runs it at boot and
ctune-gpu-persist-resume.service after suspend. It uses the standard library only and imports nothing from the
repo: the user can write the repo, and this runs as root without a password prompt.

/etc/ctune/gpu.json: {"settings": {"core_offset": MHz, "mem_offset": MHz, "max_clock": MHz or null,
"power_limit": W or null}, "gpu_uuid": ..., "driver": ..., "signoff": {...}, "persisted": ...}

It applies nothing, and says why, when:
- the GPU's UUID or driver version differs from the signed-off one: a new driver needs a new sign-off;
- a value is outside ctune's caps (the same as apply.py's);
- the crash-loop guard trips: in the last 2 boots the GPU failed after the settings were applied. A boot fails
  when it crashes or hangs within 10 minutes, or logs an NVIDIA Xid error after the apply. It stays tripped until
  `ctune gpu persist --reset-guard` or a new persist;
- resume: the boot unit did not apply them this boot (skipped, failed, or masked).
`ctune.gpu=off` or `systemd.mask=ctune-gpu-persist.service` on the kernel command line skips both units.

The power limit and the clock lock go first, then the offsets, so a failure never leaves an offset without its
clock cap. nvidia_oc stops at the first failing setter, so on any failure, or on a readback that differs, the GPU
goes back to stock: offsets 0, clocks unlocked, default power limit.

Every run except --once writes its result to /run/ctune/gpu-persist.json, which `ctune gpu status` reads. Exit 1
when the apply or its readback failed; a skip exits 0 and logs a warning.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

CONFIG = "/etc/ctune/gpu.json"
LIBEXEC = "/usr/local/libexec/ctune"
NVIDIA_OC = os.path.join(LIBEXEC, "nvidia_oc")
NVIDIA_SMI = "/usr/bin/nvidia-smi"
DMESG = "/usr/bin/dmesg"
SYSTEMD_RUN = "/usr/bin/systemd-run"
RUN_DIR = "/run/ctune"
STATE_DIR = "/var/lib/ctune"
GUARD_BOOTS = 2
GOOD_AFTER_S = 600
# the same caps as apply.py (tests/test_gpu_persist.py checks they match)
CAP_CORE = (-300, 300)
CAP_MEM = (-1000, 1500)
CAP_CLOCK = (300, 2100)


def _run(cmd, timeout=60):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def identity(smi):
    rc, out = _run([smi, "--query-gpu=uuid,driver_version,power.limit,power.default_limit",
                    "--format=csv,noheader,nounits", "-i", "0"])
    if rc:
        raise RuntimeError(f"nvidia-smi failed ({rc}): {out[-300:]}")
    uuid, driver, pl, pl_default = (x.strip() for x in out.splitlines()[0].split(","))
    return {"gpu_uuid": uuid, "driver": driver, "power_limit": float(pl), "power_default": float(pl_default)}


def readback(oc, smi):
    """Offsets from nvidia_oc get (nvidia-smi cannot read them), the power limit from nvidia-smi. NVML has no
    getter for locked clocks."""
    rc, out = _run([oc, "get", "--index", "0"])
    core = re.search(r"GPU core clock offset: (-?\d+) MHz", out)
    mem = re.search(r"GPU memory clock offset: (-?\d+) MHz", out)
    return {"core_offset": int(core.group(1)) if core else None, "mem_offset": int(mem.group(1)) if mem else None,
            "power_limit": identity(smi)["power_limit"]}


def out_of_caps(s):
    errs = []
    for k, cap in (("core_offset", CAP_CORE), ("mem_offset", CAP_MEM), ("max_clock", CAP_CLOCK)):
        if s.get(k) is not None and not cap[0] <= int(s[k]) <= cap[1]:
            errs.append(f"{k} {s[k]} is outside ctune's cap {cap[0]}..{cap[1]}")
    return errs


def set_steps(s):
    """nvidia_oc set arguments in two calls: the caps (power limit, clock lock) first, then the offsets."""
    caps, offsets = [], []
    if s.get("power_limit") is not None:
        caps += ["--power-limit", str(int(round(float(s["power_limit"]) * 1000)))]   # milliwatts
    if s.get("max_clock") is not None:
        caps += ["--min-clock", "0", "--max-clock", str(int(s["max_clock"]))]
    if s.get("core_offset") is not None:
        offsets += ["--freq-offset", str(int(s["core_offset"]))]
    if s.get("mem_offset") is not None:
        offsets += ["--mem-offset", str(int(s["mem_offset"]))]
    return [a for a in (caps, offsets) if a]


def mismatches(want, got):
    out = []
    for k in ("core_offset", "mem_offset"):
        if want.get(k) is not None and got.get(k) != int(want[k]):
            out.append(f"{k}: wanted {want[k]}, read {got.get(k)}")
    if want.get("power_limit") is not None and abs(float(want["power_limit"]) - got["power_limit"]) > 0.5:
        out.append(f"power_limit: wanted {want['power_limit']} W, read {got['power_limit']} W")
    return out


def to_stock(oc, smi, power_default):
    """Offsets 0, clocks unlocked, default power limit. Returns the commands that failed."""
    failed = []
    for cmd in ([oc, "set", "--index", "0", "--freq-offset", "0", "--mem-offset", "0"],
                [smi, "-i", "0", "-rgc"], [smi, "-i", "0", "-pl", f"{power_default:g}"]):
        try:
            rc, out = _run(cmd)
        except Exception as e:
            rc, out = -1, f"{type(e).__name__}: {e}"
        if rc:
            failed.append(f"{' '.join(cmd[1:])}: {out[-200:]}")
    return failed


# ---------------------------------------------------------------- crash-loop guard
def _attempts_path(state_dir):
    return os.path.join(state_dir, "gpu-boot-attempts")


def read_attempts(state_dir):
    try:
        with open(_attempts_path(state_dir)) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def write_attempts(state_dir, n):
    os.makedirs(state_dir, mode=0o755, exist_ok=True)
    p = _attempts_path(state_dir)
    with open(p + ".tmp", "w") as f:
        f.write(f"{n}\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(p + ".tmp", p)
    fd = os.open(state_dir, os.O_RDONLY)
    try:
        os.fsync(fd)   # the count must survive the crash it is there to detect
    finally:
        os.close(fd)


def xid_lines(dmesg=DMESG):
    """NVIDIA Xid errors this boot, from the kernel ring buffer (the journal can rotate them away). None when
    the buffer cannot be read."""
    try:
        rc, out = _run([dmesg, "--kernel", "--notime"])
    except Exception:
        return None
    return None if rc else [l for l in out.splitlines() if "NVRM: Xid" in l]


def _guard_path(run_dir):
    return os.path.join(run_dir, "gpu-guard.json")


def mark_good(state_dir, run_dir, dmesg=DMESG):
    """After 10 minutes up, and at a clean shutdown: the boot counts as good unless the GPU logged an Xid error
    after the settings were applied. A bad boot counts on top of the bad boots before it."""
    try:
        with open(_guard_path(run_dir)) as f:
            g = json.load(f)
    except (OSError, ValueError):
        return {"marked": None}   # the boot unit applied nothing this boot
    xids = xid_lines(dmesg)
    new = xids[g.get("xid_at_apply") or 0:] if xids else []
    if new:
        write_attempts(state_dir, g["before"] + 1)
        print(f"<4>ctune-gpu: {len(new)} Xid errors since the GPU settings were applied; this boot counts as failed "
              f"for the crash-loop guard: {new[-1]}")
        return {"marked": "bad", "xid": new[-3:]}
    write_attempts(state_dir, 0)
    return {"marked": "good", "xid_unreadable": xids is None}


def schedule_mark_good(self_path, state_dir, run_dir):
    """A transient timer, unique per run so a second persist in the same boot does not collide; gone at reboot."""
    return _run([SYSTEMD_RUN, f"--on-active={GOOD_AFTER_S}", f"--unit=ctune-gpu-persist-ok-{int(time.time())}",
                 "--timer-property=RemainAfterElapse=no",
                 "--description=ctune: mark this boot good for the GPU crash-loop guard",
                 sys.executable, "-I", "-B", self_path, "--mark-good", "--state-dir", state_dir, "--run-dir", run_dir])


def this_boot_result(run_dir):
    try:
        with open(os.path.join(run_dir, "gpu-persist.json")) as f:
            r = json.load(f)
        with open("/proc/sys/kernel/random/boot_id") as f:
            return r if r.get("boot_id") == f.read().strip() else None
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- one run
def apply(mode, config, oc, smi, state_dir, run_dir, dmesg=DMESG):
    """mode: boot (crash-loop guard), resume (only after a good boot apply), or once (an explicit config, no
    guard, for ctune's self-test)."""
    res = {"mode": mode, "config": config, "ok": False, "applied": None, "skipped": None, "errors": []}
    try:
        with open(config) as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        res["errors"].append(f"cannot read {config}: {e}")
        return res
    want = cfg.get("settings") or {}
    res["signoff"] = cfg.get("signoff")
    if mode == "resume":
        prev = this_boot_result(run_dir)
        if not prev or not prev.get("ok"):
            res["skipped"] = "the boot unit did not apply the settings this boot (skipped, failed or masked)"
            return res
    ident = identity(smi)
    res["identity"] = {k: ident[k] for k in ("gpu_uuid", "driver")}
    if ident["gpu_uuid"] != cfg.get("gpu_uuid"):
        res["skipped"] = f"GPU {ident['gpu_uuid']} is not the signed-off GPU {cfg.get('gpu_uuid')}"
        return res
    if ident["driver"] != cfg.get("driver"):
        res["skipped"] = (f"driver {ident['driver']} differs from the signed-off driver {cfg.get('driver')}: "
                          "run the GPU sign-off again, then `ctune gpu persist`")
        return res
    caps = out_of_caps(want)
    if caps:
        res["skipped"] = "; ".join(caps)
        return res
    steps = set_steps(want)
    if not steps:
        res["errors"].append("the config holds no settings")
        return res
    before = None
    if mode == "boot":
        before = read_attempts(state_dir)
        if before >= GUARD_BOOTS:
            res["skipped"] = (f"crash-loop guard: the GPU failed in the last {before} boots with these settings "
                              f"(a crash within {GOOD_AFTER_S // 60} minutes, or an Xid error)")
            return res
        write_attempts(state_dir, before + 1)   # counts as failed until marked good
        os.makedirs(run_dir, mode=0o755, exist_ok=True)
        with open(_guard_path(run_dir), "w") as f:   # Xid errors from before the apply do not count
            json.dump({"before": before, "xid_at_apply": len(xid_lines(dmesg) or [])}, f)
    for args in steps:
        rc, out = _run([oc, "set", "--index", "0", *args])
        if rc:
            res["errors"].append(f"nvidia_oc set {' '.join(args)} failed ({rc}): {out[-400:]}")
            break
    if not res["errors"]:
        res["readback"] = readback(oc, smi)
        res["errors"] += mismatches(want, res["readback"])
    if res["errors"]:
        res["rolled_back"] = True
        res["errors"] += [f"rollback: {e}" for e in to_stock(oc, smi, ident["power_default"])]
        res["readback"] = readback(oc, smi)
        if before is not None:   # the GPU is back at stock: this boot is no crash risk
            write_attempts(state_dir, before)
            os.remove(_guard_path(run_dir))
        return res
    res["ok"] = True
    res["applied"] = {k: want.get(k) for k in ("core_offset", "mem_offset", "max_clock", "power_limit")}
    return res


def write_result(run_dir, res):
    os.makedirs(run_dir, mode=0o755, exist_ok=True)
    with open("/proc/sys/kernel/random/boot_id") as f:
        res["boot_id"] = f.read().strip()
    res["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    res["epoch"] = time.time()
    p = os.path.join(run_dir, "gpu-persist.json")
    with open(p + ".tmp", "w") as f:
        json.dump(res, f, indent=1)
    os.chmod(p + ".tmp", 0o644)
    os.replace(p + ".tmp", p)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--boot", action="store_true")
    g.add_argument("--resume", action="store_true")
    g.add_argument("--once", metavar="CONFIG", help="apply CONFIG once: no guard, result on stdout only")
    g.add_argument("--mark-good", action="store_true")
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--nvidia-oc", default=NVIDIA_OC)
    ap.add_argument("--nvidia-smi", default=NVIDIA_SMI)
    ap.add_argument("--dmesg", default=DMESG)
    ap.add_argument("--run-dir", default=RUN_DIR)
    ap.add_argument("--state-dir", default=STATE_DIR)
    a = ap.parse_args(argv)
    if a.mark_good:
        print(json.dumps(mark_good(a.state_dir, a.run_dir, a.dmesg)))
        return 0
    mode = "boot" if a.boot else "resume" if a.resume else "once"
    try:
        res = apply(mode, a.once or a.config, a.nvidia_oc, a.nvidia_smi, a.state_dir, a.run_dir, a.dmesg)
    except Exception as e:   # report, never crash-loop on a parsing or tool error
        res = {"mode": mode, "ok": False, "applied": None, "skipped": None, "errors": [f"{type(e).__name__}: {e}"]}
    if mode == "boot" and res["ok"]:
        rc, out = schedule_mark_good(os.path.realpath(__file__), a.state_dir, a.run_dir)
        if rc:
            res["errors"].append(f"could not schedule the guard reset ({rc}): {out[-200:]}")
    if mode != "once":
        write_result(a.run_dir, res)
    if res["skipped"]:
        print(f"<4>ctune-gpu: skipped the persisted GPU settings: {res['skipped']}")
    elif not res["ok"]:
        print(f"<3>ctune-gpu: applying the persisted GPU settings failed: {'; '.join(res['errors'])[:600]}")
    print(json.dumps(res))
    return 0 if res["ok"] or res["skipped"] else 1


if __name__ == "__main__":
    sys.exit(main())
