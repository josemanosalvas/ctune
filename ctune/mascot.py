"""c'tune's mascot: Claude Code's Clawd crossed with C'Thun, as pixel sprites.

Standard library only. ansi() draws a sprite in a truecolor terminal with half blocks;
docs/brand/build.py exports the same sprites as SVG, PNG, GIF and ICO.
"""
import os

PAL = {
    "#": "#D97757",  # Claude coral (body)
    "d": "#B4573A",  # shade
    "D": "#8A3D27",  # blink line
    "w": "#F4EDE4",  # sclera
    "W": "#FFFFFF",  # glint
    "y": "#EFAE3F",  # iris
    "Y": "#C77A1C",  # iris rim
    "k": "#141413",  # pupil
    "r": "#C0392B",  # alert text
    "i": "#141413",  # wordmark ink
}


class Sprite:
    def __init__(self, w, h):
        self.w, self.h, self.px = w, h, {}

    def put(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[(x, y)] = c

    def get(self, x, y):
        return self.px.get((x, y))

    def rect(self, x0, y0, x1, y1, c):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.put(x, y, c)

    def stamp(self, x0, y0, rows, sway=0, from_row=3):
        """Draw rows of palette chars ('.' is transparent); rows from `from_row` on shift by `sway`."""
        for dy, row in enumerate(rows):
            dx0 = sway if dy >= from_row else 0
            for dx, c in enumerate(row):
                if c != ".":
                    self.put(x0 + dx + dx0, y0 + dy, c)

    def ascii(self):
        return "\n".join("".join(self.px.get((x, y), ".") for x in range(self.w)) for y in range(self.h))


def mirror(rows):
    return [r[::-1] for r in rows]


# The iris is a "( )": two rim arcs around a tall pupil, spanning the eye's height.
EYE = [
    "...wyyw...",
    ".wwYyyYww.",
    "wwwYWkYwww",
    "wwwYkkYwww",
    "wwwYkkYwww",
    ".wwYyyYww.",
    "...wyyw...",
]
STALK_EYE = [".##.", "#kw#", ".##."]  # looks left; mirror() looks right
LEG_OUTER = ["..##", "..##", ".##.", ".##.", "##..", "#..."]
LEG_INNER = [".##", ".##", "##.", "##.", ".##", ".#."]
ICON_LEGS = [
    "..##.##..##.##..",
    "..##.##..##.##..",
    ".##...#..#...##.",
    ".#....#..#....#.",
]


def _eye_px(s, x0, y0, rows):
    return {(x0 + x, y0 + y) for y, row in enumerate(rows) for x, c in enumerate(row) if c != "."}


def eye(s, x0, y0, rows=EYE, closed=False):
    """Stamp an eye in a shaded socket. closed: a blink, drawn as a lash line."""
    if closed:
        w, mid = len(rows[0]), y0 + len(rows) // 2
        s.rect(x0 + 1, mid, x0 + w - 2, mid, "D")
        s.put(x0, mid - 1, "D")
        s.put(x0 + w - 1, mid - 1, "D")
        return
    s.stamp(x0, y0, rows)
    for x, y in _eye_px(s, x0, y0, rows):
        for a, b in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if s.get(x + a, y + b) == "#":
                s.px[(x + a, y + b)] = "d"


def mascot(sway=0, look=1, blink=False):
    """Clawd's block body and arm nubs, one Old God eye, eye-stalks, tentacle legs. 30x24.
    look: 1 = stalk eyes look outward, -1 = inward."""
    s = Sprite(30, 24)
    s.rect(7, 6, 22, 17, "#")
    s.rect(5, 12, 6, 13, "#")
    s.rect(23, 12, 24, 13, "#")
    eye(s, 10, 8, closed=blink)
    left, right = (STALK_EYE, mirror(STALK_EYE)) if look > 0 else (mirror(STALK_EYE), STALK_EYE)
    s.stamp(5, 0, left)
    s.put(7, 3, "#"); s.put(8, 4, "#"); s.put(8, 5, "#")
    s.stamp(21, 1, right)
    s.put(22, 4, "#"); s.put(21, 5, "#")
    s.stamp(6, 18, LEG_OUTER, sway)
    s.stamp(11, 18, LEG_INNER, sway)
    s.stamp(16, 18, mirror(LEG_INNER), sway)
    s.stamp(20, 18, mirror(LEG_OUTER), sway)
    return s


def mascot_frames():
    """(sprite, milliseconds) for a loop: tentacles sway, the stalk eyes look around, one blink."""
    still, left, right = mascot(), mascot(sway=-1), mascot(sway=1)
    return [
        (still, 450), (right, 450), (still, 450), (left, 450),
        (still, 600), (mascot(blink=True), 130), (still, 450),
        (mascot(sway=1, look=-1), 450), (mascot(look=-1), 450), (mascot(sway=-1, look=-1), 450),
        (still, 450),
    ]


def icon():
    """16x16: Clawd head with one eye and tentacle legs."""
    s = Sprite(16, 16)
    s.rect(2, 2, 13, 11, "#")
    s.rect(0, 7, 1, 8, "#")
    s.rect(14, 7, 15, 8, "#")
    eye(s, 4, 3, [
        "..wyyw..",
        ".wYyWYw.",
        "wwYkkYww",
        "wwYkkYww",
        ".wYyyYw.",
        "..wyyw..",
    ])
    s.stamp(0, 12, ICON_LEGS)
    return s


# chunky 2px-stroke lowercase, 8 rows (x-height 6)
FONT = {
    "c": ["......", "......", ".#####", "##....", "##....", "##....", "##....", ".#####"],
    "t": [".##..", ".##..", "#####", ".##..", ".##..", ".##..", ".##..", "..###"],
    "u": ["......", "......", "##..##", "##..##", "##..##", "##..##", "##..##", ".#####"],
    "n": ["......", "......", "#####.", "##..##", "##..##", "##..##", "##..##", "##..##"],
    "e": ["......", "......", ".####.", "##..##", "######", "##....", "##....", ".#####"],
    # the apostrophe is a little eye-stalk looking at "tune"
    "'": [".##.", "#wk#", ".##.", ".#..", "#...", "....", "....", "...."],
}


def wordmark(text="c'tune"):
    """Letters in ink ('i'); the apostrophe keeps its own colours."""
    w = sum(len(FONT[ch][0]) + 2 for ch in text) - 2
    s = Sprite(w, 8)
    x = 0
    for ch in text:
        g = FONT[ch]
        s.stamp(x, 0, g if ch == "'" else [row.replace("#", "i") for row in g])
        x += len(g[0]) + 2
    return s


def lockup(body=None):
    """Mascot (or the given mascot frame) beside the wordmark."""
    m, wm = body or mascot(), wordmark()
    s = Sprite(m.w + 4 + wm.w, m.h)
    s.px.update(m.px)
    for (x, y), c in wm.px.items():
        s.put(m.w + 4 + x, 8 + y, c)
    return s


# --- terminal -------------------------------------------------------------------------------

def _rgb(hexcolor):
    h = hexcolor.lstrip("#")
    return ";".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))


def ansi(s, pal=PAL):
    """Sprite as truecolor half-block lines, two pixel rows per line."""
    lines = []
    for y in range(0, s.h, 2):
        cells = []
        for x in range(s.w):
            top, bot = s.get(x, y), s.get(x, y + 1)
            if top and bot:
                cells.append(f"\x1b[38;2;{_rgb(pal[top])};48;2;{_rgb(pal[bot])}m▀")
            elif top:
                cells.append(f"\x1b[38;2;{_rgb(pal[top])};49m▀")
            elif bot:
                cells.append(f"\x1b[38;2;{_rgb(pal[bot])};49m▄")
            else:
                cells.append("\x1b[0m ")
        lines.append("".join(cells) + "\x1b[0m")
    return lines


def color_ok(stream):
    return stream.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"


def paint(text, c):
    return f"\x1b[38;2;{_rgb(PAL[c])}m{text}\x1b[0m"


TITLE = f"\x1b[1mc{paint(chr(39), '#')}\x1b[1mtune\x1b[0m"


def banner(lines):
    """The 16px mascot with text lines beside it."""
    art = [line for line in ansi(icon()) if line.strip("\x1b[0m ")]
    top = max(0, (len(art) - len(lines)) // 2)
    text = [""] * top + list(lines) + [""] * len(art)
    return "\n".join(f"{a}  {t}".rstrip() for a, t in zip(art, text))
