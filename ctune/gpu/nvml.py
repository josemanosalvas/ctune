"""Minimal NVML binding (ctypes, no dependencies) for NVIDIA tuning: clocks, offsets, power, throttle reasons.

Setters need root. Settings made through NVML last until reboot or driver reload, so a crash undoes them.
Offsets use NVML units: the memory offset is half of what nvidia-settings / GreenWithEnvy display.
"""
import ctypes

GRAPHICS, SM, MEM, VIDEO = 0, 1, 2, 3
EVENT_REASONS = {
    0x1: "idle", 0x2: "app clocks", 0x4: "power cap", 0x8: "hw slowdown", 0x10: "sync boost",
    0x20: "sw thermal", 0x40: "hw thermal", 0x80: "power brake", 0x100: "display clock",
}

_lib = None


class NvmlError(RuntimeError):
    pass


def lib():
    global _lib
    if _lib is None:
        _lib = ctypes.CDLL("libnvidia-ml.so.1")
        _lib.nvmlErrorString.restype = ctypes.c_char_p
        _chk(_lib.nvmlInit_v2(), "nvmlInit_v2")
    return _lib


def _chk(rc, fn):
    if rc:
        msg = _lib.nvmlErrorString(rc).decode() if _lib else str(rc)
        raise NvmlError(f"{fn}: {msg} ({rc})")


def _call(fn, *args):
    _chk(getattr(lib(), fn)(*args), fn)


def handle(index=0):
    h = ctypes.c_void_p()
    _call("nvmlDeviceGetHandleByIndex_v2", ctypes.c_uint(index), ctypes.byref(h))
    return h


def name(h):
    buf = ctypes.create_string_buffer(96)
    _call("nvmlDeviceGetName", h, buf, ctypes.c_uint(96))
    return buf.value.decode()


def _uint(fn, h, *pre):
    v = ctypes.c_uint()
    _call(fn, h, *pre, ctypes.byref(v))
    return v.value


def _int(fn, h):
    v = ctypes.c_int()
    _call(fn, h, ctypes.byref(v))
    return v.value


def _range(fn, h):
    lo, hi = ctypes.c_int(), ctypes.c_int()
    _call(fn, h, ctypes.byref(lo), ctypes.byref(hi))
    return lo.value, hi.value


def clock(h, kind=GRAPHICS):
    return _uint("nvmlDeviceGetClockInfo", h, ctypes.c_int(kind))


def max_clock(h, kind=GRAPHICS):
    return _uint("nvmlDeviceGetMaxClockInfo", h, ctypes.c_int(kind))


def power_w(h):
    return _uint("nvmlDeviceGetPowerUsage", h) / 1000


def power_limit_w(h):
    return _uint("nvmlDeviceGetEnforcedPowerLimit", h) / 1000


def default_power_limit_w(h):
    return _uint("nvmlDeviceGetPowerManagementDefaultLimit", h) / 1000


def power_constraints_w(h):
    lo, hi = ctypes.c_uint(), ctypes.c_uint()
    _call("nvmlDeviceGetPowerManagementLimitConstraints", h, ctypes.byref(lo), ctypes.byref(hi))
    return lo.value / 1000, hi.value / 1000


def temperature(h):
    return _uint("nvmlDeviceGetTemperature", h, ctypes.c_int(0))


def fan_percent(h):
    try:
        return _uint("nvmlDeviceGetFanSpeed", h)
    except NvmlError:
        return None


def pstate(h):
    return _uint("nvmlDeviceGetPerformanceState", h)


def event_reasons(h):
    v = ctypes.c_ulonglong()
    _call("nvmlDeviceGetCurrentClocksEventReasons", h, ctypes.byref(v))
    return [n for bit, n in EVENT_REASONS.items() if v.value & bit]


def energy_j(h):
    v = ctypes.c_ulonglong()
    _call("nvmlDeviceGetTotalEnergyConsumption", h, ctypes.byref(v))
    return v.value / 1000


def core_offset(h):
    return _int("nvmlDeviceGetGpcClkVfOffset", h)


def mem_offset(h):
    return _int("nvmlDeviceGetMemClkVfOffset", h)


def core_offset_range(h):
    return _range("nvmlDeviceGetGpcClkMinMaxVfOffset", h)


def mem_offset_range(h):
    return _range("nvmlDeviceGetMemClkMinMaxVfOffset", h)


# ---- setters (root)
def set_core_offset(h, mhz):
    _call("nvmlDeviceSetGpcClkVfOffset", h, ctypes.c_int(mhz))


def set_mem_offset(h, mhz):
    _call("nvmlDeviceSetMemClkVfOffset", h, ctypes.c_int(mhz))


def set_power_limit_w(h, watts):
    _call("nvmlDeviceSetPowerManagementLimit", h, ctypes.c_uint(int(round(watts * 1000))))


def lock_core_clocks(h, lo_mhz, hi_mhz):
    _call("nvmlDeviceSetGpuLockedClocks", h, ctypes.c_uint(lo_mhz), ctypes.c_uint(hi_mhz))


def reset_core_clocks(h):
    _call("nvmlDeviceResetGpuLockedClocks", h)
