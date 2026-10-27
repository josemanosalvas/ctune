"""Imported by every test module: offline tests may not start podman, a privileged helper or a stress tool.

A unit test that reached gpu.test for real could start podman beside a campaign's running gpu-burn container and
replace it. Any such command raises before it starts; a test that needs one mocks it.
"""
import os
import subprocess

# not ctune.core.paths: importing it here would fix DATA before a test module sets CTUNE_DATA
TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")

BLOCKED = {"podman", "pkexec", "sudo", "systemd-run", "nvidia-smi", "nvidia_oc", "curl"}
# systemctl may read (show, list-units), never act on the real user manager
SYSTEMCTL_ACTS = {"start", "stop", "restart", "kill", "enable", "disable", "reset-failed", "daemon-reload"}


class _Guarded(subprocess.Popen):
    def __init__(self, args, *a, **kw):
        argv = os.fsdecode(args).split() if isinstance(args, (str, bytes, os.PathLike)) else list(args)
        argv = list(map(os.fsdecode, argv)) + ([os.fsdecode(kw["executable"])] if kw.get("executable") else [])
        names = [os.path.basename(arg) for arg in argv]
        for arg, name in zip(argv, names):
            if name in BLOCKED or os.path.realpath(arg).startswith(os.path.realpath(TOOLS) + os.sep) or \
                    (name == "systemctl" and SYSTEMCTL_ACTS & set(argv)):
                raise AssertionError(f"an offline test tried to run {arg!r}: mock it ({' '.join(argv)[:200]})")
        super().__init__(args, *a, **kw)


subprocess.Popen = _Guarded
