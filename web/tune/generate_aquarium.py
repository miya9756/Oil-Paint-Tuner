"""Rebuild the Studio's original fish illustrations with the actual oil-paint engine.

Run from the repo root: python web/tune/generate_aquarium.py
No downloaded imagery. The RGB artwork is painted by oilpaint.pipeline.paint; its
antialiased silhouette is restored afterward so the fish can swim on transparent planes.
The three 512 x 280 tiles face right: vermilion koi, gold, and pearl.
"""
from pathlib import Path
import math
import sys

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from oilpaint.pipeline import PaintConfig, paint

W, H, AA = 512, 280, 3


def illustration(kind):
    image = Image.new("RGBA", (W * AA, H * AA))
    draw = ImageDraw.Draw(image)
    colors = [("#e1d0a2", "#9c4a2d", "#f5e7c2"),
              ("#ca8736", "#8f4c26", "#f2d58e"),
              ("#b7ccbe", "#506f68", "#ebebce")]
    body, shade, light = colors[kind]

    def polygon(points, color):
        draw.polygon([(round(x * AA), round(y * AA)) for x, y in points], fill=color)

    def ellipse(bounds, color):
        draw.ellipse(tuple(round(x * AA) for x in bounds), fill=color)

    def curve(points, color, width=1):
        draw.line([(round(x * AA), round(y * AA)) for x, y in points],
                  fill=color, width=round(width * AA), joint="curve")

    # Long, folded fins. Their thin veins survive as loaded brush marks.
    polygon([(200, 145), (149, 112), (96, 61), (24, 44), (51, 103),
             (112, 140), (63, 181), (26, 239), (109, 210), (166, 166)], shade)
    polygon([(188, 141), (113, 95), (40, 58), (78, 104), (134, 140),
             (76, 186), (43, 224), (109, 192)], body)
    for i in range(8):
        curve([(185, 142), (116 - i * 5, 112 - i * 4), (40 + i * 4, 55 + i * 7)], light, .8)
        curve([(181, 146), (110 - i * 3, 176 + i * 3), (42 + i * 6, 226 - i * 3)], light, .7)
    polygon([(208, 118), (245, 62), (285, 46), (329, 93), (365, 118)], shade)
    polygon([(213, 116), (254, 75), (284, 57), (318, 97)], body)
    polygon([(253, 166), (280, 224), (338, 238), (320, 181)], shade)
    polygon([(258, 166), (288, 213), (325, 225), (308, 178)], body)

    # Elliptical body with a pointed, softly rounded snout.
    outline = []
    for i in range(101):
        t = i / 100 * math.tau
        x = 299 + 147 * math.cos(t)
        y = 140 + 58 * math.sin(t) * (.77 + .23 * math.cos(t))
        outline.append((x, y))
    polygon(outline, shade)
    for i in range(24):
        f = i / 24
        rgb = tuple(round(a * (1 - f) + b * f) for a, b in
                    zip(ImageColor(shade), ImageColor(body)))
        ellipse((169 + i * 1.1, 85 + i * .25, 438 - i * .4, 191 - i * 1.6), rgb)
    if kind == 0:
        for bounds in [(226, 92, 277, 125), (281, 109, 324, 153), (348, 88, 386, 126), (367, 145, 408, 176)]:
            ellipse(bounds, "#bc542e")
        polygon([(240, 103), (255, 91), (280, 105), (269, 140), (245, 144)], "#c36239")
    elif kind == 1:
        ellipse((285, 98, 397, 112), "#e8b85d")
    for row in range(5):
        for col in range(12):
            x, y = 205 + col * 14 + (row % 2) * 7, 110 + row * 13
            if ((x - 299) / 118) ** 2 + ((y - 140) / 43) ** 2 < .9:
                curve([(x, y - 3), (x + 4, y), (x, y + 4)], light if row < 2 else body, .8)
    # Pectoral fin, gill fold, mouth, and a small dark eye.
    polygon([(347, 151), (315, 178), (279, 192), (316, 190), (355, 167)], shade)
    for i in range(5):
        curve([(350, 155), (322, 171 + i * 3), (288 + i * 5, 188)], light, .8)
    curve([(389, 113), (379, 127), (377, 145), (387, 160)], shade, 2)
    curve([(427, 142), (442, 140)], shade, 1.5)
    ellipse((402, 120, 415, 133), light)
    ellipse((405, 122, 414, 131), "#25332e")
    ellipse((408, 122, 411, 125), "#ffedce")
    return image.resize((W, H), Image.Resampling.LANCZOS)


def ImageColor(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def main():
    atlas = Image.new("RGBA", (W * 3, H))
    cfg = PaintConfig(target_n=6500, min_cell=2, max_cell=12, base_block=8,
                      palette="impressionist", palette_strength=.25,
                      broken_color=.12, kappa=1.3, aniso_max=4,
                      flat_theta_deg=0, impasto_depth=.19, impasto_relief=.35,
                      gloss=.16, light_elev_deg=55, seed=27)
    for kind in range(3):
        source = illustration(kind)
        background = Image.new("RGB", (W, H), "#7b927b")
        background.paste(source, mask=source.getchannel("A"))
        rgb = np.asarray(background, dtype=np.float32) / 255
        result, info, _ = paint(rgb, cfg)
        painted = Image.fromarray(np.uint8(np.clip(result, 0, 1) * 255 + .5)).convert("RGBA")
        painted.putalpha(source.getchannel("A"))
        atlas.paste(painted, (kind * W, 0))
        print(f"Fish {kind + 1}: painted with seed {cfg.seed}", flush=True)
    path = Path(__file__).with_name("aquarium-fish.png")
    atlas.save(path, optimize=True)
    print(f"Wrote {path.name}: {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
