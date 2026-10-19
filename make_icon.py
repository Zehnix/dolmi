"""Draws the Dolmi icon (assets/dolmi-2.ico + dolmi.png, and Mac/assets/dolmi.icns for Dolmi.app; the "-2" makes Windows drop its cached old icon): "Voice D", a D whose counter holds sound bars.
Geometry is the 160-unit grid from the Figma concept. Run once: python make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024            # drawn large, then downscaled for smooth edges
U = S / 160         # one Figma unit
TILE, EDGE, TEAL = (18, 21, 28), (53, 59, 73), (45, 212, 191)

def u(*v):
    return [round(x * U) for x in v]

def draw():
    icon = Image.new("RGBA", (S, S))
    d = ImageDraw.Draw(icon)
    d.rounded_rectangle(u(0, 0, 160, 160), radius=round(36 * U), fill=TILE)
    # border keeps the dark tile visible on a dark taskbar
    d.rounded_rectangle(u(1, 1, 159, 159), radius=round(35 * U), outline=EDGE, width=round(2 * U))

    # the D: a stem from x=46 to 78, then a half-circle of radius 44 centred on (78, 80)
    d.rectangle(u(46, 36, 78, 124), fill=TEAL)
    d.pieslice(u(34, 36, 122, 124), start=-90, end=90, fill=TEAL)

    # three sound bars cut out of the D
    for x, top, h in ((62, 66, 28), (78, 56, 48), (94, 64, 32)):
        d.rounded_rectangle(u(x, top, x + 9, top + h), radius=round(4.5 * U), fill=TILE)
    return icon

if __name__ == "__main__":
    out = Path(__file__).with_name("assets")
    out.mkdir(exist_ok=True)
    img = draw()
    img.resize((256, 256), Image.LANCZOS).save(out / "dolmi.png")
    img.save(out / "dolmi-2.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("wrote", out / "dolmi-2.ico")
    mac = Path(__file__).with_name("Mac") / "assets"
    if mac.is_dir():
        img.save(mac / "dolmi.icns")   # Pillow writes every size macOS needs, up to 1024 px
        print("wrote", mac / "dolmi.icns")
