"""Behavioural checks for the depth diagonal and its sanity warning.

Run from the repo root:  python examples/check_depth.py

Deliberately asserts BEHAVIOUR, not pixel checksums. The Render container renders
a halo'd glyph slightly differently from a local box (~0.05% of pixels, purely
cosmetic, and identical for old and new code), so a checksum test would fail in
production for a reason that has nothing to do with correctness.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw  # noqa: E402

import annotate as A  # noqa: E402

FAILURES = []


def check(label, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        FAILURES.append(label)


def sample(w=900, h=700):
    im = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    ImageDraw.Draw(im).rounded_rectangle([w // 6, h // 8, w * 5 // 6, h * 7 // 8],
                                         radius=30, fill=(38, 90, 160, 255))
    return im


def width_line_end(iw, ih, w, h, d, style):
    """Where the width line stops, in px - the thing the label move protects."""
    fs, _lw, arrow_len, arrow_hw, _gap = A._sizes(iw, ih, style)
    font = A.load_font(fs)
    stroke = A._text_stroke(fs)
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    label_gap = max(4, round(fs * 0.4))
    edge = max(4, round(min(iw, ih) * 0.02))
    hb = draw.textbbox((0, 0), A._label(h, style.unit), font=font, stroke_width=stroke)
    wb = draw.textbbox((0, 0), A._label(w, style.unit), font=font, stroke_width=stroke)
    left_band = min(edge + (hb[3] - hb[1]) + label_gap, iw // 3)
    base_right = min(edge + arrow_hw, iw // 3)
    if d is None:
        return iw - base_right
    yb = ih - min(edge + (wb[3] - wb[1]) + label_gap, ih // 3)
    db = draw.textbbox((0, 0), A._label(d, style.unit), font=font, stroke_width=stroke)
    _len, band = A._depth_fit(iw, ih, w, h, d, style, left_band, base_right, yb,
                              edge, arrow_len, arrow_hw, label_gap, db[2] - db[0])
    return iw - band


def main():
    src = sample()
    st = A.DimStyle(unit="cm")

    print("geometry")
    check("inset keeps the input size",
          A.annotate(src, 30, 45, st, 20).size == src.size)
    check("inset keeps the input size with no depth",
          A.annotate(src, 30, 45, st).size == src.size)
    check("margin grows the canvas",
          A.annotate(src, 30, 45, A.DimStyle(unit="cm", mode="margin"), 20).size[0] > src.size[0])
    check("a deeper item draws a longer diagonal",
          A._depth_length(900, 700, 30, 45, 40, st) > A._depth_length(900, 700, 30, 45, 5, st))
    check("the diagonal is capped by depth_max",
          A._depth_length(900, 700, 30, 45, 9999, st) <= st.depth_max * 700 + 1)

    print("width line is not eaten by the depth label")
    mm = A.DimStyle(unit="mm")
    for depth, floor in ((44, 0.75), (124.4, 0.68), (1244, 0.68)):
        end = width_line_end(1000, 1000, 131, 121, depth, mm)
        check(f"depth={depth} leaves the width line at {100 * end / 1000:.0f}% "
              f"(floor {100 * floor:.0f}%)", end >= floor * 1000)
    check("no depth leaves the width line near the edge",
          width_line_end(1000, 1000, 131, 121, None, mm) >= 950)

    print("sanity warning")
    check("9.5x depth warns", A.depth_warning(131, 121, 1244) is not None)
    check("0.95x depth does not warn", A.depth_warning(131, 121, 124.4) is None)
    check("0.34x depth does not warn", A.depth_warning(131, 121, 44) is None)
    check("no depth never warns", A.depth_warning(131, 121, None) is None)
    check("max_ratio is honoured", A.depth_warning(131, 121, 400, max_ratio=2) is not None)
    check("the ratio rule is shared with the drawing rule",
          abs(A._depth_ratio(131, 121, 1244) - 1244 / 131) < 1e-9)

    try:
        from fastapi.testclient import TestClient
        import main as app_main
    except Exception as exc:  # pragma: no cover - optional locally
        print(f"\n(skipping HTTP checks: {exc})")
    else:
        print("http")
        c = TestClient(app_main.app)
        buf = __import__("io").BytesIO()
        src.convert("RGB").save(buf, "PNG")
        png = buf.getvalue()
        hdr = {"Content-Type": "image/png"}

        r = c.post("/annotate?width=131&height=121&depth=1244&unit=mm", content=png, headers=hdr)
        check("out-of-proportion depth still renders", r.status_code == 200)
        check("binary response carries X-Dimension-Warning",
              "x-dimension-warning" in {k.lower() for k in r.headers})

        r = c.post("/annotate?width=131&height=121&depth=124.4&unit=mm", content=png, headers=hdr)
        check("in-proportion depth has no warning header",
              "x-dimension-warning" not in {k.lower() for k in r.headers})

        r = c.post("/annotate?width=131&height=121&depth=1244&unit=mm&response=base64",
                   content=png, headers=hdr)
        check("base64 response carries a warnings array",
              r.status_code == 200 and r.json().get("warnings"))

        r = c.post("/annotate?width=131&height=121&unit=mm&response=base64",
                   content=png, headers=hdr)
        check("no depth means no warnings key", "warnings" not in r.json())

        r = c.post("/annotate?width=131&height=121&depth=1244&unit=mm&strict=1",
                   content=png, headers=hdr)
        check("strict=1 rejects with 400", r.status_code == 400)

        r = c.post("/annotate?width=131&height=121&depth=124.4&unit=mm&strict=1",
                   content=png, headers=hdr)
        check("strict=1 passes good data", r.status_code == 200)

        r = c.post("/annotate?width=30&height=45&depth=abc", content=png, headers=hdr)
        check("non-numeric depth is a 400", r.status_code == 400)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
