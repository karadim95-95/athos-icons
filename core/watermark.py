"""
The faded watermark on product photos: the shop's logo and name, repeated diagonally over the
whole picture, so a photo cannot simply be cropped and reused elsewhere.
"""

import re
from functools import lru_cache

from django.conf import settings
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = settings.BASE_DIR / "static" / "fonts"
GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]+")
OPACITY = 0.30      # the light logo and name
SHADOW = 0.16       # a soft dark edge around them, so they also show on light photos
ANGLE = 30          # degrees, rising to the right


def _font(script, size):
    font = ImageFont.truetype(str(FONTS / f"eb-garamond-{script}-wght-normal.woff2"), size)
    font.set_variation_by_axes([600])
    return font


def _runs(text):
    """The name split into its Latin and Greek parts: the shop's font has a file for each."""
    pos = 0
    for match in GREEK.finditer(text):
        if match.start() > pos:
            yield "latin", text[pos:match.start()]
        yield "greek", match.group()
        pos = match.end()
    if pos < len(text):
        yield "latin", text[pos:]


def _mark(size):
    """The logo (templates/partials/logo_mark.html, a 40 × 40 drawing) at size × size pixels."""
    scale = size * 4 / 40  # drawn 4× larger, then shrunk for smooth edges
    mark = Image.new("L", (size * 4, size * 4))
    draw = ImageDraw.Draw(mark)
    for radius, width, shade in [(18.2, 1.6, 255), (15.4, 1, 170)]:
        outer = radius + width / 2  # the line is centred on the radius, as in the SVG
        draw.ellipse([(20 - outer) * scale, (20 - outer) * scale, (20 + outer) * scale, (20 + outer) * scale],
                     outline=shade, width=round(width * scale))
    width = 2.2 * scale
    for x1, y1, x2, y2 in [(20, 8, 20, 32), (16.5, 11.5, 23.5, 11.5), (12.5, 16.5, 27.5, 16.5), (15.5, 27, 24.5, 24.4)]:
        draw.line([x1 * scale, y1 * scale, x2 * scale, y2 * scale], fill=255, width=round(width))
        for x, y in ((x1, y1), (x2, y2)):  # rounded ends
            draw.ellipse([x * scale - width / 2, y * scale - width / 2, x * scale + width / 2, y * scale + width / 2],
                         fill=255)
    return mark.resize((size, size), Image.LANCZOS)


@lru_cache(maxsize=8)
def stamp(text, height):
    """The logo followed by the name, as a mask `height` pixels tall (white where the ink is)."""
    parts = [(_font(script, round(height * 0.62)), part) for script, part in _runs(text)]
    gap = round(height * 0.28)
    width = height + gap + round(sum(font.getlength(part) for font, part in parts))
    image = Image.new("L", (width, height))
    image.paste(_mark(height))
    draw = ImageDraw.Draw(image)
    x = height + gap
    for font, part in parts:
        draw.text((x, height / 2), part, font=font, fill=255, anchor="lm")
        x += font.getlength(part)
    return image


def add_watermark(image, text):
    """A copy of the photo with the logo and `text` repeated diagonally over it, faded."""
    image = image.convert("RGB")
    width, height = image.size
    mark = stamp(text, max(16, round(min(width, height) * 0.045)))
    step_x, step_y = round(mark.width * 1.45), mark.height * 4
    # A square big enough to still cover the whole photo once turned.
    side = round((width ** 2 + height ** 2) ** 0.5) + step_x
    pattern = Image.new("L", (side, side))
    for row, y in enumerate(range(0, side, step_y)):
        for x in range(-(row % 2) * step_x // 2, side, step_x):  # every other row shifted by half
            pattern.paste(mark, (x, y))
    pattern = pattern.rotate(ANGLE, resample=Image.BICUBIC)
    left, top = (side - width) // 2, (side - height) // 2
    mask = pattern.crop((left, top, left + width, top + height))
    edge = mask.filter(ImageFilter.GaussianBlur(max(1, mark.height / 25)))
    image.paste((30, 20, 12), mask=edge.point(lambda v: round(v * SHADOW)))
    image.paste((255, 255, 255), mask=mask.point(lambda v: round(v * OPACITY)))
    return image
