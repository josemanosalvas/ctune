"""Draw the README figures as SVG, one file per colour scheme.

Run: python3 docs/figures/build.py   (standard library only; writes next to this file)

The numbers come from the journal and the benchmark logs of the reference machine
(Ryzen 7 5800X3D, B550 AORUS PRO, 4 x 8 GB DDR4, RTX 3080 Ti).
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent
W = 880
FONT = "system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"

# Colour follows the domain in every figure: mem blue, cpu orange, gpu aqua.
THEMES = {
    "light": {
        "surface": "#ffffff", "panel": "#f6f8fa", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
        "grid": "#e6e8eb", "axis": "#c8ccd1", "mem": "#2a78d6", "cpu": "#eb6834", "gpu": "#1baf7a",
        "all": "#6250d6", "neutral": "#898781", "good": "#0ca30c", "bad": "#d03b3b", "accent": "#d97757",
    },
    "dark": {
        "surface": "#0d1117", "panel": "#151b23", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
        "grid": "#262c36", "axis": "#3d444d", "mem": "#3987e5", "cpu": "#d95926", "gpu": "#199e70",
        "all": "#9085e9", "neutral": "#898781", "good": "#0ca30c", "bad": "#d03b3b", "accent": "#d97757",
    },
}


class Svg:
    def __init__(self, height, title, desc, theme):
        self.h, self.t, self.parts = height, THEMES[theme], []
        self.head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" width="{W}" height="{height}" '
                     f'font-family="{FONT}" role="img"><title>{title}</title><desc>{desc}</desc>')

    def add(self, s):
        self.parts.append(s)

    def text(self, x, y, s, size=12, fill="ink", anchor="start", weight=400):
        s = s.replace("&", "&amp;").replace("<", "&lt;")
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{self.t[fill]}" text-anchor="{anchor}" '
                 f'font-weight="{weight}">{s}</text>')

    def line(self, x1, y1, x2, y2, stroke="grid", width=1):
        self.add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{self.t[stroke]}" '
                 f'stroke-width="{width}" stroke-linecap="round"/>')

    def rect(self, x, y, w, h, fill, r=0, stroke=None):
        edge = f' stroke="{self.t[stroke]}"' if stroke else ""
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r}" fill="{self.t[fill]}"{edge}/>')

    def path(self, d, stroke=None, fill=None, width=1.5):
        paint = f'fill="{self.t[fill]}"' if fill else 'fill="none"'
        if stroke:
            paint += f' stroke="{self.t[stroke]}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"'
        self.add(f'<path d="{d}" {paint}/>')

    def hbar(self, x0, x1, y, h, fill, r=4):
        """A bar from the baseline x0 to x1, rounded at the data end only."""
        r = min(r, abs(x1 - x0))
        s = 1 if x1 > x0 else -1
        self.path(f"M{x0:.1f} {y:.1f}H{x1 - s * r:.1f}Q{x1:.1f} {y:.1f} {x1:.1f} {y + r:.1f}V{y + h - r:.1f}"
                  f"Q{x1:.1f} {y + h:.1f} {x1 - s * r:.1f} {y + h:.1f}H{x0:.1f}Z", fill=fill)

    def vbar(self, x, y0, y1, w, fill, r=4):
        """A column from the baseline y0 up to y1, rounded at the top only."""
        self.path(f"M{x:.1f} {y0:.1f}V{y1 + r:.1f}Q{x:.1f} {y1:.1f} {x + r:.1f} {y1:.1f}H{x + w - r:.1f}"
                  f"Q{x + w:.1f} {y1:.1f} {x + w:.1f} {y1 + r:.1f}V{y0:.1f}Z", fill=fill)

    def arrow(self, x1, y1, x2, y2, stroke="axis", width=1.5):
        """A straight arrow along one axis, with its head at (x2, y2)."""
        self.line(x1, y1, x2, y2, stroke, width)
        if y1 == y2:
            s = 1 if x2 > x1 else -1
            head = f"M{x2 - s * 6:.1f} {y2 - 4:.1f}L{x2:.1f} {y2:.1f}L{x2 - s * 6:.1f} {y2 + 4:.1f}"
        else:
            s = 1 if y2 > y1 else -1
            head = f"M{x2 - 4:.1f} {y2 - s * 6:.1f}L{x2:.1f} {y2:.1f}L{x2 + 4:.1f} {y2 - s * 6:.1f}"
        self.path(head, stroke=stroke, width=width)

    def verdict(self, x, y, ok, r=9):
        """A status mark: colour plus a check or a cross, with a surface ring."""
        self.add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{self.t["good" if ok else "bad"]}" '
                 f'stroke="{self.t["surface"]}" stroke-width="2"/>')
        k = r / 9
        d = (f"M{x - 4 * k:.1f} {y + 0.5 * k:.1f}L{x - 1.2 * k:.1f} {y + 3.2 * k:.1f}L{x + 4 * k:.1f} {y - 3 * k:.1f}" if ok else
             f"M{x - 3 * k:.1f} {y - 3 * k:.1f}L{x + 3 * k:.1f} {y + 3 * k:.1f}M{x + 3 * k:.1f} {y - 3 * k:.1f}L{x - 3 * k:.1f} {y + 3 * k:.1f}")
        self.add(f'<path d="{d}" fill="none" stroke="#ffffff" stroke-width="{1.8 * k:.1f}" stroke-linecap="round" '
                 f'stroke-linejoin="round"/>')

    def swatch(self, x, y, fill, label, size=11.5):
        self.rect(x, y - 9, 10, 10, fill, r=2)
        self.text(x + 16, y, label, size, "ink2")

    def render(self):
        return self.head + "".join(self.parts) + "</svg>\n"


def loop(theme):
    """One step of a campaign."""
    g = Svg(204, "One step of a campaign",
            "Plan, apply, verify, test, verdict. The last green configuration is the baseline for the next step, "
            "and every stage writes to the journal.", theme)
    boxes = [("Plan", "one variable and", "its rollback value"),
             ("Apply", "ctune for CPU and GPU,", "you in the BIOS for RAM"),
             ("Verify", "read the setting back", "from the hardware"),
             ("Test", "loads that check", "their own results"),
             ("Verdict", "green, red or", "inconclusive")]
    bw, gap, top, bh = 136, 40, 50, 44
    xs = [20 + i * (bw + gap) for i in range(len(boxes))]
    # The way back: the verdict sets the baseline the next plan starts from.
    x_first, x_last, y_loop = xs[0] + bw / 2, xs[-1] + bw / 2, 20
    g.path(f"M{x_last} {top}V{y_loop + 8}Q{x_last} {y_loop} {x_last - 8} {y_loop}H{x_first + 8}"
           f"Q{x_first} {y_loop} {x_first} {y_loop + 8}V{top - 2}", stroke="accent")
    g.path(f"M{x_first - 4} {top - 8}L{x_first} {top - 2}L{x_first + 4} {top - 8}", stroke="accent")
    g.rect(W / 2 - 196, y_loop - 10, 392, 20, "surface")
    g.text(W / 2, y_loop + 4, "the last green configuration is the baseline for the next step", 11.5, "ink2", "middle")
    for i, (x, (name, l1, l2)) in enumerate(zip(xs, boxes)):
        g.rect(x, top, bw, bh, "panel", r=8, stroke="axis")
        g.text(x + bw / 2, top + bh / 2 + 5, name, 14, "ink", "middle", 600)
        g.text(x + bw / 2, top + bh + 21, l1, 11, "ink2", "middle")
        g.text(x + bw / 2, top + bh + 35, l2, 11, "ink2", "middle")
        if i:
            g.arrow(x - gap + 7, top + bh / 2, x - 7, top + bh / 2)
    y_j = 152
    for x in xs:
        g.arrow(x + bw / 2, top + bh + 44, x + bw / 2, y_j - 5, "axis", 1.2)
    g.rect(20, y_j, W - 40, 36, "panel", r=8, stroke="axis")
    g.text(W / 2, y_j + 22, "Journal: every plan, change and verdict, with the snapshot it was measured on",
           12, "ink", "middle")
    return g.render()


# (step, [(lane, passed)]); a lane is the deepest test the step reached.
CAMPAIGN = [
    (1, [("boot", False)]), (2, [("boot", False)]), (3, [("screen", True)]), (4, [("screen", False)]),
    (5, [("screen", True)]), (6, [("screen", True)]), (7, [("screen", True)]), (8, [("screen", True)]),
    (9, [("screen", True)]), (10, [("screen", True)]), (11, [("screen", True)]), (12, [("boot", False)]),
    (13, [("screen", True)]), (14, [("screen", True)]), (15, [("screen", False)]),
    (16, [("screen", True), ("tm5", False)]), (17, [("tm5", False)]), (18, [("tm5", False)]), (19, [("tm5", False)]),
    (20, [("tm5", True), ("signoff", True)]), (21, [("tm5", False)]), (22, [("tm5", True)]), (23, [("tm5", True)]),
]
CAMPAIGN_GROUPS = [(1, 4, "Frequency"), (5, 12, "Secondary timings"), (13, 19, "Tighter primaries"),
                   (20, 23, "Sign-off and after")]
LANES = [("signoff", "Sign-off", "the full suite, 7 h"), ("tm5", "TestMem5", "anta777 Extreme1, 3 cycles"),
         ("screen", "Stress screen", "stressapptest, 25 to 90 min"), ("boot", "Boot", "POST and memory training")]


def campaign(theme):
    """A RAM campaign: how far each step got, and its verdict there."""
    g = Svg(312, "A RAM campaign, step by step",
            "Twenty-three single-variable steps. Each stem shows the deepest test the step reached, "
            "and each mark the verdict of that test.", theme)
    left, right, floor = 196, W - 20, 232
    dx = (right - left) / len(CAMPAIGN)
    ys = {name: 40 + i * 56 for i, (name, _, _) in enumerate(LANES)}
    for name, label, sub in LANES:
        g.line(left, ys[name], right, ys[name])
        g.text(20, ys[name] - 1, label, 12.5, "ink", weight=600)
        g.text(20, ys[name] + 13, sub, 10.5, "muted")
    g.line(left, floor, right, floor, "axis")
    for step, marks in CAMPAIGN:
        x = left + dx * (step - 0.5)
        g.line(x, floor, x, min(ys[lane] for lane, _ in marks), "axis", 1.5)
        g.text(x, floor + 17, str(step), 11, "muted", "middle")
    for step, marks in CAMPAIGN:
        for lane, ok in marks:
            g.verdict(left + dx * (step - 0.5), ys[lane], ok)
    g.text(20, floor + 17, "Step", 11, "muted")
    for a, b, label in CAMPAIGN_GROUPS:
        xa, xb = left + dx * (a - 1) + 5, left + dx * b - 5
        g.path(f"M{xa:.1f} {floor + 27}V{floor + 32}H{xb:.1f}V{floor + 27}", stroke="axis", width=1)
        g.text((xa + xb) / 2, floor + 49, label, 11.5, "ink2", "middle")
    x16, x20 = left + dx * 15.5, left + dx * 19.5
    g.text(x16 - 18, ys["tm5"] - 8, "tRCD and tRP at 14 pass the screen and fail here", 11.5, "ink2", "end")
    g.text(x20 - 18, ys["signoff"] - 8, "tRCD and tRP at 15: the signed-off configuration", 11.5, "ink2", "end")
    g.verdict(26, floor + 62, True, 7)
    g.text(39, floor + 66, "passed", 11.5, "ink2")
    g.verdict(96, floor + 62, False, 7)
    g.text(109, floor + 66, "failed", 11.5, "ink2")
    return g.render()


# Tuned (360 W limit, 1905 MHz lock, +70 MHz core offset) against stock (400 W), same GPU screen.
GPU_COST = [("gpu-burn, FP32", -1.7, "26,362 to 25,905 GFLOPS"), ("gpu-burn, FP64", -3.1, "540 to 523 GFLOPS"),
            ("Quake II RTX", -1.2, "96.0 to 94.8 fps"), ("Unigine Superposition", -1.1, "7,435 to 7,352 points"),
            ("Unigine Heaven", -0.5, "154.3 to 153.6 fps")]
GPU_SAVED = [("Power at full load", -9.9, "397 to 358 W"), ("Energy for the whole screen", -10.4, "613 to 549 kJ"),
             ("Fan speed after Superposition", -12.8, "64 to 56 %")]


def gpu(theme):
    """Tuned against stock, as a change in percent."""
    g = Svg(330, "A tuned RTX 3080 Ti against stock",
            "Performance falls by 0.5 to 3.1 percent while power, energy and fan speed fall by 10 to 13 percent.", theme)
    x_zero, scale, x_detail = 560, 290 / 15, 584
    row, bar = 27, 14
    y = 22
    bottoms = []
    for title, rows, fill in (("Performance given up", GPU_COST, "neutral"), ("Power and noise saved", GPU_SAVED, "gpu")):
        g.text(20, y, title, 12.5, "ink", weight=600)
        y += 12
        for label, pct, detail in rows:
            x_tip = x_zero + pct * scale
            g.text(20, y + bar - 2, label, 12, "ink2")
            g.hbar(x_zero, x_tip, y, bar, fill)
            g.text(x_tip - 7, y + bar - 2.5, f"−{abs(pct):.1f} %", 11.5, "ink", "end", 600)
            g.text(x_detail, y + bar - 2, detail, 11.5, "muted")
            y += row
        bottoms.append(y)
        y += 22
    axis_y = bottoms[-1] + 4
    g.line(x_zero, 26, x_zero, axis_y, "axis")
    for pct in (-15, -10, -5, 0):
        x = x_zero + pct * scale
        g.line(x, axis_y, x, axis_y + 4, "axis")
        g.text(x, axis_y + 18, f"−{abs(pct)} %" if pct else "stock", 11, "muted", "middle")
    g.line(x_zero - 15 * scale, axis_y, x_zero, axis_y, "axis")
    return g.render()


# (panel title, unit and direction, [stock, RAM tuned, RAM and Curve Optimizer], number format)
BENCH = [("7-Zip, 16 threads", "MIPS, higher is better", [76927, 85370, 88736], "{:,.0f}"),
         ("7-Zip, 1 thread", "MIPS, higher is better", [8136, 8596, 8595], "{:,.0f}"),
         ("y-cruncher, 1 billion digits of Pi", "seconds, lower is better", [41.41, 33.63, 32.69], "{:.1f}")]
BENCH_SERIES = [("neutral", "stock", "Stock, DDR4-2133"), ("mem", "RAM", "RAM tuned, DDR4-3733 CL14"),
                ("cpu", "RAM + CO", "RAM tuned and Curve Optimizer at −20 to −30")]


def bench(theme):
    """Three benchmarks, three configurations each."""
    g = Svg(292, "CPU benchmarks: stock, RAM tuned, RAM and Curve Optimizer",
            "7-Zip multithreaded gains 15 percent, single-threaded 6 percent, and y-cruncher finishes 21 percent sooner.",
            theme)
    x = 20
    for fill, _, label in BENCH_SERIES:
        g.swatch(x, 20, fill, label)
        x += 34 + len(label) * 6.3
    pw, gap, base, height, bw, pitch = 260, 30, 252, 128, 24, 64
    for p, (title, unit, values, fmt) in enumerate(BENCH):
        px = 20 + p * (pw + gap)
        g.text(px, 58, title, 12.5, "ink", weight=600)
        g.text(px, 73, unit, 11, "muted")
        g.line(px, base, px + pw, base, "axis")
        for i, (v, (fill, short, _)) in enumerate(zip(values, BENCH_SERIES)):
            bx = px + (pw - 2 * pitch - bw) / 2 + i * pitch
            top = base - height * v / max(values)
            g.vbar(bx, base, top, bw, fill)
            g.text(bx + bw / 2, top - 7, fmt.format(v), 11.5, "ink", "middle", 600)
            if i:
                change = (v / values[0] - 1) * 100
                g.text(bx + bw / 2, top - 21, f"{'+' if change > 0 else '−'}{abs(change):.1f} %", 10.5, "muted", "middle")
            g.text(bx + bw / 2, base + 16, short, 10.5, "muted", "middle")
    return g.render()


# (minutes, colour, name, what runs)
SIGNOFF = [(60, "mem", "RAM stress", "stressapptest"), (90, "mem", "TestMem5", "anta777 Extreme1, 3 cycles"),
           (45, "cpu", "y-cruncher", "all cores"), (50, "cpu", "Per core", "mprime SSE"), (25, "cpu", "Per core", "AVX2"),
           (89, "gpu", "GPU screen", "compute, ray tracing, raster"), (30, "all", "Combined", "all loads"),
           (30, "neutral", "Idle", "soak")]
SIGNOFF_LEGEND = [("mem", "RAM"), ("cpu", "CPU"), ("gpu", "GPU"), ("all", "RAM, GPU and NVMe at once"),
                  ("neutral", "Nothing running")]


def signoff(theme):
    """The sign-off suite as one bar, split by phase."""
    g = Svg(150, "The sign-off suite",
            "Eight phases over seven hours: two for RAM, three for the CPU, one for the GPU, a combined load and an idle soak.",
            theme)
    x = 20
    for fill, label in SIGNOFF_LEGEND:
        g.swatch(x, 18, fill, label)
        x += 36 + len(label) * 6.3
    left, width, top, h, gap = 20, W - 40, 34, 28, 2
    total = sum(m for m, *_ in SIGNOFF)
    per_min = (width - gap * (len(SIGNOFF) - 1)) / total
    x = left
    for minutes, fill, name, what in SIGNOFF:
        w = minutes * per_min
        g.rect(x, top, w, h, fill, r=4)
        g.add(f'<text x="{x + 8:.1f}" y="{top + 18.5}" font-size="11" font-weight="600" fill="#ffffff">{minutes} min</text>')
        g.text(x + 1, top + h + 17, name, 11.5, "ink", weight=600)
        g.text(x + 1, top + h + 31, what, 10.5, "muted")
        x += w + gap
    y = top + h + 46
    g.line(left, y, left + width, y, "axis")
    for hour in range(0, total // 60 + 1):
        hx = left + hour * 60 * (width / total)
        g.line(hx, y, hx, y + 4, "axis")
        g.text(hx, y + 17, f"{hour} h" if hour else "0", 10.5, "muted", "start" if hour == 0 else "middle")
    return g.render()


FIGURES = {"loop": loop, "campaign": campaign, "gpu": gpu, "bench": bench, "signoff": signoff}

if __name__ == "__main__":
    for name, draw in FIGURES.items():
        for theme in THEMES:
            (OUT / f"{name}-on-{theme}.svg").write_text(draw(theme), encoding="utf-8")
            print(f"{name}-on-{theme}.svg")
