#!/usr/bin/env python
"""How much each Palette and Flow trim actually moves the picture, as a number.

    conda run -n 4dre python scripts/knob_impact.py [IMAGE ...] [--side 800]

This is what decided which rows stay on the tuner's front cards (`FINE` in
web/tune/index.html carries the table it printed). Two measurements, because the two
cards attach to the pipeline in different places:

  * PALETTE attaches after the geometry, so the whole effect of a trim is a change of
    stroke colour. The figure is the area-weighted mean OKLab dE over the strokes between
    a preset as written and the same preset with ONE trim at the end of its range. Exact
    -- it re-runs the grade `plan` runs, on the same buffer -- and, checked against full
    renders on the sample, within ~10% of the per-pixel dE of the finished painting.
  * FLOW attaches inside step 3 of `strokes.from_cells`, so it moves marks rather than
    recolouring them, and a pixel dE cannot tell "the marks moved" from "the look
    changed". The figures are the geometry: mean stroke turn in degrees, mean |log| change
    of elongation, and mean centre displacement in stroke radii.

A trim's number is worth reading next to the first row of its table, which is what
switching the preset on at all does. For scale, an OKLab dE of ~0.02 spread over a whole
painting is about where a side-by-side stops being obvious.
"""

import argparse
import collections
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oilpaint import palette as pm, reference as rm  # noqa: E402
from oilpaint.flow import FLOWS  # noqa: E402
from oilpaint.image import load_image  # noqa: E402
from oilpaint.palette import _to_oklab  # noqa: E402
from oilpaint.pipeline import PaintConfig, plan  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_IMAGES = ["web/tune/sample.jpg", "data/samples/photo.jpg",
                  "data/samples/museum_type.jpg", "data/samples/grain_example.jpg"]

# (label, knob, values). One line per trim; the values are the ends of its schema range
# plus the half-way point people actually drag to.
PALETTE_TRIMS = [
    ("palette_strength", (0.5, 0.0)), ("warm_cool", (-1.0, 1.0, 0.5)),
    ("chroma", (-0.6, 0.8, 0.4)), ("value_compress", (1.0, 0.5)),
    ("pigment", (-0.6, 0.6, 0.3)), ("broken_color", (1.0, 0.5)),
    ("hue_rotate", (-180.0, 180.0, 60.0)), ("hue_boost", (-1.0, 1.0, 0.5)),
]
FLOW_TRIMS = [
    ("flow_strength", (0.5,)), ("flow_coh", (-0.6, 0.6)), ("flow_scale", (-0.75, 2.0)),
    ("flow_rot", (-90.0, 90.0, 30.0)), ("flow_drift", (-1.0, 1.0, 0.5)),
]


def load(path, side):
    from PIL import Image
    img = load_image(path)
    h, w = img.shape[:2]
    s = side / max(h, w)
    pil = Image.fromarray((img * 255 + 0.5).astype("uint8"))
    pil = pil.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    return np.asarray(pil, dtype="float32") / 255.0


def graded(sb, cfg):
    """Mirror of the grade `pipeline.plan` applies at its end, on an ungraded buffer."""
    gp = pm.params_for(cfg)
    rp = rm.params_for(cfg)
    if rp is not None:
        gp = gp if gp is not None else pm.block_for(cfg)
        gp["xfer"] = rm.fit(sb.rgb, rp, linear=cfg.linear)
    return sb.rgb if gp is None else pm.grade_strokes(sb.rgb, sb.phase, gp, linear=cfg.linear)


def lab(c):
    return np.stack(_to_oklab(c.astype(np.float64)), 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="*", default=[os.path.join(ROOT, p) for p in DEFAULT_IMAGES])
    ap.add_argument("--side", type=int, default=800)
    a = ap.parse_args()

    pal = collections.defaultdict(list)     # setting -> [dE per image x preset]
    flo = collections.defaultdict(list)     # setting -> [(turn, elong, disp)]
    for path in a.images:
        img = load(path, a.side)
        # The palette half: ONE plan, ungraded, then every grade on the same buffer.
        sb, _, _ = plan(img, PaintConfig())
        w = (sb.r_major * sb.r_minor).astype(np.float64)
        w /= w.sum()

        def de(base, var):
            d = np.linalg.norm(lab(graded(sb, base)) - lab(graded(sb, var)), axis=1)
            return float((d * w).sum())

        for name in pm.PALETTES:
            if name == "none":
                continue
            base = PaintConfig(palette=name)
            pal["palette on"].append(de(PaintConfig(), base))
            for knob, vals in PALETTE_TRIMS:
                for v in vals:
                    pal[f"{knob}={v:g}"].append(de(base, PaintConfig(palette=name, **{knob: v})))
            for ref in rm.NAMES:
                if ref == "none":
                    continue
                rb = PaintConfig(palette=name, reference=ref)
                pal["reference on"].append(de(base, rb))
                pal["reference_strength=0.5"].append(
                    de(rb, PaintConfig(palette=name, reference=ref, reference_strength=0.5)))

        # The flow half: a plan per setting, compared stroke for stroke -- the leaves,
        # radii and order are pinned identical, so the arrays line up.
        for name in FLOWS:
            if name == "none":
                continue
            base = PaintConfig(flow=name)
            s0 = plan(img, base)[0]

            def geo(var, s0=s0):
                s1 = plan(img, var)[0]
                turn = np.degrees(np.abs((s0.theta - s1.theta + np.pi / 2) % np.pi - np.pi / 2))
                el = np.abs(np.log((s1.r_major / s1.r_minor) / (s0.r_major / s0.r_minor)))
                disp = np.hypot(s0.x - s1.x, s0.y - s1.y) / s0.r_major
                return float(turn.mean()), float(el.mean()), float(disp.mean())

            flo[f"{name}: flow on"].append(geo(PaintConfig()))
            for knob, vals in FLOW_TRIMS:
                for v in vals:
                    flo[f"{name}: {knob}={v:g}"].append(geo(PaintConfig(flow=name, **{knob: v})))

    print(f"\nPALETTE  mean OKLab dE over strokes, {len(a.images)} image(s) x "
          f"{len(pm.PALETTES) - 1} presets (min .. max)")
    for k, v in sorted(pal.items(), key=lambda kv: -np.mean(kv[1])):
        print(f"  {k:28s} {np.mean(v):.4f}   ({np.min(v):.4f} .. {np.max(v):.4f})")
    print("\nFLOW     mean turn (deg) / elongation change / displacement (radii)")
    for k, v in flo.items():
        m = np.mean(v, axis=0)
        print(f"  {k:28s} {m[0]:5.1f} deg   {m[1]:.3f}   {m[2]:.3f}")


if __name__ == "__main__":
    main()
