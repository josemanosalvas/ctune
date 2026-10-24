"""Single entry point used by both the CLI and the MCP server."""
import json
import os
import subprocess
import sys

from memtune import platforms
from memtune.core import errors as _errors
from memtune.core import journal as _journal
from memtune.core import sensors as _sensors

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("MEMTUNE_DATA", os.path.join(REPO, "data"))
SNAPS = os.path.join(DATA, "snapshots")


def _rd(p):
    try:
        return open(p).read().strip()
    except OSError:
        return None


def _platform_dir():
    p = platforms.detect()
    if not p["implemented"]:
        raise RuntimeError(f"platform {p['platform']} ({p['cpu']}) is not implemented yet")
    return p["dir"]


# ---------------------------------------------------------------- snapshots
def snapshots():
    if not os.path.isdir(SNAPS):
        return []
    return sorted(d for d in os.listdir(SNAPS) if os.path.isfile(os.path.join(SNAPS, d, "snapshot.json")))


def resolve(name):
    """'latest', 'previous', a directory name, or a path."""
    s = snapshots()
    if name in (None, "", "latest"):
        if not s:
            raise FileNotFoundError("no snapshots yet")
        return os.path.join(SNAPS, s[-1])
    if name == "previous":
        if len(s) < 2:
            raise FileNotFoundError("fewer than two snapshots")
        return os.path.join(SNAPS, s[-2])
    if os.path.isdir(name):
        return os.path.abspath(name)
    return os.path.join(SNAPS, name)


def snapshot(label="", skip_devmem=False):
    """Take a full snapshot (needs root: re-runs itself through pkexec, which shows a password prompt)."""
    script = os.path.join(_platform_dir(), "ram_snapshot.py")
    cmd = [sys.executable, "-I", script, "--out", SNAPS, "--label", label, "--owner", f"{os.getuid()}:{os.getgid()}"]
    if skip_devmem:
        cmd.append("--skip-devmem")
    if os.geteuid() != 0:
        cmd = ["pkexec"] + cmd
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"snapshot failed ({r.returncode}): {r.stderr.strip()[-800:]}")
    d = [l for l in r.stdout.splitlines() if l.startswith(SNAPS)]
    d = d[-1] if d else resolve("latest")
    _journal.add(DATA, "applied", snapshot=os.path.basename(d), label=label)
    return {"dir": d, "summary": summary(d)}


def summary(name="latest"):
    d = resolve(name)
    with open(os.path.join(d, "snapshot.md")) as f:
        return f.read()


def compare(old="previous", new="latest"):
    script = os.path.join(_platform_dir(), "ram_compare.py")
    r = subprocess.run([sys.executable, "-I", script, resolve(old), resolve(new)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-800:])
    return r.stdout


def bios_search(query, name="latest", limit=60, include_hidden=False):
    """Search the decoded BIOS settings of a snapshot by menu path / prompt text (case-insensitive)."""
    p = os.path.join(resolve(name), "decoded", "bios_settings.json")
    with open(p) as f:
        settings = json.load(f)["settings"]
    q = query.lower()
    hits = []
    for e in settings:
        if not include_hidden and e.get("visible_now") is False:
            continue
        locs = [l for l in e.get("locations") or [] if q in l.lower()]
        if not locs:
            continue
        v = e.get("value_text") if e.get("value_text") is not None else e.get("value")
        hits.append({"location": locs[0], "value": v, "state": e.get("auto_state"),
                     "default": e.get("default_text"), "storage": e.get("key")})
        if len(hits) >= limit:
            break
    return hits


# ---------------------------------------------------------------- live state
def sensors():
    return _sensors.read()


def errors(since="-24h"):
    return _errors.summary(since)


def journal_add(kind, **fields):
    return _journal.add(DATA, kind, **fields)


def journal_list(n=20, kind=None):
    return _journal.tail(DATA, n, kind)


def status():
    p = platforms.detect()
    snaps = snapshots()
    return {
        "platform": p,
        "board": _rd("/sys/class/dmi/id/board_name"),
        "bios": f"{_rd('/sys/class/dmi/id/bios_version')} ({_rd('/sys/class/dmi/id/bios_date')})",
        "kernel": os.uname().release,
        "ryzen_smu": _rd("/sys/kernel/ryzen_smu_drv/drv_version"),
        "smu_fw": _rd("/sys/kernel/ryzen_smu_drv/version"),
        "it87_driver": _rd("/sys/module/it87/version") or ("in-tree" if os.path.isdir("/sys/module/it87") else None),
        "snapshots": snaps[-5:],
        "snapshot_count": len(snaps),
        "data_dir": DATA,
        "journal_tail": journal_list(5),
        "sensors": sensors(),
        "errors_since_boot": _errors.summary()["total_corrected_errors_since_boot"],
    }
