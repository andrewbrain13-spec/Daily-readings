#!/usr/bin/env python3
"""
Draw the podcast cover art: two crossed keys on a deep red field.

This is original artwork in the Petrine tradition, the crossed keys of Saint
Peter, rather than a copy of the Vatican coat of arms. The official emblem is
a state symbol, and reproducing it would suggest this feed is an official
publication when it is not.

Run:  python3 tools/make_cover.py
Out:  docs/cover.jpg  (3000x3000, comfortably above Spotify's 1400 minimum)
"""

import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SIZE = 3000
OUT = os.path.join("docs", "cover.jpg")

DEEP_RED = (122, 17, 20)
DARK_RED = (68, 8, 11)
GOLD = (214, 174, 84)
GOLD_DARK = (154, 118, 44)
SILVER = (214, 214, 214)
SILVER_DARK = (150, 150, 152)
CREAM = (243, 234, 214)

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
SERIF_BOLD = os.path.join(FONT_DIR, "DejaVuSerif-Bold.ttf")
SANS_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")


def background():
    """A deep red field, darker at the edges so the keys sit forward."""
    img = Image.new("RGB", (SIZE, SIZE), DEEP_RED)
    draw = ImageDraw.Draw(img)
    for y in range(SIZE):
        t = y / SIZE
        draw.line(
            [(0, y), (SIZE, y)],
            fill=tuple(
                int(DEEP_RED[i] + (DARK_RED[i] - DEEP_RED[i]) * (t ** 1.4))
                for i in range(3)
            ),
        )

    # Vignette: a soft dark frame that pulls the eye to the centre.
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).ellipse(
        [-SIZE * 0.15, -SIZE * 0.15, SIZE * 1.15, SIZE * 1.15], fill=255
    )
    mask = mask.filter(ImageFilter.GaussianBlur(SIZE // 12))
    img = Image.composite(img, Image.new("RGB", (SIZE, SIZE), DARK_RED), mask)
    return img


def draw_key(length, colour, shadow):
    """One key lying horizontally, bow on the left, wards on the right."""
    pad = length // 4
    w, h = length + pad * 2, length // 2 + pad * 2
    key = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(key)

    cy = h // 2
    shaft = max(6, length // 26)          # thickness of the stem
    bow_r = length // 7                   # radius of the ring handle
    bow_cx = pad + bow_r

    # Ring handle, drawn as a thick circle outline with a highlight inside.
    d.ellipse(
        [bow_cx - bow_r, cy - bow_r, bow_cx + bow_r, cy + bow_r],
        outline=colour, width=shaft,
    )
    d.ellipse(
        [bow_cx - bow_r + shaft, cy - bow_r + shaft,
         bow_cx + bow_r - shaft, cy + bow_r - shaft],
        outline=shadow, width=max(2, shaft // 3),
    )

    # A small collar where the ring meets the stem.
    collar_x = bow_cx + bow_r
    d.rounded_rectangle(
        [collar_x, cy - shaft, collar_x + shaft * 2, cy + shaft],
        radius=shaft // 2, fill=colour,
    )

    # The stem.
    tip = pad + length
    d.rounded_rectangle(
        [collar_x, cy - shaft // 2, tip, cy + shaft // 2],
        radius=shaft // 4, fill=colour,
    )

    # Wards: the teeth at the business end, plus a decorative cross cut.
    ward_h = length // 11
    d.rounded_rectangle(
        [tip - length // 5, cy - shaft // 2, tip - length // 5 + shaft, cy + ward_h],
        radius=shaft // 4, fill=colour,
    )
    d.rounded_rectangle(
        [tip - length // 9, cy - shaft // 2, tip - length // 9 + shaft, cy + ward_h * 0.7],
        radius=shaft // 4, fill=colour,
    )
    d.rounded_rectangle(
        [tip - shaft, cy - ward_h, tip, cy + ward_h],
        radius=shaft // 4, fill=colour,
    )
    return key


def crossed_keys(length):
    """Two keys crossed in a saltire, gold over silver."""
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    silver = draw_key(length, SILVER, SILVER_DARK).rotate(
        -38, resample=Image.BICUBIC, expand=True)
    gold = draw_key(length, GOLD, GOLD_DARK).rotate(
        38, resample=Image.BICUBIC, expand=True)

    for layer, dx in ((silver, -length // 22), (gold, length // 22)):
        canvas.alpha_composite(
            layer,
            (SIZE // 2 - layer.width // 2 + dx, SIZE // 2 - layer.height // 2),
        )
    return canvas


def fitted_font(draw, text, path, max_width, start):
    """Largest size at which text still fits inside max_width."""
    size = start
    while size > 12:
        font = ImageFont.truetype(path, size)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width:
            return font
        size -= 4
    return ImageFont.truetype(path, 12)


def add_text(img):
    d = ImageDraw.Draw(img)
    # Keep type well inside the border rather than running up against it.
    safe = SIZE * 0.74
    title = fitted_font(d, "DAILY READINGS", SERIF_BOLD, safe, SIZE // 9)
    sub = fitted_font(d, "A N D  W O R D S  F R O M  T H E  P O P E", SANS_BOLD,
                      safe * 0.82, SIZE // 34)

    def centred(text, font, y, fill):
        box = d.textbbox((0, 0), text, font=font)
        d.text(((SIZE - (box[2] - box[0])) / 2 - box[0], y), text,
               font=font, fill=fill)

    centred("DAILY READINGS", title, int(SIZE * 0.730), CREAM)
    centred("A N D  W O R D S  F R O M  T H E  P O P E", sub, int(SIZE * 0.858), GOLD)

    # Thin rule between the two lines of type.
    y = int(SIZE * 0.836)
    d.line([(SIZE * 0.34, y), (SIZE * 0.66, y)], fill=GOLD_DARK,
           width=max(2, SIZE // 600))


def main():
    img = background()

    keys = crossed_keys(int(SIZE * 0.52))

    # Lift the whole emblem to leave room for the type underneath. The shadow
    # has to move with it, offset by only a few pixels, or it reads as a second
    # detached object rather than as a shadow.
    lift = -int(SIZE * 0.075)
    emblem = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    emblem.alpha_composite(keys, (0, lift))

    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 110), (0, 0), emblem.split()[3])
    shadow = shadow.filter(ImageFilter.GaussianBlur(SIZE // 130))
    offset = int(SIZE * 0.006)
    img.paste((0, 0, 0), (offset, offset), shadow.split()[3])

    img.paste(emblem.convert("RGB"), (0, 0), emblem.split()[3])

    # A restrained border.
    d = ImageDraw.Draw(img)
    inset = SIZE // 26
    d.rectangle([inset, inset, SIZE - inset, SIZE - inset],
                outline=GOLD_DARK, width=max(3, SIZE // 340))

    add_text(img)

    os.makedirs("docs", exist_ok=True)
    img.save(OUT, "JPEG", quality=92, optimize=True)
    print("Wrote {} at {}x{}".format(OUT, *img.size))


if __name__ == "__main__":
    main()
