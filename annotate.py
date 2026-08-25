"""
Core image dimension-annotation logic (Pillow only, no web framework).

Given a PIL image plus a width and height value, this draws engineering-style
dimension lines for the WIDTH (bottom) and HEIGHT (left), with arrows + labels,
plus an optional DEPTH diagonal receding from the bottom-right corner.

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
import math
import os
from dataclasses import dataclass

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

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
    depth_angle: float = 33.0      # slant of the depth diagonal, degrees
    depth_max: float = 0.22        # longest depth diagonal, fraction of the short side
    watermark: bool = False        # composite the NAWAQIS logo BEHIND the image
    watermark_opacity: float = 0.20
    watermark_scale: float = 0.9   # logo width as a fraction of the image width
    watermark_bg: str = "#FFFFFF"  # fill shown where the product is transparent


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
# Watermark — the NAWAQIS logo placed BEHIND the product image
# ---------------------------------------------------------------------------
_LOGO_PATH = os.environ.get(
    "WATERMARK_LOGO",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "nawaqis-logo.png"),
)
_logo_cache = None


def _load_logo():
    global _logo_cache
    if _logo_cache is None:
        try:
            logo = Image.open(_LOGO_PATH).convert("RGBA")
            logo.load()
            _logo_cache = logo
        except Exception:
            _logo_cache = False
    return _logo_cache or None


def _has_transparency(im):
    if im.mode in ("RGBA", "LA"):
        return im.convert("RGBA").getchannel("A").getextrema()[0] < 255
    return im.mode == "P" and "transparency" in im.info


def _product_keep_mask(product, white_thresh=235):
    """L mask: 255 = product (keep), 0 = background (let the watermark show).

    Uses the alpha channel when the image is transparent; otherwise treats the
    near-white region *connected to the border* as background — studio
    product-on-white shots. Internal light areas (labels, caps) are NOT border
    connected, so they stay part of the product and the logo never punches
    through them.
    """
    iw, ih = product.size
    if _has_transparency(product):
        return product.convert("RGBA").getchannel("A")
    gray = product.convert("L")
    cand = gray.point(lambda v: 255 if v >= white_thresh else 0)   # white-ish
    work = cand.copy()
    filled = False
    for c in [(0, 0), (iw - 1, 0), (0, ih - 1), (iw - 1, ih - 1)]:
        if work.getpixel(c) == 255:
            ImageDraw.floodfill(work, c, 128, thresh=0)            # border-white
            filled = True
    if not filled:
        return Image.new("L", (iw, ih), 255)                       # no white bg
    keep = work.point(lambda v: 0 if v == 128 else 255)
    return keep.filter(ImageFilter.GaussianBlur(1.2))              # soften edge


def _apply_watermark(product, style):
    """Place the NAWAQIS logo BEHIND the product: a faint centered logo on the
    background, product kept on top. Works for transparent cut-outs (via alpha)
    and for opaque product-on-white JPEGs (via background detection).
    """
    iw, ih = product.size
    wm = Image.new("RGBA", (iw, ih), _rgba(style.watermark_bg))
    logo = _load_logo()
    if logo is not None:
        scale = max(0.05, min(1.0, float(style.watermark_scale)))
        tw = max(1, int(iw * scale))
        th = max(1, round(logo.height * tw / logo.width))
        max_h = int(ih * scale)
        if max_h > 0 and th > max_h:                 # keep it inside on wide logos
            th = max_h
            tw = max(1, round(logo.width * th / logo.height))
        piece = logo.resize((tw, th), Image.LANCZOS)
        op = max(0.0, min(1.0, float(style.watermark_opacity)))
        if op < 1.0:
            piece.putalpha(piece.split()[-1].point(lambda v: int(v * op)))
        wm.alpha_composite(piece, ((iw - tw) // 2, (ih - th) // 2))
    keep = _product_keep_mask(product)
    out = wm.copy()
    out.paste(product.convert("RGB"), (0, 0), keep)
    return out


# ---------------------------------------------------------------------------
# Derived sizes (shared by both modes)
# ---------------------------------------------------------------------------
DEPTH_MAX_RATIO = 4.0      # depth beyond this multiple of w/h is called suspect


def _depth_ratio(width_value, height_value, depth_value):
    """Depth as a multiple of the largest stated dimension.

    Single source of truth: both the drawing length and the sanity warning read
    this, so the two can never drift apart.
    """
    ref = max(abs(width_value or 0), abs(height_value or 0)) or abs(depth_value) or 1.0
    return abs(depth_value) / ref


def depth_warning(width_value, height_value, depth_value, max_ratio=DEPTH_MAX_RATIO):
    """Return a message when depth looks out of proportion, else None.

    The drawing rule clamps the ratio at 1.0, so every depth at or above the
    largest other dimension renders an IDENTICAL diagonal - 300 and 1244 are
    indistinguishable in the output. A wrong value therefore produces a
    perfectly plausible-looking image. This is the only thing that tells the
    caller the number is nonsense.
    """
    if depth_value is None:
        return None
    try:
        ratio = _depth_ratio(width_value, height_value, depth_value)
    except (TypeError, ValueError):
        return None
    if max_ratio and ratio > float(max_ratio):
        return (f"depth {_fmt(depth_value)} is {ratio:.1f}x the largest of "
                f"width/height ({_fmt(max(abs(width_value or 0), abs(height_value or 0)))}) "
                f"- check the source data")
    return None


def _depth_length(iw, ih, width_value, height_value, depth_value, style):
    """Visual length of the depth diagonal, in pixels.

    A true projection is not usable here: at real scale a 30x20 box wants a
    diagonal two-thirds the width of the frame, which inset mode cannot fit, so
    every plausible value ends up pinned to the same clamp. Instead the depth is
    mapped by RATIO against the largest stated dimension into a fixed visual
    range. Deeper items always draw longer, the line always fits, and the result
    is predictable rather than a projection that is silently wrong.
    """
    short = min(iw, ih)
    r = min(1.0, max(0.0, _depth_ratio(width_value, height_value, depth_value)))
    lo = 0.06 * short
    hi = max(lo + 1.0, float(style.depth_max) * short)
    return lo + r * (hi - lo)


def _depth_fit(iw, ih, width_value, height_value, depth_value, style, left_band,
               base_right, yb, edge, arrow_len, arrow_hw, label_gap, d_tw):
    """Depth length plus the right band it needs, kept inside the frame (inset)."""
    ang = math.radians(style.depth_angle)
    ca, sa = math.cos(ang), math.sin(ang)
    length = _depth_length(iw, ih, width_value, height_value, depth_value, style)

    run = length * ca
    max_run = max(arrow_len, iw // 2 - base_right - arrow_hw)
    if run > max_run:
        run = max_run
        length = (run / ca) if ca > 1e-6 else length
    rise = length * sa
    head = yb - edge - arrow_hw          # vertical room above the width line
    if rise > head and sa > 1e-6:
        length = max(arrow_len, head / sa)
        run = length * ca
    band = base_right + int(round(_depth_band(run, d_tw, arrow_hw, label_gap)))
    return length, min(band, iw - left_band - arrow_len)


def _depth_band(run, label_w, arrow_hw, label_gap):
    """Right-hand space the depth diagonal needs, in pixels.

    The label is centred ABOVE the diagonal's midpoint rather than parked off its
    tip, so only half the label overhangs. Reserving the full label width here is
    what used to collapse the width bracket to ~52% of the frame on a wide label
    such as "124.4 mm".
    """
    return max(run + arrow_hw, run / 2.0 + label_w / 2.0 + label_gap)


def _draw_depth(draw, origin, length, angle_deg, font, text, col, line_w,
                arrow_len, arrow_hw, label_gap, halo=None, halo_pad=0,
                stroke_w=0, bounds=None):
    """Draw the receding depth line up-and-right from `origin`, label kept level.

    Only the far end gets an arrowhead. The origin is left bare on purpose: the
    width line already puts an arrow AND a witness cap on this exact vertex, and
    a third mark there turns the corner to mush.

    The label sits centred ABOVE the diagonal's midpoint, horizontal. Rotating it
    to the diagonal's angle resamples badly at label sizes, and parking it off
    the tip forces the width line to give up its full width (see _depth_band).
    """
    ang = math.radians(angle_deg)
    dx, dy = math.cos(ang), -math.sin(ang)
    ox, oy = origin
    tip = (ox + dx * length, oy + dy * length)
    _hline(draw, [origin, tip], col, line_w, halo, halo_pad)
    _arrow(draw, tip, (dx, dy), arrow_len, arrow_hw, col, halo, halo_pad)

    tb = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    mx = (ox + tip[0]) / 2.0
    lx = mx - tw / 2.0
    # Clear the whole diagonal, not just the midpoint: the line RISES to the
    # right, so a label sitting above the midpoint gets speared by the arrowhead.
    ly = tip[1] - (label_gap + line_w + arrow_hw) - th
    if bounds:                      # keep it on the canvas, as the height label does
        bw, bh = bounds
        lx = max(0, min(bw - tw, lx))
        ly = max(0, min(bh - th, ly))
    draw.text((lx - tb[0], ly - tb[1]), text, font=font, fill=col,
              stroke_width=stroke_w, stroke_fill=halo)
    return tip


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
def _annotate_inset(img, width_value, height_value, style, depth_value=None):
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

    d_text = _label(depth_value, style.unit) if depth_value is not None else None
    d_tw = 0
    if d_text is not None:
        db = draw.textbbox((0, 0), d_text, font=font, stroke_width=stroke_w)
        d_tw = db[2] - db[0]

    # reserve an outer band between each line and the image edge for its label
    edge = max(4, round(min(iw, ih) * 0.02))
    left_band = min(edge + h_th + label_gap, iw // 3)      # left of the height line
    bottom_band = min(edge + w_th + label_gap, ih // 3)    # below the width line
    top_inset = min(edge + arrow_hw, ih // 3)
    right_inset = min(edge + arrow_hw, iw // 3)
    xl = left_band            # vertical (height) line x
    yb = ih - bottom_band     # horizontal (width) line y
    y_top = top_inset

    # DEPTH — widen the right band so the width line stops short and the
    # diagonal has somewhere to go; inset mode cannot expand the canvas.
    depth_len = 0.0
    if d_text is not None:
        depth_len, right_inset = _depth_fit(
            iw, ih, width_value, height_value, depth_value, style, left_band,
            right_inset, yb, edge, arrow_len, arrow_hw, label_gap, d_tw)
    x_end = iw - right_inset

    # === WIDTH — horizontal line, number BELOW the line ===
    _hline(draw, [(xl, yb), (x_end, yb)], col, line_w, halo, halo_pad)
    if style.show_witness:
        _hline(draw, [(xl, yb - cap), (xl, yb + cap)], col, line_w, halo, halo_pad)
        _hline(draw, [(x_end, yb - cap), (x_end, yb + cap)], col, line_w, halo, halo_pad)
    _arrow(draw, (xl, yb), (-1, 0), arrow_len, arrow_hw, col, halo, halo_pad)
    _arrow(draw, (x_end, yb), (1, 0), arrow_len, arrow_hw, col, halo, halo_pad)
    cx = (xl + x_end) / 2
    ty = yb + label_gap
    draw.text((cx - w_tw / 2 - wb[0], ty - wb[1]), w_text, font=font, fill=col,
              stroke_width=stroke_w, stroke_fill=halo)

    # === HEIGHT — vertical line, number to the LEFT (outer) of the line ===
    _hline(draw, [(xl, y_top), (xl, yb)], col, line_w, halo, halo_pad)
    if style.show_witness:
        _hline(draw, [(xl - cap, y_top), (xl + cap, y_top)], col, line_w, halo, halo_pad)
        _hline(draw, [(xl - cap, yb), (xl + cap, yb)], col, line_w, halo, halo_pad)
    _arrow(draw, (xl, y_top), (0, -1), arrow_len, arrow_hw, col, halo, halo_pad)
    _arrow(draw, (xl, yb), (0, 1), arrow_len, arrow_hw, col, halo, halo_pad)

    lbl = Image.new("RGBA", (max(1, h_tw), max(1, h_th)), (0, 0, 0, 0))
    ImageDraw.Draw(lbl).text((-hb[0], -hb[1]), h_text, font=font, fill=col,
                             stroke_width=stroke_w, stroke_fill=halo)
    lbl = lbl.rotate(90, expand=True)
    rw, rh = lbl.size
    px = int(xl - label_gap - rw)
    py = int((y_top + yb) / 2 - rh / 2)
    px = max(0, min(iw - rw, px))
    py = max(0, min(ih - rh, py))
    canvas.alpha_composite(lbl, (px, py))

    # === DEPTH — diagonal receding up-right from the bottom-right corner ===
    if d_text is not None:
        _draw_depth(draw, (x_end, yb), depth_len, style.depth_angle, font, d_text,
                    col, line_w, arrow_len, arrow_hw, label_gap, halo, halo_pad,
                    stroke_w, bounds=(iw, ih))

    return canvas


# ---------------------------------------------------------------------------
# MARGIN mode — expand the canvas, draw the dimensions outside the image
# ---------------------------------------------------------------------------
def _annotate_margin(img, width_value, height_value, style, depth_value=None):
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

    # DEPTH — the diagonal lives entirely to the RIGHT of the image here, so it
    # never covers the product; the canvas simply grows to fit it.
    d_text = _label(depth_value, style.unit) if depth_value is not None else None
    depth_len = run = 0.0
    d_tw = 0
    if d_text is not None:
        db = m.textbbox((0, 0), d_text, font=font)
        d_tw = db[2] - db[0]
        ang = math.radians(style.depth_angle)
        depth_len = _depth_length(iw, ih, width_value, height_value,
                                  depth_value, style)
        run = depth_len * math.cos(ang)

    left_margin = gap + arrow_hw + label_gap + h_label_w + label_gap + outer
    bottom_margin = gap + arrow_hw + label_gap + w_th + label_gap + outer
    top_margin = outer + arrow_hw
    right_margin = outer + arrow_hw
    if d_text is not None:
        right_margin += int(round(_depth_band(run, d_tw, arrow_hw, label_gap)))
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

    # === DEPTH — diagonal receding up-right from the width line's right end ===
    if d_text is not None:
        _draw_depth(draw, (img_right, y_dim), depth_len, style.depth_angle, font,
                    d_text, col, line_w, arrow_len, arrow_hw, label_gap,
                    bounds=(canvas_w, canvas_h))

    return canvas


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def annotate(image, width_value, height_value, style=None, depth_value=None):
    """Return a new RGBA image with width/height dimension lines drawn on it.

    In the default "inset" mode the output has the SAME pixel dimensions as the
    input; in "margin" mode the canvas is expanded.
    """
    style = style or DimStyle()
    img = image.convert("RGBA")
    if style.watermark:
        img = _apply_watermark(img, style)
    if (style.mode or "inset").lower() == "margin":
        return _annotate_margin(img, width_value, height_value, style, depth_value)
    return _annotate_inset(img, width_value, height_value, style, depth_value)
