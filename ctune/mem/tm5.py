"""TestMem5 with the anta777 Extreme1 profile, run under Proton.

- TM5 0.12 from its author's site (testmem.tz.ru), unpacked in the gitignored tools/tm5/ and never redistributed. The
  profile is extreme@anta777.cfg from integralfx/MemTestHelper at a pinned commit. Both have pinned SHA256 hashes.
- TM5 is a 32-bit Windows program. It starts one worker process per logical CPU (Cores=0), and each worker tests its
  own "Testing Window Size" of locked memory, so no worker needs more than 4 GB. The test loops in MT0.dll are plain
  x86 code and run unchanged under Proton. Only allocation and threads go through Wine.
- MT0.dll asks for SeLockMemoryPrivilege, which Wine's token never holds, so TM5 shows "To enable AWE, you must run
  with Administrator privileges!" and waits on it before it starts any worker. Without AWE it tests the same window
  with VirtualLock, so a helper inside gamescope's X display presses Return on that box (xdotool, Bazzite ships it).
- When the log has its final line, the helper also saves a PNG of TM5's window next to the run's log (ImageMagick's
  import, Bazzite ships it), because the window shows the errors per test and the log sometimes names none.
- Wine traces every message box TM5 opens (WINEDEBUG=trace+msgbox). Any other box blocks TM5 the same way, so ctune
  stops the run there and reports its text. A run that has not logged "Start testing" after START_TIMEOUT_S is
  stopped too, and is inconclusive.
- umu-run (umu-launcher, Bazzite ships it) runs it with GE-Proton in its own prefix under ctune's data directory.
  It renders into gamescope's headless backend, as the Unigine loads do, so it needs no desktop session.
- The whole tree runs in a transient systemd user scope with MemorySwapMax=0, because Wine cannot pin pages the way
  Windows does, and a tested page that sits in zram is not tested in the DIMMs. systemd kills a scope that runs out
  of memory, and that run is inconclusive, not red. RuntimeMaxSec ends it if ctune dies. Stopping the scope stops
  every Wine process. A process group kill does not, because wineserver starts them outside the group.
- ctune changes one value of the profile, the window per worker. It keeps anta777's 1408 MB when that fits in the
  free RAM and lowers it otherwise (the profile's own note says to fit it to the machine). Everything else, the test
  sequence, Time (%) 500 and 3 cycles, stays as published.
- TM5 writes Log.txt next to TM5.exe and then waits in a dialog, so ctune reads the log and stops the scope at the
  final line: "Testing completed in 1:23.41, no errors." or "Testing is completed through 1:23.41, detected N
  error(s).". The final dialog says the same and is not a blocking box.
"""
import os
import re
import subprocess
import tempfile
import threading
import time

from ctune.core import stress
from ctune.core.paths import DATA, TOOLS

VERSION = "0.12"
URL = "https://testmem.tz.ru/tm5.rar"
SHA256 = "2da16bd4a3990c4db524feecdb41d25884c37ab7891948b957c81109c442c2b0"
# the site sends the wrong GlobalSign intermediate, so curl gets the right one from the leaf's AIA URL and checks it
# against the system roots
INTERMEDIATE_URL = "http://secure.globalsign.com/cacert/gsgccr6alphasslca2025.crt"
CFG_COMMIT = "da422e50ec2170b578301fe9cc77f101143c38a7"
CFG_URL = f"https://raw.githubusercontent.com/integralfx/MemTestHelper/{CFG_COMMIT}/TM5-Configs/extreme%40anta777.cfg"
CFG_SHA256 = "e7b17592cbaea3b8a4f86e015fdee7062f5409cce64f58dfd4cda392f0b60bb5"
ROOT = os.path.join(TOOLS, "tm5")
DIR = os.path.join(ROOT, "TM5")
EXE = os.path.join(DIR, "TM5.exe")
PROFILE = os.path.join(ROOT, "extreme@anta777.cfg")
CFG = os.path.join(DIR, "bin", "MT.cfg")
LOG = os.path.join(DIR, "Log.txt")
PREFIX = os.path.join(DATA, "tm5-prefix")
PROTON = "GE-Proton"         # umu-run resolves it to the newest GE-Proton and downloads it when none is installed
PER_PROCESS_MB = 64          # Wine's own memory per worker on top of its window (about 22 MB measured)
MIN_WINDOW_MB = 512          # TM5 replaces a smaller window (256 and 304 MB seen) with its own 1 GB per worker
POLL_S = 5
STOP_TIMEOUT = "15s"
START_TIMEOUT_S = 300        # Proton starts in under a minute, and every worker locks its window before the log line
AWE_BOX = "TestMem5, AWE module"
# runs inside gamescope, so DISPLAY is its Xwayland; $1 is TM5.exe, $2 the title of the box to close, $3 the log and
# $4 the screenshot to take of TM5's window once the log has its final line (the window shows the errors per test)
DISMISS = r'''umu-run "$1" & p=$!
while kill -0 "$p" 2>/dev/null; do
  for w in $(xdotool search --onlyvisible --name "^$2\$" 2>/dev/null); do xdotool key --window "$w" Return 2>/dev/null; done
  if [ ! -e "$4" ] && [ ! -e "$4.failed" ] && grep -aqE "completed (in|through)" "$3" 2>/dev/null; then
    sleep 1
    w=$(xdotool search --onlyvisible --name "^TestMem5 v" 2>/dev/null | head -n 1)
    [ -n "$w" ] && import -window "$w" "$4.part.png" 2>/dev/null && mv "$4.part.png" "$4"
    [ -e "$4" ] || : > "$4.failed"
  fi
  sleep 2
done
wait "$p"'''
SHOT_WAIT_S = 20

# the log's own final lines, and the final dialog's text for logs that quote it
DONE_OK = re.compile(r"Testing completed in [\d:.hms ]+, no errors|The testing is completed, of errors is not detected")
DONE_BAD = re.compile(r"(?:detected|is revealed) (\d+) (?:error|of errors)")
STARTED = re.compile(r"Start testing at [\d:]+, ([\d.]+)(Mb|Gb) x(\d+)")
FATAL = re.compile(r"Fatal error|not started|need to run with Administrator")
WINDOW = re.compile(r"^(Testing Window Size \(Mb\)=)(\d+)", re.M)
CYCLES = re.compile(r"^(Cycles=)(\d+)", re.M)
MSGBOX = re.compile(r'trace:msgbox:MSGBOX_OnInit L"(.*)"')


def unavailable():
    """None if a run can start, else why not (inconclusive, not red)."""
    if not (os.path.exists(EXE) and os.path.exists(PROFILE)):
        return "TestMem5 is missing from tools/tm5/: run `ctune mem tm5-setup` once"
    for tool in ("umu-run", "gamescope", "systemd-run", "xdotool"):
        if not _which(tool):
            return f"{tool} is missing (Bazzite ships it)"
    return None


def _which(tool):
    return any(os.access(os.path.join(d, tool), os.X_OK) for d in os.environ.get("PATH", "").split(os.pathsep) if d)


def profile_window_mb(text):
    m = WINDOW.search(text)
    return int(m.group(2)) if m else 1408


def window_mb(mb, threads, cap):
    """The window per worker that fits mb of RAM over `threads` workers, in steps of the 16 MB lock granularity and
    never above the profile's own window, and 0 when even the minimum does not fit."""
    w = (mb // max(1, threads) - PER_PROCESS_MB) // 16 * 16
    w = min(cap, w)
    return w if w >= MIN_WINDOW_MB else 0


def write_cfg(text, window, cycles=None):
    """The profile with its window (and optionally its cycle count) set, in CRLF: TM5 ignores a config with bare LF
    line endings."""
    text = WINDOW.sub(lambda m: f"{m.group(1)}{window}", text, count=1)
    if cycles:
        text = CYCLES.sub(lambda m: f"{m.group(1)}{cycles}", text, count=1)
    return "\r\n".join(text.replace("\r\n", "\n").split("\n"))


def parse_log(text):
    """Verdict fields from TM5's Log.txt."""
    s = STARTED.search(text)
    bad = DONE_BAD.search(text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    errors = [l for l in lines if re.search(r"rror", l) and not DONE_OK.search(l)][:10]
    return {"started": bool(s), "threads": int(s.group(3)) if s else None,
            "window": f"{s.group(1)}{s.group(2)}" if s else None,
            "completed": bool(DONE_OK.search(text) or bad), "errors_reported": int(bad.group(1)) if bad else
            (0 if DONE_OK.search(text) else None), "fatal": [l for l in lines if FATAL.search(l)][:3],
            "errors": errors}


def blocking_boxes(out):
    """The texts of the message boxes TM5 opened, from Wine's msgbox trace, except the AWE box ctune closes."""
    texts = [m.group(1).replace("\\r\\n", " ").replace("\\n", " ").strip() for m in MSGBOX.finditer(out)]
    return [t for t in texts if not t.startswith(("To enable AWE", "The testing is completed"))]


def _read(path):
    try:
        with open(path, encoding="cp1251", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


class Load(stress.Load):
    """TM5 as a phase load that ends the phase when it finishes (ends_phase). The phase's seconds are only a cap."""

    def __init__(self, mb, seconds, threads=None, cycles=None):
        self.threads = threads or os.cpu_count() or 1
        with open(PROFILE, encoding="utf-8", errors="replace") as f:
            self.profile = f.read()
        self.window = window_mb(mb, self.threads, profile_window_mb(self.profile))
        m = CYCLES.search(self.profile)
        self.cycles = cycles or (int(m.group(2)) if m else 3)
        self.scope = f"ctune-tm5-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.scope"
        self.shot = os.path.join(DATA, "runs", self.scope.replace(".scope", ".png"))
        cmd = ["systemd-run", "--user", "--scope", "--quiet", f"--unit={self.scope}", "-p", "MemorySwapMax=0",
               "-p", f"RuntimeMaxSec={seconds + 300}", "-p", f"TimeoutStopSec={STOP_TIMEOUT}",
               "gamescope", "--backend", "headless", "-w", "640", "-h", "480", "--",
               "sh", "-c", DISMISS, "sh", EXE, AWE_BOX, LOG, self.shot]
        super().__init__("tm5 (anta777 extreme)", cmd, None, cwd=DIR)
        self.ends_phase, self.completed, self.scope_result = True, False, None
        self.no_start, self.boxes = False, []

    @property
    def tested_mb(self):
        return self.window * self.threads

    def start(self):
        if not self.window:   # result() reports it, and the phase ends at once
            return self
        for p in (LOG, os.path.join(DIR, "bin", "Cfg.link")):   # Cfg.link would point TM5 at a stale path
            if os.path.exists(p):
                os.remove(p)
        with open(CFG, "w", encoding="utf-8", newline="") as f:
            f.write(write_cfg(self.profile, self.window, self.cycles))
        os.makedirs(PREFIX, exist_ok=True)
        os.makedirs(os.path.dirname(self.shot), exist_ok=True)
        env = dict(stress.user_env(), WINEPREFIX=PREFIX, GAMEID="umu-0", PROTONPATH=PROTON,
                   WINEDEBUG="-all,trace+msgbox")
        self.proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, cwd=self.cwd, env=env, text=True, errors="replace",
                                     start_new_session=True)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        threading.Thread(target=self._watch, daemon=True).start()
        return self

    def _watch(self):
        """Stop the scope at TM5's final log line, at a message box ctune does not close, or when TM5 has not
        started testing in time: in each case TM5 would wait in a dialog forever."""
        t0 = time.time()
        while self.alive():
            log = parse_log(_read(LOG))
            if log["completed"]:
                self.completed = True
                deadline = time.time() + SHOT_WAIT_S   # the helper's screenshot of the errors per test
                while self.alive() and time.time() < deadline and not any(
                        os.path.exists(p) for p in (self.shot, self.shot + ".failed")):
                    time.sleep(1)
                self._stop_scope()
                return
            self.boxes = blocking_boxes("".join(getattr(self, "_chunks", [])))
            self.no_start = not log["started"] and time.time() - t0 > START_TIMEOUT_S
            if self.boxes or self.no_start:
                self._stop_scope()
                return
            time.sleep(POLL_S)

    def _stop_scope(self):
        st = stress.unit_state(self.scope) or {}
        self.scope_result = self.scope_result or st.get("Result")
        subprocess.run(["systemctl", "--user", "stop", self.scope], capture_output=True, timeout=60,
                       env=stress.user_env())

    def signal_stop(self):
        if self.alive():
            self.stopped = True
            self._stop_scope()

    def result(self):
        if self.proc is None:
            why = "not started" if self.window else \
                f"too little free RAM for {self.threads} TM5 workers of {MIN_WINDOW_MB} MB: close large apps"
            return {"name": self.name, "ok": False, "unavailable": not self.window, "errors": [why]}
        self.proc.wait()
        self._reader.join(10)
        st = stress.unit_state(self.scope) or {}
        oom = "oom" in (st.get("Result") or self.scope_result or "")
        if st.get("ActiveState") == "failed":
            subprocess.run(["systemctl", "--user", "reset-failed", self.scope], capture_output=True,
                           env=stress.user_env())
        log = parse_log(_read(LOG))
        out = self.out or "".join(getattr(self, "_chunks", []))
        boxes = blocking_boxes(out)
        r = {"name": self.name, "rc": self.proc.returncode, "threads": self.threads, "window_mb": self.window,
             "cycles": self.cycles,
             "tested_mb": self.tested_mb, **log, "message_boxes": boxes,
             "screenshot": self.shot if os.path.exists(self.shot) else None}
        r["ok"] = log["completed"] and log["errors_reported"] == 0 and not log["fatal"]
        if r["ok"]:
            return r
        if log["errors_reported"]:
            r["errors"].insert(0, f"TM5 reported {log['errors_reported']} errors")
        elif oom:
            r["unavailable"] = True
            r["errors"].insert(0, "the TM5 scope ran out of memory (swap is off for it): close large apps or cap "
                                  "the RAM with --mb")
        elif boxes and log["started"]:   # TM5 itself stopped mid-test, for example a worker that died
            r["errors"].insert(0, f"TM5 stopped on a message box: {' / '.join(boxes)}")
        elif not log["started"] and not self.stopped:
            r["unavailable"] = True   # Proton, gamescope or a dialog kept TM5 from its first test
            tail = " | ".join([l for l in out.strip().splitlines() if "trace:msgbox" not in l][-4:])[-300:]
            why = (f"a message box: {' / '.join(boxes)}" if boxes else
                   f"no 'Start testing' line after {START_TIMEOUT_S} s" if self.no_start else tail or "no output")
            r["errors"].insert(0, f"TM5 never started testing ({why})")
        elif self.stopped:
            r["errors"].insert(0, f"stopped before TM5 finished its {self.cycles} cycles")
        else:
            r["errors"].insert(0, "TM5 ended without its final log line")
        r["errors"] += log["fatal"]
        return r


def _ca_bundle(tmp):
    pem = os.path.join(tmp, "ca.pem")
    der = os.path.join(tmp, "intermediate.der")
    subprocess.run(["curl", "-fsSL", "--retry", "3", "-o", der, INTERMEDIATE_URL], check=True)
    inter = subprocess.run(["openssl", "x509", "-inform", "der", "-in", der], capture_output=True, text=True,
                           check=True).stdout
    roots = next(p for p in ("/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem", "/etc/ssl/certs/ca-certificates.crt")
                 if os.path.exists(p))
    with open(roots) as f, open(pem, "w") as out:
        out.write(f.read() + "\n" + inter)
    return pem


def _sha256(path):
    return subprocess.run(["sha256sum", path], capture_output=True, text=True, check=True).stdout.split()[0]


def _fetch(url, path, sha, cacert=None):
    if not os.path.exists(path):
        subprocess.run(["curl", "-fsSL", "--retry", "3", *(["--cacert", cacert] if cacert else []), "-o",
                        path + ".part", url], check=True)
        os.replace(path + ".part", path)
    got = _sha256(path)
    if got != sha:
        raise RuntimeError(f"{path}: sha256 {got}, expected {sha}; delete it and run setup again")


def setup():
    """Download TM5 and the anta777 profile into tools/tm5/, check both SHA256, unpack, create the Proton prefix."""
    os.makedirs(ROOT, exist_ok=True)
    rar = os.path.join(ROOT, "tm5.rar")
    with tempfile.TemporaryDirectory() as tmp:
        _fetch(URL, rar, SHA256, None if os.path.exists(rar) else _ca_bundle(tmp))
    _fetch(CFG_URL, PROFILE, CFG_SHA256)
    if _which("unrar"):
        subprocess.run(["unrar", "x", "-o+", "-inul", rar, ROOT + "/"], check=True)
    elif _which("7z"):
        subprocess.run(["7z", "x", "-y", f"-o{ROOT}", rar], check=True, capture_output=True)
    else:
        raise RuntimeError("unrar or 7z is needed to unpack tm5.rar")
    # umu-run '' creates the prefix and fetches Proton and its runtime once, then exits 1 because it had nothing
    # to start
    os.makedirs(PREFIX, exist_ok=True)
    env = dict(stress.user_env(), WINEPREFIX=PREFIX, GAMEID="umu-0", PROTONPATH=PROTON, WINEDEBUG="-all")
    r = subprocess.run(["umu-run", ""], env=env, timeout=1800, capture_output=True, text=True, errors="replace")
    if not os.path.isdir(os.path.join(PREFIX, "drive_c")):
        raise RuntimeError(f"umu-run did not create the prefix {PREFIX}: {(r.stdout + r.stderr).strip()[-600:]}")
    with open(os.path.join(ROOT, "VERSION"), "w") as f:
        f.write(f"TestMem5 {VERSION} ({URL}, sha256 {SHA256}); profile extreme@anta777.cfg from integralfx/"
                f"MemTestHelper {CFG_COMMIT} (sha256 {CFG_SHA256}); freeware, never redistribute\n")
    return {"tm5": EXE, "profile": PROFILE, "prefix": PREFIX}
