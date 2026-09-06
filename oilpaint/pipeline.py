"""Orchestration. One image in, one painting out, plus the numbers to judge it by.

Config-first with a dataclass, mirroring 4d-relight so the two repos read alike.
Spec §3.
"""

import time
from dataclasses import asdict, dataclass, field

import numpy as np

from . import (
    flow as flow_mod,
    palette as palette_mod,
    regions as regions_mod,
    reference as reference_mod,
    quadtree,
    strokes as strokes_mod,
)
from .detail import DetailField, FovealField
from .image import linear_to_srgb, srgb_to_linear
from .render import base_layer, ground_height, light, render
from .tensor import flat_tensor, structure_tensor


@dataclass
class PaintConfig:
    # allocation
    metric: str = "var"
    target_n: int = 5000
    tau: float = None  # set directly to bypass the budget search
    # Largest and smallest stroke, in PIXELS. These were depths in the first draft, which
    # was a mistake: a depth is relative to the padded tree size, so `dmin=2` silently
    # meant 256px strokes on a 1024 tree and flat regions came out as huge blobs. Pixels
    # are what the look actually depends on, so pixels are what the config exposes.
    max_cell: int = 64
    min_cell: int = 8  # below ~8px a stroke is too small to read AS a stroke
    base_block: int = 16
    # Absolute detail floor -- the budget cannot buy subdivision below it. 1e-4 is a luma
    # VARIANCE, i.e. std 0.01 (~2.5/255): under that a cell is compression noise, not
    # detail. Calibrated for metric='var'; the other metrics have wildly different scales
    # (dct energy runs to ~1e3) so set it per-metric or leave it at 0 for them.
    tau_floor: float = 1e-4
    # Foveal map. The map ITSELF is not a config field -- it is an image, not a scalar, so
    # it arrives as the `foveal` argument to plan/paint, exactly as `rgb` does. This one
    # number is the whole control surface: 0 ignores the map, 1 hands it the decision, and
    # everything between is the blend. See detail.FovealField. Inert at 0, which is the
    # default, so every existing result is untouched.
    foveal_strength: float = 0.0

    # stroke geometry
    # 1.35 is just under KAPPA_FULL_COVER (sqrt(2)); measured coverage rises from 0.61 at
    # 1.15 to 0.78 at 1.41 on the test photo, and the overlap reads as brushwork rather
    # than as tiling. The base layer means this is a look choice, not a coverage floor.
    kappa: float = 1.35
    jitter_centre: float = 0.35
    jitter_radius: float = 0.15
    jitter_theta_deg: float = 20.0
    jitter_aniso: float = 0.2
    aniso_max: float = 6.0
    orient: str = "structure"  # structure | random | fixed
    # Structure-tensor smoothing. 3.5 rather than 2.0: at the wider aniso_max above, a
    # stroke commits much harder to whatever angle the tensor hands it, so a noisy flow
    # field stops being a wobble and starts being long marks pointing the wrong way. The
    # metric `edge_alignment` deliberately does NOT follow this knob -- it stays at
    # sigma 2.0 so the number means the same thing across a sweep of it.
    tensor_sigma: float = 3.5
    # The flat-region directional brush. In a sky, a gradient ground or an out-of-focus
    # background the detail-scale tensor has no signal, so its angle is the arctangent of
    # noise and its coherence is ~0 -- and step 4 of strokes.py reads a coherence of 0 as
    # "isotropic, stay round". The result is the blob field this fixes. A painter reaches
    # for the opposite: the flatter the passage, the more it is laid in with one sweep.
    # `flat_dir` is the handover strength, `flat_sigma` the scale the sweep reads the
    # picture at, `flat_theta_deg` where it points when there is nothing to read.
    # See strokes.flat_blend. Inert at flat_dir 0, which is bit-identical to before.
    flat_dir: float = 0.8
    flat_sigma: float = 8.0
    flat_theta_deg: float = 45.0

    # the artistic flow field [flow.py]
    # The structural counterpart to the pigment grade below. `flat_dir` above answers "what
    # direction does the PICTURE want here"; this one answers "what direction does the
    # PAINTER want", which is a different question and the one that separates a Starry Night
    # from a Nympheas. `flow` picks a field AND the mark shape that goes with it (ribbons or
    # dabs), and the five numbers under it trim that. Applied inside step 3 of
    # strokes.from_cells, the one step that draws no random numbers, so the tree, the
    # budget, the radii, the colours and the whole rng stream are untouched -- see flow.py.
    # Inert at 'none', and inert by skipping, so every earlier painting reproduces exactly.
    flow: str = "none"
    flow_strength: float = 1.0
    flow_scale: float = 0.0
    flow_rot: float = 0.0
    flow_coh: float = 0.0
    flow_drift: float = 0.0

    # the pigment grade [palette.py]
    # A photograph's colours are not a painting's, and the stroke geometry cannot fix that
    # on its own. `palette` picks a character AND a set of real pigments, the five numbers
    # below TRIM it, and `palette_strength` blends the whole thing back toward the photo.
    # Applied to the pigment AFTER the geometry is settled, so none of it can move a
    # leaf, a stroke or the RNG stream -- see palette.grade. Inert at 'none' with the trims
    # at 0, and inert by skipping, so the default painting is bit-identical to before.
    palette: str = "none"
    palette_strength: float = 1.0
    warm_cool: float = 0.0
    chroma: float = 0.0
    value_compress: float = 0.0
    broken_color: float = 0.0
    pigment: float = 0.0

    # Hue-band steering: turn ONE family of hues rather than all of them. A global rotation
    # cannot cool a sky without cooling the skin in front of it; this can, and the widest
    # `hue_range` is the global rotation as its limiting case. Inert unless `hue_rotate` or
    # `hue_boost` is non-zero, so parking the band costs nothing. See palette._steer_hue.
    hue_target: float = 120.0
    hue_range: float = 40.0
    hue_rotate: float = 0.0
    hue_boost: float = 0.0

    # Match a named painting's colour distribution, by optimal transport. The other kind of
    # colour control from the ones above: they ask you to know what you want, this asks you
    # to point at a picture. Fitted against the STROKE colours, applied first in the grade,
    # and it composes with `palette` on purpose -- the reference supplies the statistics and
    # the palette supplies the tubes. See oilpaint/reference.py.
    reference: str = "none"
    reference_strength: float = 1.0

    # appearance
    color: str = "median"  # median | mean | centre
    alpha: float = 1.0
    hard: bool = True
    hard_r: float = 1.5
    linear: bool = False  # average and composite in linear light

    # irregularity -- what stops the output reading as a lattice of identical ellipses
    size_sigma: float = 0.30  # log-normal spread on radius
    drop_p: float = 0.0  # random drop, with area-compensating expansion
    # Per-stroke pigment variation [Litwinowicz97]. OFF by default: in flat regions it reads
    # as speckle rather than as loaded paint, and the irregularity it was carrying is
    # already covered by size_sigma and the outline textures. The knob stays for A/B.
    color_jitter: float = 0.0
    wobble_amp: float = 0.18  # ragged outline instead of a perfect ellipse
    # Step 2, the paint-texture pair [Hertzmann02]. Both are closed form in render.py and
    # both are inert at 0, so `bristle_amp=0, taper_amp=0` reproduces a step-1 painting
    # exactly. `taper_amp` is the one that changes the SILHOUETTE, and it is doing more of
    # the work than the bristles are -- see render.taper.
    bristle_amp: float = 0.24
    taper_amp: float = 0.35
    # High-frequency outline roughness, at an order derived from the stroke's size in
    # pixels. This is the one that stops a large blob reading as a smooth vector shape --
    # `wobble_amp` cannot, because its two harmonics scale with the stroke.
    fringe_px: float = 2.0

    # impasto: the height field and its lighting [Hertzmann02]. Everything here is inert
    # unless `impasto`, and the height pass is not even allocated then.
    impasto: bool = True
    impasto_relief: float = 0.45  # bristle grooves as RELIEF, independent of their opacity
    impasto_layer: float = 0.6  # height gained from first stroke to last
    canvas_weave: float = 0.15  # canvas tooth, showing only where the paint is thin
    impasto_depth: float = 0.25  # height -> slope gain; the "how thick is the paint" knob
    light_deg: float = 135.0  # screen compass: 0 from the right, 90 from the top
    light_elev_deg: float = 35.0  # low is dramatic, high is flat
    gloss: float = 0.30  # 0 matte tempera, 1 wet oil
    # Ambient occlusion: paint shading itself where a stroke edge meets what is under it.
    # View-INdependent by construction -- it is a property of the surface, not the eye.
    occlusion: float = 0.6
    # The eye. 90 elevation is straight on, which is the default and reproduces the fixed
    # viewpoint this had before. THIS is the view-dependent seam: an interactive page drives
    # these two from the pointer and the impasto's sheen moves with it.
    view_deg: float = 90.0
    view_elev_deg: float = 90.0

    # Ground under the strokes. 'blur' -- a blurred coarse stand-in -- is the default
    # because it simply looks better: the coarse STROKE underpainting [Hertzmann98] fills
    # every gap with more big marks, and at this stroke size that reads as clutter, whereas
    # the blur lets the canvas weave and the detail strokes carry the picture. It also
    # halves coverage (0.91 -> 0.55), which is why `ground` above matters so much: with
    # 45% of the canvas showing ground, the height the ground sits at IS the look.
    # 'strokes' is still there, and 'none' shows the holes.
    base: str = "blur"
    base_cell_scale: float = 2.0  # underpainting cell size, relative to max_cell

    seed: int = 0


def plan(rgb, cfg=None, foveal=None, vortices=None, regions=None):
    """Everything up to the rasteriser: detail field, tree, strokes, base canvas.

    Split out of `paint` because this is the cross-language seam. The static tuner page
    runs THIS under Pyodide -- the real Python, not a port of it -- and hands the stroke
    buffer to `web/tune/raster.js`, which is the only routine that exists twice and the
    only one carrying a parity test (.claude/skills/python-js-parity/SKILL.md). Keeping the
    orchestration on one side of the seam is what stops the page growing a second copy of
    it. Returns (strokes, canvas, info).

    `foveal` is an optional HxW map in [0,1], 1 = spend strokes here. It reweights the
    detail field and nothing else -- see detail.FovealField for the two ways it combines
    with the content metric, and for why an unpainted map is a no-op.

    `vortices` is an optional list of (fx, fy) fractions saying where the flow field's
    swirls go -- what a click on the tuner page produces. Like `foveal` it is data, not a
    number, so it arrives here rather than in `cfg`; and like `foveal` it is inert unless
    something in `cfg` asks for it, which here means a swirl-kind `flow`. None or empty is
    the preset's own golden-angle spiral. It may also be `{region id: [points]}`, giving one
    passage a set of its own -- a plain list is the base's, and a region without its own
    inherits it. See flow.vortex_map and flow.centres_for.

    `regions` is an optional `{"labels": (h,w) ints, "overrides": {id: {field: value}}}`
    dividing the picture into passages that are painted separately -- the third input of
    this shape, after the two above, and for the same reason: a mask is an image and a
    per-region parameter set is not a number, so neither can be a `PaintConfig` field.

    It attaches at the two cheap seams and at nothing else: the COLOUR fields to the
    pigment grade at the very end, which moves no stroke, and the FLOW fields to step 3 of
    `strokes.from_cells`, which moves the strokes inside that region and nothing else --
    no leaf, no radius, no colour and no random number, because step 3 is the one step of
    `from_cells` that draws none. None, or a mask nothing overrides, is one region and
    today's painting. See regions.py.
    """
    cfg = cfg or PaintConfig()
    h, w = rgb.shape[:2]
    info = {"h": h, "w": w, "cfg": asdict(cfg), "timing": {}}
    work = srgb_to_linear(rgb).astype(np.float32) if cfg.linear else rgb

    t = time.perf_counter()
    fld = DetailField(rgb, cfg.metric, tree_size=quadtree.tree_size(h, w))
    if foveal is not None and cfg.foveal_strength > 0.0:
        fld = FovealField(fld, foveal, cfg.foveal_strength)
    info["foveal"] = isinstance(fld, FovealField)
    info["timing"]["detail"] = time.perf_counter() - t

    t = time.perf_counter()
    # dmin is derived: it is whatever depth first brings the cell down to max_cell px.
    size = quadtree.tree_size(h, w)
    dmin = max(0, int(np.ceil(np.log2(max(1, size / max(1, cfg.max_cell))))))
    info["dmin"] = dmin
    floor = cfg.tau_floor if cfg.metric == "var" else 0.0
    tau = cfg.tau
    if tau is None:
        tau, reachable, ceiling = quadtree.solve_tau(
            fld, h, w, cfg.target_n, dmin, cfg.min_cell, tau_floor=floor
        )
        info["budget_reachable"] = bool(reachable)
        info["stroke_ceiling"] = int(ceiling)
    cells = quadtree.build(fld, h, w, tau, dmin, cfg.min_cell, tau_floor=floor)
    info["tau"] = float(tau)
    info["n_strokes"] = int(cells[0].size)
    info["timing"]["quadtree"] = time.perf_counter() - t

    t = time.perf_counter()
    theta, coh = structure_tensor(rgb, cfg.tensor_sigma)
    # The coarse companion field, computed on a decimated grid -- it is band-limited to
    # `flat_sigma` by construction, so full resolution would be spending 16x the arithmetic
    # to represent nothing. One sigma drives both the pre-blur and the tensor smoothing,
    # because they are the same statement, "read the picture at this scale". See
    # tensor.flat_tensor for both halves and for what `flat_step` obliges the caller to do.
    theta_flat = coh_flat = None
    flat_step = 1
    if cfg.flat_dir > 0.0:
        theta_flat, coh_flat, flat_step = flat_tensor(rgb, cfg.flat_sigma)
    info["flat_step"] = flat_step
    info["timing"]["tensor"] = time.perf_counter() - t

    t = time.perf_counter()
    # `None` at the defaults, and then step 3 of from_cells never enters flow.py at all.
    # The placed swirl centres, normalised ONCE: `{region id: points}`, a plain list being
    # the base's set. The base's field reads `centres_for(vmap, 0)` and each region's reads
    # its own or, having none, the base's -- see flow.centres_for for why that inheritance
    # is forced rather than chosen.
    vmap = flow_mod.vortex_map(vortices)
    fp = flow_mod.params_for(cfg, flow_mod.centres_for(vmap, 0))
    info["flow"] = fp is not None
    # The regions at their OTHER attachment point, and the reason this one sits up here
    # rather than with the grade at the end: what it changes is where the strokes point, so
    # it has to be decided before they are made. `flow_params_for` returns None unless a
    # region actually names a flow field, so a picture with only colour regions -- or with
    # none -- never enters it and comes out bit-identical. See regions.py.
    frp = regions_mod.flow_params_for(cfg, regions, vmap)
    info["flow_regions"] = 0 if frp is None else len(frp["blocks"])
    # How many placed centres this painting actually read, counted once per OWNING set: a
    # region that inherited the base's is not a second placement, and reporting it as one
    # would turn the panel's "(n placed)" -- which exists so a placement that never reached
    # the engine cannot look like one that reached it and did little -- into a number that
    # grows when you add a layer that changed nothing.
    owners = set()
    if fp is not None and fp["vortices"]:
        owners.add(0)
    for rid, p in (frp["blocks"] if frp is not None else {}).items():
        # Which SET this field read: its own if the region has one, else the base's. So a
        # region reading the base's centres adds nothing, and a region reading the base's
        # centres when the base's own field is 'none' still counts them once.
        if p is not None and p["vortices"]:
            owners.add(rid if rid in vmap else 0)
    info["flow_vortices"] = sum(len(vmap[o]) for o in sorted(owners))
    common = dict(
        jitter_centre=cfg.jitter_centre,
        jitter_radius=cfg.jitter_radius,
        jitter_theta_deg=cfg.jitter_theta_deg,
        jitter_aniso=cfg.jitter_aniso,
        aniso_max=cfg.aniso_max,
        orient=cfg.orient,
        theta_flat=theta_flat,
        coh_flat=coh_flat,
        flat_step=flat_step,
        flat_dir=cfg.flat_dir,
        flat_theta_deg=cfg.flat_theta_deg,
        flow=fp,
        flow_regions=frp,
        color=cfg.color,
        alpha=cfg.alpha,
        size_sigma=cfg.size_sigma,
        color_jitter=cfg.color_jitter,
    )

    # The underpainting is a COMPLETE canvas of coarse strokes, painted before anything
    # else [Hertzmann98]. It replaces the blurred base layer, whose visible smooth
    # patches were the flat regions of the first results -- those were not paint at all.
    under = None
    if cfg.base == "strokes":
        # This layer must actually cover, and saying so with KAPPA_FULL_COVER was not
        # enough: measured on a 152 px grid it covered 0.704, because `common` carries the
        # detail layer's randomisation and three parts of it fight the guarantee. The
        # dominant one is ELONGATION, and it is worth spelling out because it is not
        # obvious: the aspect ratio is applied AREA-PRESERVINGLY, r_minor = r / sqrt(ratio),
        # so a stroke stretched to 5.3:1 has a minor semi-axis 2.3x SHORTER than the round
        # stroke the covering condition was derived for -- and the condition binds on the
        # minor axis. At the tuner's aniso_max the underpainting cannot cover at any seed.
        #
        # Restoring it by growing the brush needs kappa ~5.2, i.e. strokes wider than the
        # canvas. So the ground goes ROUND instead, and that is the right trade rather than
        # a concession: elongation is a DESCRIPTIVE cue -- it makes paint follow the form --
        # and this layer is never read as strokes, only seen through the gaps in the layer
        # above. It buys nothing here and costs the one thing the layer exists for.
        #
        # Round + the jitter-corrected kappa measures 1.000. Neither the draw ORDER nor the
        # draw COUNT changes (the aniso jitter is still drawn, it is just multiplied by 0),
        # so the detail layer is untouched and base='blur' is bit-identical.
        under = strokes_mod.from_cells(
            work,
            strokes_mod.uniform_grid(h, w, int(cfg.max_cell * cfg.base_cell_scale)),
            theta,
            coh,
            kappa=strokes_mod.covering_kappa(cfg.jitter_centre, cfg.jitter_radius),
            drop_p=0.0,  # ... so it never drops
            seed=cfg.seed + 9973,
            # Drift off here for the same reason elongation is: this layer's whole job is
            # to cover, and drift makes strokes bunch along the flow lines and open gaps
            # between them. The field still turns the ground's strokes -- it is only the
            # term that MOVES them that the covering guarantee cannot afford.
            **{**common, "aniso_max": 1.0, "jitter_aniso": 0.0,
               "flow": None if fp is None else {**fp, "drift": 0.0},
               "flow_regions": regions_mod.flow_undrifted(frp)},
        )

    detail_sb = strokes_mod.from_cells(
        work,
        cells,
        theta,
        coh,
        drop_p=cfg.drop_p,
        kappa=cfg.kappa,
        seed=cfg.seed,
        **common,
    )
    # Underpainting first, so array order stays paint order across both layers.
    sb = strokes_mod.concat(under, detail_sb) if under is not None else detail_sb
    info["n_under"] = 0 if under is None else len(under)
    info["n_detail"] = len(detail_sb)
    info["timing"]["strokes"] = time.perf_counter() - t

    t = time.perf_counter()
    # The blurred base stays underneath the stroke underpainting rather than being
    # replaced by it. Without this the canvas is black wherever the coarse layer leaves a
    # gap, and at ~1.8% uncovered that is a scatter of hard black specks across the
    # painting -- the black-hole problem coming back through the door marked "fixed".
    # Cost is one separable blur; it is never seen, because the strokes cover it.
    canvas = base_layer(work, cfg.base_block) if cfg.base in ("blur", "strokes") else None
    info["timing"]["canvas"] = time.perf_counter() - t

    # The pigment grade, LAST: everything above has already decided where the strokes go
    # and how big they are, so this can only change what colour they are. See palette.py
    # for why that placement is the whole design. `params_for` returns None at the
    # defaults and the two lines below never run.
    t = time.perf_counter()
    gp = palette_mod.params_for(cfg)
    # The reference match rides the SAME grade pass. It is fitted here rather than inside
    # the grade because it is a statistic OVER ALL THE STROKES -- their mean and covariance
    # in OKLab -- which a routine that runs a chunk at a time cannot see. Fitting it here
    # and handing the grade a 3x3 keeps the per-colour cost at nine multiplies and, since
    # the transport lands in `gp`, the JS canvas memo re-keys on it for free.
    rp = reference_mod.params_for(cfg)
    info["reference"] = rp is not None
    if rp is not None:
        gp = gp if gp is not None else palette_mod.block_for(cfg)
        gp["xfer"] = reference_mod.fit(sb.rgb, rp, linear=cfg.linear)
    # Reported so a front end can re-aim it; None when no reference ran.
    info["xfer"] = None if gp is None else gp.get("xfer")
    info["palette"] = gp is not None
    # The regions, which are the same grade aimed one passage at a time. `params_for`
    # returns None unless some region actually asks for something different, and then
    # the three lines below are exactly the painting this made before regions existed --
    # not an equivalent one. See regions.py for why a per-stroke label is cheap.
    rspec = regions_mod.params_for(cfg, regions)
    lab = None
    if rspec is None:
        if gp is not None:
            sb.rgb = palette_mod.grade_strokes(sb.rgb, sb.phase, gp, linear=cfg.linear)
            if canvas is not None:
                canvas = palette_mod.grade_image(canvas, gp, linear=cfg.linear)
    else:
        lab = regions_mod.stroke_labels(rspec, sb.x, sb.y)
        blocks = regions_mod.blocks_for(cfg, rspec, gp, sb.rgb, lab, linear=cfg.linear)
        sb.rgb = regions_mod.apply_strokes(sb.rgb, sb.phase, lab, blocks,
                                          linear=cfg.linear)
        if canvas is not None:
            canvas = regions_mod.apply_image(canvas, rspec["labels"], blocks,
                                            linear=cfg.linear)
    # ONE count over BOTH plans: a layer that only re-aims the flow is as live as one that
    # only regrades, and a panel reporting "0 regions" for it would be showing the
    # still-image failure's own symptom. Counted, not assumed: a mask painted at one size
    # against a picture rendered at another catches nothing, and silently produces the
    # undivided painting.
    ids = regions_mod.live_ids(rspec, frp)
    info["regions"] = len(ids)
    if ids:
        if lab is None:
            lab = regions_mod.stroke_labels(frp, sb.x, sb.y)
        info["region_strokes"] = regions_mod.summary(ids, lab)
    info["timing"]["palette"] = time.perf_counter() - t
    return sb, canvas, info


# Below this, a pixel has essentially no paint on it: what shows is the base layer, or
# bare canvas when there is none. THE HOLE MEASURE, and it is a different question from
# `coverage` above -- see `finish`. 0.05 rather than 0: an alpha of a few percent is a
# stroke's antialiased last pixel, not paint anyone can see.
BARE = 0.05


# The `PaintConfig` fields that `finish` reads and `render` does not -- everything below is
# an argument to `light()` alone. THE POINT OF THE SET: a change that moves only these can
# reuse ONE rasterisation and re-light it, which measured 42x cheaper than re-rastering
# (0.056 s against 2.311 s at 640px, 3571 strokes). The tuner's `relight` path keys off it,
# and so does `web/tune/oilpaint/pipeline.js`, which mirrors the set name for name.
#
# `linear` and `impasto` are deliberately NOT here even though `finish` reads them: `linear`
# also chooses the working colour space back in `plan`, and `impasto` decides whether the
# height field is allocated at all, so neither can be changed without re-rendering.
#
# A stale entry here is a control that silently does nothing -- the picture is served from
# the pre-lighting cache when it should have been repainted -- so tests/test_core.py checks
# the claim directly: perturbing any name in this set leaves the pre-lighting buffers
# BIT-IDENTICAL, and it fails if a field is added that does more.
RELIGHT_PARAMS = frozenset({
    "impasto_depth", "light_deg", "light_elev_deg", "gloss",
    "canvas_weave", "occlusion", "view_deg", "view_elev_deg",
})


def finish(out, cover, cover_detail, info, cfg=None, height=None):
    """Post-rasterisation: the coverage statistics, the colour-space exit, the lighting.

    The other half of the seam described in `plan`. Both the Python `paint` below and the
    static page call this, so the numbers the page prints are computed by the same code
    that produces the numbers in the CLI.

    Coverage is measured BEFORE the lighting, and that is the point of the ordering: the
    bristles cut real holes in the alpha, so `coverage` genuinely falls when `bristle_amp`
    rises and the panel should say so. Shading it first would have hidden that behind a
    change in brightness.

    TWO NUMBERS, because `coverage` alone cannot answer the question it looks like it
    answers. It is a `> 0.99` threshold, so anything that modulates alpha inside a painted
    pixel moves it without opening a hole: measured on a 300px portrait at the defaults,
    turning `bristle_amp` off moves `coverage` 0.567 -> 0.712 while `bare` stays at 0.1165
    to four figures. The panel was therefore reporting 0.57 for a canvas that was 88%
    painted, and the 12% that really was bare -- 63% of it in contiguous blobs above
    1000px, which is what reads as a smooth unpainted patch -- had no number at all.
    `bare` is that number, and it is the one to watch when the output looks blurred rather
    than painted.
    """
    cfg = cfg or PaintConfig()
    info["coverage"] = float((cover_detail > 0.99).mean())
    info["coverage_all"] = float((cover > 0.99).mean())
    # From `cover`, not `cover_detail`: the question is whether ANY layer painted here.
    info["bare"] = float((cover < BARE).mean())
    if cfg.linear:
        out = linear_to_srgb(out).astype(np.float32)
    if cfg.impasto and height is not None:
        return light(
            np.clip(out, 0.0, 1.0), height, cover,
            depth=cfg.impasto_depth, light_deg=cfg.light_deg,
            elev_deg=cfg.light_elev_deg, gloss=cfg.gloss,
            canvas_weave=cfg.canvas_weave, occlusion=cfg.occlusion,
            view_deg=cfg.view_deg, view_elev_deg=cfg.view_elev_deg,
        )
    return np.clip(out, 0.0, 1.0)


def paint(rgb, cfg=None, foveal=None, vortices=None, regions=None):
    """Run the pipeline. Returns (painting, info, strokes)."""
    cfg = cfg or PaintConfig()
    h, w = rgb.shape[:2]
    sb, canvas, info = plan(rgb, cfg, foveal, vortices, regions)

    t = time.perf_counter()
    # Both coverages come out of ONE pass: `split_at` is where the detail layer starts, so
    # the second accumulator measures the detail layer's own holes with the underpainting
    # discounted. That number used to cost a whole extra render.
    rendered = render(
        sb, h, w, hard=cfg.hard, hard_r=cfg.hard_r, wobble_amp=cfg.wobble_amp,
        canvas=canvas, split_at=info["n_under"],
        bristle_amp=cfg.bristle_amp, taper_amp=cfg.taper_amp,
        fringe_px=cfg.fringe_px, want_height=cfg.impasto, impasto_relief=cfg.impasto_relief,
        impasto_layer=cfg.impasto_layer,
        # No base layer means the gaps ARE bare canvas, and should read as holes.
        ground=0.0 if cfg.base == "none" else ground_height(cfg.impasto_layer),
    )
    # `split_at` is always passed here, so the first three are always (out, cover, tail);
    # the height, when asked for, is always LAST -- indexed from the end so this does not
    # quietly grab the coverage buffer if a caller ever drops `split_at`.
    out, cover, cover_detail = rendered[:3]
    height = rendered[-1] if cfg.impasto else None
    info["timing"]["render"] = time.perf_counter() - t
    return finish(out, cover, cover_detail, info, cfg, height), info, sb
