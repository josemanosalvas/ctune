"""Platform detection. Each platform package provides the privileged snapshot collector and decoders.

Supported: zen3_am4 (Vermeer/Cezanne-class desktop, DDR4). Planned: zen4_am5 / zen5_am5 (DDR5), using the
same layout: UMC register map + pm_table offsets + AMI BIOS decode (the AMI part is shared).
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# (cpu family, model range) -> platform package
TABLE = [
    (0x19, range(0x20, 0x30), "zen3_am4"),   # Vermeer (5000 desktop incl. 5800X3D)
    (0x19, range(0x50, 0x60), "zen3_am4"),   # Cezanne (5000G) - same DDR4 UMC map
    (0x19, range(0x60, 0x70), "zen4_am5"),   # Raphael (7000) - not implemented yet
    (0x1A, range(0x40, 0x50), "zen5_am5"),   # Granite Ridge (9000) - not implemented yet
]
IMPLEMENTED = {"zen3_am4"}


def cpu():
    txt = open("/proc/cpuinfo").read()
    fam = int(re.search(r"cpu family\s*:\s*(\d+)", txt).group(1))
    model = int(re.search(r"model\s*:\s*(\d+)", txt).group(1))
    name = re.search(r"model name\s*:\s*(.*)", txt).group(1)
    return fam, model, name


def detect():
    fam, model, name = cpu()
    for f, models, plat in TABLE:
        if fam == f and model in models:
            return {"platform": plat, "implemented": plat in IMPLEMENTED, "cpu": name,
                    "family": hex(fam), "model": hex(model), "dir": os.path.join(HERE, plat)}
    return {"platform": None, "implemented": False, "cpu": name, "family": hex(fam), "model": hex(model), "dir": None}
