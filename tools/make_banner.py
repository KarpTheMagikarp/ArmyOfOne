"""
XIII-2's area-name banner (Academia -4XX AF- / Central Bridge) as a title card
for the demo video: a ProRes 4444 clip with alpha that drops straight onto a
track over the footage.

    python tools/make_banner.py "Mouse Support" --tag -LR- --sub "Camera, strikes and menus"
    python tools/make_banner.py "Mouse Support" --still 1.5    # one frame as a png, to eyeball

every number below came off a 60 fps capture of the real banner: positions to
the pixel, size and letter spacing fitted glyph by glyph, motion tracked frame
by frame. checked against the original by rebuilding Academia over the same
footage; it lands within a frame and a couple of pixels.

needs: ffmpeg on PATH, pillow, numpy, fontTools + brotli (the app ships its
font as woff2 and pillow can't open that on its own)
"""

import argparse
import io
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "assets" / "saira.woff2"

W, H, FPS = 1920, 1080, 60
SS = 4  # drawn at 4x so the slow slides move in quarter pixels instead of jittering

# where the game puts it at 1080p
NAME_X, NAME_CAP_TOP = 1154, 499
TAG_GAP, TAG_CAP_TOP = 8, 508
SUB_OFFSET, SUB_CAP_TOP = 377, 554
RULE_Y, RULE_FROM = 542, 600

# (size px, letter spacing px, colour at the cap top, colour at the baseline)
NAME = (45.5, 2.4, (186, 183, 180), (246, 243, 241))
TAG = (36.5, 3.75, (205, 214, 220), (238, 244, 248))
SUB = (24, 1.25, (214, 214, 216), (166, 166, 170))

# seconds from the rule's first frame. the slides and sweeps are what the
# capture did, so changing these makes it stop looking like the game
RULE_REACH, RULE_BRIGHT = 0.18, 0.12
SUB_IN, SUB_FADE, SUB_SLIDE, SUB_SLIDE_FOR = 0.21, 0.17, 150, 0.8
NAME_IN, NAME_FADE, TAG_IN, TAG_FADE, SWEEP_IN = 0.40, 0.09, 0.775, 0.045, 3400
NAME_SLIDE, NAME_SLIDE_FOR = 160, 0.9
SETTLED = 1.2
GLIDE, SWEEP_OUT, FADE_OUT, RULE_OUT = 340, 1600, 0.18, 0.5


def clamp01(t):
    return min(max(t, 0.0), 1.0)


def smooth(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def ease_out(t, power=2):
    return 1 - (1 - clamp01(t)) ** power


# the sizes above were fitted with NewRodin Pro DB, whose caps are 778/1000 of
# its em. Any other face is scaled so its caps come out the same height, which
# is the number the capture pins
FITTED_CAP = 0.778


def load_font(px, path=FONT):
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.ttLib import TTFont

    # woff2 is brotli-packed sfnt; unpack it in memory rather than leave an otf lying around
    font = TTFont(str(path))
    font.flavor = None
    glyphs = font.getGlyphSet()
    pen = BoundsPen(glyphs)
    glyphs[font.getBestCmap()[ord("H")]].draw(pen)
    cap = pen.bounds[3] / font["head"].unitsPerEm
    buf = io.BytesIO()
    font.save(buf)
    buf.seek(0)
    return ImageFont.truetype(buf, int(round(px * FITTED_CAP / cap * SS)))


class Line:
    """one run of text at 4x, tracked by hand (pillow has no letter spacing) with
    the game's top-to-bottom sheen baked into its colour"""

    def __init__(self, text, spec, cap_top, font_path=FONT, thin=0):
        px, tracking, top, bottom = spec
        font = load_font(px, font_path)
        self.cap_off = font.getbbox("H")[1] / SS
        widths = [font.getlength(c) for c in text]
        w = int(sum(widths) + tracking * SS * max(len(text) - 1, 0)) + 2 * SS
        asc, desc = font.getmetrics()
        mask = Image.new("L", (w, asc + desc), 0)
        draw = ImageDraw.Draw(mask)
        x = 0
        for c, cw in zip(text, widths):
            draw.text((x, 0), c, font=font, fill=255)
            x += cw + tracking * SS
        # the subtitle is a weight lighter in the game. shaving the 4x outline
        # gets DB there without shipping a second font
        for _ in range(thin):
            mask = mask.filter(ImageFilter.MinFilter(3))
        self.a = np.asarray(mask, np.float32) / 255
        g = np.clip((np.arange(self.a.shape[0]) / SS - self.cap_off) / (px * 0.8), 0, 1)[:, None, None]
        self.colour = np.array(top, np.float32) * (1 - g) + np.array(bottom, np.float32) * g
        self.width = w / SS
        self.y = cap_top - self.cap_off

    def columns(self):
        return np.arange(self.a.shape[1]) / SS

    def draw(self, rgb, alpha, x, per_column):
        a = self.a * per_column[None, :]
        # shrink 4x -> 1x with the fractional offset baked in, so subpixel x stays subpixel
        ox, oy = int(round((x % 1) * SS)), int(round((self.y % 1) * SS))
        hh, ww = a.shape[0] + SS, a.shape[1] + SS
        pa = np.zeros((hh, ww), np.float32)
        pc = np.zeros((hh, ww, 3), np.float32)
        pa[oy:oy + a.shape[0], ox:ox + a.shape[1]] = a
        pc[oy:oy + a.shape[0], ox:ox + a.shape[1]] = self.colour * a[..., None]
        h, w = hh // SS, ww // SS
        sa = pa[:h * SS, :w * SS].reshape(h, SS, w, SS).mean(axis=(1, 3))
        sc = pc[:h * SS, :w * SS].reshape(h, SS, w, SS, 3).mean(axis=(1, 3))
        x0, y0 = int(np.floor(x)), int(np.floor(self.y))
        cx0, cy0, cx1, cy1 = max(x0, 0), max(y0, 0), min(x0 + w, W), min(y0 + h, H)
        if cx1 <= cx0 or cy1 <= cy0:
            return
        sa = sa[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0]
        sc = sc[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0]
        rgb[cy0:cy1, cx0:cx1] = sc + rgb[cy0:cy1, cx0:cx1] * (1 - sa[..., None])
        alpha[cy0:cy1, cx0:cx1] = sa + alpha[cy0:cy1, cx0:cx1] * (1 - sa)


class Banner:
    def __init__(self, name, tag="", sub="", hold=1.6, font_path=FONT, halo=0.35):
        self.name = Line(name, NAME, NAME_CAP_TOP, font_path)
        self.tag = Line(tag, TAG, TAG_CAP_TOP, font_path) if tag else None
        self.sub = Line(sub, SUB, SUB_CAP_TOP, font_path, thin=1) if sub else None
        # a subtitle longer than any the game uses steps left instead of running off screen
        # same for a name and tag too long to fit: the block steps left as one
        # piece so the spacing the game uses between them stays put
        line_end = NAME_X + self.name.width + (TAG_GAP + self.tag.width if self.tag else 0)
        self.name_x = NAME_X - max(0, line_end - (W - 48))
        self.sub_x = min(self.name_x + SUB_OFFSET, W - 48 - self.sub.width) if self.sub else 0
        self.leaves = SETTLED + hold
        self.halo = halo
        self.duration = self.leaves + 0.7
        self._xs = np.arange(W, dtype=np.float32)

    def frame(self, t):
        rgb = np.zeros((H, W, 3), np.float32)
        alpha = np.zeros((H, W), np.float32)
        gone = t - self.leaves

        # rule: shoots in from the right edge, brighter toward the right, and
        # simply fades in place on the way out
        left = W - (W - RULE_FROM) * ease_out(t / RULE_REACH, 3)
        level = smooth(t / RULE_BRIGHT) * (1 - smooth((gone - 0.05) / RULE_OUT))
        if level > 0:
            ramp = np.clip((self._xs - RULE_FROM) / 1300, 0, 1) * 0.92 * level * (self._xs >= left)
            for dy, k in ((0, 1.0), (1, 0.55)):
                a = ramp * k
                rgb[RULE_Y + dy] = 235 * a[:, None] + rgb[RULE_Y + dy] * (1 - a[:, None])
                alpha[RULE_Y + dy] = a + alpha[RULE_Y + dy] * (1 - a)

        # subtitle: first in, sliding right into place, first out
        if self.sub:
            level = smooth((t - SUB_IN) / SUB_FADE) * (1 - smooth((gone + 0.05) / 0.15))
            if level > 0:
                x = self.sub_x - SUB_SLIDE * (1 - ease_out((t - SUB_IN - 0.04) / SUB_SLIDE_FOR))
                self.sub.draw(rgb, alpha, x, np.full(self.sub.a.shape[1], level, np.float32))

        # name and tag slide left into place while a quick soft sweep fades them in
        # left to right. leaving, they keep gliding left and a slower sweep fades
        # them out the same way round, so the tag is the last thing on screen
        x = self.name_x + NAME_SLIDE * (1 - ease_out((t - NAME_IN - 0.05) / NAME_SLIDE_FOR)) - GLIDE * max(gone, 0)
        parts = [(self.name, NAME_IN, NAME_FADE, x, 0.0)]
        if self.tag:
            offset = self.name.width + TAG_GAP
            parts.append((self.tag, TAG_IN, TAG_FADE, x + offset, offset))
        for line, start, fade, px, along in parts:
            if t < start:
                continue
            q = line.columns()
            col = smooth((t - start - q / SWEEP_IN) / fade)
            if gone > 0:
                col = col * (1 - smooth((gone + 0.01 - (along + q) / SWEEP_OUT) / FADE_OUT))
            if col.max() > 0:
                line.draw(rgb, alpha, px, col)

        # the game keeps it readable over bright floors with a faint dark halo.
        # 0.35 is the game's; the title screen's pale fog wants more
        a8 = Image.fromarray(np.clip(alpha * 255, 0, 255).astype(np.uint8))
        halo = np.asarray(a8.filter(ImageFilter.GaussianBlur(5)), np.float32) / 255 * self.halo
        out_a = alpha + halo * (1 - alpha)
        colour = np.where(out_a[..., None] > 0, rgb / np.maximum(out_a[..., None], 1e-6), 0)
        return np.dstack([np.clip(colour, 0, 255), out_a * 255]).astype(np.uint8)

    def write(self, out):
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
               "-r", str(FPS), "-i", "-", "-c:v", "prores_ks", "-profile:v", "4444",
               "-pix_fmt", "yuva444p10le", "-vendor", "apl0", str(out)]
        enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for f in range(int(round(self.duration * FPS))):
            enc.stdin.write(self.frame(f / FPS).tobytes())
        enc.stdin.close()
        if enc.wait():
            sys.exit(f"ffmpeg gave up writing {out}")


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "banner"


def dashed_values(argv):
    # the game's tags start with a dash (-4XX AF-, -LR-), which argparse reads as
    # the next option. glue them on so --tag -LR- works the way you'd type it
    out, it = [], iter(argv)
    for arg in it:
        if arg in ("--tag", "--sub"):
            value = next(it, "")
            out.append(f"{arg}={value}")
        else:
            out.append(arg)
    return out


def main(argv=None):
    argv = dashed_values(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description="XIII-2 area-name banner as a transparent title card")
    ap.add_argument("name", help="the big line, e.g. 'Mouse Support'")
    ap.add_argument("--tag", default="", help="the small tag after it, e.g. -LR-")
    ap.add_argument("--sub", default="", help="the line under the rule")
    ap.add_argument("--hold", type=float, default=1.6, help="seconds it sits still before leaving")
    ap.add_argument("--out", type=Path, help="defaults to banner_<name>.mov here")
    ap.add_argument("--still", type=float, help="write one frame at this time as a png instead")
    ap.add_argument("--font", type=Path, default=FONT)
    ap.add_argument("--halo", type=float, default=0.35, help="dark halo strength; raise it over pale backgrounds")
    args = ap.parse_args(argv)

    if not args.font.is_file():
        sys.exit(f"no font at {args.font}: it's the app's own, in assets/")
    banner = Banner(args.name, args.tag, args.sub, args.hold, args.font, args.halo)
    if args.still is not None:
        out = args.out or Path(f"banner_{slug(args.name)}_{args.still:.2f}s.png")
        Image.fromarray(banner.frame(args.still), "RGBA").save(out)
    else:
        out = args.out or Path(f"banner_{slug(args.name)}.mov")
        banner.write(out)
    print(f"{out} ({banner.duration:.2f}s)" if args.still is None else out)


if __name__ == "__main__":
    main()
