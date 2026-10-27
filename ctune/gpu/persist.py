"""Keep signed-off GPU settings across reboots: a root-owned boot unit re-applies them with nvidia_oc.

Policy (docs/design.md section 1): only settings that passed `signoff`
persist. Experiments stay runtime-only and a reboot clears them. persist() refuses unless:
- ctune applied settings this boot, and NVML reads back the same offsets and power limit;
- the latest sign-off run with exactly these settings and a full-length gpu phase was green, on this GPU and
  this driver. A sign-off without the gpu phase says nothing about GPU settings.

nvidia_oc (github.com/Dreaming-Codes/nvidia_oc, MIT) sets the same NVML values as ctune's own helper
(nvmlDeviceSetGpcClkVfOffset, ...SetMemClkVfOffset, ...SetPowerManagementLimit, ...SetGpuLockedClocks). nvidia-smi
covers the power limit and the clock lock but has no clock offsets, which the undervolt needs. ctune builds it in
podman from a pinned commit with its Cargo.lock, like gpu-burn. The boot side is boot_apply.py; the installer is
install.py (root, pkexec).
"""
import json
import os
import subprocess
import sys
import tempfile
import time

from ctune.core import journal
from ctune.core.paths import DATA, REPO, TOOLS

COMMIT = "54f59c3aad68d671dbd670a1ffaa8459419bd258"   # tag 0.1.24
RUST_IMAGE = "docker.io/library/rust:1-slim-bookworm"
SRC = os.path.join(TOOLS, "nvidia_oc", "src")
OUT = os.path.join(TOOLS, "nvidia_oc", "out")
BINARY = os.path.join(OUT, "nvidia_oc")
CONTAINERFILE = os.path.join(REPO, "ctune", "gpu", "nvidia_oc.Containerfile")
HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "install.py")
CONFIG = "/etc/ctune/gpu.json"
BOOT_RESULT = "/run/ctune/gpu-persist.json"
KEYS = ("core_offset", "mem_offset", "max_clock", "power_limit")


def setup():
    """Clone the pinned commit, build nvidia_oc in podman, drop the Rust image (about 1 GB), write VERSION."""
    if not os.path.isdir(os.path.join(SRC, ".git")):
        subprocess.run(["git", "clone", "-q", "https://github.com/Dreaming-Codes/nvidia_oc", SRC], check=True)
    subprocess.run(["git", "-C", SRC, "fetch", "-q", "origin"], check=False)
    subprocess.run(["git", "-C", SRC, "checkout", "-q", "--detach", COMMIT], check=True)
    r = subprocess.run(["podman", "build", "--pull=missing", "--build-arg", f"RUST_IMAGE={RUST_IMAGE}", "-f",
                        CONTAINERFILE, "-o", f"type=local,dest={OUT}", SRC], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"podman build failed ({r.returncode}): {(r.stdout + r.stderr)[-1500:]}")
    rustc = subprocess.run(["podman", "run", "--rm", "--network=none", RUST_IMAGE, "rustc", "--version"],
                           capture_output=True, text=True).stdout.strip()
    digest = subprocess.run(["podman", "image", "inspect", "--format", "{{.Digest}}", RUST_IMAGE],
                            capture_output=True, text=True).stdout.strip()
    subprocess.run(["podman", "rmi", RUST_IMAGE], capture_output=True)
    subprocess.run(["podman", "image", "prune", "-f"], capture_output=True)
    sha = _sha256(BINARY)
    with open(os.path.join(TOOLS, "nvidia_oc", "VERSION"), "w") as f:
        f.write(f"nvidia_oc {COMMIT} (tag 0.1.24, github.com/Dreaming-Codes/nvidia_oc, MIT), cargo build --release "
                f"--locked with {rustc} in {RUST_IMAGE}@{digest}; sha256 {sha}\n")
    return {"binary": BINARY, "sha256": sha, "rustc": rustc}


def _sha256(p):
    import hashlib
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def normalize(s):
    """The four persisted values. An offset ctune never set is 0 after boot; an unset clock lock or power limit
    stays None (not touched)."""
    s = s if isinstance(s, dict) else {}
    return {"core_offset": int(s.get("core_offset") or 0), "mem_offset": int(s.get("mem_offset") or 0),
            "max_clock": s.get("max_clock"),
            "power_limit": None if s.get("power_limit") is None else float(s["power_limit"])}


def same(a, b):
    a, b = normalize(a), normalize(b)
    if (a["core_offset"], a["mem_offset"], a["max_clock"]) != (b["core_offset"], b["mem_offset"], b["max_clock"]):
        return False
    if a["power_limit"] is None or b["power_limit"] is None:
        return a["power_limit"] is b["power_limit"]
    return abs(a["power_limit"] - b["power_limit"]) <= 0.5


def identity():
    from ctune.gpu import nvml
    h = nvml.handle(0)
    return {"gpu_uuid": nvml.uuid(h), "driver": nvml.driver_version()}


def plan(current, readback, ident, records):
    """What persist() would write, or why it refuses. current: what ctune applied this boot; readback: NVML now;
    records: journal results, oldest first."""
    reasons = []
    s = normalize(current)
    if not current:
        reasons.append("ctune applied no GPU settings this boot: apply the signed-off settings first")
    for k in ("core_offset", "mem_offset"):
        if readback.get(k) != s[k]:
            reasons.append(f"{k} reads {readback.get(k)} but ctune applied {s[k]}: something else changed it")
    if s["power_limit"] is not None and abs(readback["power_limit"] - s["power_limit"]) > 0.5:
        reasons.append(f"power limit reads {readback['power_limit']} W but ctune applied {s['power_limit']} W")
    from ctune.core.signoff import MINUTES
    runs = [r for r in records if r.get("domain") == "signoff" and isinstance(r.get("settings"), dict)
            and isinstance(r["settings"].get("gpu"), dict) and same(r["settings"]["gpu"], s)]
    full = [r for r in runs if "gpu" in (r.get("phases") or [])
            and (r.get("minutes") or {}).get("gpu", 0) >= MINUTES["gpu"]]
    last = full[-1] if full else None
    if not last:
        reasons.append("no sign-off with a full gpu phase ran with these GPU settings"
                       + (f" ({len(runs)} without it)" if runs else ""))
    elif last.get("verdict") != "green":
        reasons.append(f"the latest sign-off with these settings ({last.get('ts')}) is {last.get('verdict')}")
    elif last["settings"].get("gpu_identity") != ident:
        reasons.append(f"that sign-off ran on {last['settings'].get('gpu_identity')}, this is {ident}: "
                       "run it again on this GPU and driver")
    config = {"settings": s, **ident, "persisted": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "signoff": {k: last.get(k) for k in ("ts", "verdict", "duration_min", "text")} if last else None}
    return {"ok": not reasons, "config": config, "reasons": reasons}


def _plan():
    from ctune.gpu import api as gpu, nvml
    h = nvml.handle(0)
    rb = {"core_offset": nvml.core_offset(h), "mem_offset": nvml.mem_offset(h), "power_limit": nvml.power_limit_w(h)}
    return plan(gpu.applied(), rb, identity(), journal.tail(DATA, 1_000_000, "result"))


def _root(args):
    """Run install.py as root (pkexec prompt) and return its JSON result."""
    cmd = [sys.executable, "-I", "-B", HELPER, *args]
    try:
        r = subprocess.run(["pkexec", *cmd] if os.geteuid() else cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise RuntimeError("the root password prompt was not answered within 10 minutes; nothing was changed")
    if r.returncode in (126, 127) and os.geteuid():
        raise RuntimeError("the root password prompt was dismissed; nothing was changed")
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError(f"install helper failed ({r.returncode}): {r.stderr.strip()[-600:]}")


def _install(flag, config):
    if not os.path.isfile(BINARY):
        raise RuntimeError("nvidia_oc is not built: run `ctune gpu persist-setup`")
    with tempfile.NamedTemporaryFile("w", suffix=".json", prefix="ctune-gpu-", delete=False) as f:
        json.dump(config, f)
    try:
        return _root(["--binary", BINARY, flag, f.name])
    finally:
        os.unlink(f.name)


def persist(dry_run=False):
    """Persist the settings applied now, if a green sign-off covers them. Root: one password prompt."""
    p = _plan()
    if dry_run or not p["ok"]:
        return p
    out = _install("--config", p["config"])
    journal.add(DATA, "applied", domain="gpu", text="persisted the signed-off GPU settings (boot unit)",
                settings=p["config"]["settings"], signoff=p["config"]["signoff"], ok=out.get("ok"),
                boot_unit=out.get("boot_unit"))
    return out


def selftest():
    """Install the boot side, then apply the values NVML reads now through it once: a no-op that checks the
    installed nvidia_oc and applier on this driver. No config is written, so the units stay inert. Root."""
    from ctune.gpu import nvml
    h = nvml.handle(0)
    cfg = {"settings": {"core_offset": nvml.core_offset(h), "mem_offset": nvml.mem_offset(h),
                        "max_clock": None, "power_limit": nvml.power_limit_w(h)}, **identity()}
    out = _install("--selftest", cfg)
    journal.add(DATA, "note", domain="gpu", text="GPU persistence self-test (no-op apply through the boot side)",
                ok=out.get("ok"), result=out.get("selftest"))
    return out


def reset_guard():
    """Clear a tripped crash-loop guard, so the next boot applies the persisted settings again. Root."""
    out = _root(["--reset-guard"])
    journal.add(DATA, "note", domain="gpu", text="GPU crash-loop guard reset", was=out.get("was"))
    return out


def unpersist(uninstall=False):
    """Stop re-applying at boot. Settings applied now stay until reboot. Root: one password prompt."""
    out = _root(["--clear"] + (["--uninstall"] if uninstall else []))
    journal.add(DATA, "applied", domain="gpu", text="GPU settings no longer persist" + (" (uninstalled)" if uninstall else ""))
    return out


def passwordless(remove=False):
    """Install (or remove) the passwordless path for every domain: root-owned copies of the GPU runtime helper, the
    CPU Curve Optimizer helper and the RAM snapshot collector, and a polkit rule for this user (install.py). Root:
    one last password prompt."""
    if remove:
        out = _root(["--passwordless-remove"])
    else:
        if not os.path.isfile(BINARY):
            raise RuntimeError("nvidia_oc is not built: run `ctune gpu persist-setup`")
        out = _root(["--binary", BINARY, "--passwordless"])
    journal.add(DATA, "note", text="passwordless root path (GPU, CPU, RAM) " + ("removed" if remove else "installed"),
                result=out)
    return out


def boot_result():
    """This boot's result from the boot unit, or None."""
    try:
        with open(BOOT_RESULT) as f:
            r = json.load(f)
        with open("/proc/sys/kernel/random/boot_id") as f:
            return r if r.get("boot_id") == f.read().strip() else None
    except (OSError, ValueError):
        return None


def status():
    try:
        with open(CONFIG) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        cfg = None
    except (OSError, ValueError) as e:
        cfg = {"error": str(e)}
    out = {"persisted": cfg}
    b = boot_result()
    if b:
        out["this_boot"] = {k: b.get(k) for k in ("mode", "ok", "applied", "skipped", "errors", "ts")}
    return out
