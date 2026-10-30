"""Unigine Superposition and Heaven: the raster loads. Quake II RTX traces even its primary rays, so it barely touches
the ROPs; these two rasterise every frame through OpenGL 4.5 (Superposition: heavy shading, screen-space GI,
post-processing; Heaven: extreme tessellation and 8x MSAA). Neither checks what it renders, so a pass only means it
did not crash or hang; Xid errors and the kernel log remain the main signal. Sources: docs/research/gpu-graphics-loads.md.

- The free Basic editions, unpacked in the gitignored tools/ and never redistributed (personal use, EULA section 2.1).
  Basic sells no command line, so ctune starts the engine binary with the arguments Phoronix Test Suite uses.
- Both downloads are Makeself archives. setup() checks the whole file's SHA256 and the payload's MD5, then unpacks
  the payload with tail and tar: the vendor script never runs (Superposition's only creates desktop shortcuts).
- One process per pass: Heaven with the PHORONIX define runs the benchmark once, prints Time, Frames and FPS and
  quits; Superposition in mode 2 runs it once, writes its score file under $HOME/.Superposition/automation/ and quits.
- HOME points at ctune's data directory, so the engine caches, logs and score files stay out of the user's home.
  Heaven rewrites its engine config at exit and resolves every path from bin/, so each pass gets a fresh copy of
  the shipped config in that directory, named by a path relative to bin/.
- They render in gamescope's headless backend, as Quake II RTX does, off-screen at a fixed size.
"""
import glob
import hashlib
import os
import re
import shutil
import statistics
import subprocess
import time
from dataclasses import dataclass

from ctune.core import stress
from ctune.core.paths import TOOLS
from ctune.gpu import passes

TERM_GRACE_S = 10   # then SIGKILL
SIZE = (2560, 1440)   # on the tested RTX 3080 Ti at the 1905 MHz lock and 330 W, both ran at the power cap here
                      # (Superposition 1785 to 1800 MHz, Heaven about 1840 MHz), 78 to 79 °C on the Silent BIOS


@dataclass(frozen=True)
class Bench:
    key: str
    name: str
    version: str
    url: str
    file_bytes: int
    sha256: str            # Unigine publishes none: the FreeBSD port's distinfo
    header_lines: int      # Makeself header; the payload starts on the next line
    payload_bytes: int
    payload_md5: str       # the archive's own MD5 field
    top: str               # directory the payload unpacks to
    binary: str            # engine binary, relative to bin/
    libs: tuple            # LD_LIBRARY_PATH entries, relative to bin/
    pass_timeout_s: int    # one pass, the engine start and the world load included
    min_seconds: int       # shortest part that holds one full pass

    @property
    def dir(self):
        return os.path.join(TOOLS, self.key, self.top)

    @property
    def bin_dir(self):
        return os.path.join(self.dir, "bin")


SUPERPOSITION = Bench(
    key="superposition", name="Unigine Superposition", version="1.1",
    url="https://assets.unigine.com/d/Unigine_Superposition-1.1.run", file_bytes=1642169125,
    sha256="7494e1c73bf59ef2161513d5d5cb9d9bffbd8479929d4976ac53b6955de580f8",
    header_lines=588, payload_bytes=1642154173, payload_md5="895cd9ca9ac9c06a06a41de96b928635",
    top="Unigine_Superposition-1.1", binary="superposition", libs=(".",),
    pass_timeout_s=360,    # 197 s at 2560x1440 Extreme on that card
    min_seconds=150)

HEAVEN = Bench(
    key="heaven", name="Unigine Heaven", version="4.0",
    url="https://assets.unigine.com/d/Unigine_Heaven-4.0.run", file_bytes=286332610,
    sha256="52db2e5dedd5621d7c2bfa936b482c0a7cc69b30616278ef199513d494d8e397",
    header_lines=589, payload_bytes=286317559, payload_md5="5e81eb18237acef87a1f69d3ba1e2b04",
    top="Unigine_Heaven-4.0", binary="heaven_x64", libs=("./x64", "."),
    pass_timeout_s=420,    # 269 s at 2560x1440 Extreme on that card (the benchmark itself is 260 s)
    min_seconds=240)

BENCHES = (SUPERPOSITION, HEAVEN)
HEAVEN_CFG = "heaven_4.0.cfg"

RESULT = {"fps": re.compile(r"^FPS:\s*([\d.]+)", re.M), "score": re.compile(r"^Score:\s*(\d+)", re.M),
          "min_fps": re.compile(r"^Min FPS:\s*([\d.]+)", re.M), "max_fps": re.compile(r"^Max FPS:\s*([\d.]+)", re.M)}
ERROR = re.compile(r"Segmentation fault|Unigine fatal error|Fatal error|GL_OUT_OF_MEMORY|GL_CONTEXT_LOST|"
                   r"Received signal|SIG(SEGV|ILL|FPE|BUS|ABRT)")
ENVIRONMENT = re.compile(r"can't create .*context|Can't open display|GLX is not supported|"
                         r"error while loading shared libraries|OpenGL 4\.\d.* is required")


def cmd(bench, home):
    """One benchmark pass, fullscreen in gamescope's headless output: Superposition at its Extreme shaders and high
    textures, Heaven at its Extreme preset (Ultra quality, extreme tessellation, 8x MSAA)."""
    w, h = map(str, SIZE)
    wrap = ["gamescope", "--backend", "headless", "-w", w, "-h", h, "-W", w, "-H", h, "--"]
    video = ["-video_mode", "-1", "-video_width", w, "-video_height", h, "-video_fullscreen", "1",
             "-video_app", "opengl"]
    if bench is SUPERPOSITION:
        return wrap + ["./superposition", "-sound_app", "null", "-system_script", "superposition/system_script.cpp",
                       "-data_path", "../", "-engine_config", "../data/superposition/unigine.cfg",
                       "-project_name", "Superposition", "-video_resizable", "1", "-console_command",
                       "config_readonly 1 && world_load superposition/superposition", "-mode", "2", "-preset", "0",
                       "-shaders_quality", "3", "-textures_quality", "2"] + video
    cfg = os.path.relpath(os.path.join(home, HEAVEN_CFG), bench.bin_dir)
    return wrap + ["./heaven_x64", "-data_path", "../", "-sound_app", "null", "-engine_config", cfg,
                   "-system_script", "heaven/unigine.cpp", "-extern_define",
                   "PHORONIX,RELEASE,QUALITY_ULTRA,TESSELLATION_EXTREME", "-video_multisample", "3"] + video


def unavailable(bench):
    """None if a pass can start, else why not (the test cannot run: inconclusive)."""
    if not os.path.exists(os.path.join(bench.bin_dir, bench.binary)):
        return f"{bench.name} is missing from tools/{bench.key}/: run `ctune gpu unigine-setup` once"
    if not shutil.which("gamescope"):
        return "gamescope is missing (Bazzite ships it)"
    return None


def parse(text):
    """Heaven prints its result; Superposition writes it to a score file. Both share the FPS line."""
    found = {k: (m.group(1) if (m := rx.search(text)) else None) for k, rx in RESULT.items()}
    lines = [l.strip() for l in text.splitlines()]
    return {"fps": float(found["fps"]) if found["fps"] else None,
            "score": int(found["score"]) if found["score"] else None,
            "min_fps": float(found["min_fps"]) if found["min_fps"] else None,
            "max_fps": float(found["max_fps"]) if found["max_fps"] else None,
            "errors": [l for l in lines if ERROR.search(l)][:10],
            "environment": [l for l in lines if ENVIRONMENT.search(l)][:3]}


def _pass(bench, argv, home, timeout_s, guard):
    """One pass: (rc, what it printed plus its score file, seconds, why it was stopped or None)."""
    scores = os.path.join(home, ".Superposition", "automation")
    for f in glob.glob(os.path.join(scores, "log*.txt")):
        os.remove(f)
    if bench is HEAVEN:   # a fresh copy each pass: Heaven rewrites its config at exit
        shutil.copyfile(os.path.join(bench.dir, "data", HEAVEN_CFG), os.path.join(home, HEAVEN_CFG))
    libs = ":".join(bench.libs)
    env = dict(stress.user_env(), HOME=home, LD_LIBRARY_PATH=libs)
    out = os.path.join(home, "pass.out")
    rc, took, why = passes.run(argv, env, bench.bin_dir, out, timeout_s, guard, TERM_GRACE_S)
    text = passes.read(out)
    for f in sorted(glob.glob(os.path.join(scores, "log*.txt"))):
        text += "\n" + passes.read(f)
    return rc, text, took, why


def run(bench, seconds, home, guard=None, make_argv=None, pass_timeout_s=None, min_pass_s=30):
    """Benchmark passes until `seconds` are used up (at least one). guard() returning a reason stops the run (the
    temperature guardrail). Red on a crash, a fatal error, a missing result or a hang; inconclusive when it never
    started rendering."""
    os.makedirs(home, exist_ok=True)
    make_argv = make_argv or (lambda: cmd(bench, home))
    pass_timeout_s = pass_timeout_s or bench.pass_timeout_s
    t_end = time.monotonic() + seconds
    fps, scores, errors, unavail, durations = [], [], [], False, []
    while not errors:
        n = len(durations) + 1
        rc, text, took, why = _pass(bench, make_argv(), home, pass_timeout_s, guard)
        durations.append(took)
        r = parse(text)
        if why == "timeout":
            errors.append(f"pass {n} did not finish in {pass_timeout_s} s: it hung "
                          f"({'; '.join(r['errors']) or 'no error in its output'})")
        elif why:
            errors.append(why)
        elif r["errors"] or rc != 0 or r["fps"] is None:
            if n == 1 and not r["errors"] and r["environment"]:
                unavail = True   # never rendered: the setup, not the GPU
            tail = " | ".join(l for l in text.strip().splitlines()[-4:]) or "no output"
            errors.append(f"pass {n}: exit {rc}: " + "; ".join(r["errors"] or r["environment"] or
                                                                  [f"no result ({tail[-300:]})"]))
        else:
            fps.append(r["fps"])
            if r["score"]:
                scores.append(r["score"])
        if time.monotonic() + max(min_pass_s, statistics.median(durations)) > t_end:
            break
    return {"ok": not errors and bool(fps), "unavailable": unavail, "passes": len(durations), "errors": errors,
            "fps": fps, "fps_median": round(statistics.median(fps), 2) if fps else None,
            "score_median": round(statistics.median(scores)) if scores else None,
            "pass_s_median": round(statistics.median(durations), 1) if durations else None,
            "bench": f"{bench.name} {bench.version}", "size": "x".join(map(str, SIZE))}


def _check(path, bench):
    if os.path.getsize(path) != bench.file_bytes:
        raise RuntimeError(f"{path}: {os.path.getsize(path)} bytes, expected {bench.file_bytes}; delete it and run "
                           "setup again")
    got = subprocess.run(["sha256sum", path], capture_output=True, text=True, check=True).stdout.split()[0]
    if got != bench.sha256:
        raise RuntimeError(f"{path}: sha256 {got}, expected {bench.sha256} (Unigine has replaced files under the "
                           "same name before); delete it and run setup again")


def _payload_offset(path, header_lines):
    with open(path, "rb") as f:
        for _ in range(header_lines):
            f.readline()
        return f.tell()


def _unpack(path, bench):
    """The Makeself payload alone, never its script: skip the header, check the payload's MD5 while untarring it."""
    d = os.path.join(TOOLS, bench.key, bench.top)
    os.makedirs(d, exist_ok=True)
    md5, left = hashlib.md5(), bench.payload_bytes
    tar = subprocess.Popen(["tar", "xzf", "-", "-C", d], stdin=subprocess.PIPE)
    with open(path, "rb") as f:
        f.seek(_payload_offset(path, bench.header_lines))
        while left:
            chunk = f.read(min(1 << 20, left))
            if not chunk:
                break
            md5.update(chunk)
            tar.stdin.write(chunk)
            left -= len(chunk)
    tar.stdin.close()
    if tar.wait() or md5.hexdigest() != bench.payload_md5:
        raise RuntimeError(f"{path}: payload md5 {md5.hexdigest()}, expected {bench.payload_md5} (tar exit "
                           f"{tar.returncode})")
    return d


def setup():
    """Download Superposition and Heaven into tools/, check each SHA256 and payload MD5, unpack, write VERSION."""
    out = {}
    for b in BENCHES:
        d = os.path.join(TOOLS, b.key)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, os.path.basename(b.url))
        if not os.path.exists(path):
            subprocess.run(["curl", "-fL", "--retry", "3", "-o", path + ".part", b.url], check=True)
            os.replace(path + ".part", path)
        _check(path, b)
        _unpack(path, b)
        with open(os.path.join(d, "VERSION"), "w") as f:
            f.write(f"{b.name} {b.version} Basic ({b.url}, sha256 {b.sha256} as pinned by the FreeBSD port, payload "
                    f"md5 {b.payload_md5}); unpacked without its Makeself script; personal use only\n")
        out[b.key] = {"file": path, "sha256": b.sha256}
    return out
