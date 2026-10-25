#!/usr/bin/env python3
"""Root helper for persisting GPU settings, run through pkexec by ctune.gpu.persist (one password prompt per call).

  install.py --binary NVIDIA_OC --config FILE   install, write FILE as /etc/ctune/gpu.json, and (re)start the boot
                                                unit, which applies it now through the installed copy
  install.py --binary NVIDIA_OC --selftest FILE install, then apply FILE once through the installed copy; no config
                                                is written, so the units stay inert
  install.py --clear [--uninstall]              remove /etc/ctune/gpu.json; --uninstall also removes the units and
                                                /usr/local/libexec/ctune
  install.py --reset-guard                      clear the crash-loop guard, so the next boot applies again

Installing copies nvidia_oc and boot_apply.py into /usr/local/libexec/ctune, owned by root, and writes and enables
ctune-gpu-persist.service (boot) and ctune-gpu-persist-resume.service (after suspend). The boot path then runs only
these root-owned copies, never the repo, which the user can write. Settings applied now are not changed by
--clear; a reboot clears them. A persist whose apply fails removes its config again.
"""
import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from ctune.gpu import boot_apply  # noqa: E402

LIBEXEC = "/usr/local/libexec/ctune"
APPLIER = os.path.join(LIBEXEC, "gpu-boot-apply")
NVIDIA_OC = os.path.join(LIBEXEC, "nvidia_oc")
CONFIG = "/etc/ctune/gpu.json"
STATE_DIR = "/var/lib/ctune"
UNITS = "/etc/systemd/system"
BOOT_UNIT = "ctune-gpu-persist.service"
RESUME_UNIT = "ctune-gpu-persist-resume.service"
SLEEP = "suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target"
UNIT_TEXT = {
    BOOT_UNIT: f"""[Unit]
Description=ctune: apply the signed-off GPU settings in {CONFIG}
Documentation=file://{APPLIER}
ConditionPathExists={CONFIG}
ConditionKernelCommandLine=!ctune.gpu=off
Wants=nvidia-persistenced.service
After=nvidia-persistenced.service systemd-modules-load.service

[Service]
Type=oneshot
RemainAfterExit=yes
TimeoutStartSec=3min
ExecStart=/usr/bin/python3 -I -B {APPLIER} --boot
# at a clean shutdown the boot counts as good for the crash-loop guard, unless the GPU logged an Xid error
ExecStop=/usr/bin/python3 -I -B {APPLIER} --mark-good

[Install]
WantedBy=multi-user.target
""",
    RESUME_UNIT: f"""[Unit]
Description=ctune: re-apply the signed-off GPU settings after resume
Documentation=file://{APPLIER}
ConditionPathExists={CONFIG}
ConditionKernelCommandLine=!ctune.gpu=off
After={SLEEP} nvidia-resume.service

[Service]
Type=oneshot
TimeoutStartSec=3min
ExecStart=/usr/bin/python3 -I -B {APPLIER} --resume

[Install]
WantedBy={SLEEP}
""",
}


def sha256(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def check_root_owned(path):
    """Refuse to install below a directory that a user could swap out. Follows symlinks: on Fedora Atomic
    /usr/local is a link to /var/usrlocal."""
    p = os.path.realpath(os.path.dirname(path))
    while True:
        st = os.stat(p)
        if st.st_uid != 0 or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise RuntimeError(f"{p} is not owned by root or is writable by others; not installing below it")
        if p == "/":
            return
        p = os.path.dirname(p)


def put(src_or_text, dst, mode, text=False):
    os.makedirs(os.path.dirname(dst), mode=0o755, exist_ok=True)
    check_root_owned(dst)
    tmp = dst + ".ctune-tmp"
    if text:
        with open(tmp, "w") as f:
            f.write(src_or_text)
    else:
        shutil.copyfile(src_or_text, tmp)
    os.chown(tmp, 0, 0)
    os.chmod(tmp, mode)
    os.replace(tmp, dst)


def systemctl(*args):
    try:
        r = subprocess.run(["/usr/bin/systemctl", *args], capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        return {"cmd": " ".join(args), "rc": -1, "out": "timed out after 240 s"}
    return {"cmd": " ".join(args), "rc": r.returncode, "out": (r.stdout + r.stderr).strip()[-400:] or None}


def install(binary):
    put(binary, NVIDIA_OC, 0o755)
    put(os.path.join(HERE, "boot_apply.py"), APPLIER, 0o755)
    for name, text in UNIT_TEXT.items():
        put(text, os.path.join(UNITS, name), 0o644, text=True)
    steps = [systemctl("daemon-reload"), systemctl("enable", BOOT_UNIT, RESUME_UNIT)]
    return {"installed": {NVIDIA_OC: sha256(NVIDIA_OC), APPLIER: sha256(APPLIER)}, "steps": steps}


def run_applier(*args):
    r = subprocess.run([sys.executable, "-I", "-B", APPLIER, *args], capture_output=True, text=True, timeout=180)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "errors": [f"applier exit {r.returncode}: {(r.stdout + r.stderr).strip()[-400:]}"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--config")
    g.add_argument("--selftest")
    g.add_argument("--clear", action="store_true")
    g.add_argument("--reset-guard", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("must run as root")
    out = {"ok": True}
    attempts = os.path.join(STATE_DIR, "gpu-boot-attempts")
    if a.reset_guard:
        out["was"] = boot_apply.read_attempts(STATE_DIR)
        if os.path.exists(attempts):
            os.remove(attempts)
        print(json.dumps(out))
        return 0
    if a.clear:
        removed = [p for p in (CONFIG, attempts) if os.path.exists(p)]
        for p in removed:
            os.remove(p)
        if a.uninstall:
            out["steps"] = [systemctl("disable", BOOT_UNIT, RESUME_UNIT)]
            for name in UNIT_TEXT:
                p = os.path.join(UNITS, name)
                if os.path.exists(p):
                    os.remove(p)
                    removed.append(p)
            if os.path.isdir(LIBEXEC):
                shutil.rmtree(LIBEXEC)
                removed.append(LIBEXEC)
            out["steps"].append(systemctl("daemon-reload"))
        out["removed"] = removed
        print(json.dumps(out))
        return 0
    if not a.binary or not os.path.isfile(a.binary):
        print(json.dumps({"ok": False, "errors": [f"nvidia_oc binary not found: {a.binary}"]}))
        return 2
    try:
        with open(a.config or a.selftest) as f:
            cfg = json.load(f)
        if not isinstance(cfg.get("settings"), dict):
            raise ValueError("no settings")
        caps = boot_apply.out_of_caps(cfg["settings"])
        if caps:
            raise ValueError("; ".join(caps))
    except (OSError, ValueError) as e:
        print(json.dumps({"ok": False, "errors": [f"bad config: {e}"]}))
        return 2
    out.update(install(a.binary))
    if a.selftest:
        out["selftest"] = run_applier("--once", a.selftest)
        out["ok"] = bool(out["selftest"].get("ok"))
    else:
        put(json.dumps(cfg, indent=1) + "\n", CONFIG, 0o644, text=True)
        out["steps"].append(systemctl("stop", BOOT_UNIT))   # its ExecStop updates the guard: clear it after
        result = "/run/ctune/gpu-persist.json"
        for p in (attempts, result, "/run/ctune/gpu-guard.json"):   # a fresh guard; never an earlier result
            if os.path.exists(p):
                os.remove(p)
        out["steps"].append(systemctl("start", BOOT_UNIT))
        try:
            with open(result) as f:
                out["boot_unit"] = json.load(f)
        except (OSError, ValueError) as e:
            out["boot_unit"] = {"ok": False, "errors": [f"no result from the boot unit: {e}"]}
        out["ok"] = bool(out["boot_unit"].get("ok"))
        if not out["ok"]:   # never leave a config that failed to apply for the next boot
            os.remove(CONFIG)
            out["config_removed"] = CONFIG
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
