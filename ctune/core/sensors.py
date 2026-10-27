"""hwmon readings relevant to memory tuning, with board-specific labels.

Board maps say which Super I/O input carries which rail or fan header. Unknown boards still get raw values.
"""
import glob
import os
import subprocess

# Gigabyte B550 AORUS PRO (rev 1.0): LibreHardwareMonitor SuperIOHardware.cs (IT8688E and IT8792E sections),
# frankcrawford/it87 'gigabyte-it87-amd.conf' (SIV 0x6008090B). Voltages without a divider only.
BOARDS = {
    "B550 AORUS PRO": {
        "volts": {("it8688", "in6"): "VDIMM", ("it8688", "in4"): "VSOC (VRM)", ("it8688", "in5"): "CPU VDDP (board rail)",
                  ("it8792", "in1"): "DDR VTT (A/B)"},
        "fans": {("it8688", "fan1"): "CPU_FAN", ("it8688", "fan2"): "SYS_FAN1", ("it8688", "fan3"): "SYS_FAN2",
                 ("it8688", "fan4"): "SYS_FAN3", ("it8688", "fan5"): "CPU_OPT", ("it8792", "fan1"): "SYS_FAN5_PUMP",
                 ("it8792", "fan2"): "SYS_FAN6_PUMP", ("it8792", "fan3"): "SYS_FAN4"},
    },
}


def _rd(p):
    try:
        return open(p).read().strip()
    except OSError:
        return None


def board_name():
    return _rd("/sys/class/dmi/id/board_name") or ""


def _chips():
    for h in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        name = _rd(f"{h}/name") or ""
        yield name, name.split("_")[0], h  # frankcrawford it87 appends the SIV: it8688_6008090b


def read():
    bmap = BOARDS.get(board_name(), {"volts": {}, "fans": {}})
    out = {"board": board_name(), "voltages": {}, "fans": {}, "temps": {}}
    for name, chip, h in _chips():
        for f in glob.glob(f"{h}/in*_input"):
            ch = os.path.basename(f).split("_")[0]
            label = bmap["volts"].get((chip, ch))
            if label:
                out["voltages"][label] = round(int(_rd(f)) / 1000, 3)
        for f in glob.glob(f"{h}/fan*_input"):
            ch = os.path.basename(f).split("_")[0]
            n = ch[3:]
            label = bmap["fans"].get((chip, ch), f"{name}/{ch}")
            out["fans"][label] = {"rpm": int(_rd(f) or 0), "pwm": _rd(f"{h}/pwm{n}"), "pwm_enable": _rd(f"{h}/pwm{n}_enable")}
        if chip == "k10temp":
            t = _rd(f"{h}/temp1_input")
            if t:
                out["temps"]["CPU Tctl"] = int(t) / 1000
    vtt = out["voltages"].get("DDR VTT (A/B)")
    if vtt and "VDIMM" not in out["voltages"]:
        out["voltages"]["VDIMM (est. 2.02 x VTT)"] = round(vtt * 2.02, 3)
    try:
        g = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10).stdout.strip()
        if g:
            out["temps"]["GPU"] = float(g.splitlines()[0])
    except Exception:
        pass
    return out
