"""Generate assets/cover.png — a 1400x1400 podcast cover (Spotify: 1400–3000px square, PNG/JPEG).

Run: python scripts/make_cover.py   (needs Pillow: pip install -e ".[dev]")
Replace the output with real artwork whenever you have it; the pipeline just uploads the file.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "assets" / "cover.png"
SIZE = 1400
BG = (13, 13, 13)
ACCENT = (107, 124, 255)
FG = (255, 255, 255)
MUTED = (136, 136, 136)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def font(size: int) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def main() -> None:
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)

    # Accent frame: a "context window" bracket motif
    m = 120
    d.rectangle([m, m, SIZE - m, SIZE - m], outline=ACCENT, width=14)
    d.rectangle([m + 40, m + 40, SIZE - m - 40, SIZE - m - 40], outline=(40, 40, 60), width=4)

    kicker = font(40)
    d.text((m + 90, m + 110), "DAILY  ·  AI  &  AGENTIC  ENGINEERING", font=kicker, fill=ACCENT)

    title = font(150)
    d.text((m + 84, 480), "Context", font=title, fill=FG)
    d.text((m + 84, 640), "Window", font=title, fill=FG)

    d.rectangle([m + 90, 830, m + 90 + 260, 842], fill=ACCENT)
    sub = font(54)
    d.text((m + 90, 880), "with FLINT & CLAIRE", font=sub, fill=MUTED)

    d.text((m + 90, SIZE - m - 150), "Every weekday · 25 min", font=font(40), fill=MUTED)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
