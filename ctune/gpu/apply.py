#!/usr/bin/env python3
"""Root helper: apply or reset NVIDIA GPU tuning within guardrails. Run through pkexec by ctune.gpu.api.

  apply.py --state FILE [--owner UID:GID] [--core-offset MHZ] [--mem-offset MHZ] [--max-clock MHZ] [--power-limit W]
  apply.py --state FILE --reset

Values outside the NVML-reported range or the ctune caps are rejected before anything is written.
Settings last until reboot or driver reload.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
from ctune.gpu import nvml  # noqa: E402

CAP_CORE = (-300, 300)      # MHz, NVML units
CAP_MEM = (-1000, 1500)     # MHz, NVML units (nvidia-settings shows double)
CAP_CLOCK = (300, 2100)     # MHz, upper bound for a locked max core clock


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--owner", default="")
    ap.add_argument("--core-offset", type=int)
    ap.add_argument("--mem-offset", type=int)
    ap.add_argument("--max-clock", type=int, help="lock the core clock to 0..MHZ (the undervolt knob)")
    ap.add_argument("--power-limit", type=float)
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("must run as root")
    h = nvml.handle(0)
    errors = []

    def check(label, v, nvml_range, cap):
        lo, hi = max(nvml_range[0], cap[0]), min(nvml_range[1], cap[1])
        if not lo <= v <= hi:
            errors.append(f"{label} {v} outside allowed range {lo}..{hi}")

    if a.reset:
        plan = {"core_offset": 0, "mem_offset": 0, "max_clock": None,
                "power_limit": nvml.default_power_limit_w(h)}
    else:
        plan = {"core_offset": a.core_offset, "mem_offset": a.mem_offset, "max_clock": a.max_clock,
                "power_limit": a.power_limit}
        if a.core_offset is not None:
            check("core offset", a.core_offset, nvml.core_offset_range(h), CAP_CORE)
        if a.mem_offset is not None:
            check("mem offset", a.mem_offset, nvml.mem_offset_range(h), CAP_MEM)
        if a.max_clock is not None:
            check("max clock", a.max_clock, (0, nvml.max_clock(h)), CAP_CLOCK)
        if a.power_limit is not None:
            check("power limit", a.power_limit, nvml.power_constraints_w(h), (0, 10_000))
    if errors:
        print(json.dumps({"ok": False, "errors": errors}))
        return 2

    if plan["power_limit"] is not None:
        nvml.set_power_limit_w(h, plan["power_limit"])
    if plan["core_offset"] is not None:
        nvml.set_core_offset(h, plan["core_offset"])
    if plan["mem_offset"] is not None:
        nvml.set_mem_offset(h, plan["mem_offset"])
    if a.reset:
        nvml.reset_core_clocks(h)
    elif plan["max_clock"] is not None:
        nvml.lock_core_clocks(h, 0, plan["max_clock"])

    # merge into the persisted state (what is currently applied since boot)
    try:
        state = json.load(open(a.state))
        if state.get("boot_id") != open("/proc/sys/kernel/random/boot_id").read().strip():
            state = {}
    except (OSError, ValueError):
        state = {}
    if a.reset:
        state = dict(plan)
    else:
        state.update({k: v for k, v in plan.items() if v is not None})
    state["boot_id"] = open("/proc/sys/kernel/random/boot_id").read().strip()
    state["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    os.makedirs(os.path.dirname(a.state), exist_ok=True)
    json.dump(state, open(a.state, "w"), indent=1)
    if a.owner:
        uid, gid = (int(x) for x in a.owner.split(":"))
        os.chown(a.state, uid, gid)
    readback = {"core_offset": nvml.core_offset(h), "mem_offset": nvml.mem_offset(h),
                "power_limit": nvml.power_limit_w(h)}
    print(json.dumps({"ok": True, "applied": state, "readback": readback}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
