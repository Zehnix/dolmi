"""Draws the Microsoft Store package images (tiles, Start/taskbar icon, Store logo, splash) from the Dolmi
icon. Uses make_icon.draw() (the 1024 px master) when it imports, else assets/dolmi.png.

    python installer/msix/make_assets.py <out dir>      (build-msix.ps1 calls it; out = <layout>\\Assets)

Writes every scale Windows asks for (scale-100..400) plus the unplated taskbar sizes (targetsize-16..256);
build-msix.ps1 indexes them into resources.pri with makepri, so Windows picks the sharp one.
"""
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SPLASH_BG = (0xF2, 0xEC, 0xDF, 255)      # the window's start colour (main.py background_color)
SCALES = (100, 125, 150, 200, 400)
TARGET_SIZES = (16, 24, 32, 48, 256)

# name: (width, height at scale-100, icon height as a share of the image height, background)
IMAGES = {
    "Square44x44Logo": (44, 44, 1.0, None),
    "Square150x150Logo": (150, 150, 0.66, None),
    "Wide310x150Logo": (310, 150, 0.66, None),
    "StoreLogo": (50, 50, 1.0, None),
    "SplashScreen": (620, 300, 0.5, SPLASH_BG),
}


def master():
    """The icon at 1024 px."""
    sys.path.insert(0, str(ROOT))
    try:
        import make_icon
        return make_icon.draw()
    except Exception:
        return Image.open(ROOT / "assets" / "dolmi.png").convert("RGBA").resize((1024, 1024), Image.LANCZOS)


def compose(icon, w, h, share, bg):
    canvas = Image.new("RGBA", (w, h), bg or (0, 0, 0, 0))
    side = max(1, round(h * share))
    small = icon.resize((side, side), Image.LANCZOS)
    canvas.alpha_composite(small, ((w - side) // 2, (h - side) // 2))
    return canvas


def write_all(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    icon = master()
    written = []
    for name, (w, h, share, bg) in IMAGES.items():
        for s in SCALES:
            img = compose(icon, round(w * s / 100), round(h * s / 100), share, bg)
            path = out / f"{name}.scale-{s}.png"
            img.save(path)
            written.append(path)
    for t in TARGET_SIZES:                       # taskbar, Alt+Tab, Start list: no plate behind the icon
        for suffix in ("", "_altform-unplated"):
            path = out / f"Square44x44Logo.targetsize-{t}{suffix}.png"
            compose(icon, t, t, 1.0, None).save(path)
            written.append(path)
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "msix-assets"
    files = write_all(target)
    print(f"wrote {len(files)} images to {target}")
