"""Region-wise look: the same grade and the same flow field, aimed one passage at a time.

`palette.py` answers "what colour would a painter have mixed", and `reference.py` answers
"what colour distribution does that painting have" -- but both answer it for the WHOLE
picture at once, and that is the limit they share. A global hue band can cool the sky only
by also cooling the skin in front of it; a global reference moves the foliage onto Van
Gogh's statistics along with everything else. What a painter does is per PASSAGE: the sky
gets one mixture, the field another, and they were never on the brush at the same time.

WHY THIS IS CHEAP HERE, AND IT IS NOT OBVIOUS. Region-wise colour reads like a spatial
control, and spatial controls in this pipeline are expensive -- see the attachment-point
table in .claude/skills/artistic-controls/. This one is not, because of a property the
pipeline already had: colour is decided PER STROKE, and a stroke knows where it is.
`StrokeBuffer` carries `x` and `y` alongside `rgb`, so "which region is this stroke in" is
one array lookup per stroke -- 5 000 of them against a megapixel canvas, four orders of
magnitude apart. It attaches at exactly the same place as the global grade, the end of
`plan`, and moves no leaf, no radius, no angle and no random number. The same argument
carries the region FLOW field one seam earlier: the stroke knows where it is there too
(`cy`, `cx`, before it has been drifted), so a per-passage flow field is the same one
lookup and the field itself is already evaluated analytically per stroke.

A STROKE GETS ONE LABEL, FROM ITS CENTRE, and that is a design decision rather than an
approximation. A mark that straddles the skyline is one dab of one mixture -- that is what
paint is, and it is the same argument `palette.py` makes for why the pigment projection is
per stroke and not per pixel. So a region boundary falls on stroke boundaries: no
feathering, no matting, no halo, and a mask that is sloppy at the edges still produces an
edge that looks deliberate. The canvas underneath IS labelled per pixel, because it has no
strokes to fall on, but only ~12% of it is ever seen.

WHAT A REGION MAY OVERRIDE is `REGION_PARAMS`, and it is two sets rather than one, because
a passage wants two things said about it and they attach at two different points.

  * the COLOUR fields, read by `palette.params_for` and `reference.params_for` at the end of
    `plan`, where the geometry has already been settled. A region that sets one of them
    moves no leaf, no radius, no angle and no random number.
  * the FLOW fields, read by `flow.params_for` inside step 3 of `strokes.from_cells` -- the
    OTHER cheap attachment point in this pipeline, and cheap for the same reason flow.py is:
    it is the one step of `from_cells` that draws no random number. A region that sets one
    of these DOES move strokes, which is the entire point of it -- van Gogh's sky over
    Monet's water is not a colour statement -- but it moves only its own, and it still moves
    no leaf, no radius, no colour and no draw.

Anything outside the two sets has already run by the time either attaches: overriding
`target_n` per region would be a request to rebuild the quadtree, and the quadtree is what
decided where these strokes are. Like `pipeline.RELIGHT_PARAMS`, both sets below are CHECKED
by tests/test_core.py rather than asserted here -- a wrong entry in the colour set would
silently change the geometry of one part of a picture, and a wrong entry in the flow set
would name a field `flow.params_for` does not read and change nothing at all.

THE FLOW LABEL IS READ FROM THE STROKE'S BIRTH CENTRE, and that is forced rather than
chosen. `flow_drift` MOVES a mark along the field, so at the moment the field has to be
picked the drifted position does not exist yet -- the drift is that field's own output. The
colour label, decided at the end of `plan`, keeps reading `sb.x`/`sb.y`, which is where the
mark actually lands and therefore where its colour is seen. The two agree everywhere except
a sliver of boundary strokes at non-zero drift, and each is read from the only position
available to it.

THE SWIRL CENTRES ARE ADDRESSED THE SAME WAY, and they are the one input of this kind that
could not stay global once the flow could differ per passage: a swirl placed in the sky was
being read by the field painting the ground as well. `vortices` is therefore
`{region id: [points]}` (a plain list still means the base's), and a region with no set of
its own INHERITS the base's -- see `flow.centres_for` for why that inheritance is forced by
the equivalence invariant rather than chosen. A region set to `waterlily` ignores centres
however it got them, exactly as the base does (see flow.field_centres).

Inert by SKIPPING, and now four times over: each of `params_for` and `flow_params_for`
returns None when no region asks for its half, and inside each, a region whose own settings
come to nothing is left alone rather than round-tripped through an identity. And the
invariant that proves the several paths are one path: give every region the base config's
own values -- colour and flow -- and the painting is bit-identical to the undivided one.
tests/test_core.py requires that with `np.array_equal`, for both halves.

The mask itself is DATA, not a number -- an image, like the foveal map, and like it a list
of pixels no slider could carry. So it arrives as an argument to `plan`/`paint` rather than
as a `PaintConfig` field, and an absent or empty one means "the whole picture is one
region", which is the painting this repo made before this file existed.
"""

from dataclasses import replace

import numpy as np

from . import flow as flow_mod
from . import palette as palette_mod
from . import reference as reference_mod

# Region 0 is the base -- the picture as the panel's own settings grade it -- so there are
# seven addressable regions. The cap is the legend below rather than an algorithmic limit:
# eight colours a person can name and tell apart at a glance is the real constraint on a
# hand-painted mask, and a picture that needs more passages than that wants a different
# tool. `MAX_REGIONS` is what the page enforces, so it comes from here rather than from a
# copy of the number in the page.
MAX_REGIONS = 8

# The legend a mask image is read through: paint region 3 in pure blue and this is what
# says so. Chosen to be the corners of the RGB cube plus black, so they survive JPEG,
# antialiasing and a lossy screenshot -- the nearest-corner assignment below has the widest
# possible margin, which is the entire reason not to use eight tasteful greys. Black is
# region 0 for the same reason the foveal map's black means "spend strokes here": an
# unpainted mask is a mask that asks for nothing.
LEGEND = (
    (0.0, 0.0, 0.0),    # 0  base -- the panel's own settings
    (1.0, 0.0, 0.0),    # 1  red
    (0.0, 1.0, 0.0),    # 2  green
    (0.0, 0.0, 1.0),    # 3  blue
    (1.0, 1.0, 0.0),    # 4  yellow
    (1.0, 0.0, 1.0),    # 5  magenta
    (0.0, 1.0, 1.0),    # 6  cyan
    (1.0, 1.0, 1.0),    # 7  white
)

# Exactly the set the two colour modules read. The test in tests/test_core.py perturbs each
# of these inside a region and requires the stroke GEOMETRY to come out bit-identical. A
# wrong entry here would not raise: it would quietly repaint one part of the picture from a
# different quadtree.
REGION_COLOR_PARAMS = frozenset({
    "palette", "palette_strength", "warm_cool", "chroma", "value_compress",
    "broken_color", "pigment",
    "hue_target", "hue_range", "hue_rotate", "hue_boost",
    "reference", "reference_strength",
})

# Exactly the set `flow.params_for` reads, and no more. The test perturbs each of these
# inside a region too, but requires the OPPOSITE of the colour set: the strokes inside the
# region must move and the ones outside it must be bit-identical, because a structural
# control that moved nothing looks exactly like one that moved everything a little.
# `flow_coh` is what carries mark SHAPE across -- coherence drives elongation in step 4 --
# so "short dabs here, long ribbons there" needs no field from step 4 itself.
REGION_FLOW_PARAMS = frozenset({
    "flow", "flow_strength", "flow_scale", "flow_rot", "flow_coh", "flow_drift",
})

# What `--region` and the page's layer rows may name. The near misses -- the fields that
# would need the quadtree rebuilt: `target_n`, `max_cell`, `kappa`, `aniso_max`, `seed` --
# are REFUSED rather than ignored, or the CLI accepts a request it cannot honour.
REGION_PARAMS = REGION_COLOR_PARAMS | REGION_FLOW_PARAMS


def labels_from_image(rgb):
    """An (h,w,3) mask in [0,1] -> an (h,w) uint8 of region ids, by nearest legend colour.

    Nearest in plain RGB, not OKLab: the legend is the corners of the cube, so the decision
    is never close and a perceptual metric would only make it slower and harder to explain.
    This is a PARSER, not part of the transform -- the parity boundary is the label array
    it returns, so the browser is free to author one any way it likes.
    """
    c = np.asarray(rgb, dtype=np.float32).reshape(-1, 3)
    legend = np.asarray(LEGEND, dtype=np.float32)
    d = ((c[:, None, :] - legend[None, :, :]) ** 2).sum(axis=2)
    return np.argmin(d, axis=1).astype(np.uint8).reshape(np.shape(rgb)[:2])


def _live(regions, keep):
    """Validate the whole override set, then keep only the fields in `keep`.

    Returns `(labels, {id: {field: value}})`, or `(None, {})` when there is nothing to do.

    THE VALIDATION IS OVER THE FULL SET on both calls, not over `keep`. A field this control
    cannot honour has to be REFUSED, and refused wherever it is named -- filtering it out
    here and again over there is how `--region 1:target_n=9000` would end up accepted by
    both paths and read by neither, which is exactly the silent-no-op this raises to avoid.
    """
    if not regions:
        return None, {}
    labels = regions.get("labels")
    overrides = regions.get("overrides") or {}
    if labels is None:
        return None, {}
    live = {}
    for key, ov in overrides.items():
        rid = int(key)
        if not 0 <= rid < MAX_REGIONS:
            raise ValueError(f"region id {rid} outside 0..{MAX_REGIONS - 1}")
        bad = sorted(set(ov) - REGION_PARAMS)
        if bad:
            raise ValueError(f"region {rid} cannot set {bad}; a region may only set "
                             f"colour and flow: {sorted(REGION_PARAMS)}")
        # An override that says nothing about THIS half is not a region for this half. It is
        # what makes "clear region 2" on the page restore the base painting exactly rather
        # than a copy of it, and what keeps a flow-only layer out of the grade entirely.
        sub = {k: v for k, v in ov.items() if k in keep}
        if sub:
            live[rid] = sub
    if not live:
        return None, {}
    lab = np.asarray(labels, dtype=np.int32)
    if lab.ndim != 2:
        raise ValueError(f"region labels must be (h,w), got {lab.shape}")
    return lab, live


def params_for(cfg, regions):
    """The region COLOUR plan for a config, or None when no region asks for a colour.

    None rather than a plan with one empty region, for the reason every other control in
    this repo returns None: the region path gathers and scatters even where it grades with
    the base block, and "gather, grade, scatter" is only bit-identical to "grade" because
    the arithmetic is elementwise -- there is no reason to spend it proving that on a
    picture nobody asked to divide.

    `regions` is `{"labels": (h,w) ints, "overrides": {id: {field: value}}}`. A region that
    names only flow fields is not a colour region and does not appear here at all; see
    `flow_params_for` for the other half.
    """
    lab, live = _live(regions, REGION_COLOR_PARAMS)
    if lab is None:
        return None
    return {"labels": lab, "overrides": live}


def flow_params_for(cfg, regions, vortices=None):
    """The region FLOW plan, or None when no region asks for a flow of its own.

    `{"labels": (h,w) ints, "blocks": {id: flow block or None}}` -- one block straight out
    of `flow.params_for`, so a region's dropdown and its five trims read exactly as the
    base's do and there is no second copy of that arithmetic. A block of None is a region
    that asked for `flow='none'` (or strength 0) over a base that has one, which is a real
    thing to want -- the figure left alone under a swirling sky -- and it is served by
    SKIPPING those strokes rather than by blending an identity into them.

    Unlike the colour plan this is needed BEFORE the strokes exist, because what it changes
    is where they point; `pipeline.plan` therefore calls it up next to `flow.params_for`
    rather than at the end. `vortices` takes either shape `flow.vortex_map` accepts, and each
    region gets the set `flow.centres_for` says it uses -- its own, or the base's inherited.
    """
    lab, live = _live(regions, REGION_FLOW_PARAMS)
    if lab is None:
        return None
    vmap = flow_mod.vortex_map(vortices)
    return {"labels": lab,
            "blocks": {rid: flow_mod.params_for(replace(cfg, **ov),
                                                flow_mod.centres_for(vmap, rid))
                       for rid, ov in live.items()}}


def flow_undrifted(spec):
    """The same flow plan with every region's `drift` off -- what the underpainting gets.

    Mirrors what `pipeline.plan` already does to the base block, and for the same reason:
    that layer's whole job is to COVER, and drift makes strokes bunch along the flow lines
    and open gaps between them. The field still turns the ground's strokes.
    """
    if spec is None:
        return None
    return dict(spec, blocks={rid: (None if p is None else dict(p, drift=0.0))
                              for rid, p in spec["blocks"].items()})


def live_ids(*specs):
    """The regions live in ANY of the given plans -- what the info dict counts and reports.

    Two plans, one number: a layer that only re-aims the flow is as live as one that only
    regrades, and a panel that said "0 regions" because the colour half was empty would be
    reporting the still-image failure's own symptom at exactly the wrong moment.
    """
    out = set()
    for spec in specs:
        if spec:
            out |= set(spec["overrides"] if "overrides" in spec else spec["blocks"])
    return sorted(out)


def stroke_labels(spec, x, y):
    """The region id under each stroke centre. Truncation, not rounding, on purpose.

    `np.round` is half-to-even and JS `Math.round` is half-up, which is exactly the one-ulp
    fork this project cannot see by looking -- and here it would not be one ulp, it would be
    a stroke on the wrong side of a boundary. `floor` agrees in both languages for every
    finite value, and the clip is not defensive: `jitter_centre` can push a stroke centre a
    little outside the canvas, and such a stroke still has to belong to a region.
    """
    lab = spec["labels"]
    h, w = lab.shape
    xi = np.clip(np.floor(np.asarray(x, dtype=np.float64)), 0, w - 1).astype(np.int32)
    yi = np.clip(np.floor(np.asarray(y, dtype=np.float64)), 0, h - 1).astype(np.int32)
    return lab[yi, xi]


def blocks_for(cfg, spec, gp, rgb, lab, linear=False):
    """`{region id: grade block or None}` for the base and every live region.

    Built once and used twice -- for the strokes and for the canvas under them -- because
    one of these blocks can carry a fitted optimal transport, and fitting it twice against
    two different sets of colours would grade the canvas toward a different painting than
    the strokes on top of it.

    THE TRANSPORT IS FITTED OVER THE COLOURS IT WILL BE APPLIED TO, which for a region that
    names its own reference means that region's strokes: the whole point of putting
    'starry-night' on the sky is that the sky's own distribution is what gets moved onto
    it, and fitting over the whole photograph would aim the map with the field and the
    figures included. A region that does NOT name its own reference inherits the base's
    already-fitted transport instead of refitting the same reference over a subset -- same
    colour match, different tubes -- and that inheritance is also what keeps the
    equivalence invariant true: give every region the base's own values and there is
    exactly one fit in the picture, so the result is bit-identical.
    """
    base_xfer = None if gp is None else gp.get("xfer")
    blocks = {0: gp}
    for rid, ov in spec["overrides"].items():
        sub = replace(cfg, **ov)
        g = palette_mod.params_for(sub)
        rp = reference_mod.params_for(sub)
        if rp is not None:
            # Mirrors `plan`: a reference with no palette behind it still needs a block to
            # ride on, and `block_for` is that block with no activity test in front of it.
            g = g if g is not None else palette_mod.block_for(sub)
            if "reference" in ov or "reference_strength" in ov:
                sel = rgb[lab == rid]
                # A region nobody painted has no strokes to fit against. Skipping is the
                # only honest answer -- a transport fitted on an empty set is a singular
                # matrix, and the region has no pixels for it to be wrong on anyway.
                g = None if len(sel) == 0 else dict(
                    g, xfer=reference_mod.fit(sel, rp, linear=linear))
            else:
                g = dict(g, xfer=base_xfer)
        blocks[rid] = g
    return blocks


def apply_strokes(rgb, phase, lab, blocks, linear=False):
    """Grade an (n,3) stroke buffer region by region. Returns a new float32 array.

    Every region is a gather, a grade and a scatter, and the grade is `palette.grade_strokes`
    itself -- the same routine, the same block shape, the same arithmetic. That is what
    makes this a placement of an existing control rather than a second copy of it.
    """
    out = np.asarray(rgb, dtype=np.float32).copy()
    for rid in sorted(blocks):
        gp = blocks[rid]
        if gp is None:
            continue
        idx = np.flatnonzero(lab == rid)
        if idx.size == 0:
            continue
        out[idx] = palette_mod.grade_strokes(
            np.asarray(rgb, dtype=np.float32)[idx], np.asarray(phase)[idx], gp, linear=linear)
    return out


def apply_image(img, labels, blocks, linear=False):
    """The same for the (h,w,3) base canvas: per pixel, and neither per-dab effect.

    The canvas is graded region by region for one reason that is easy to miss -- it shows
    through the ~12% of the picture the strokes do not cover, and a warm ground under a
    cool sky is visible exactly where it should not be.
    """
    shape = np.shape(img)
    flat = np.asarray(img, dtype=np.float32).reshape(-1, 3)
    out = flat.copy()
    lab = np.asarray(labels, dtype=np.int32).reshape(-1)
    for rid in sorted(blocks):
        gp = blocks[rid]
        if gp is None:
            continue
        idx = np.flatnonzero(lab == rid)
        if idx.size == 0:
            continue
        out[idx] = palette_mod.grade_image(flat[idx], gp, linear=linear)
    return out.reshape(shape)


def summary(ids, lab):
    """`{region id: stroke count}` for the info dict -- what the mask actually caught.

    A region with zero strokes is the failure this reports: the mask was painted at one
    size and the picture rendered at another, or the fill landed somewhere the quadtree
    spent nothing. It is the same class of quiet failure as the still-image video, so it is
    counted rather than assumed.

    `ids` rather than a plan, because there are two plans and a region is live if it is in
    either; `lab` is the colour labelling, off the strokes' final centres, so a flow-only
    region is counted where its marks LANDED. That is the right answer for a guard that
    asks "did the mask catch anything" -- the pixels are where the mask was painted.
    """
    return {int(rid): int(np.count_nonzero(lab == rid)) for rid in ids}
