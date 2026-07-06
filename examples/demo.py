"""Render a sample annotated image without needing the server or a real photo.

    python examples/demo.py            # -> examples/demo_out.png
"""
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from annotate import DimStyle, annotate, load_font, to_bytes  # noqa: E402

# A stand-in "product" image (a rounded bottle-ish shape on a tint).
img = Image.new("RGB", (620, 820), "#F1ECFA")
d = ImageDraw.Draw(img)
d.rounded_rectangle([210, 70, 410, 150], radius=24, fill="#C9B8EC")      # cap
d.rounded_rectangle([120, 150, 500, 760], radius=60, fill="#563D96")     # body
f = load_font(46)
d.text((310, 455), "NAWAQIS", fill="white", font=f, anchor="mm")

here = os.path.dirname(os.path.abspath(__file__))

# inset (default): output keeps the SAME size as the input
out = annotate(img, 18, 24, DimStyle(unit="cm"))
data, _ = to_bytes(out, "png")
with open(os.path.join(here, "demo_out.png"), "wb") as fh:
    fh.write(data)
print(f"inset : input {img.size[0]}x{img.size[1]} -> output {out.size[0]}x{out.size[1]}  (same size)")

# margin: adds a white border (larger output)
outm = annotate(img, 18, 24, DimStyle(unit="cm", mode="margin"))
datam, _ = to_bytes(outm, "png")
with open(os.path.join(here, "demo_out_margin.png"), "wb") as fh:
    fh.write(datam)
print(f"margin: input {img.size[0]}x{img.size[1]} -> output {outm.size[0]}x{outm.size[1]}  (expanded)")
