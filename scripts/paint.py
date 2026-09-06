#!/usr/bin/env python
"""CLI: one image in, one painting out.

    conda run -n 4dre python scripts/paint.py IN.jpg OUT.png [--target-n 8000] ...

Every PaintConfig field is exposed as --kebab-case, so an A/B arm is a flag.
"""

import argparse
import json
import os
import sys
from dataclasses import fields

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oilpaint.image import load_image, save_image  # noqa: E402
from oilpaint.pipeline import PaintConfig, paint  # noqa: E402
from oilpaint.regions import MAX_REGIONS  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--strokes-out", help="write the stroke buffer as .npz")
    ap.add_argument("--max-side", type=int, default=0, help="downscale longest side first")
    # Mirrors WORK_MIN_SIDE in web/tune/index.html, which carries the reasoning. Short
    # version: below ~1000px the stroke budget is unreachable, so the quadtree stops
    # allocating and degenerates into a uniform min_cell grid. A small source is enlarged
    # onto a working canvas rather than painted at its own size. 0 disables.
    ap.add_argument("--min-side", type=int, default=1000,
                    help="enlarge longest side up to this before painting (0 = off)")
    # The foveal map cannot be a --flag-with-a-number, so it is not in the loop below.
    # BLACK is high importance, matching the brush on the tuner page, where the map is
    # painted onto white. --foveal-invert reads a white-is-high map instead.
    ap.add_argument("--foveal", help="importance map image; BLACK = spend strokes here")
    ap.add_argument("--foveal-invert", action="store_true",
                    help="read the map as white = spend strokes here")
    # Where the flow field's swirls go, and the same story as --foveal: a LIST of points is
    # not a number, so it cannot be a PaintConfig field with a slider. Fractions of the
    # image, which is what a click on the tuner page produces and what makes the placement
    # survive a resize. Only 'starry' (or any swirl-kind flow) has vortices to place.
    ap.add_argument("--vortices",
                    help="swirl centres for --flow starry, as fractions of the image: "
                         "\"0.3,0.4 0.72,0.55\". Omit for the preset's own spiral.")
    # ...and the same list aimed at ONE passage. It cannot ride in `--region` for the reason
    # the flag above cannot be a PaintConfig field: a list of points is not a number. A
    # region with no set of its own inherits these (flow.centres_for), so this flag is only
    # for the passage whose swirls sit somewhere the picture's do not.
    ap.add_argument("--region-vortices", action="append", default=[], metavar="N:x,y x,y",
                    help="swirl centres for ONE region, e.g. \"1:0.3,0.2 0.7,0.3\". A "
                         "region without its own uses --vortices. Repeatable.")
    # Region-wise look, and the third input of the same kind: a mask is an image and a
    # per-region parameter set is not a number, so neither fits in PaintConfig. The mask is
    # read through regions.LEGEND -- paint region 3 in pure blue -- and every region names
    # the same colour AND flow fields the flags above do, so `--region 1:palette=zorn` is
    # the panel aimed at one passage and `--region 1:flow=starry` is the brush aimed at it.
    # Those two sets and no more: see regions.REGION_PARAMS.
    ap.add_argument("--regions", metavar="MASK.png",
                    help="region mask; BLACK is the base and each other legend colour "
                         "(red green blue yellow magenta cyan white) is a region")
    ap.add_argument("--region", action="append", default=[], metavar="N:k=v,k=v",
                    help="colour and flow settings for one region, e.g. "
                         "\"1:palette=zorn,hue_rotate=25\" or \"1:flow=starry,"
                         "flow_coh=-0.3\". Repeatable.")

    for f in fields(PaintConfig):
        flag = "--" + f.name.replace("_", "-")
        if f.type is bool or isinstance(f.default, bool):
            ap.add_argument(flag, dest=f.name, action="store_true", default=None)
            ap.add_argument("--no-" + f.name.replace("_", "-"), dest=f.name,
                            action="store_false", default=None)
        elif f.name == "tau":
            ap.add_argument(flag, type=float, default=None)
        else:
            ap.add_argument(flag, type=type(f.default), default=None)

    a = ap.parse_args()
    cfg = PaintConfig(**{f.name: getattr(a, f.name) for f in fields(PaintConfig)
                         if getattr(a, f.name) is not None})

    img = load_image(a.src)
    # Down first, then up: --max-side is a cost ceiling and --min-side a quality floor, so
    # asking for both is not contradictory, and a --max-side under --min-side is the caller
    # saying "cheap" and is left to win.
    scale = None
    if a.max_side and max(img.shape[:2]) > a.max_side:
        scale = a.max_side / max(img.shape[:2])
    elif a.min_side and max(img.shape[:2]) < a.min_side:
        scale = a.min_side / max(img.shape[:2])
    if scale is not None:
        from PIL import Image
        import numpy as np
        h, w = img.shape[:2]
        pil = Image.fromarray((img * 255 + 0.5).astype("uint8"))
        pil = pil.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        img = np.asarray(pil, dtype="float32") / 255.0

    # Resampled to the image AFTER the scaling above, so a map painted at any size lines
    # up. Bilinear: this is a smooth weight field, not a label mask, and the quadtree reads
    # it as a per-cell mean anyway.
    foveal = None
    if a.foveal:
        from PIL import Image
        import numpy as np
        m = Image.open(a.foveal).convert("L").resize(
            (img.shape[1], img.shape[0]), Image.BILINEAR)
        m = np.asarray(m, dtype="float32") / 255.0
        foveal = m if a.foveal_invert else 1.0 - m
        if cfg.foveal_strength <= 0.0:
            print("note: --foveal given but --foveal-strength is 0, so the map is ignored",
                  file=sys.stderr)

    # "0.3,0.4 0.72,0.55" -> [(0.3, 0.4), (0.72, 0.55)]. Fractions, not pixels, so the same
    # argument places the same swirls at any --max-side.
    def _points(text, flag):
        try:
            pts = [tuple(float(t) for t in pair.split(",")) for pair in text.split()]
            if any(len(q) != 2 for q in pts):
                raise ValueError("each point needs exactly an x and a y")
            return pts
        except ValueError as e:
            ap.error(f"{flag}: {e}; expected \"x,y x,y ...\" in [0,1]")

    vortices = None
    if a.vortices:
        vortices = _points(a.vortices, "--vortices")
    # A per-region set turns the plain list into the MAP shape `plan` also takes, with the
    # bare list becoming region 0's. Done here rather than in the engine so that the one
    # place a shape is chosen is the one place both flags are read.
    if a.region_vortices:
        vmap = {0: vortices or []}
        for spec in a.region_vortices:
            head, _, body = spec.partition(":")
            try:
                rid = int(head)
            except ValueError:
                ap.error(f"--region-vortices {spec!r}: {head!r} is not a region id")
            if not 0 <= rid < MAX_REGIONS:
                ap.error(f"--region-vortices {spec!r}: region id outside "
                         f"0..{MAX_REGIONS - 1}")
            vmap[rid] = _points(body, f"--region-vortices {spec!r}")
        vortices = vmap

    # NEAREST, and this is the one line where the foveal map's bilinear would be a bug:
    # these are label ids, and interpolating between region 2 and region 4 invents a
    # region 3 along every boundary in the picture.
    regions = None
    if a.regions or a.region:
        from PIL import Image
        import numpy as np
        from oilpaint.regions import REGION_PARAMS, labels_from_image
        if not a.regions:
            ap.error("--region needs a --regions mask to say where that region is")
        m = Image.open(a.regions).convert("RGB").resize(
            (img.shape[1], img.shape[0]), Image.NEAREST)
        labels = labels_from_image(np.asarray(m, dtype="float32") / 255.0)
        overrides = {}
        for spec in a.region:
            head, _, body = spec.partition(":")
            if not body:
                ap.error(f"--region {spec!r}: expected N:field=value,field=value")
            try:
                rid = int(head)
            except ValueError:
                ap.error(f"--region {spec!r}: {head!r} is not a region id")
            ov = overrides.setdefault(rid, {})
            for term in body.split(","):
                k, _, v = term.partition("=")
                k = k.strip().replace("-", "_")
                if k not in REGION_PARAMS:
                    ap.error(f"--region {spec!r}: {k!r} is not a region colour or flow "
                             f"field; one of {sorted(REGION_PARAMS)}")
                kind = {f.name: type(f.default) for f in fields(PaintConfig)}[k]
                try:
                    ov[k] = v.strip() if kind is str else kind(v)
                except ValueError:
                    ap.error(f"--region {spec!r}: {v!r} is not a {kind.__name__}")
        regions = {"labels": labels, "overrides": overrides}
        # The mask is only as good as what it caught, and a mask that caught nothing looks
        # exactly like no mask at all -- the still-image failure, wearing a colour hat.
        seen = set(np.unique(labels).tolist())
        empty = sorted(r for r in overrides if r not in seen)
        if empty:
            print(f"note: --region given for {empty}, which the mask does not contain",
                  file=sys.stderr)

    # Deferred until the regions are parsed, because a swirl a region asks for reads the
    # placed centres just as the base's does -- and the note this replaces would otherwise
    # tell you nothing reads them while one passage was swirling around them.
    if vortices and cfg.flow == "none" and not any(
            ov.get("flow", "none") != "none"
            for ov in ((regions or {}).get("overrides") or {}).values()):
        print("note: swirl centres given but no --flow is set, base or region, "
              "so nothing reads them", file=sys.stderr)

    out, info, sb = paint(img, cfg, foveal, vortices, regions)
    save_image(a.dst, out)

    if a.strokes_out:
        import numpy as np
        np.savez_compressed(a.strokes_out, x=sb.x, y=sb.y, r_major=sb.r_major,
                            r_minor=sb.r_minor, theta=sb.theta, rgb=sb.rgb,
                            alpha=sb.alpha)

    info.pop("cfg")
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
