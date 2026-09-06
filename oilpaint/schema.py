"""The tuning control surface: one row per `PaintConfig` field, and the drift guard.

This lived in `web/tune/serve_tune.py` until the tuner became a static page. It has to sit
in the package now because there are two front ends -- the local server and the browser
build, which imports this module under Pyodide -- and a control surface that exists twice
is a control surface that disagrees with itself.

The assertion at the bottom is the point of the file: importing it fails loudly when
`PaintConfig` gains a field and this list does not. The failure it prevents is a parameter
that silently cannot be tuned because nobody noticed it was missing from the panel.

`LABELS` and `GROUP_NOTES` are the same file doing the same job for the WORDS -- the name a
person reads on a control, and the line under each card heading. They are here rather than
in a front end because a control described twice is a control described differently, and
they are guarded by the same kind of assertion: a field arriving without a human name fails
the import exactly as one arriving without a control does.
"""

from dataclasses import fields

from .detail import METRICS
from .flow import FLOWS
from .palette import PALETTES
from .reference import NAMES as REFERENCES
from .pipeline import PaintConfig

SCHEMA = [
    # group, field, kind, min, max, step, help
    ("Allocation", "metric", "choice", METRICS, None, None,
     "Where the tree spends strokes. 'var' is the classic quadtree criterion."),
    ("Allocation", "target_n", "int", 200, 40000, 100,
     "Stroke budget. A CEILING, not a quota -- tau_floor can stop it being reached."),
    ("Allocation", "max_cell", "int", 8, 256, 4,
     "Largest stroke, in pixels."),
    ("Allocation", "min_cell", "int", 2, 64, 1,
     "Smallest stroke. This is the fine end of the range -- where an eye, a hand or a roof "
     "edge gets its definition -- so it is a floor on what the budget may buy, not a size "
     "the picture is made of. Much below ~5px a mark stops reading as a stroke at all and "
     "starts reading as grain."),
    ("Allocation", "tau_floor", "float", 0.0, 0.002, 0.00002,
     "Absolute detail floor the budget cannot buy past. Stops the tree subdividing "
     "JPEG noise in flat regions. Calibrated for metric='var'."),

    # The foveal map itself is not here: it is an image, painted on the page or passed to
    # the CLI as a file, so there is no slider that could carry it. This one row says what
    # the allocator does with one, and it is inert until a map arrives.
    ("Allocation", "foveal_strength", "float", 0.0, 1.0, 0.01,
     "How much the painted foveal map steers the stroke budget. 0 ignores it entirely, 1 "
     "makes it a hard gate where an unmarked cell cannot subdivide at all, and between the "
     "two it blends with the content metric. The budget is REDISTRIBUTED, never increased "
     "-- so if target_n is already unreachable there is nothing for the map to move and "
     "this will look inert. Lower target_n first."),

    # The pigment grade. `palette` is the one control that carries a whole look; the four
    # numbers under it are TRIMS on whatever it chose, so 0 means "as the preset wrote it"
    # rather than "off". See oilpaint/palette.py.
    ("Palette", "palette", "choice", PALETTES, None, None,
     "The colour a painter would have mixed, rather than the one the camera recorded. "
     "'none' is the photograph's own colour and every trim below still works on it; the "
     "rest each stand for a real palette -- 'zorn' is four pigments and cannot make a "
     "saturated blue, 'impressionist' has no black on it at all and puts violet in the "
     "shadows, 'old-master' is earths under a varnish, 'fauve' is colour off the leash."),
    ("Palette", "palette_strength", "float", 0.0, 1.0, 0.01,
     "How far toward the graded colour the paint goes. 1 is the preset as written, 0 is "
     "the photograph and skips the grade entirely."),
    ("Palette", "warm_cool", "float", -1.0, 1.0, 0.01,
     "The warm-cool split, on top of the preset's own. Positive swings the lights toward "
     "the light's hue and the shadows toward its complement -- the single strongest cue "
     "that a colour was mixed rather than measured. Negative swaps the two."),
    ("Palette", "chroma", "float", -0.6, 0.8, 0.01,
     "Chroma trim. Down toward a limited palette, which cannot reach a camera's most "
     "saturated colours; up toward paint used straight from the tube."),
    ("Palette", "value_compress", "float", 0.0, 1.0, 0.01,
     "Pulls the darkest and lightest paint off the ends of the range. Oil has no pure "
     "black and no pure white, and the tinted ends the preset supplies only become "
     "visible once there is room for them."),
    ("Palette", "pigment", "float", -0.6, 0.6, 0.01,
     "How far each stroke is pulled onto a colour the palette can actually MIX. Every "
     "preset names real tubes, and this is what makes the name binding rather than "
     "decorative: at the top of the range a Zorn painting contains no blue anywhere, "
     "because four pigments cannot make one. A TRIM like the rest -- 0 is whatever the "
     "preset asked for, and the range goes negative so a preset's own projection can be "
     "turned off. INERT at palette = 'none', which has no pigments to project onto. The "
     "ground is never projected: a stroke is one mixture, a blurred gradient is not."),
    ("Palette", "broken_color", "float", 0.0, 1.0, 0.01,
     "Broken colour: neighbouring strokes alternate warm and cool about their own hue, so "
     "the eye mixes them rather than the palette knife. Unlike color_jitter this is a "
     "HUE alternation and it costs no random draw -- it rides the stroke's existing "
     "phase, so the painting's geometry is untouched at any value."),

    # Hue-band steering. Four numbers describing ONE band, applied before everything else
    # in the grade so it reads the photograph's own hue -- and before the pigment
    # projection, so the palette still has the last word on what it can mix.
    # The reference match. A dropdown and one slider, because the whole point of it is that
    # it needs no expertise: pick a painting, decide how far to go.
    ("Palette", "reference", "choice", REFERENCES, None, None,
     "Move this photograph's colours onto a named painting's, by optimal transport -- the "
     "mean AND the covariance, so 'the darks go blue while the lights go yellow' comes "
     "across, not just an average tint. It composes with the palette above rather than "
     "replacing it: the reference decides the STATISTICS, the palette decides which tubes "
     "the strokes may land on. Try 'starry-night' with palette 'impressionist'."),
    ("Palette", "reference_strength", "float", 0.0, 1.0, 0.01,
     "How far toward the reference's colour distribution the painting goes. 1 lands on it "
     "exactly; the useful range is usually 0.5-0.8, where the photograph still has its own "
     "identity. 0 skips the transport entirely."),

    ("Palette", "hue_target", "float", 0.0, 360.0, 1.0,
     "Which family of hues the band below grabs, in degrees of the OKLab colour circle: "
     "~30 red, ~70 orange-yellow, ~140 green, ~260 blue, ~330 magenta. Inert on its own "
     "-- it says WHERE to work, and hue_rotate/hue_boost say what to do there."),
    ("Palette", "hue_range", "float", 5.0, 180.0, 1.0,
     "How wide that family is, in degrees. Narrow picks out one colour (the foliage in a "
     "landscape); at the top of the range the band covers the whole circle and this "
     "becomes an ordinary global hue rotation -- which is the point, a global turn is the "
     "widest case of a control that can do better."),
    ("Palette", "hue_rotate", "float", -180.0, 180.0, 1.0,
     "How far to turn that family, in degrees, leaving every other hue where it is. The "
     "classic use is foliage: digital green is a hue paint can barely make, and pulling "
     "just the greens toward yellow-green fixes what a global rotation only trades for a "
     "different wrongness."),
    ("Palette", "hue_boost", "float", -1.0, 1.0, 0.01,
     "Chroma gain inside the band alone: up to make one colour sing against a muted "
     "picture, down to -1 to take it out entirely without touching the rest. Applied to "
     "the band's own hue, so it is a saturation control for one family rather than for "
     "the picture."),

    # The flow field. `flow` is the structural twin of `palette` -- it carries a whole look,
    # and the five numbers under it are TRIMS on whatever it chose. See oilpaint/flow.py.
    ("Flow", "flow", "choice", FLOWS, None, None,
     "The organisation a PAINTER brings to the marks, as opposed to the one the subject "
     "has. 'none' follows the photograph's own structure and every trim below is inert. "
     "'starry' is counter-rotating vortices and long chained ribbons; 'waterlily' is a "
     "near-horizontal weave of short dabs, and the SHORTNESS is doing as much of the work "
     "as the direction; 'hatch' is Cezanne's constructive stroke, one diagonal laid over "
     "everything. Nothing here moves a quadtree leaf, a stroke's size or its colour. A "
     "swirl field puts its vortices on a golden-angle spiral by default; on the tuner page "
     "the 'Swirl centres' tool places them by hand instead, and the CLI takes --vortices."),
    ("Flow", "flow_strength", "float", 0.0, 1.0, 0.01,
     "How far the field takes over from the picture's own structure. Low values fill only "
     "the passages where the tensor had no signal anyway -- a sky, a flat ground -- and 1 "
     "overrules real edges too, which is what van Gogh does to a cypress. 0 skips the "
     "whole thing and is bit-identical to 'none'."),
    ("Flow", "flow_coh", "float", -0.6, 0.6, 0.01,
     "Ribbons vs dabs, on top of the preset's own choice. Elongation is driven by "
     "coherence, so asserting a high one makes every mark stretch to aniso_max and a low "
     "one keeps them stubby. NEGATIVE range on purpose: without it a preset's ribbons "
     "could not be turned back into dabs and the other knobs could not be judged."),
    ("Flow", "flow_scale", "float", -0.75, 2.0, 0.01,
     "Feature size trim -- swirl radius, wave wavelength and the turbulence together. "
     "-0.75 is a quarter of the preset's scale, 2.0 is three times it. In normalised "
     "canvas units, so a resize does not change the field. INERT at flow = 'none'."),
    ("Flow", "flow_rot", "float", -180.0, 180.0, 5.0,
     "Rotates the field, in degrees, on top of the preset's own direction. For 'waterlily' "
     "this is the water plane; for 'hatch' it is which way the hatching runs; for a swirl "
     "it turns the spiral of vortices AND the background drift they sit on. Hand-placed "
     "centres are not turned by it -- they stay where they were put, and only the drift "
     "between them swings. INERT at flow = 'none'."),
    ("Flow", "flow_drift", "float", -1.0, 1.0, 0.01,
     "How far each stroke slides ALONG the flow, in stroke radii. The only term here that "
     "moves a mark rather than turning it: it makes strokes lying on a flow line bunch and "
     "read as a CHAIN following the curve, which orientation alone cannot do. Colours do "
     "not move with it -- a stroke still carries its own cell's paint. Negative so a "
     "preset's drift can be cancelled -- the range reaches past the largest drift any "
     "preset asks for, which tests/test_core.py checks -- and far enough to run the marks "
     "BACKWARDS along the flow. Never applied to the underpainting, which has to cover."),

    ("Geometry", "kappa", "float", 0.7, 2.0, 0.01,
     "Radius vs cell edge. 1.414 (sqrt 2) exactly covers a square cell."),
    ("Geometry", "orient", "choice", ["structure", "random", "fixed"], None, None,
     "Stroke angle source. 'structure' follows the form; 'random' reads as confetti."),
    ("Geometry", "aniso_max", "float", 1.0, 12.0, 0.1,
     "Max elongation on a fully coherent edge. 1.0 = round blobs = pointillist dots; the "
     "top of the range gives long dragged marks that follow the form."),
    ("Geometry", "tensor_sigma", "float", 0.5, 8.0, 0.1,
     "Structure-tensor smoothing. Larger = calmer, more coherent flow field."),
    ("Geometry", "flat_dir", "float", 0.0, 1.0, 0.01,
     "The flat-region directional brush. Where the detail-scale tensor has no signal -- "
     "sky, a gradient ground, an out-of-focus background -- its angle is the arctangent of "
     "noise and its coherence is 0, so the strokes come out as randomly-turned round blobs. "
     "This hands those places over to a coarse-scale reading of the same image and lets "
     "them elongate along it, the way a painter lays a flat passage in with one sweep. "
     "0 is off and bit-identical to before; coherent edges are untouched at any value."),
    ("Geometry", "flat_sigma", "float", 1.0, 32.0, 0.5,
     "The scale, in pixels, that the flat brush reads the picture at. Too small and it "
     "just recovers the same noise; too large and a sky's whole direction collapses to one "
     "angle. INERT at flat_dir = 0."),
    ("Geometry", "flat_theta_deg", "float", 0.0, 180.0, 5.0,
     "Which way the flat brush sweeps where even the coarse scale finds nothing -- a truly "
     "uniform ground. 0 is horizontal, 90 vertical. INERT at flat_dir = 0, and it never "
     "reaches anywhere that has a gradient to follow instead."),
    ("Geometry", "jitter_centre", "float", 0.0, 1.0, 0.01,
     "Centre jitter within the cell. REQUIRED -- at 0 the centres sit on a lattice."),
    ("Geometry", "jitter_theta_deg", "float", 0.0, 90.0, 1.0,
     "Random angle spread around the base orientation [Litwinowicz97]."),
    ("Geometry", "jitter_radius", "float", 0.0, 0.8, 0.01,
     "Uniform radius jitter. Bounded -- cannot leave its octave; see size_sigma."),
    ("Geometry", "jitter_aniso", "float", 0.0, 1.0, 0.01,
     "Random spread on the elongation ratio."),

    ("Irregularity", "size_sigma", "float", 0.0, 1.0, 0.01,
     "Log-normal spread on radius. The main cure for 'every stroke is the same size'."),
    ("Irregularity", "wobble_amp", "float", 0.0, 0.4, 0.01,
     "Ragged outline instead of a perfect ellipse."),
    ("Irregularity", "color_jitter", "float", 0.0, 0.15, 0.005,
     "Per-stroke pigment variation. Off by default -- in flat regions it reads as speckle "
     "rather than as loaded paint. Keep low if you turn it on."),
    ("Irregularity", "drop_p", "float", 0.0, 0.8, 0.01,
     "Random drop with area-compensating expansion. Neutral at matched stroke count; "
     "largely subsumed by size_sigma."),
    ("Irregularity", "bristle_amp", "float", 0.0, 0.8, 0.01,
     "Bristle grooves along the stroke [Hertzmann02]. Cuts real holes in the alpha, so "
     "the coverage figure genuinely falls as this rises."),
    ("Irregularity", "fringe_px", "float", 0.0, 6.0, 0.1,
     "Fractal roughness on the outline, IN PIXELS -- the crumble is the same size on a "
     "130px blob as on an 8px dab. The cure for a smooth, CG-looking boundary; wobble_amp "
     "cannot fix that, because its harmonics scale with the stroke."),
    ("Irregularity", "taper_amp", "float", 0.0, 0.7, 0.01,
     "Thins the stroke towards one end -- the cue an ellipse cannot carry. Worth more "
     "than the bristles are."),
    ("Irregularity", "seed", "int", 0, 9999, 1, "Random seed."),

    ("Paint", "base", "choice", ["blur", "strokes", "none"], None, None,
     "Ground under the strokes. 'blur' is the default and looks best -- it leaves the "
     "canvas weave visible and lets the detail strokes carry the picture. 'strokes' is a "
     "full coarse underpainting [Hertzmann98], which at this stroke size reads as clutter. "
     "'none' shows the holes."),
    ("Paint", "base_cell_scale", "float", 0.5, 6.0, 0.1,
     "Underpainting stroke size, relative to max_cell. INERT unless base = 'strokes'."),
    ("Paint", "base_block", "int", 2, 64, 2,
     "Blur-base block size (also the safety canvas under the stroke underpainting)."),
    ("Paint", "hard", "bool", None, None, None,
     "Hard-edged ellipsoids vs soft Gaussian falloff. Hard is the demo's look."),
    ("Paint", "hard_r", "float", 0.5, 2.0, 0.01,
     "Hard-edge cutoff, in sigmas."),
    ("Paint", "alpha", "float", 0.05, 1.0, 0.01,
     "Per-stroke opacity. Below 1 the strokes glaze over each other."),
    ("Paint", "color", "choice", ["median", "mean", "centre"], None, None,
     "Cell colour rule. 'mean' goes muddy across an edge."),
    ("Paint", "linear", "bool", None, None, None,
     "Average and composite in linear light rather than sRGB."),

    # Step 2 [Hertzmann02]. A second buffer accumulates stroke height, and the painting is
    # bump-mapped from it. Off, the pipeline is exactly step 1.
    ("Impasto", "impasto", "bool", None, None, None,
     "Height field + lighting [Hertzmann02]. Off = flat paint, and no height pass runs."),
    ("Impasto", "impasto_layer", "float", 0.0, 3.0, 0.05,
     "Height gained from the first stroke to the last. This is what makes a stroke edge a "
     "RIDGE, and it is most of the effect -- try it with relief at 0."),
    ("Impasto", "impasto_relief", "float", 0.0, 2.0, 0.05,
     "Bristle grooves as relief. Independent of bristle_amp: paint can be thicker without "
     "being more opaque."),
    ("Impasto", "canvas_weave", "float", 0.0, 1.0, 0.01,
     "Canvas tooth, added to the height field only where the paint is thin."),
    ("Impasto", "impasto_depth", "float", 0.0, 1.5, 0.01,
     "Height-to-slope gain: how thick the paint reads. At 0 the lighting is a no-op."),
    ("Impasto", "light_deg", "float", 0.0, 360.0, 5.0,
     "Light azimuth as a screen compass -- 0 from the right, 90 from the top."),
    ("Impasto", "light_elev_deg", "float", 5.0, 85.0, 1.0,
     "Light elevation. Low rakes across the relief; high flattens it."),
    ("Impasto", "gloss", "float", 0.0, 1.0, 0.01,
     "0 matte tempera, 1 wet oil. Drives both the specular strength and its tightness."),
    ("Impasto", "occlusion", "float", 0.0, 4.0, 0.05,
     "Ambient occlusion -- paint shading itself where strokes stack. Darkens creases only, "
     "and does NOT move with the light or the eye. If this ever reads as a drop shadow, "
     "the cause is the ground height, not this knob -- see render.GROUND_AT."),
    ("Impasto", "view_elev_deg", "float", 15.0, 90.0, 1.0,
     "The eye. 90 is straight on; lower leans away and the sheen swings across the "
     "relief. The view-dependent term -- drive it from the pointer for a live painting."),
    ("Impasto", "view_deg", "float", 0.0, 360.0, 5.0,
     "Which way the eye leans, as a screen compass. Inert at view_elev_deg = 90."),
]

# ---------------------------------------------------------------------------------------
# The HUMAN NAME of each control, and its unit.
#
# The field name is the contract -- it is what `scripts/paint.py` takes, what `Copy setup`
# writes and what a saved session stores -- so it is never replaced, only led. Both front
# ends show the label first and the field name under it in small type, which is what lets
# someone who found a look on the page reproduce it from the command line without guessing.
#
# It lives HERE, beside SCHEMA, for the reason SCHEMA lives here: a control surface that
# exists twice is a control surface that disagrees with itself. The assertion below is the
# whole point of the dict -- a new PaintConfig field fails the import until it has a name a
# person can read, exactly as it already fails until it has a control.
#
# Style, and it is a rule rather than a taste: the label says WHAT MOVES, in the words
# someone looking at a painting would use, and never restates the field name in spaces
# ("tau_floor" -> "Detail floor", not "Tau floor"). A unit is written only where the number
# means nothing without one.
LABELS = {
    # group, field: (label, unit)
    "metric":            ("Where detail goes", ""),
    "target_n":          ("Stroke budget", ""),
    "max_cell":          ("Largest stroke", "px"),
    "min_cell":          ("Smallest stroke", "px"),
    "tau_floor":         ("Detail floor", ""),
    "foveal_strength":   ("Follow the focus map", ""),

    "palette":           ("Palette", ""),
    "palette_strength":  ("Palette strength", ""),
    "warm_cool":         ("Warm–cool split", ""),
    "chroma":            ("Colour intensity", ""),
    "value_compress":    ("Tonal range squeeze", ""),
    "pigment":           ("Mix from real tubes", ""),
    "broken_color":      ("Broken colour", ""),
    "reference":         ("Match a painting", ""),
    "reference_strength": ("Match strength", ""),
    "hue_target":        ("Hue band centre", "°"),
    "hue_range":         ("Hue band width", "°"),
    "hue_rotate":        ("Turn those hues", "°"),
    "hue_boost":         ("Intensify those hues", ""),

    "flow":              ("Flow field", ""),
    "flow_strength":     ("Flow strength", ""),
    "flow_coh":          ("Ribbons vs dabs", ""),
    "flow_scale":        ("Feature size", ""),
    "flow_rot":          ("Field rotation", "°"),
    "flow_drift":        ("Slide along the flow", ""),

    "kappa":             ("Stroke size vs cell", ""),
    "orient":            ("Stroke angle from", ""),
    "aniso_max":         ("Longest elongation", "×"),
    "tensor_sigma":      ("Direction smoothing", "px"),
    "flat_dir":          ("Flat-area brush", ""),
    "flat_sigma":        ("Flat-area scale", "px"),
    "flat_theta_deg":    ("Flat-area sweep", "°"),
    "jitter_centre":     ("Position jitter", ""),
    "jitter_theta_deg":  ("Angle jitter", "°"),
    "jitter_radius":     ("Size jitter", ""),
    "jitter_aniso":      ("Elongation jitter", ""),

    "size_sigma":        ("Stroke size spread", ""),
    "wobble_amp":        ("Ragged outline", ""),
    "color_jitter":      ("Colour variation", ""),
    "drop_p":            ("Random dropout", ""),
    "bristle_amp":       ("Bristle grooves", ""),
    "fringe_px":         ("Edge crumble", "px"),
    "taper_amp":         ("Taper to one end", ""),
    "seed":              ("Random seed", ""),

    "base":              ("Ground under the strokes", ""),
    "base_cell_scale":   ("Underpainting stroke size", "×"),
    "base_block":        ("Ground block size", "px"),
    "hard":              ("Hard-edged strokes", ""),
    "hard_r":            ("Hard-edge cutoff", "σ"),
    "alpha":             ("Stroke opacity", ""),
    "color":             ("Cell colour rule", ""),
    "linear":            ("Blend in linear light", ""),

    "impasto":           ("Thick paint", ""),
    "impasto_layer":     ("Paint build-up", ""),
    "impasto_relief":    ("Groove depth", ""),
    "canvas_weave":      ("Canvas tooth", ""),
    "impasto_depth":     ("Paint thickness", ""),
    "light_deg":         ("Light direction", "°"),
    "light_elev_deg":    ("Light height", "°"),
    "gloss":             ("Gloss", ""),
    "occlusion":         ("Crease shadow", ""),
    "view_elev_deg":     ("Viewing angle", "°"),
    "view_deg":          ("Viewing direction", "°"),
}

# One line under each card heading, in the same register as the labels. A group name is a
# filing decision -- it says which drawer a control is in, not what the drawer is for -- and
# seven of them in a row is the point at which a panel stops being self-explanatory.
GROUP_NOTES = {
    "Allocation":   "Where the strokes go, and how many.",
    "Palette":      "Which tubes the paint is mixed from, and how the mixture is graded.",
    "Flow":         "Which way the marks run, whatever the photograph is of.",
    "Geometry":     "The size, angle and elongation of a single mark.",
    "Irregularity": "What stops every stroke looking like the last one.",
    "Paint":        "How the paint is laid down and blended.",
    "Impasto":      "Paint with thickness, lit from a direction you choose.",
}

_SCHEMA_FIELDS = {row[1] for row in SCHEMA}
_CFG_FIELDS = {f.name for f in fields(PaintConfig)} - {"tau"}  # tau is bypassed by target_n
_MISSING = _CFG_FIELDS - _SCHEMA_FIELDS
_EXTRA = _SCHEMA_FIELDS - _CFG_FIELDS
assert not _MISSING, f"PaintConfig fields with no control: {sorted(_MISSING)}"
assert not _EXTRA, f"controls with no PaintConfig field: {sorted(_EXTRA)}"

# The same guard, for the same failure one step further on: a control nobody can read is
# only marginally better than a control nobody has. A field arriving without a label would
# otherwise fall back to its own name on the panel and nothing would ever say so.
_UNNAMED = _SCHEMA_FIELDS - set(LABELS)
_STRAY = set(LABELS) - _SCHEMA_FIELDS
assert not _UNNAMED, f"controls with no human label: {sorted(_UNNAMED)}"
assert not _STRAY, f"labels for controls that do not exist: {sorted(_STRAY)}"
_NOTELESS = {row[0] for row in SCHEMA} - set(GROUP_NOTES)
assert not _NOTELESS, f"control groups with no note: {sorted(_NOTELESS)}"


def defaults():
    """Every tunable field at its `PaintConfig` default."""
    d = PaintConfig()
    return {f.name: getattr(d, f.name) for f in fields(PaintConfig) if f.name != "tau"}


def as_dict():
    """The payload the control panel is built from, server or browser."""
    return {
        "schema": [
            {"group": g, "name": n, "kind": k, "min": lo, "max": hi, "step": st, "help": hp,
             "label": LABELS[n][0], "unit": LABELS[n][1]}
            for (g, n, k, lo, hi, st, hp) in SCHEMA
        ],
        "defaults": defaults(),
        "groupNotes": GROUP_NOTES,
    }
