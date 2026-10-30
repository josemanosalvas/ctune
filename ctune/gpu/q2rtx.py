"""Quake II RTX: the graphics and ray-tracing load. gpu-burn never touches the RT cores, ROPs or TMUs; Q2RTX
path-traces every frame (primary rays included) through VK_KHR_ray_query, with a compute denoiser and texture
sampling. It checks nothing it renders, so a pass only means it did not crash, lose the device or hang. Xid errors
and the kernel log remain the main signal. Sources: docs/research/gpu-graphics-loads.md.

- Q2RTX 1.8.1 (final release; GPL v2 engine, the free shareware levels are in the tarball) in tools/q2rtx/.
- It was built in Steam's sniper SDK and needs libssl 1.1, which Fedora 44 lacks, so it runs in the standalone
  Steam Linux Runtime sniper (tools/steamrt-sniper/), which brings those libraries and imports the host's driver.
- One process per pass (+timedemo 1): each pass has its own exit code and FPS line in console.log, which the game
  rewrites at every start under $XDG_DATA_HOME/quake2rtx/baseq2/logs/.
- A fatal error opens a modal dialog first, and the blocked game ignores SIGTERM: a pass that runs past
  pass_timeout_s is killed. "Device lost!" exits 1 without a dialog.
- It renders in gamescope's headless backend (Bazzite ships gamescope), off-screen at a fixed size: the sign-off
  reaches the gpu phase hours in, when GNOME has long blanked and locked the screen, and nothing then has to be
  drawn on the desktop. MODE "session" runs it fullscreen on the desktop instead.
"""
import os
import re
import shutil
import statistics
import subprocess
import time

from ctune.core import stress
from ctune.core.paths import TOOLS
from ctune.gpu import passes

VERSION = "1.8.1"
URL = f"https://github.com/NVIDIA/Q2RTX/releases/download/v{VERSION}/q2rtx-{VERSION}-linux.tar.gz"
SHA256 = "31dfd0a924366272161d3e1ff728e8f1ae8304142e3ce954d0fa73d9ff5b7562"   # GitHub digest, as in AUR q2rtx
RUNTIME_VERSION = "3.0.20260805.254768"
RUNTIME_URL = f"https://repo.steampowered.com/steamrt3/images/{RUNTIME_VERSION}/SteamLinuxRuntime_sniper.tar.xz"
RUNTIME_SHA256 = "e264f0639ab775338311036f207b35cebe99bc417b016b53931ebca8b30b3d94"   # that snapshot's SHA256SUMS
DIR = os.path.join(TOOLS, "q2rtx", "q2rtx")
BIN = os.path.join(DIR, "q2rtx")
RUNTIME = os.path.join(TOOLS, "steamrt-sniper", "SteamLinuxRuntime_sniper", "run")
MODE = "headless"      # or "session": fullscreen on the user's desktop
SIZE = (2560, 1440)    # on the tested RTX 3080 Ti at the 1905 MHz lock and 400 W, 3440x1440 ran at the power cap
                       # (at the lock 52 % of the time), 2560x1440 at the lock 90 % of the time at 381 W, and
                       # 1920x1080 was bursty (busy about 30 %)
PASS_TIMEOUT_S = 120   # one q2demo1 pass took 10 to 15 s on that card, including the start of gamescope and the
                       # runtime
TERM_GRACE_S = 10      # then SIGKILL: a game blocked in its error dialog ignores SIGTERM
MIN_SECONDS = 60

FPS = re.compile(r"(\d+) frames, ([\d.]+) seconds: ([\d.]+) fps")
ENVIRONMENT = re.compile(r"No ray tracing capable GPU found|Couldn't initialize refresh|Error during initialization")
ERROR = re.compile(r"Device lost!|FATAL: |Received signal|Segmentation fault|SIG(SEGV|ILL|FPE|BUS|ABRT|TRAP)")


def cmd(mode=None):
    """One timedemo pass of the shareware demo, fullscreen (in gamescope: its whole headless output), path tracing
    with the heaviest global illumination, no sound, quit at the end."""
    w, h = map(str, SIZE)
    wrap = ["gamescope", "--backend", "headless", "-w", w, "-h", h, "-W", w, "-H", h, "--"] \
        if (mode or MODE) == "headless" else []
    return wrap + [RUNTIME, "--", BIN, "+set", "s_enable", "0", "+set", "vid_modelist", "desktop", "+set",
                   "vid_fullscreen", "1", "+set", "ray_tracing_api", "query", "+set", "pt_num_bounce_rays", "2",
                   "+timedemo", "1", "+demo", "q2demo1", "+set", "nextserver", "quit"]


def unavailable(mode=None):
    """None if a pass can start, else why not (the test cannot run: inconclusive)."""
    missing = [p for p in (BIN, RUNTIME) if not os.path.exists(p)]
    if missing:
        return f"{', '.join(missing)} missing: run `ctune gpu q2rtx-setup` once"
    if (mode or MODE) == "headless":
        return None if shutil.which("gamescope") else "gamescope is missing (Bazzite ships it): set q2rtx.MODE session"
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return "no display: Quake II RTX renders in the desktop session (DISPLAY or WAYLAND_DISPLAY)"
    return None


def parse_log(text):
    lines = [l.strip() for l in text.splitlines()]
    fps = next((m for l in lines if (m := FPS.search(l))), None)
    return {"fps": round(float(fps.group(3)), 2) if fps else None, "frames": int(fps.group(1)) if fps else None,
            "api": next((a for a in ("VK_KHR_ray_query", "VK_KHR_ray_tracing_pipeline")
                         if any(f"Using {a}" in l for l in lines)), None),
            "errors": [l for l in lines if ERROR.search(l)][:10],
            "environment": [l for l in lines if ENVIRONMENT.search(l)][:3]}


def screen_off():
    """True while GNOME's screen shield is up (blanked or locked), where the game may stop being drawn."""
    try:
        r = subprocess.run(["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver", "--object-path",
                            "/org/gnome/ScreenSaver", "--method", "org.gnome.ScreenSaver.GetActive"],
                           capture_output=True, text=True, timeout=10, env=stress.user_env())
    except (OSError, subprocess.SubprocessError):
        return False
    return "true" in r.stdout


def _inhibit_idle(why):
    """Keep GNOME from blanking the screen during the run (it cannot lift a lock that is already up)."""
    if not shutil.which("gnome-session-inhibit") or "GNOME" not in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        return None
    return subprocess.Popen(["gnome-session-inhibit", "--inhibit", "idle", "--app-id", "ctune", "--reason", why,
                             "sleep", "infinity"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True, env=stress.user_env())


def _pass(argv, home, timeout_s, guard):
    """One pass: (rc, log, seconds, why it was stopped or None)."""
    log = os.path.join(home, "quake2rtx", "baseq2", "logs", "console.log")
    try:
        os.remove(log)
    except OSError:
        pass
    env = dict(stress.user_env(), XDG_DATA_HOME=home)   # its config and logs stay out of the user's own Q2RTX data
    # pass.out holds gamescope's and the runtime's own messages
    rc, took, why = passes.run(argv, env, DIR, os.path.join(home, "pass.out"), timeout_s, guard, TERM_GRACE_S)
    return rc, passes.read(log), took, why


def run(seconds, home, guard=None, make_argv=None, pass_timeout_s=PASS_TIMEOUT_S, min_pass_s=30):
    """Timedemo passes until `seconds` are used up (at least one). guard() returning a reason stops the run (the
    temperature guardrail). Red on a lost device, a fatal error, a crash or a hang; inconclusive when the game could
    not start, or hung while the screen was off."""
    os.makedirs(home, exist_ok=True)
    make_argv = make_argv or cmd
    t_end = time.monotonic() + seconds
    fps, errors, api, unavail, durations = [], [], None, False, []
    holder = _inhibit_idle("ctune graphics load running") if MODE == "session" else None
    try:
        while not errors:
            n = len(durations) + 1
            rc, text, took, why = _pass(make_argv(), home, pass_timeout_s, guard)
            durations.append(took)
            r = parse_log(text)
            api = api or r["api"]
            if why == "timeout":
                if MODE == "session" and screen_off():
                    unavail = True
                    errors.append(f"pass {n} did not finish in {pass_timeout_s} s while the screen was blanked or "
                                  "locked: the game is not drawn then; unlock the screen and run it again")
                else:
                    errors.append(f"pass {n} did not finish in {pass_timeout_s} s: the game hung "
                                  f"({'; '.join(r['errors']) or 'no error in its log'})")
            elif why:
                errors.append(why)
            elif r["errors"] or rc != 0 or r["fps"] is None:
                if n == 1 and not r["errors"] and (r["environment"] or not r["api"]):
                    unavail = True   # never rendered a frame: the setup, not the GPU
                tail = (text.strip() or passes.read(os.path.join(home, "pass.out")).strip())[-300:] or "no output"
                errors.append(f"pass {n}: exit {rc}: " + "; ".join(r["errors"] or r["environment"] or
                                                                      [f"no FPS line ({tail})"]))
            else:
                fps.append(r["fps"])
            if time.monotonic() + max(min_pass_s, statistics.median(durations)) > t_end:
                break
    finally:
        if holder:
            holder.terminate()
            holder.wait(10)
    return {"ok": not errors and bool(fps), "unavailable": unavail, "passes": len(durations), "errors": errors,
            "fps": fps, "fps_median": round(statistics.median(fps), 2) if fps else None, "api": api,
            "pass_s_median": round(statistics.median(durations), 1) if durations else None, "q2rtx": VERSION}


def setup():
    """Download Q2RTX and the Steam Linux Runtime sniper into tools/, check both SHA256s, unpack, write VERSION."""
    out = {}
    for name, url, sha, sub in (("q2rtx", URL, SHA256, "q2rtx"), ("runtime", RUNTIME_URL, RUNTIME_SHA256,
                                                                  "steamrt-sniper")):
        d = os.path.join(TOOLS, sub)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, os.path.basename(url))
        if not os.path.exists(path):
            subprocess.run(["curl", "-fL", "--retry", "3", "-o", path + ".part", url], check=True)
            os.replace(path + ".part", path)
        got = subprocess.run(["sha256sum", path], capture_output=True, text=True, check=True).stdout.split()[0]
        if got != sha:
            raise RuntimeError(f"{path}: sha256 {got}, expected {sha}; delete it and run setup again")
        subprocess.run(["tar", "xf", path, "-C", d], check=True)
        out[name] = {"file": path, "sha256": sha}
    with open(os.path.join(TOOLS, "q2rtx", "VERSION"), "w") as f:
        f.write(f"Quake II RTX {VERSION} ({URL}, sha256 {SHA256}), GPL v2 engine with the id Software shareware data; "
                f"runs in Steam Linux Runtime sniper {RUNTIME_VERSION} ({RUNTIME_URL}, sha256 {RUNTIME_SHA256})\n")
    return out
