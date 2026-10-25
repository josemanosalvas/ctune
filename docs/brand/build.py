"""Export c'tune's mascot (ctune/mascot.py) as SVG, PNG, GIF, ICO and a social preview.

Run: python3 docs/brand/build.py   (needs Pillow; writes next to this file)
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT.parents[1]))
from ctune import mascot as m  # noqa: E402

INK = {"dark": "#141413", "light": "#F4EDE4"}
# Ink that follows the viewer's colour scheme, for renderers that ignore <picture> (editor previews, apps).
ADAPTIVE = {**m.PAL, "i": None}
INK_CSS = f".i{{fill:{INK['dark']}}}@media (prefers-color-scheme:dark){{.i{{fill:{INK['light']}}}}}"
BG_DARK = "#141413"


def _rgba(hexcolor, a=255):
    h = hexcolor.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


def _rects(px, pal):
    """Horizontal runs of one colour as <rect>s."""
    out = []
    rows = {}
    for (x, y), c in px.items():
        rows.setdefault(y, {})[x] = c
    for y in sorted(rows):
        xs = rows[y]
        for x in sorted(xs):
            if xs.get(x - 1) == xs[x]:
                continue
            run = 1
            while xs.get(x + run) == xs[x]:
                run += 1
            paint = 'class="i"' if pal[xs[x]] is None else f'fill="{pal[xs[x]]}"'
            out.append(f'<rect x="{x}" y="{y}" width="{run}" height="1" {paint}/>')
    return out


def _svg_open(w, h, scale=16):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'width="{w * scale}" height="{h * scale}" shape-rendering="crispEdges">')


def to_svg(s, path, pal=m.PAL):
    style = [f"<style>{INK_CSS}</style>"] if None in pal.values() else []
    path.write_text("\n".join([_svg_open(s.w, s.h), *style, *_rects(s.px, pal), "</svg>"]) + "\n")


def to_image(s, scale=1, pal=m.PAL):
    img = Image.new("RGBA", (s.w, s.h), (0, 0, 0, 0))
    for (x, y), c in s.px.items():
        img.putpixel((x, y), _rgba(pal[c]))
    return img.resize((s.w * scale, s.h * scale), Image.NEAREST)


def to_png(s, path, scale=16, pal=m.PAL):
    to_image(s, scale, pal).save(path)


def to_animated_svg(frames, path, pal=m.PAL):
    """Pixels shared by every frame drawn once; each distinct frame's other pixels in a group that
    CSS shows during that frame's time windows."""
    total = sum(ms for _, ms in frames)
    uniq, windows, t = [], [], 0
    for s, ms in frames:
        key = s.ascii()
        i = next((j for j, u in enumerate(uniq) if u.ascii() == key), None)
        if i is None:
            uniq.append(s)
            windows.append([])
            i = len(uniq) - 1
        windows[i].append((t, t + ms))
        t += ms
    common = {p: c for p, c in uniq[0].px.items() if all(u.px.get(p) == c for u in uniq)}
    css, groups = [], []
    for i, (u, win) in enumerate(zip(uniq, windows)):
        steps = [] if win[0][0] == 0 else ["0%{visibility:hidden}"]
        for a, b in win:
            steps.append(f"{a * 100 / total:.3f}%{{visibility:visible}}")
            steps.append(f"{b * 100 / total:.3f}%{{visibility:hidden}}")
        css.append(f"@keyframes f{i}{{{''.join(steps)}}}"
                   f".f{i}{{visibility:hidden;animation:f{i} {total}ms step-end infinite}}")
        diff = {p: c for p, c in u.px.items() if p not in common}
        groups.append(f'<g class="f{i}">' + "".join(_rects(diff, pal)) + "</g>")
    css.append("@media (prefers-reduced-motion:reduce){g{animation:none!important}.f0{visibility:visible}}")
    if None in pal.values():
        css.append(INK_CSS)
    s0 = frames[0][0]
    path.write_text("\n".join([
        _svg_open(s0.w, s0.h), f"<style>{''.join(css)}</style>", *_rects(common, pal), *groups, "</svg>",
    ]) + "\n")


def to_gif(frames, path, scale=12, pal=m.PAL):
    colors = sorted({pal[c] for s, _ in frames for c in s.px.values()})
    flat = [0, 0, 0] + [v for col in colors for v in _rgba(col)[:3]]
    index = {col: i + 1 for i, col in enumerate(colors)}
    imgs = []
    for s, _ in frames:
        im = Image.new("P", (s.w, s.h), 0)
        im.putpalette(flat)
        for (x, y), c in s.px.items():
            im.putpixel((x, y), index[pal[c]])
        imgs.append(im.resize((s.w * scale, s.h * scale), Image.NEAREST))
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=[ms for _, ms in frames],
                 loop=0, transparency=0, disposal=2)


def to_ico(s, path, sizes=(16, 32, 48)):
    imgs = [to_image(s, n // s.w) for n in sizes]
    imgs[-1].save(path, format="ICO", sizes=[(n, n) for n in sizes], append_images=imgs[:-1])


def social_preview(path, tagline=("RAM, CPU and GPU tuning,", "driven by an agent, on Linux")):
    """1280x640 card for GitHub: mascot, wordmark, tagline, centred as one group."""
    card = Image.new("RGBA", (1280, 640), _rgba(BG_DARK))
    draw = ImageDraw.Draw(card)
    art = to_image(m.mascot(), 14)
    wm = to_image(m.wordmark(), 11, {**m.PAL, "i": INK["light"]})
    try:
        font = ImageFont.truetype("FiraCode-Regular.ttf", 30)
    except OSError:
        font = ImageFont.load_default(30)
    line_h = 44
    text_w = max([wm.width] + [int(draw.textlength(t, font=font)) for t in tagline])
    text_h = wm.height + 40 + line_h * len(tagline)
    gap = 80
    x0 = (1280 - (art.width + gap + text_w)) // 2
    card.alpha_composite(art, (x0, (640 - art.height) // 2))
    x, y = x0 + art.width + gap, (640 - text_h) // 2
    card.alpha_composite(wm, (x, y))
    for i, t in enumerate(tagline):
        draw.text((x, y + wm.height + 40 + i * line_h), t, font=font, fill=_rgba("#A8A69E"))
    card.save(path)


if __name__ == "__main__":
    for name, s in (("mascot", m.mascot()), ("icon", m.icon())):
        to_svg(s, OUT / f"{name}.svg")
        to_png(s, OUT / f"{name}.png")
    for name, s in (("wordmark", m.wordmark()), ("lockup", m.lockup())):
        for tone, ink in INK.items():
            pal = {**m.PAL, "i": ink}
            to_svg(s, OUT / f"{name}-{tone}.svg", pal)
            to_png(s, OUT / f"{name}-{tone}.png", pal=pal)
    frames = m.mascot_frames()
    to_animated_svg(frames, OUT / "mascot-animated.svg")
    lockups = [(m.lockup(s), ms) for s, ms in frames]
    for tone, ink in INK.items():
        to_animated_svg(lockups, OUT / f"lockup-animated-{tone}.svg", {**m.PAL, "i": ink})
    to_animated_svg(lockups, OUT / "lockup-animated.svg", ADAPTIVE)
    to_svg(m.lockup(), OUT / "lockup.svg", ADAPTIVE)
    to_svg(m.wordmark(), OUT / "wordmark.svg", ADAPTIVE)
    to_gif(frames, OUT / "mascot.gif")
    to_ico(m.icon(), OUT / "favicon.ico")
    social_preview(OUT / "social-preview.png")
