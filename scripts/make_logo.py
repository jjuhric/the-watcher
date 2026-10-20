"""Generates The Watcher's logo assets into web/: logo.svg, favicon.ico, icon-192.png, icon-512.png.
Run: python scripts/make_logo.py   (needs Pillow)"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "web"
S = 1024
BG, SCLERA, IRIS, IRIS_DARK, PUPIL = "#0f1722", "#eef4ff", "#2f8cff", "#1757b8", "#05080d"


def bezier(p0, p1, p2, n=80):
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1])
            for t in (i / n for i in range(n + 1))]


def render():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((0, 0, S - 1, S - 1), radius=210, fill=BG)

    upper = bezier((110, 512), (512, 170), (914, 512))
    lower = bezier((914, 512), (512, 854), (110, 512))
    almond = upper + lower
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).polygon(almond, fill=255)

    eye = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(eye)
    d.polygon(almond, fill=SCLERA)
    d.ellipse((512 - 182, 512 - 182, 512 + 182, 512 + 182), fill=IRIS_DARK)
    d.ellipse((512 - 160, 512 - 160, 512 + 160, 512 + 160), fill=IRIS)
    d.ellipse((512 - 82, 512 - 82, 512 + 82, 512 + 82), fill=PUPIL)
    d.ellipse((512 + 22, 512 - 70, 512 + 72, 512 - 20), fill="white")
    clipped = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    clipped.paste(eye, (0, 0), mask)
    img.alpha_composite(clipped)
    ImageDraw.Draw(img).line(almond + [almond[0]], fill=IRIS, width=14, joint="curve")
    return img


SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" role="img" aria-label="The Watcher">
  <defs><clipPath id="eye"><path d="M110 512Q512 170 914 512Q512 854 110 512Z"/></clipPath></defs>
  <rect width="1024" height="1024" rx="210" fill="{BG}"/>
  <g clip-path="url(#eye)">
    <rect width="1024" height="1024" fill="{SCLERA}"/>
    <circle cx="512" cy="512" r="182" fill="{IRIS_DARK}"/>
    <circle cx="512" cy="512" r="160" fill="{IRIS}"/>
    <circle cx="512" cy="512" r="82" fill="{PUPIL}"/>
    <circle cx="547" cy="467" r="25" fill="#fff"/>
  </g>
  <path d="M110 512Q512 170 914 512Q512 854 110 512Z" fill="none" stroke="{IRIS}" stroke-width="14" stroke-linejoin="round"/>
</svg>
"""

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    big = render()
    (OUT / "logo.svg").write_text(SVG, encoding="utf-8")
    big.resize((512, 512), Image.LANCZOS).save(OUT / "icon-512.png")
    big.resize((192, 192), Image.LANCZOS).save(OUT / "icon-192.png")
    big.save(OUT / "favicon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", sorted(p.name for p in OUT.glob("*") if p.suffix in (".svg", ".ico", ".png")))
