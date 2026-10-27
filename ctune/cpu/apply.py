#!/usr/bin/python3 -IB
"""Root helper: read or set the per-core Curve Optimizer margin through the SMU. Run through pkexec by ctune.cpu.api.

  apply.py --state FILE [--owner UID:GID] --probe [--expect M0,M1,...]   read every core (MP1 0x48), compare
  apply.py --state FILE [--owner UID:GID] --core N --margin M            set one core (MP1 0x35), read it back
  apply.py --state FILE [--owner UID:GID] --reset                        restore the cores ctune changed this boot

Writes are refused unless this boot's probe read back the expected (BIOS) margin on every core, the core still holds
the value ctune last left there, and the change stays within -30..30 and 5 per step. Margins last until reboot.
The command IDs and encoding are cited in ctune/cpu/smu.py.

`ctune passwordless` installs this file and smu.py, owned by root, in /usr/local/libexec/ctune/cpu, and a polkit rule
lets one user run it through pkexec with no password (docs/research/reuse-decisions.md). Any process running as
that user can then call it, so the installed copy keeps its state in the root-owned /run/ctune (cleared at boot) and
refuses --state and --owner: a state file the caller could write would let it skip the probe and value checks.
Calls take an exclusive lock, so two at once cannot lose a margin from the state. The guardrails bind each call: a
caller that loops can still walk a core across the whole -30..30 range in steps of 5, and that range is the cap.
"""
import argparse
import fcntl
import importlib.machinery
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
LIBEXEC_CPU = "/usr/local/libexec/ctune/cpu"
RUN_STATE = "/run/ctune/cpu-co.json"
RUN_LOCK = "/run/ctune/cpu-co.lock"


def _sibling(name):
    """smu.py from this file's own directory: the repo, or the root-owned copy. Never from sys.path."""
    loader = importlib.machinery.SourceFileLoader("ctune_cpu_" + name[:-3], os.path.join(HERE, name))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


smu = _sibling("smu.py")


def installed():
    return HERE == os.path.realpath(LIBEXEC_CPU)


def _boot_id():
    return open("/proc/sys/kernel/random/boot_id").read().strip()


def _load(path):
    try:
        s = json.load(open(path))
        if s.get("boot_id") == _boot_id():
            return s
    except (OSError, ValueError):
        pass
    return {}


def _save(path, state, owner):
    state["boot_id"] = _boot_id()
    state["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    if owner:
        uid, gid = (int(x) for x in owner.split(":"))
        os.chown(path, uid, gid)


def _out(ok, errors=None, **kw):
    print(json.dumps({"ok": ok, "errors": errors or [], **kw}))
    return 0 if ok else 2


def probe(mb, state, ncores, expect):
    values = [mb.read_margin(c) for c in range(ncores)]
    applied = {int(k): v for k, v in state.get("applied", {}).items()}
    boot = state.setdefault("boot_values", {})
    for c, v in enumerate(values):
        if c not in applied:
            boot.setdefault(str(c), v)
    # cores ctune changed this boot are expected at the value ctune left, the rest at the BIOS value
    expected = [applied.get(c, expect[c]) for c in range(ncores)] if expect else None
    state["probe"] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "values": values, "expected": expected,
                      "matched": expected is not None and values == expected}
    return state["probe"]


def set_core(mb, state, core, margin):
    if not state.get("probe", {}).get("matched"):
        return ["no matching probe in this boot: run `ctune cpu probe` first, so the 0x48 getter is shown to read the "
                "BIOS values before ctune writes"], None
    applied = state.setdefault("applied", {})
    boot = state.setdefault("boot_values", {})
    cur = mb.read_margin(core)
    known = applied.get(str(core), boot.get(str(core)))
    if known is not None and known != cur:
        return [f"core {core} reads {cur}, but ctune expects {known}: something else changed it; reboot or reset"], None
    errs = smu.validate_change(core, cur, margin)
    if errs:
        return errs, None
    boot.setdefault(str(core), cur)
    arg = mb.write_margin(core, margin)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "core": core, "before": cur, "requested": margin,
             "mailbox": {"op": hex(smu.MP1_SET_CORE), "arg0": f"{arg:#010x}"}}
    try:
        rb = mb.read_margin(core)
    except smu.SmuError as e:
        rb = None
        entry["readback_error"] = str(e)
    entry["readback"] = rb
    applied[str(core)] = rb if rb is not None else margin
    state.setdefault("history", []).append(entry)
    errs = [] if rb == margin else [f"core {core}: requested {margin}, read back {rb}"]
    return errs, entry


def reset(mb, state):
    applied = state.get("applied", {})
    boot = state.get("boot_values", {})
    done, errs = [], []
    for k in sorted(applied, key=int):
        core, target = int(k), boot.get(k)
        if target is None:
            errs.append(f"core {core}: no boot value recorded")
            continue
        arg = mb.write_margin(core, target)
        rb = mb.read_margin(core)
        done.append({"core": core, "from": applied[k], "to": target, "readback": rb, "arg0": f"{arg:#010x}"})
        if rb == target:
            applied.pop(k)
        else:
            applied[k] = rb
            errs.append(f"core {core}: reset to {target}, read back {rb}")
    state.setdefault("history", []).append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "reset": done})
    return errs, done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state")
    ap.add_argument("--owner", default="")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--expect", help="comma-separated BIOS margins, core 0 first")
    ap.add_argument("--core", type=int)
    ap.add_argument("--margin", type=int)
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("must run as root")
    os.umask(0o022)   # pkexec keeps the caller's umask: the state must never be writable by the caller
    if installed():
        if a.state or a.owner:
            return _out(False, [f"the installed helper keeps its state in {RUN_STATE}: no --state or --owner"])
        a.state = RUN_STATE
        os.makedirs(os.path.dirname(RUN_LOCK), mode=0o755, exist_ok=True)
        lock = os.open(RUN_LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)   # held until the process exits
    elif not a.state:
        return _out(False, ["--state is required"])
    modes = sum([a.probe, a.reset, a.core is not None or a.margin is not None])
    if modes != 1 or (a.core is None) != (a.margin is None):
        return _out(False, ["give exactly one of --probe, --reset, or --core with --margin"])
    info, problems = smu.platform()
    if problems:
        return _out(False, problems, platform=info)
    ncores = len(info["cores"])
    mb = smu.Mailbox(lock=smu.LOCK)
    state = _load(a.state)
    state["smu_fw"] = info["smu_fw"]
    try:
        if a.probe:
            expect = [int(x) for x in a.expect.split(",")] if a.expect else None
            if expect is not None and len(expect) != ncores:
                return _out(False, [f"--expect has {len(expect)} values for {ncores} cores"])
            p = probe(mb, state, ncores, expect)
            _save(a.state, state, a.owner)
            errs = [] if p["matched"] or expect is None else [f"probe read {p['values']}, expected {p['expected']}"]
            return _out(not errs, errs, probe=p, platform=info)
        if a.reset:
            errs, done = reset(mb, state)
            _save(a.state, state, a.owner)
            return _out(not errs, errs, reset=done, applied=state.get("applied", {}))
        errs, entry = set_core(mb, state, a.core, a.margin)
        if entry is not None:
            _save(a.state, state, a.owner)
        return _out(not errs, errs, step=entry, applied=state.get("applied", {}),
                    boot_values=state.get("boot_values", {}))
    except (smu.SmuError, ValueError, OSError) as e:
        if state.get("history") or state.get("probe"):
            _save(a.state, state, a.owner)
        return _out(False, [f"{type(e).__name__}: {e}"])


if __name__ == "__main__":
    sys.exit(main())
