"""
Core image dimension-annotation logic (Pillow only, no web framework).

Given a PIL image plus a width and height value, this draws engineering-style
dimension lines for the WIDTH (bottom) and HEIGHT (left), with arrows + labels.

Two layout modes:
  * "inset"  (default) — draws ON the image, just inside the edges. The output
                         keeps the EXACT input size (1024x1024 -> 1024x1024).
                         A contrast halo keeps lines/labels readable on photos.
  * "margin"           — expands the canvas with white margins and draws the
                         dimensions outside the image (output is larger).

Everything (font, line weight, arrows, insets) auto-scales with the image size
and can be overridden via `DimStyle`.
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
    "DejaVuSans.ttf",
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
    try:
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
    bg: str = "#FFFFFF"            # margin colour (margin mode only)
    mode: str = "inset"            # "inset" (same size) | "margin" (expands)
    halo: str = "auto"             # inset outline: "auto" | colour | "none"
    scale: float = 1.0             # global multiplier on all derived sizes
    line_width: int | None = None
    font_size: int | None = None
    arrow_size: int | None = None
    gap: int | None = None         # gap/inset between edge and dimension line
    margin_extra: int = 0          # extra outer space (margin mode only)
    show_witness: bool = True      # end ticks / extension lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _rgba(color: str):
    return ImageColor.getcolor(color, "RGBA")


def _auto_halo(color_rgba):
    r, g, b = color_rgba[:3]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (255, 255, 255, 255) if lum < 140 else (0, 0, 0, 255)


def _fmt(value: float) -> str:
    f = float(value)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _label(value, unit: str) -> str:
    txt = _fmt(value)
    return f"{txt} {unit}".strip() if unit else txt


def _hline(draw, xy, color, width, halo=None, halo_pad=0):
    if halo and halo_pad > 0:
        draw.line(xy, fill=halo, width=width + 2 * halo_pad)
    draw.line(xy, fill=color, width=width)


def _arrow(draw, tip, direction, length, half_width, color, halo=None, halo_w=0):
    tx, ty = tip
    dx, dy = direction
    bx, by = tx - dx * length, ty - dy * length      # base-line centre
    px, py = -dy, dx                                 # perpendicular unit
    p1 = (bx + px * half_width, by + py * half_width)
    p2 = (bx - px * half_width, by - py * half_width)
    if halo and halo_w > 0:
        draw.polygon([tip, p1, p2], fill=color, outline=halo, width=halo_w)
    else:
        draw.polygon([tip, p1, p2], fill=color)


def _text_stroke(font_size):
    return max(2, round(font_size * 0.10))


def open_image(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def to_bytes(canvas: Image.Image, fmt: str = "png", bg: str = "#FFFFFF",
             quality: int = 92) -> tuple[bytes, str]:
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
# Derived sizes (shared by both modes)
# ---------------------------------------------------------------------------
def _sizes(iw, ih, style):
    base = (iw + ih) / 2.0
    s = max(0.1, float(style.scale))
    font_size = style.font_size or max(16, round(base * 0.045))
    font_size = max(8, round(font_size * s))
    line_w = style.line_width or max(2, round(base * 0.004))
    line_w = max(1, round(line_w * s))
    arrow_len = style.arrow_size or max(10, round(base * 0.022))
    arrow_len = max(4, round(arrow_len * s))
    arrow_hw = max(3, round(arrow_len * 0.5))
    gap = style.gap or max(10, round(base * 0.03))
    gap = max(4, round(gap * s))
    return font_size, line_w, arrow_len, arrow_hw, gap


# ---------------------------------------------------------------------------
# INSET mode — draw on the image, keep the exact input size
# ---------------------------------------------------------------------------
def _annotate_inset(img, width_value, height_value, style):
    iw, ih = img.size
    font_size, line_w, arrow_len, arrow_hw, pad = _sizes(iw, ih, style)

    col = _rgba(style.color)
    if not style.halo or style.halo == "none":
        halo = None
    elif style.halo == "auto":
        halo = _auto_halo(col)
    else:
        halo = _rgba(style.halo)
    halo_pad = max(2, round(line_w * 0.9)) if halo else 0
    stroke_w = _text_stroke(font_size) if halo else 0
    cap = max(3, round(arrow_hw * 1.3))
    label_gap = max(4, round(font_size * 0.4))

    canvas = img.convert("RGBA").copy()
    draw = ImageDraw.Draw(canvas)
    font = load_font(font_size)

    w_text = _label(width_value, style.unit)
    h_text = _label(height_value, style.unit)
    wb = draw.textbbox((0, 0), w_text, font=font, stroke_width=stroke_w)
    hb = draw.textbbox((0, 0), h_text, font=font, stroke_width=stroke_w)
    w_tw, w_th = wb[2] - wb[0], wb[3] - wb[1]
    h_tw, h_th = hb[2] - hb[0], hb[3] - hb[1]

    # keep the lines comfortably inside the frame
    pad = max(4, min(pad, iw // 4, ih // 4))
    x0, x1 = pad, iw - pad
    y0, y1 = pad, ih - pad

    # === WIDTH — horizontal line near the bottom ===
    yb = ih - pad
    _hline(draw, [(x0, yb), (x1, yb)], col, line_w, halo, halo_pad)
    if style.show_witness:
        _hline(draw, [(x0, yb - cap), (x0, yb + cap)], col, line_w, halo, halo_pad)
        _hline(draw, [(x1, yb - cap), (x1, yb + cap)], col, line_w, halo, halo_pad)
    _arrow(draw, (x0, yb), (-1, 0), arrow_len, arrow_hw, col, halo, halo_pad)
    _arrow(draw, (x1, yb), (1, 0), arrow_len, arrow_hw, col, halo, halo_pad)
    cx = iw / 2
    ty = yb - label_gap - w_th
    draw.text((cx - w_tw / 2 - wb[0], ty - wb[1]), w_text, font=font, fill=col,
              stroke_width=stroke_w, stroke_fill=halo)

    # === HEIGHT — vertical line near the left ===
    xl = pad
    _hline(draw, [(xl, y0), (xl, y1)], col, line_w, halo, halo_pad)
    if style.show_witness:
        _hline(draw, [(xl - cap, y0), (xl + cap, y0)], col, line_w, halo, halo_pad)
        _hline(draw, [(xl - cap, y1), (xl + cap, y1)], col, line_w, halo, halo_pad)
    _arrow(draw, (xl, y0), (0, -1), arrow_len, arrow_hw, col, halo, halo_pad)
    _arrow(draw, (xl, y1), (0, 1), arrow_len, arrow_hw, col, halo, halo_pad)

    lbl = Image.new("RGBA", (max(1, h_tw), max(1, h_th)), (0, 0, 0, 0))
    ImageDraw.Draw(lbl).text((-hb[0], -hb[1]), h_text, font=font, fill=col,
                             stroke_width=stroke_w, stroke_fill=halo)
    lbl = lbl.rotate(90, expand=True)
    rw, rh = lbl.size
    px = int(xl + label_gap)
    py = int(ih / 2 - rh / 2)
    px = max(0, min(iw - rw, px))
    py = max(0, min(ih - rh, py))
    canvas.alpha_composite(lbl, (px, py))

    return canvas


# ---------------------------------------------------------------------------
# MARGIN mode — expand the canvas, draw the dimensions outside the image
# ---------------------------------------------------------------------------
def _annotate_margin(img, width_value, height_value, style):
    iw, ih = img.size
    font_size, line_w, arrow_len, arrow_hw, gap = _sizes(iw, ih, style)
    witness_ext = max(2, round(arrow_len * 0.3))
    witness_w = max(1, line_w // 2)
    label_gap = max(4, round(font_size * 0.35))
    outer = max(6, round(gap * 0.6)) + int(style.margin_extra)
    col = _rgba(style.color)
    font = load_font(font_size)

    m = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w_text = _label(width_value, style.unit)
    h_text = _label(height_value, style.unit)
    wb = m.textbbox((0, 0), w_text, font=font)
    hb = m.textbbox((0, 0), h_text, font=font)
    w_tw, w_th = wb[2] - wb[0], wb[3] - wb[1]
    h_tw, h_th = hb[2] - hb[0], hb[3] - hb[1]
    h_label_w = h_th

    left_margin = gap + arrow_hw + label_gap + h_label_w + label_gap + outer
    bottom_margin = gap + arrow_hw + label_gap + w_th + label_gap + outer
    top_margin = outer + arrow_hw
    right_margin = outer + arrow_hw
    canvas_w = left_margin + iw + right_margin
    canvas_h = top_margin + ih + bottom_margin

    canvas = Image.new("RGBA", (canvas_w, canvas_h), _rgba(style.bg))
    src = img.convert("RGBA")
    canvas.paste(src, (left_margin, top_margin), src)
    draw = ImageDraw.Draw(canvas)

    img_left, img_right = left_margin, left_margin + iw
    img_top, img_bottom = top_margin, top_margin + ih

    y_dim = img_bottom + gap
    if style.show_witness:
        draw.line([(img_left, img_bottom), (img_left, y_dim + witness_ext)], fill=col, width=witness_w)
        draw.line([(img_right, img_bottom), (img_right, y_dim + witness_ext)], fill=col, width=witness_w)
    draw.line([(img_left, y_dim), (img_right, y_dim)], fill=col, width=line_w)
    _arrow(draw, (img_left, y_dim), (-1, 0), arrow_len, arrow_hw, col)
    _arrow(draw, (img_right, y_dim), (1, 0), arrow_len, arrow_hw, col)
    cx = (img_left + img_right) / 2
    ly = y_dim + arrow_hw + label_gap
    draw.text((cx - w_tw / 2 - wb[0], ly - wb[1]), w_text, font=font, fill=col)

    x_dim = img_left - gap
    if style.show_witness:
        draw.line([(img_left, img_top), (x_dim - witness_ext, img_top)], fill=col, width=witness_w)
        draw.line([(img_left, img_bottom), (x_dim - witness_ext, img_bottom)], fill=col, width=witness_w)
    draw.line([(x_dim, img_top), (x_dim, img_bottom)], fill=col, width=line_w)
    _arrow(draw, (x_dim, img_top), (0, -1), arrow_len, arrow_hw, col)
    _arrow(draw, (x_dim, img_bottom), (0, 1), arrow_len, arrow_hw, col)

    lbl = Image.new("RGBA", (max(1, h_tw), max(1, h_th)), (0, 0, 0, 0))
    ImageDraw.Draw(lbl).text((-hb[0], -hb[1]), h_text, font=font, fill=col)
    lbl = lbl.rotate(90, expand=True)
    rw, rh = lbl.size
    px = int(x_dim - arrow_hw - label_gap - rw)
    py = int(img_top + (ih - rh) / 2)
    px = max(0, min(canvas_w - rw, px))
    py = max(0, min(canvas_h - rh, py))
    canvas.alpha_composite(lbl, (px, py))

    return canvas


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def annotate(image, width_value, height_value, style=None):
    """Return a new RGBA image with width/height dimension lines drawn on it.

    In the default "inset" mode the output has the SAME pixel dimensions as the
    input; in "margin" mode the canvas is expanded.
    """
    style = style or DimStyle()
    if (style.mode or "inset").lower() == "margin":
        return _annotate_margin(image, width_value, height_value, style)
    return _annotate_inset(image, width_value, height_value, style)
