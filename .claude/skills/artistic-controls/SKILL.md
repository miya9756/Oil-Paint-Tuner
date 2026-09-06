---
name: artistic-controls
description: How a look control — colour, texture, light, anything that makes a photograph read as paint rather than as a photograph — attaches to this pipeline without moving a stroke, shifting the RNG stream or breaking the port. The six attachment points and what each one costs, the rules that keep a new knob inert by default, the parity arms it owes, and the ideas already designed but not yet built. Read BEFORE adding any new look parameter to PaintConfig.
---

# Adding an artistic control

The pipeline turns a photograph into a painting. Every parameter that exists to make it
look **more like paint and less like the photograph** is an artistic control, and they all
face the same question first:

> Where in the pipeline does it attach?

That is not a style question. It decides how much of the existing test surface the change
puts at risk, and the range is enormous — from "costs nothing that is already pinned" to
"every painting anyone has ever made with this repo is now different".

## The six attachment points, cheapest first

`pipeline.plan()` runs: detail field → tau search → quadtree → structure tensor →
`strokes.from_cells` → base canvas. Then `render()` rasterises, then `finish()` lights.

| where | what it can see | what it costs |
| --- | --- | --- |
| **After the geometry**, on `sb.rgb` and the canvas (end of `plan`) | one colour per stroke, one per canvas pixel, **and where each stroke is** | **Nothing that is pinned.** The leaves, stroke order, angles, radii and the RNG stream are all already decided. This is where `oilpaint/palette.py` attaches — and, because `sb.x`/`sb.y` are right there, `oilpaint/regions.py` too. |
| **In `finish()`**, after the lighting | the finished picture only | Cheap, but it is a photo filter at that point — it cannot know what a stroke is. |
| **In `render.py`** — per-stroke, per-pixel rasterisation | the stroke's shape, its coverage, the height field | One new arm in `tests/test_raster_parity.py`. Fine, but the rasteriser is the hot loop: measure. |
| **In step 3 of `strokes.from_cells`** — the orientation branch | the stroke's own position, the tensor's angle and coherence there | **Cheap, and this is not obvious.** Step 3 is the only step of `from_cells` that draws NO random number, so a control that lives there moves no leaf, no radius, no colour and no paint order. This is where `oilpaint/flow.py` attaches — and, because the stroke's position is right there too, where `regions.py`'s FLOW half attaches. One new arm in `test_pipeline_parity.py`; nothing else already pinned can move. |
| **Anywhere else in `strokes.from_cells`** | the cell, the tensor, everything | **Expensive.** Any new `rng.*` call shifts the draw stream and gives a completely different painting from the same seed. See below. |
| **Before `plan`**, grading the source image | everything downstream | **Almost never right.** It moves the detail field, therefore tau, therefore the quadtree, therefore every stroke. A colour control that changes where the strokes go is a bug wearing a feature's clothes. |

The **first** row is the default answer for anything about **colour**; the **third** is the
default answer for anything about **structure** — which way the marks run, and how long they
are. Note what separates the third row from the fifth: the cost in that last row is the RNG
stream, not the file, so a control that arrives at `from_cells` without drawing a random
number pays almost none of it. `flow.py` is the worked example, down to evaluating its field
analytically per stroke rather than as an image-sized array.

The reason the first row is the answer for colour is not
only that it is cheap. A stroke is one dab of one mixture — that is what paint *is*. Per
stroke, "project onto a limited palette" is a palette; per pixel it is a posterise filter
that bands every smooth passage. The placement is the design, not an optimisation.

## The RNG rule, which outranks everything

From the root `CLAUDE.md`, and it is not an exaggeration:

> **the RNG draw ORDER is the algorithm.** Adding, removing or reordering one `rng.*` call
> in `strokes.js` shifts the whole stream and gives a completely different painting.

So: **a new artistic control must not draw a random number.** If it needs per-stroke
variation, use what `StrokeBuffer` already carries — `phase`, uniform on `[0, 2π)`, drawn
once per stroke and otherwise only seeding the outline wobble. `palette.py`'s broken-colour
alternation is `sin(phase)` for exactly this reason, and it therefore costs **zero** in the
parity suites at any value.

If you genuinely cannot avoid a draw, it goes at the very **end** of the existing sequence
in `from_cells`, never in the middle, and you say so in a comment — and every stored result
still changes.

## The five rules for a new knob

1. **Inert at the default, and inert by SKIPPING.** Not "an identity transform" — an
   actual early return. `palette.params_for()` returns `None` when nothing is asked for, so
   the default painting never round-trips through OKLab; a float32 → float64 → cube root →
   float32 identity is *not* the identity. Prove it with `np.array_equal`, not `allclose`.
2. **One `PaintConfig` field per knob, plus a row in `oilpaint/schema.py`.** The assert at
   the bottom of that file fails on import if the two disagree — that is the whole point of
   it. The CLI picks the flag up automatically (`scripts/paint.py` loops `fields()`), and so
   does the panel.
3. **Presets supply a block; sliders TRIM it.** A named look is a full parameter block in
   the module; the exposed sliders are *added* to it, so 0 means "as the preset wrote it"
   rather than "off". Give at least one trim a negative range so a preset's own value can be
   cancelled — otherwise the other knobs cannot be judged on their own.
4. **float64 all the way through, `astype(np.float32)` once on the way out.** This is what
   makes the JS a transliteration instead of a reconstruction: a JS number *is* a float64,
   so the port is the same expressions in the same order with `Math.fround` exactly where
   numpy's `.astype` is. Write `x*x*x` rather than `x**3` on both sides — `pow` is correctly
   rounded and the multiply is not, and two roundings both sides agree on beat one rounding
   each side computes with a different libm.
5. **Write the "why" where the numbers are.** Every constant in this repo carries the
   measurement or the artistic claim that justifies it. A preset with no comment saying
   which pigments it stands for is a magic number.

## What it owes the tests

- **`tests/test_core.py`** — the invariant in the abstract, on synthetic data. For a colour
  control that means: inert at defaults; geometry bit-identical to an ungraded plan; and the
  *artistic* claim as a number. `zorn cannot mix a blue (-0.003 on the blue axis)` is a
  better test than any tolerance, because it is the thing the preset's name promises.
- **`tests/test_pipeline_parity.py`** + **`web/tune/pipeline_node.mjs`** — two arms, not
  one: a named preset with every branch live at once, and the trims alone with no preset,
  which is the case a port that read "none" as "skip" would get silently wrong. Add the
  configs to the `job` dict as `asdict(cfg)`; the node side plans and paints them.
- **Pin derived tables, not only the output.** The pigment mixture LUT is compared directly,
  per palette (`8.88e-16`, i.e. a double ulp). Without that, a divergence in the *mixing
  model* would surface only as "the browser one looks a bit off" — which is the exact class
  of bug this project cannot see by looking.
- **Assert the negative too.** `not np.allclose(graded, ungraded)` catches the arm that
  passes because the feature never ran.

## What the page gives you for free, and what it does not

Free: the control panel is generated from `schema.json`, so a new field with a schema row
gets its slider, its help text and its default with no page edit at all.

Not free:

- **`FRONT_GROUPS`** in `web/tune/index.html` decides which cards are open; everything else
  folds into Advanced. `HIDDEN` drops a control from the panel while leaving it in the
  schema for the CLI. `FINE` is the middle road: a trim that stays in its group but moves
  from the front card to a `<group> · fine` card under Advanced. A NEW trim lands on the
  front card by default, so decide which it is -- and decide with a number: the comment
  over `FINE` carries the measured effect of every Palette and Flow trim at the end of its
  range (`scripts/knob_impact.py` reproduces the table), and the bar for the front card is
  roughly half the effect of the preset itself.
- **Anything the page needs that is not a tunable number** rides on the worker's `ready`
  message — that is how `PIGMENTS` reaches the swatch strip under the palette dropdown.
  Never restate such a value in the page; a swatch that disagreed with the pigments doing
  the mixing is worse than no swatch.
- **A control with a picture TOOL attached puts the button on its own card**, via
  `CARD_TOOLS` — a table from a schema field name to a button the card builder appends under
  that field's row. The swirl centres went there from the toolbar for one reason worth
  keeping: the tool is live only when `flow` is swirl-kind, and a *dimmed button next to the
  dropdown that decides it* explains itself, where a dimmed button in a toolbar just looks
  broken. Build it fresh each time the panel is rebuilt — the panel is cleared with
  `innerHTML = ''` — and scroll the picture back into view when the tool opens, since the
  card is below the fold and the tool's whole request is a click on the picture.
- **Recompute a tool's availability with the PANEL, not with the tool's own bar.** The swirl
  button was refreshed only while its bar was open, so setting a layer to `starry` with the
  bar shut left it dimmed: correct when last computed, and nothing recomputed it. Anything
  whose enabled-ness depends on another control's value belongs in the same function that
  syncs the rows.
- **`conda run -n 4dre python web/tune/verify_page.py` after ANY `index.html` edit.** There
  is no build step; nothing else catches a broken edit.
- **A stat that changes meaning needs its label changed.** `psnr` reads
  `(vs the ungraded photo)` when a grade is on, because a graded painting scoring worse is
  the feature working, not a regression.

## Cost, with numbers

- Per **stroke** is ~4 orders of magnitude cheaper than per **pixel**: 5 000 strokes against
  a 1 Mpx canvas. Do the expensive thinking on strokes.
- The OKLab grade runs at ~1.5 Mpx/s in numpy. It was 0.45 Mpx/s before `grade()` was cut
  into cache-sized chunks (2224 → 666 ms per megapixel, elementwise-identical) — the cost
  was ~40 float64 temporaries, not the libm calls.
- The nearest-mixture projection: 10–30 ms at the default 5 000 strokes, 200–480 ms at the
  40 000 ceiling with the largest palette. Full scan, no tree — a few hundred rows is below
  where an acceleration structure pays for itself, and an approximate nearest neighbour
  would be a second thing to hold in parity for no gain.
- Anything applied to the **base canvas** gets its own memo slot in `makeCache()` (see
  `cache.graded`), keyed on the canvas's key *and* the effect's parameters. Grade it in
  place and you poison the cache for every later render.

## Built, and not yet built

**Built** — `oilpaint/flow.py` / `web/tune/oilpaint/flow.js`, the *Flow* card: a procedural
flow field over the stroke orientation, blended into the tensor's with the same doubled-angle
vector sum `strokes.flat_blend` uses. Three presets (`starry`, `waterlily`, `hatch`) over two
closed-form field kinds, plus `flow_drift`, the one term that moves a mark rather than turning
it. Two lessons worth carrying to the next structural control:

- **do not let one number serve two jobs.** The first version scaled the style's weight in
  the direction blend by the elongation it wanted, which made a style that asks for SHORT
  marks automatically a WEAK style — measured, Monet lost the argument 3.7 : 1 in any passage
  the tensor was confident about. `strength` (authority) and `coh` (mark shape) are separate.
- **the presets need a picture, not only a test.** Every invariant passed at `turb=0.35`, and
  the output still read as fur rather than as combed paint, because neighbouring strokes were
  scattered 30° apart. A statistic over all strokes cannot see that; the sunflowers could.

A look control's input is not always a number. `flow`'s swirl centres are a **list of
points**, so — exactly like the foveal map — they cannot be a `PaintConfig` field and arrive
as an argument to `plan`/`paint` instead, with the page supplying them from a click tool.
When you add one of those:

- **fall back, don't switch off.** An empty list means the preset's own procedural layout,
  not "none of them" — otherwise Clear hands the visitor a broken picture.
- **it will want to be per region eventually, so decide what ABSENCE means.** Once the flow
  could differ per passage, one global list of centres was wrong: a swirl placed in the sky
  was read by the field painting the ground. The shape that worked is `{region id: [points]}`
  with a bare list still meaning the base's — so no existing caller changed — and *absence*
  meaning "inherit the base's", not "empty". Inheritance is forced by the equivalence
  invariant (a region falling back to the preset's spiral while the base used placed centres
  is a different painting), and absence-rather-than-empty is what keeps one visible UI state
  meaning one thing: the page deletes a layer's key when its last marker goes, so two layers
  with no markers behave identically instead of differing by history.
- **route it through the transform the strokes already use.** `field_centres` calls
  `norm_coords`, so a point placed at a pixel lands where a stroke at that pixel lands. A
  second copy of that arithmetic is how a swirl ends up half a canvas away, and it is only
  falsifiable on a **non-square** canvas — make the test image one.
- **the equivalence test is the one that matters.** Hand the procedural layout back in as
  placed points and require the painting to be *bit-identical*. That is what proves the two
  are one code path rather than two that agree today.
- **anything the page needs that is not a tunable number rides the worker's `ready`
  message** — here `flowKinds` (which presets even have vortices) and `maxVortices`. The
  page must never be the place that decides which presets are swirls.

**Built** — `oilpaint/palette.py` / `web/tune/oilpaint/palette.js`, the *Palette* card:

- the statistical grade — warm-cool value split (with separate warm and cool directions,
  which is most of what distinguishes the presets), value compression with tinted endpoints,
  chroma shaped by value, broken colour;
- the pigment projection — six presets naming real tubes, subtractive mixing as a weighted
  geometric mean of reflectances (a linear average makes ultramarine + cadmium yellow *grey*,
  which is the one thing a viewer would call wrong), each stroke pulled onto the nearest
  reachable mixture.

**Built** — hue-band steering, inside `palette.py` rather than in a module of its own,
because it is the same OKLab arithmetic on the same colours at the same attachment point.
A band is `(centre, width, rotation, chroma gain)`; the preset block carries a `bands`
tuple (empty in every shipped preset, so no existing painting moved) and the four
`hue_*` sliders append one more. Four things it settled that the next colour knob inherits:

- **the weight is a von MISES bump, `exp((cos(dh) - 1)/sigma^2)`, not a Gaussian on a
  wrapped angle difference.** Hue is circular; the circular Gaussian needs no wrap, so
  there is no `mod` (numpy's and JS's disagree in sign) and no `round` (half-to-even
  against half-up) for the port to fork on — and `cos(h - centre)` is just
  `(a*cx + b*cy)/r`, so there is no `arctan2` either. The stroke colours came out
  **bit-identical** across the port, which the older angle code in the same file does not
  manage.
- **degrees → radians is one multiply by a precomputed constant on both sides.**
  `np.radians(x)` and JS `x * Math.PI / 180` are different arithmetic and differ in the
  last bit; harmless for a 60° preset constant, not harmless multiplied by a 180° rotation.
- **the degenerate case is the control you were replacing.** At the widest `hue_range` the
  band is the global rotation, and the test says so in numbers rather than the docs saying
  so in prose: 37.5° in-band against 0.1° far at width 30, and 29.8° far at width 180.
- **inert means inert while you AIM it.** `hue_target`/`hue_range` are deliberately not in
  `params_for`'s activity test, so dragging the band over a hue does not start grading.

It had an instrument, the **colour board** — a probe ring of hues and what the grade painted
them as, the page choosing the probe colours and the worker's `swatches` message running the
real `palette.grade`. Removed 2026-09-04 as unintuitive: a ring of synthetic hues makes the
visitor map an abstract strip back onto their own photograph. Keep the *shape* if you build a
replacement — send data to the engine, never restate the transform in the page — and prefer
an instrument that shows the effect ON THE PICTURE over one that shows it on stand-in colours.

**Built** — `oilpaint/regions.py` / `web/tune/oilpaint/regions.js`, the *Layers* tool on the
page and `regions` everywhere else: the picture divided into passages, each painted on its
own. (Two vocabularies on purpose — the visitor's word for "a thing I select and edit
separately" against what it actually is, a label per stroke.) The general lesson, and it is the one
to carry to the next spatial control: **a spatial control is cheap here because a stroke
carries its own position, and it does so at BOTH of the cheap seams.** `sb.x`/`sb.y` sit next
to `sb.rgb` at the grade's own attachment point, so "which region is this stroke in" is one
lookup per stroke — 5 000 against a megapixel — and a region's COLOUR stays in row one of the
table above. Its FLOW is the same lookup in row four, on `cy`/`cx` inside step 3, so a
per-passage flow field moves that passage's marks and still costs nothing that is pinned.
**A spatial control does not have to pick one seam**; it has to pick one seam per FIELD, and
say which in a set the tests can perturb. Six more things it settled:

- **a stroke gets one label, from its CENTRE**, so a boundary falls on stroke boundaries. No
  feathering, no matting, no halo; a sloppy mask still gives a deliberate edge. Do not reach
  for per-pixel blending here — it would be the posterise-filter mistake from the top of this
  file, one level up.
- **`floor`, never `round`.** numpy rounds half to even and JS rounds half up, and on a
  boundary that is not a one-ulp disagreement — it is a stroke graded as the wrong passage.
  And `stroke_labels(spec, x, y)` takes **x first**: passing `(cy, cx)` labels a transposed
  picture, which on a square test image is invisible. That is the second time a non-square
  test image has been the thing that caught it (the first was `field_centres`), so make the
  test image non-square and split the mask by ROW.
- **the equivalence invariant is the test that matters**, and it is the same shape as the
  placed-vortex one: give every region the base config's own values and require the painting
  to be BIT-identical, canvas included. It is what forced the transport-inheritance rule (a
  region that does not name its own reference reuses the base's already-fitted one, so there
  is exactly one fit in the picture) rather than leaving it a preference — and, on the flow
  half, it is what says the gather/scatter really is the whole-array arithmetic rather than
  something that agrees to five decimals.
- **the parameter sets are CHECKED like `RELIGHT_PARAMS`**, and the two are checked for
  OPPOSITE things. A colour field, perturbed inside a region, must leave the geometry
  bit-identical. A flow field, perturbed inside a region, must MOVE that passage's strokes
  and leave every stroke outside it bit-identical — a structural control that moved nothing
  looks exactly like one that moved everything a little, so both halves have to be said.
  Perturb the whole set, not a representative: `flow_coh` changes only the mark's shape and
  `flow_drift` only its position, so a check written against `theta` alone passes both of
  them by saying nothing. Then require the near misses to be REFUSED — refused, not ignored:
  `--region 1:target_n=9000` has to fail, or the CLI accepts a request it silently cannot
  honour. The line between accepted and refused is "does this need the strokes RE-ALLOCATED".
- **a spatial control on a field that MOVES the mark has one forced ordering question**, and
  it is worth recognising early: `flow_drift` slides a stroke along the field, so the label
  cannot be read from the drifted position — the drift is that field's own output. It is read
  from the birth centre, while colour keeps reading `sb.x`/`sb.y`, because each is the only
  position available where it is needed. Say which, and say why, next to the lookup.
- **the panel is the editor; the SELECTION says where it writes** — and once that rule
  exists, every *other* tool that acts on the picture has to adopt it too, or the page has
  two ideas about what is selected. It now governs four: the colour rows, the flow rows, the
  region fill and the swirl-centre clicks. No per-region sliders: those fields already have a
  control surface generated from `schema.json`, and N copies of it is N things to keep in
  step. Selecting a layer re-points the existing rows at it, several show `—` where they
  disagree, and the Base points them back at `params` — which is the page as it was before
  layers, unchanged. Which fields are layer-aware rides the `ready` message, so the page
  holds no list of its own; that is the rule `PIGMENTS` and `flowKinds` already follow, and
  here it is load-bearing rather than tidy.
- **one writer, or the routing is a suggestion.** Adding the swirl tool to that rule found a
  helper (`setControl`, used to arm a field from a click tool) writing straight into
  `params` and skipping the one branch that decides where a value goes — so arming always
  armed the whole picture, however a layer was selected. Anything that sets a control from
  code goes through the same function the row's own handler does.
- **a tool whose target cannot use it must look like it cannot**, and must say what would
  fix it. The swirl tool is live only when the field it is *aimed at* is swirl-kind;
  otherwise the button is dimmed, the canvas is inert, and the bar names the field that
  cannot, the ones that can, and offers the change as a button. It replaced a first-click
  *arming* behaviour that was right while there was one global field and became wrong the
  moment a click could silently re-aim a layer.
- **a UI whose failure mode is invisible has to be driven, not inspected.** A row that writes
  to the wrong layer looks exactly like a row that works. So `index_node.mjs` works the real
  handlers and reads each value back off the render message the engine RECEIVED — and the
  panel's generated controls grew an `id` for no other reason than to make that possible.
  Before that the harness could read a card's shape back and nothing else.

A mask is the third input of its kind, after the foveal map and the swirl centres, and they
now share a shape worth naming: **a look control's input is not always a number, and when it
is not, it is an argument to `plan`/`paint` rather than a `PaintConfig` field.** One thing
regions add to that pattern: a LABEL map resizes NEAREST, where the foveal map's smooth one
resizes bilinear. Interpolating between region 2 and region 4 invents a region 3 along every
boundary, and it is the kind of bug that looks like a soft edge rather than like an error.

**Built** — `oilpaint/reference.py` / `web/tune/oilpaint/reference.js`, colour transfer by
optimal transport: pick a named painting and the photograph's colour distribution is moved
onto its. It is the answer when the controls above are too sharp — they ask you to know what
you want, this asks you to point at a picture — and it is worth reading before adding any
control that has to LEARN something from data rather than be dialled:

- **the target is a TABLE, not an asset.** Each reference is key colours with area weights,
  from which the mean and covariance are derived at import. No thumbnail ships, nothing is
  decoded, and there is no risk of PIL and a browser's JPEG decoder disagreeing about the
  reference itself. The weights are the part that matters: The Starry Night is mostly dark
  blue, and six equally-weighted colours from it describe a painting that does not exist.
- **say what the numbers are.** The table is AUTHORED from documented pigment lists, and the
  module's docstring says so rather than implying a measurement it did not make — the same
  epistemic status `PIGMENTS` claims for itself. `scripts/extract_reference.py` prints a
  measured block from a real image, so the authored ones can be replaced.
- **a learned control still has to be deterministic.** That extraction script uses
  furthest-point k-means seeding with no random state, because its output gets pasted into a
  source file and a table you cannot reproduce from its own input is a magic number.
- **it rides an existing pass.** The transport is one 3x3, so rather than a second OKLab
  round trip over every stroke and a second canvas memo slot, it is fitted in `plan` and
  applied as step 0 of `palette._grade_chunk`. `palette.block_for` exists for that one
  caller. Any new global colour op should do the same.
- **and it needed one honest tolerance.** The transport has an operator norm of 4–7, so it
  multiplies the base layer's pre-existing port gap (`CANVAS_SLACK`) — measured 5.0e-5 on
  the canvas while the STROKES stay bit-identical at 0.00e+00. That asymmetry is the proof
  the arithmetic did not diverge; the constant is `TRANSPORT_AMPLIFIED` and it names the
  mechanism rather than a number that made the suite go green.

**Designed, not built.** These were worked out and deliberately deferred; do not re-derive
them from scratch:

- **Imprimatura** — tint the base canvas toward a warm ground so the ~12% bare area reads as
  toned canvas rather than as blur. Canvas only; one colour, one strength.
- **Palette transfer from a reference painting** — four 64px thumbnails shipped with the
  page, k-means in OKLab → a pigment set → straight into the existing projection. Needs a
  *deterministic* k-means init (no `Math.random`) for the parity test.
- **Simultaneous contrast** — nudge neighbouring regions apart in hue. Spatial, and now
  no longer the only one: `regions.py` showed that spatial does not mean expensive here,
  because a stroke carries its own position. What this still needs that regions do not is
  a NEIGHBOURHOOD, so it cannot ride on a per-stroke label lookup alone.
- **A region that re-allocates.** Everything outside the two sets — `target_n`, `max_cell`,
  `kappa`, `aniso_max` — is refused because it would need the quadtree rebuilt, and the
  quadtree is what decided where these strokes are. "More strokes in the face" is a real
  want and it already has a control: the **foveal map**, which reweights the detail field
  before the tree is built. Do not answer it here.

## The file checklist

```
oilpaint/<feature>.py            the module: presets, params_for(), the transform
oilpaint/pipeline.py             the PaintConfig fields + the call site in plan()/finish()
oilpaint/strokes.py              ONLY for a structural control: the step-3 call site, and
                                 the underpainting's override if the knob would stop it
                                 covering (flow.py's `drift` does)
oilpaint/schema.py               one row per field (the import-time assert enforces it)
web/tune/oilpaint/<feature>.js   the transliteration
web/tune/oilpaint/pipeline.js    DEFAULTS + the same call site + any new cache slot
web/tune/engine.worker.js        only if the page needs non-tunable data or a new stat
web/tune/index.html              only if it needs a front card, a custom widget or a label
web/tune/pipeline_node.mjs       the parity harness arms
tests/test_pipeline_parity.py    the arms, their tolerance, and the derived tables
tests/test_core.py               inert-at-default, geometry-preserved, the artistic claim
CLAUDE.md                        the check count, and a CLI example if it earns one
```

Then: `test_core` → `test_rng_parity` → `test_raster_parity` → `test_pipeline_parity` →
`verify_page.py` → `build_static.py`. All six, in that order.
