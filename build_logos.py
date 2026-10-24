#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10", "requests>=2.31"]
# ///
"""Build square 660x660 PNG logos for Google Wallet passes.

Reads logos/sources.json (merchant name -> source), writes logos/<slug>.png.
A source is an image URL (PNG/JPG/ICO/SVG), "favicon:<domain>" for Google's
favicon service, or null. Missing or low-resolution sources fall back to a
generated monogram. SVG rendering needs `rsvg-convert` (librsvg).

Usage: uv run build_logos.py [--only NAME ...]
"""
import argparse
import hashlib
import io
import json
import math
import pathlib
import subprocess
import sys

import requests
from PIL import Image, ImageDraw, ImageFont

from import_to_wallet import colour, slug

SIZE = 660
MIN_SOURCE = 120  # smaller sources look blurry, use a monogram instead
HERE = pathlib.Path(__file__).resolve().parent
LOGOS = HERE / "logos"
UA = {"User-Agent": "klarna2google/0.1 (https://github.com/carroarmato0/klarna2google)"}
FAVICON = "https://t1.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=https://{}&size=256"


def fetch(source):
    url = FAVICON.format(source.split(":", 1)[1]) if source.startswith("favicon:") else source
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    data = r.content
    if data.lstrip()[:5] in (b"<?xml", b"<svg ") or b"<svg" in data[:1000]:
        data = subprocess.run(["rsvg-convert", "-w", "1200", "--keep-aspect-ratio"],
                              input=data, capture_output=True, check=True).stdout
    img = Image.open(io.BytesIO(data))
    if getattr(img, "n_frames", 1) > 1:  # .ico: pick the largest frame
        img = max((img.seek(i) or img.copy() for i in range(img.n_frames)), key=lambda f: f.width)
    return img.convert("RGBA")


def trim(img):
    """Crop transparent or white margins."""
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    flat = Image.alpha_composite(bg, img).convert("RGB")
    diff = Image.eval(flat, lambda p: 255 - p).getbbox()
    alpha = img.getchannel("A").getbbox()
    box = diff or alpha
    return img.crop(box) if box else img


def place(img, fill=0.86):
    """Center on a white square, sized so the logo's corners stay inside the
    circle Wallet crops it to: square logos get ~60%, wide ones nearly full width."""
    img = trim(img)
    f = fill * SIZE / math.hypot(*img.size)
    img = img.resize((max(1, round(img.width * f)), max(1, round(img.height * f))), Image.LANCZOS)
    canvas = Image.new("RGBA", (SIZE, SIZE), (255, 255, 255, 255))
    canvas.alpha_composite(img, ((SIZE - img.width) // 2, (SIZE - img.height) // 2))
    return canvas.convert("RGB")


def monogram(name):
    words = [w for w in name.replace("-", " ").split() if w[0].isalnum()]
    text = "".join(w[0] for w in words[:2]).upper() or "?"
    img = Image.new("RGB", (SIZE, SIZE), colour(name))
    draw = ImageDraw.Draw(img)
    font = None
    try:
        system_bold = subprocess.run(["fc-match", "-f", "%{file}", "sans:bold"],
                                     capture_output=True, text=True).stdout
    except OSError:
        system_bold = ""
    for candidate in (system_bold, "DejaVuSans-Bold.ttf", "Arial Bold.ttf", "Arial_Bold.ttf"):
        try:
            font = ImageFont.truetype(candidate, 280 if len(text) == 1 else 230)
            break
        except (OSError, ValueError):
            continue
    font = font or ImageFont.load_default(size=220)
    draw.text((SIZE / 2, SIZE / 2), text, fill="white", font=font, anchor="mm")
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", help="only rebuild these merchants")
    args = ap.parse_args()

    sources = json.loads((LOGOS / "sources.json").read_text())
    for name, source in sorted(sources.items()):
        if args.only and name not in args.only:
            continue
        how = "monogram"
        img = None
        if source:
            try:
                raw = fetch(source)
                if max(trim(raw).size) >= MIN_SOURCE or source.endswith(".svg") or ".svg" in source:
                    img, how = place(raw), "logo"
                else:
                    how = f"monogram (source only {max(raw.size)}px)"
            except Exception as e:  # noqa: BLE001 - any failure means fall back
                how = f"monogram ({type(e).__name__}: {str(e)[:60]})"
        (img or monogram(name)).save(LOGOS / f"{slug(name)}.png", optimize=True)
        print(f"{name:<20} {how}", file=sys.stderr)


if __name__ == "__main__":
    main()
