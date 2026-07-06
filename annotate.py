"""
Core image dimension-annotation logic (Pillow only, no web framework).

Given a PIL image plus a width and height value, this returns a NEW image
with the original centered inside white margins and engineering-style
dimension lines drawn on it:

    * WIDTH  -> a horizontal dimension line along the BOTTOM  (arrows + label)
    * HEIGHT -> a vertical   dimension line along the LEFT    (arrows + label)

Everything (font size, line weight, arrow size, margins) auto-scales with the
image size so the annotation looks proportional at any resolution, and every
value can be overridden via `DimStyle`.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass

from PIL import Image, ImageColor, ImageDraw, ImageFont

# Guard against decompression bombs while still allowing large product photos.
Image.MAX_IMAGE_PIXELS = 64_000_000  # ~64 megapixels


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------
_FONT_CANDIDATES = [
    os.environ.get("FONT_PATH"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
    "DejaVuSans.ttf",  # Pillow can resolve some bundled/relative names
    "Arial.ttf",
]


def load_font(size: int):
    """Return a scalable TrueType font, falling back to Pillow's default."""
    size = max(8, int(size))
    for path in _FONT_CANDIDATES:
        if not path:
            continue
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:  # Pillow >= 10.1 can scale the built-in bitmap font
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------
@dataclass
class DimStyle:
    unit: str = ""                 # appended to each value, e.g. "cm"
    color: str = "#151515"         # line + label colour (name or hex)
    bg: str = "#FFFFFF"            # margin / background colour
    scale: float = 1.0             # global multiplier on all derived sizes
    line_width: int | None = None  # explicit line weight (px)
    font_size: int | None = None   # explicit label font size (px)
    arrow_size: int | None = None  # explicit arrowhead length (px)
    gap: int | None = None         # gap between image edge and dimension line
    margin_extra: int = 0          # extra outer breathing space (px)
    show_witness: bool = True      # draw the short extension/witness lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _rgba(color: str):
    return ImageColor.getcolor(color, "RGBA")


def _fmt(value: float) -> str:
    """Render a number without a trailing '.0' but keep real decimals."""
    f = float(value)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _label(value, unit: str) -> str:
    txt = _fmt(value)
    return f"{txt} {unit}".strip() if unit else txt


def _arrow(draw, tip, direction, length, half_width, color):
    """Draw a filled triangular arrowhead whose tip is at `tip`, pointing
    along the unit vector `direction`."""
    tx, ty = tip
    dx, dy = direction
    bx, by = tx - dx * length, ty - dy * length      # base-line centre
    px, py = -dy, dx                                 # perpendicular unit
    p1 = (bx + px * half_width, by + py * half_width)
    p2 = (bx - px * half_width, by - py * half_width)
    draw.polygon([tip, p1, p2], fill=color)


def open_image(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def to_bytes(canvas: Image.Image, fmt: str = "png", bg: str = "#FFFFFF",
             quality: int = 92) -> tuple[bytes, str]:
    """Serialise a canvas to PNG or JPEG bytes; returns (data, media_type)."""
    fmt = (fmt or "png").lower()
    buf = io.BytesIO()
    if fmt in ("jpg", "jpeg"):
        flat = Image.new("RGB", canvas.size, ImageColor.getcolor(bg, "RGB"))
        flat.paste(canvas, mask=canvas.split()[-1])
        flat.save(buf, "JPEG", quality=quality)
        return buf.getvalue(), "image/jpeg"
    if fmt == "webp":
        canvas.save(buf, "WEBP", quality=quality)
        return buf.getvalue(), "image/webp"
    canvas.save(buf, "PNG")
    return buf.getvalue(), "image/png"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def annotate(
    image: Image.Image,
    width_value: float,
    height_value: float,
    style: DimStyle | None = None,
) -> Image.Image:
    """Return a new RGBA image = original + width/height dimension lines."""
    style = style or DimStyle()

    img = image.convert("RGBA")
    iw, ih = img.size
    base = (iw + ih) / 2.0
    s = max(0.1, float(style.scale))

    # --- derive sizes (auto-scaled, then user-overridable, then * scale) ---
    font_size = style.font_size or max(16, round(base * 0.045))
    font_size = max(8, round(font_size * s))
    line_w = style.line_width or max(2, round(base * 0.004))
    line_w = max(1, round(line_w * s))
    arrow_len = style.arrow_size or max(10, round(base * 0.022))
    arrow_len = max(4, round(arrow_len * s))
    arrow_hw = max(3, round(arrow_len * 0.5))
    gap = style.gap or max(10, round(base * 0.03))
    gap = max(4, round(gap * s))
    witness_ext = max(2, round(arrow_len * 0.3))
    witness_w = max(1, line_w // 2)
    label_gap = max(4, round(font_size * 0.35))
    outer = max(6, round(gap * 0.6)) + int(style.margin_extra)

    col = _rgba(style.color)
    font = load_font(font_size)

    # --- measure the two labels ---
    m = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w_text = _label(width_value, style.unit)
    h_text = _label(height_value, style.unit)
    wb = m.textbbox((0, 0), w_text, font=font)
    hb = m.textbbox((0, 0), h_text, font=font)
    w_tw, w_th = wb[2] - wb[0], wb[3] - wb[1]
    h_tw, h_th = hb[2] - hb[0], hb[3] - hb[1]
    h_label_w = h_th  # rotated 90 deg, horizontal footprint == text height

    # --- margins ---
    left_margin = gap + arrow_hw + label_gap + h_label_w + label_gap + outer
    bottom_margin = gap + arrow_hw + label_gap + w_th + label_gap + outer
    top_margin = outer + arrow_hw
    right_margin = outer + arrow_hw

    canvas_w = left_margin + iw + right_margin
    canvas_h = top_margin + ih + bottom_margin

    canvas = Image.new("RGBA", (canvas_w, canvas_h), _rgba(style.bg))
    canvas.paste(img, (left_margin, top_margin), img)
    draw = ImageDraw.Draw(canvas)

    img_left, img_right = left_margin, left_margin + iw
    img_top, img_bottom = top_margin, top_margin + ih

    # === WIDTH — horizontal dimension line along the bottom ===
    y_dim = img_bottom + gap
    if style.show_witness:
        draw.line([(img_left, img_bottom), (img_left, y_dim + witness_ext)],
                  fill=col, width=witness_w)
        draw.line([(img_right, img_bottom), (img_right, y_dim + witness_ext)],
                  fill=col, width=witness_w)
    draw.line([(img_left, y_dim), (img_right, y_dim)], fill=col, width=line_w)
    _arrow(draw, (img_left, y_dim), (-1, 0), arrow_len, arrow_hw, col)
    _arrow(draw, (img_right, y_dim), (1, 0), arrow_len, arrow_hw, col)
    cx = (img_left + img_right) / 2
    ly = y_dim + arrow_hw + label_gap
    draw.text((cx - w_tw / 2 - wb[0], ly - wb[1]), w_text, font=font, fill=col)

    # === HEIGHT — vertical dimension line along the left ===
    x_dim = img_left - gap
    if style.show_witness:
        draw.line([(img_left, img_top), (x_dim - witness_ext, img_top)],
                  fill=col, width=witness_w)
        draw.line([(img_left, img_bottom), (x_dim - witness_ext, img_bottom)],
                  fill=col, width=witness_w)
    draw.line([(x_dim, img_top), (x_dim, img_bottom)], fill=col, width=line_w)
    _arrow(draw, (x_dim, img_top), (0, -1), arrow_len, arrow_hw, col)
    _arrow(draw, (x_dim, img_bottom), (0, 1), arrow_len, arrow_hw, col)

    # rotated height label, vertically centered to the left of the line
    label_img = Image.new("RGBA", (max(1, h_tw), max(1, h_th)), (0, 0, 0, 0))
    ImageDraw.Draw(label_img).text((-hb[0], -hb[1]), h_text, font=font, fill=col)
    label_img = label_img.rotate(90, expand=True)
    rot_w, rot_h = label_img.size
    paste_x = int(x_dim - arrow_hw - label_gap - rot_w)
    paste_y = int(img_top + (ih - rot_h) / 2)
    paste_x = max(0, min(canvas_w - rot_w, paste_x))
    paste_y = max(0, min(canvas_h - rot_h, paste_y))
    canvas.alpha_composite(label_img, (paste_x, paste_y))

    return canvas
