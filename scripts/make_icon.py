"""Generate the optional Windows icon used by scripts/build_exe.ps1.

The icon is deliberately text-free so it renders consistently without relying
on a particular Chinese font being installed.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

SIZE = 256
INK = "#1e1d1a"
PAPER = "#f3f0e9"
ACCENT = "#b23a2b"

image = Image.new("RGBA", (SIZE, SIZE), INK)
draw = ImageDraw.Draw(image)

# Three ruled lines, like a page from a notebook.
draw.rounded_rectangle((56, 62, 200, 74), radius=6, fill=PAPER)
draw.rounded_rectangle((56, 110, 176, 122), radius=6, fill=PAPER)
draw.rounded_rectangle((56, 158, 188, 170), radius=6, fill=PAPER)
# A red correction mark.
draw.rounded_rectangle((56, 198, 126, 210), radius=5, fill=ACCENT)
draw.ellipse((168, 186, 204, 222), outline=ACCENT, width=5)
draw.line((177, 195, 195, 213), fill=ACCENT, width=4)
draw.line((195, 195, 177, 213), fill=ACCENT, width=4)

image.save(
    ASSETS / "cuotibu.ico",
    format="ICO",
    sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
print(f"Wrote {ASSETS / 'cuotibu.ico'}")
