"""Append-only tuning journal (JSON lines): one record per plan, applied check, test result or note.

The journal records a tuning campaign: what was tried, what the BIOS applied, and what the tests and
error counters said. The next step should follow from it alone.
"""
import json
import os
import time

KINDS = ("plan", "applied", "result", "note")


def path(data_dir):
    return os.path.join(data_dir, "journal.jsonl")


def add(data_dir, kind, **fields):
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": kind, **fields}
    os.makedirs(data_dir, exist_ok=True)
    with open(path(data_dir), "a") as f:
        f.write(json.dumps(rec, sort_keys=False) + "\n")
    return rec


def tail(data_dir, n=20, kind=None):
    try:
        with open(path(data_dir)) as f:
            recs = [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []
    if kind:
        recs = [r for r in recs if r.get("kind") == kind]
    return recs[-n:]
