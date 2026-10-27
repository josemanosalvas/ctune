#!/usr/bin/python3 -IB
"""Apply or reset GPU settings at runtime, with no password prompt: ctune's passwordless path.

`ctune gpu passwordless --install` copies this file, owned by root, to /usr/local/libexec/ctune/gpu-runtime-apply,
beside the gpu-boot-apply and nvidia_oc copies, and adds a polkit rule that lets one user run exactly that path
through pkexec without a password (docs/design.md section 2).

Any process running as that user can call it, so it takes nothing from the caller but numbers:
- no path, file or tool argument: nvidia_oc, nvidia-smi and gpu-boot-apply are the root-owned copies at fixed
  paths, and it loads gpu-boot-apply from its fixed path, never from the repo;
- every value inside ctune's caps (gpu-boot-apply's, the same as apply.py's) and the power limit between the
  GPU's minimum and its default, as the skill's guardrail says;
- it writes no file: the caller keeps ctune's state file.
The shebang carries one flag word (-IB) because Linux passes everything after the interpreter as one argument.
It reuses gpu-boot-apply's steps: the power limit and the clock lock first, then the offsets, a readback, and stock
on any failure or mismatch.

  gpu-runtime-apply [--core-offset MHZ] [--mem-offset MHZ] [--max-clock MHZ] [--power-limit W]
  gpu-runtime-apply --reset
"""
import argparse
import importlib.machinery
import importlib.util
import json
import math
import os
import sys

LIBEXEC = "/usr/local/libexec/ctune"
BOOT_APPLY = os.path.join(LIBEXEC, "gpu-boot-apply")
NVIDIA_OC = os.path.join(LIBEXEC, "nvidia_oc")
NVIDIA_SMI = "/usr/bin/nvidia-smi"


def load_boot_apply(path=BOOT_APPLY):
    loader = importlib.machinery.SourceFileLoader("ctune_gpu_boot_apply", path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], allow_abbrev=False)
    ap.add_argument("--core-offset", type=int)
    ap.add_argument("--mem-offset", type=int)
    ap.add_argument("--max-clock", type=int)
    ap.add_argument("--power-limit", type=float)
    ap.add_argument("--reset", action="store_true")
    return ap.parse_args(argv)


def power_min(ba, smi):
    """The GPU's lowest power limit: apply.py checks against NVML's range, this path against nvidia-smi's."""
    rc, out = ba._run([smi, "--query-gpu=power.min_limit", "--format=csv,noheader,nounits", "-i", "0"])
    if rc:
        raise RuntimeError(f"nvidia-smi failed ({rc}): {out[-300:]}")
    return float(out.splitlines()[0])


def plan(a, ba, power_default, power_floor):
    """(settings to apply, errors that refuse them)."""
    if a.reset:
        return {"core_offset": 0, "mem_offset": 0, "max_clock": None, "power_limit": power_default}, []
    want = {"core_offset": a.core_offset, "mem_offset": a.mem_offset, "max_clock": a.max_clock,
            "power_limit": a.power_limit}
    errors = ba.out_of_caps(want)
    p = a.power_limit
    if p is not None and not (math.isfinite(p) and power_floor <= p <= power_default):
        errors.append(f"power_limit {p} must be between the minimum {power_floor:g} W and the default "
                      f"{power_default:g} W")
    if all(v is None for v in want.values()):
        errors.append("nothing to apply")
    return want, errors


def run(a, ba, oc=NVIDIA_OC, smi=NVIDIA_SMI):
    power_default = ba.identity(smi)["power_default"]
    want, errors = plan(a, ba, power_default, power_min(ba, smi))
    res = {"ok": False, "reset": a.reset, "applied": None, "errors": errors}
    if errors:
        return res
    if a.reset:
        res["errors"] = ba.to_stock(oc, smi, power_default)
    else:
        for args in ba.set_steps(want):
            rc, out = ba._run([oc, "set", "--index", "0", *args])
            if rc:
                res["errors"].append(f"nvidia_oc set {' '.join(args)} failed ({rc}): {out[-400:]}")
                break
        if not res["errors"]:
            res["errors"] += ba.mismatches(want, ba.readback(oc, smi))
        if res["errors"]:
            res["rolled_back"] = True
            res["errors"] += [f"rollback: {e}" for e in ba.to_stock(oc, smi, power_default)]
    res["readback"] = ba.readback(oc, smi)
    res["ok"] = not res["errors"]
    res["applied"] = want if res["ok"] else None
    return res


def main(argv=None, ba=None, oc=NVIDIA_OC, smi=NVIDIA_SMI):
    a = parse(argv)
    if os.geteuid() != 0 and ba is None:
        sys.exit("must run as root")
    try:
        res = run(a, ba or load_boot_apply(), oc, smi)
    except Exception as e:   # report, never a traceback the caller cannot parse
        res = {"ok": False, "reset": a.reset, "applied": None, "errors": [f"{type(e).__name__}: {e}"]}
    print(json.dumps(res))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
