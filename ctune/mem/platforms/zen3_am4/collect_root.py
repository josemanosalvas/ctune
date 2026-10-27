#!/usr/bin/python3 -IB
"""The root reads of a RAM snapshot, for the passwordless path: print them as JSON and keep nothing.

`ctune passwordless` installs this file with ram_snapshot.py, zen3_ddr4_decode.py, am4_bios_memcfg.py and
dump_fw_mem.py, owned by root, in /usr/local/libexec/ctune/mem/zen3_am4, and a polkit rule lets one user run this
file through pkexec with no password (docs/research/reuse-decisions.md). ram_snapshot.py, run as the user, calls it,
decodes the output and writes the snapshot.

Any process running as that user can call it, so it takes nothing from the caller but --skip-devmem. It only reads:
the SMN register list comes from zen3_ddr4_decode.py, and the /dev/mem addresses from the firmware's own tables
(ACPI, and EFI variables only root can write). It skips the bulk ACPI NVS dumps (several MB that can hold other
firmware data, readable once the kernel runs with iomem=relaxed): a snapshot that needs them runs ram_snapshot.py as
root, behind the password. The helpers run from this root-owned directory into a private temp directory under the
root-owned /run/ctune, removed before the output is printed. One collector runs at a time.
"""
import argparse
import base64
import fcntl
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
RUN_DIR = "/run/ctune"
LOCK = os.path.join(RUN_DIR, "mem-collect.lock")


def _sibling(name):
    """A module from this file's own directory, never from sys.path."""
    loader = importlib.machinery.SourceFileLoader("ctune_mem_" + name[:-3], os.path.join(HERE, name))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


def pack(tmp):
    """Every file below tmp, base64 by relative path."""
    files = {}
    for root, _, names in os.walk(tmp):
        for n in names:
            p = os.path.join(root, n)
            with open(p, "rb") as f:
                files[os.path.relpath(p, tmp)] = base64.b64encode(f.read()).decode()
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], allow_abbrev=False)
    ap.add_argument("--skip-devmem", action="store_true")
    a = ap.parse_args(argv)
    if os.geteuid() != 0:
        sys.exit("must run as root")
    os.umask(0o022)
    os.makedirs(RUN_DIR, mode=0o755, exist_ok=True)   # shared with the CPU and GPU state: readable by the user
    os.umask(0o077)
    os.environ["PATH"] = "/usr/sbin:/usr/bin"
    lock = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("another RAM snapshot collector is running")
    rs = _sibling("ram_snapshot.py")
    tmp = tempfile.mkdtemp(prefix="mem-collect-", dir=RUN_DIR)
    try:
        out = rs.collect_privileged(a.skip_devmem, tmp, nvs_dump=False)
        out["files"] = pack(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
