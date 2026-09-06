# CLAUDE.md

Guidance for Claude Code / AI agents in this repo. It routes to the skills for the rules
that govern a change, and carries the reasoning behind the parts of the pipeline where the
code alone does not explain itself.

**`README.md` is the public front page and is written for a visitor, not a maintainer** —
it is deliberately free of pipeline vocabulary. Do not move engineering detail into it, and
do not treat its plain-language descriptions as the specification; this file and the code
are that.

## What this is (one line)

A finished tool: a still-image oil-paint renderer, in Python, ported file-for-file to
JavaScript so the whole thing runs in a browser with no server behind it. The browser
tuner is the product; the CLI and the Python package are the same painter without the page.

## Read these first

- [`.claude/skills/sibling-repos/SKILL.md`](.claude/skills/sibling-repos/SKILL.md) — the two
  repos this one sits between, what is reusable from each, and the remote / branch / CI / LFS
  differences that silently break a copied change. **Read before porting anything.**
- [`.claude/skills/python-js-parity/SKILL.md`](.claude/skills/python-js-parity/SKILL.md) —
  the rule governing the Python→browser architecture: Python is the source of truth, the port
  mirrors it to float epsilon, a `node`-driven parity test proves it. **Read before writing
  either side of a ported routine.**
- [`.claude/skills/artistic-controls/SKILL.md`](.claude/skills/artistic-controls/SKILL.md) —
  where a look control attaches to the pipeline and what each attachment point costs, the
  rules that keep a new knob inert by default, the parity arms it owes, and the ideas already
  designed but not yet built. **Read before adding any new look parameter to `PaintConfig`.**
  Note that `oilpaint/flow.py` has since opened a **sixth** attachment point the skill's
  table does not list — step 3 of `strokes.from_cells`, the one step that draws no random
  number — and it is cheap for exactly that reason. See *The artistic layer* below.
- [`.claude/skills/ship-to-site/SKILL.md`](.claude/skills/ship-to-site/SKILL.md) — the
  contract for publishing a page on the public site. **Read before writing a browser page.**

## Common tasks

The `conda run -n 4dre` prefix is inherited and **does not apply on this machine** — see *Environment*. Read it as plain `python`.

```bash
# one image in, one painting out
conda run -n 4dre python scripts/paint.py IN.jpg OUT.png --max-side 700

# ...with a hand-painted foveal map steering the stroke budget (BLACK = spend strokes here)
conda run -n 4dre python scripts/paint.py IN.jpg OUT.png --foveal MASK.png \
    --foveal-strength 0.85

# ...with an artistic colour grade: a named palette (a real pigment set + a statistical
# grade), trimmed by five sliders
conda run -n 4dre python scripts/paint.py IN.jpg OUT.png --palette impressionist \
    --palette-strength 0.8 --broken-color 0.3

# ...or the other way round: point at a famous painting and move this photograph's colour
# distribution onto its, by optimal transport. The reference sets the STATISTICS and the
# palette sets the TUBES, so they compose.
conda run -n 4dre python scripts/paint.py IN.jpg OUT.png --reference starry-night \
    --reference-strength 0.75 --palette impressionist

# measure your own reference from a scan you have the rights to
conda run -n 4dre python scripts/extract_reference.py PAINTING.jpg --name my-reference

# ...with the look aimed ONE PASSAGE AT A TIME rather than at the whole picture. The mask
# is read through a legend -- BLACK is the base and each cube corner is a region -- and each
# region names the same colour fields the flags above do. The tuner's "Layers" tool is a
# click-to-fill for the same mask; its "Save mask" button writes this file.
conda run -n 4dre python scripts/paint.py IN.jpg OUT.png --regions MASK.png \
    --region "1:palette=nocturne,hue_target=220,hue_range=50,hue_rotate=-20" \
    --region "0:palette=impressionist"

# ...and the same for the STRUCTURE: van Gogh's sky over Monet's water, in one picture. A
# region names the flow fields too, so one passage swirls while another weaves and the rest
# of the painting keeps the photograph's own direction.
conda run -n 4dre python scripts/paint.py IN.jpg OUT.png --regions MASK.png \
    --region "1:flow=starry,flow_strength=0.9,palette=nocturne" \
    --region "2:flow=waterlily,flow_coh=-0.1"
# ...with an artistic STRUCTURE: a flow field that decides which way the marks run and how
# long they are, independently of what the photograph is of
conda run -n 4dre python scripts/paint.py IN.jpg OUT.png --flow starry \
    --flow-strength 0.85 --palette nocturne
conda run -n 4dre python scripts/paint.py IN.jpg OUT.png --flow waterlily \
    --flow-rot -10 --palette impressionist

# ...with the swirl centres placed by hand rather than on the default spiral. Fractions of
# the image, so the same argument places the same swirls at any --max-side. The tuner page
# has this as a click tool ("Swirl centres"); this is the same list.
conda run -n 4dre python scripts/paint.py IN.jpg OUT.png --flow starry \
    --vortices "0.30,0.11 0.63,0.12 0.17,0.45"

# ...and one passage's swirls somewhere the picture's are not. A region with no set of its
# own INHERITS --vortices, so this flag is only for the passage that disagrees.
conda run -n 4dre python scripts/paint.py IN.jpg OUT.png --flow starry --regions MASK.png \
    --vortices "0.30,0.11 0.63,0.12" --region "1:flow=starry" \
    --region-vortices "1:0.24,0.71 0.61,0.88"

# how much each Palette / Flow trim moves the picture, as a number. This is what decided
# which rows sit on the tuner's front cards and which fold into Advanced (FINE in index.html)
conda run -n 4dre python scripts/knob_impact.py

# interactive parameter tuner: preview web/tune/ AS YOU EDIT IT, no build -> :8137
conda run -n 4dre python web/tune/serve_tune.py

# the same tuner as a STATIC page: build the deployable tree, then preview it -> :8138
conda run -n 4dre python web/tune/build_static.py --serve

# core invariants: 288 checks, synthetic, seconds
conda run -n 4dre python tests/test_core.py

# the JS port vs the Python, three suites. ALL need node; they SKIP without it.
conda run -n 4dre python tests/test_rng_parity.py        # numpy's RNG, bit for bit
conda run -n 4dre python tests/test_raster_parity.py     # the rasteriser alone
conda run -n 4dre python tests/test_pipeline_parity.py   # every stage + the worker

# regenerate the ziggurat tables (only after a numpy upgrade)
conda run -n 4dre python web/tune/gen_ziggurat.py

# static checks for the tuner page -- run after ANY edit to web/tune/index.html
conda run -n 4dre python web/tune/verify_page.py
```

There is no build step for the tuner page, so `verify_page.py` is the only thing that
catches a broken edit. Every check in it is there because it caught a real defect; the
header lists which. **One of them is dynamic**: `web/tune/index_node.mjs` shims the DOM,
puts a fake engine behind the page and *works its controls* — boot with and without a
stored session, the automatic first render, the living-painting fold. It exists because
everything else in that file is static, and static is what missed a `const` read one line
before its own declaration — valid syntax, `ReferenceError` at run time, thrown inside an
async click handler and swallowed as an unhandled rejection. A button did nothing and said
nothing, and `node --check` passed it. The page reports its own runtime errors to `#err`
rather than to a console nobody has open. **Restart the server after changing `serve_tune.py`** — the HTML is
re-read from disk per request, but the Python is loaded once at process start, and running
a stale server against a fresh page has already cost real debugging time.

The tuner exists in two forms:

- **`serve_tune.py`** serves the source directory. The compute is the browser's either
  way; what this adds over `python -m http.server` is `schema.json`, which is generated
  from `oilpaint/schema.py` and so is not on disk — without it the engine will not start.
  (Its `/render` POST still runs the Python pipeline, for comparing against the JS. The
  page does not call it.)
- **the static build** (`build_static.py` → `public/`, deployed by `.gitlab-ci.yml`) is
  plain HTML + JS + CSS. No server, no Python, no WASM, no CDN — one page, 14 JS
  modules, the worker and `schema.json`, plus a 349 KB sample image. ~713 KB, of which the
  sample is half.

**There is no animation here, and that is a scope decision rather than an omission.** The
page had a *living painting* fold — the stroke buffer replayed at frame rate on a keyframe
timeline — and the repo had an animation layer (`anim.py`, vortex paths, relight sweeps) and
a GPU video layer (`gpu.py`, `video.py`, `scripts/paint_video.py`) behind it. All of it was
dropped when this repo was split off from `oil-paint-hack`, which still has every bit of it
if any of it is wanted back. What survives from that side is exactly one thing, and only
because it is a still-image claim: `RELIGHT_PARAMS`, the set of parameters `finish` can
re-apply to an already-rasterised painting, which is what lets the worker serve a moved
light slider ~42x cheaper than a re-render. It lives in `pipeline.py` / `pipeline.js` now,
file for file, rather than in the animation module it used to be re-exported from.

**The toolbar is grouped by what a control is FOR**, and within that by how often a hand
reaches for one: the two tools that mark the picture, then `full res` + **Download** (paired,
because the toggle says what Download will write), then the painting's dice-roll and reset,
then — past the spacer, at the far edge — `help`, `stats`, `Copy setup` and `Clear`. **The
`Render` button is hidden while `auto` is on**, which is the default: with every change
re-rendering there is nothing for it to do, and a button with nothing to do is worse than no
button. Turning `auto` off brings it back, because then it is the *only* way to see a change
— and `auto` off is not a corner case, it is what a full-res render on a large photo needs.
`verify_page.py` drives both directions, because a page that hid it and kept it hidden is a
tuner that cannot render.

**The stats strip is off by default** (the `stats` toggle in the toolbar). The numbers —
strokes, coverage, psnr, tau — are for judging a *change*, and the page is for looking at a
picture. **Warnings are exempt and always show**, toggle or no: `⚠ ceiling`, `⚠ capped at
Npx`, the foveal map with nothing left to redistribute. The filter is one line over the
markup the entries already carry (`isWarn` tests for a `.warn` span) rather than a second
list that could drift from `showStats`; a layer that caught no strokes is warned about in
the layers panel itself, which is never hidden.

**`web/tune/session.js`** persists the session across page loads — a reload is a handoff
to a page with no memory, and without this it means re-dropping the photograph and retuning
from scratch. Two stores, because the state has two sizes:
parameters and placed swirl centres go in **localStorage** (a few hundred bytes, wanted
synchronously while the panel is built), and the source image goes in **IndexedDB** — a
full-resolution phone photo is 5–8 MB of base64, which does not degrade past localStorage's
quota, it *throws*. Everything is wrapped: both stores fail outright in a private window, and
a session that cannot be saved must degrade to re-dropping the image, not take the page down.
The tuner saves on every change **rather than after a render**, which is a lesson bought
at full price: a setup written only when a render finishes disagrees with the panel for as
long as a render takes, and anything reading it in that window gets stale numbers with
nothing to say they are stale. `boot(restore=false)` is what keeps the **Defaults** button
meaning defaults rather than "whatever I had last time".

So the whole package now exists **twice**, `oilpaint/*.py` and `web/tune/oilpaint/*.js`,
file for file. That is the central risk of this architecture, and what contains it is three
parity suites, all torch-free and seconds long:

| suite | what it pins |
| --- | --- |
| `test_rng_parity.py` | `nprandom.js` reproduces numpy's `default_rng` **bit for bit** — SeedSequence, PCG64, and the 256-level ziggurat. Run this first; without it nothing else means anything. |
| `test_raster_parity.py` | `render.js` vs `render.py` on the branches a single painting misses — soft falloff, glazing, off-canvas strokes, split coverage, and step 2's bristle / taper / height field / lighting pass. |
| `test_pipeline_parity.py` | every stage, the whole painting, the real `engine.worker.js`, and the cached render path. Also the pigment grade (`palette.js` vs `palette.py`) and the flow field (`flow.js` vs `flow.py`), including the invariant each rests on: the grade runs AFTER the geometry, and the flow runs inside the one step that draws no random number, so both arms' cells, colours and paint order are bit-identical to the unstyled ones. Both pin their PRESET BLOCKS directly, per name, not only through a painting. `regions.js` gets **two** arms for the same reason it has two parameter sets — one where the grade is aimed a passage at a time and the geometry may not move, one where the FLOW is and the strokes outside the passage may not — and each drives its own equivalence invariant inside the port as well as against the Python, because a JS that skipped where it should blend would otherwise pass by matching a Python that did the same. The worker's **relight** fast path is compared against a full render, because an optimisation that returns something subtly different is a wrong picture produced 42x faster, and `RELIGHT_PARAMS` itself is compared as a SET across the two languages — a stale entry there is a control that appears to do nothing. |

Results: the quadtree leaves, stroke order, pigment and tau are **exact**; geometry sits
within a documented float32 bound; **the painting is bit-identical at 8 bits**.

## The artistic layer

`oilpaint/palette.py` is the first control that exists to make the output look like **paint**
rather than like the photograph, and it establishes the pattern the next ones should follow.
It attaches at the end of `plan()` — to `sb.rgb`, the per-stroke pigment, and to the base
canvas, **after** the quadtree, the tau search, the tensor and the stroke geometry have all
been decided. Three consequences, all pinned by tests:

- a graded painting's strokes are **bit-identical** to an ungraded one's. Colour moved;
  geometry did not.
- **no new `rng` call.** The per-stroke broken-colour alternation rides the `phase` the
  `StrokeBuffer` already carries, so the draw stream is untouched at any value.
- the defaults are inert **by skipping** (`params_for` returns `None`), so every painting
  made before the feature existed still reproduces bit for bit.

It does two things in that order: a *statistical grade* (warm-cool value split, value
compression with tinted endpoints, chroma shaped by value, broken colour) and a *pigment
projection* onto what a named set of real tubes can actually mix, subtractively. The grade is
a wish; the projection is what the palette can grant. **The skill above carries the rest** —
the other attachment points and their cost, the rules, and the designed-but-unbuilt list.

**Hue-band steering** is the newest knob and the one worth knowing the shape of, because it
answers a question that keeps coming back: a *global* hue rotation cannot cool a sky without
also cooling the skin in front of it. A band is `(hue_target, hue_range, hue_rotate,
hue_boost)` — turn and saturate one family of hues and leave the rest — and it runs FIRST in
the grade, so it reads the photograph's own hue and the pigment projection still has the last
word on what the tubes can do with the result. Three things about it:

- the weight is a **von Mises** bump, `exp((cos(dh) - 1)/sigma^2)`, not a Gaussian on a
  wrapped angle. Hue is circular, so this needs no `mod` and no `arctan2` — which is also
  what keeps the port bit-identical (`np.mod` vs JS `%` and numpy's round-half-to-even
  against JS's round-half-up are exactly the one-ulp forks this repo cannot see by looking);
- **the widest `hue_range` IS the global rotation**, as a limiting case. `test_core` pins
  both halves as numbers: at 30° the in-band hues turn 37.5° and the far ones 0.1°, and at
  180° the far ones turn 29.8°;
- it is inert unless `hue_rotate` or `hue_boost` is non-zero, so *parking* the band over a
  hue — which is what dragging `hue_target` does — grades nothing.

`oilpaint/reference.py` is the answer to the complaint the hue band earns: it is a scalpel,
and it asks you to already know what you want. This asks you to point at a painting. It
matches the photograph's colour distribution to a named masterpiece's by **optimal
transport** — the linear Monge–Kantorovich map between two Gaussians, closed form, which
moves the mean AND the full covariance, so "the darks go blue while the lights go yellow"
comes across rather than an average tint. Five things worth knowing:

- **it composes with `palette` rather than replacing it.** The reference decides the
  statistics; the palette decides which tubes a stroke may land on, because the pigment
  projection still runs after. `--reference starry-night --palette impressionist`.
- **it rides the grade's own OKLab pass.** Fitted once per `plan` against the whole stroke
  buffer (a statistic the chunked grade cannot see), then applied as one 3×3 and an offset
  per colour — nine multiplies. `palette.block_for` exists for this one caller.
- **why not the newer things.** Full-distribution transport (Pitié's IDT, sliced
  Wasserstein) matches the whole histogram, and against a *painting* that is wrong: a
  painting's histogram is spiky because a painter used eleven tubes, and forcing a
  photograph onto those spikes posterises the smooth passages. Neural transfer (AdaIN, WCT,
  diffusion) is out on architecture, not quality — the page downloads nothing at runtime and
  a network cannot be held to float-epsilon parity. Both halves are in `reference.py`'s
  docstring, along with the fact that WCT *is* a Gaussian transport in feature space.
- **the eigensolver is a fixed-sweep Jacobi, and every 3×3 product is written out**, on both
  sides. LAPACK has no browser equivalent, and a solver that stops on a convergence test can
  take a different number of iterations in the two languages. The stroke colours come out
  **bit-identical**; only the canvas diverges, by the base layer's pre-existing gap times the
  transport's gain (see `TRANSPORT_AMPLIFIED`).
- **the source's variance has a FLOOR, and it is the most important number in the file.** A
  photograph's colour cloud is often a thin sliver — a field of sunflowers varies along
  essentially one direction — and inverting a sliver to stretch it onto a fat target needs
  enormous gain: measured, an eigenvalue of **15.0**, which turned sensor noise into
  saturated speckle that clipped and scrambled hues. `SRC_SD_FLOOR` (0.025) is the standard
  regularisation and the exact counterpart of `GRAIN` on the target side; it brings the same
  map to 2.6 and the speckle disappears.
- **the reference table is AUTHORED, not measured**, and the module says so: key colours with
  area weights, written from documented pigment lists. `scripts/extract_reference.py` prints
  a measured block from any image you own — deterministic k-means in OKLab, furthest-point
  seeding, no random state — so any entry can be replaced with the real thing.

The tuner had a **colour board** under the toolbar for this — a probe ring of hues and what
the grade painted them as, fed by the worker's `swatches` message. It was **removed on
2026-09-04**: reading a grade off a ring of synthetic hues asks the visitor to do the
mapping from an abstract strip back to their own photograph, which is not how anyone judges
colour. The shape it demonstrated is still the right one for any future "show what a control
is doing" panel — send data to the engine and let the real transform answer, never restate
the transform in the page. `reference.fit` still returns the source statistics that let a
front end re-aim the transport without the stroke buffer; nothing reads them now.

`oilpaint/regions.py` is the answer to the limit `palette`, `reference` and `flow` share:
all three act on the WHOLE picture, which is the one thing a painter never does. It divides
the picture into **passages** and paints each one on its own — `--regions MASK.png` plus
`--region 1:palette=zorn,hue_rotate=25` or `--region 1:flow=starry,flow_coh=-0.2`, or the
tuner's **Layers** tool: `+ Layer`, click to flood-fill it, and the Palette and Flow rows
then write to the selected layer instead of to the whole picture. (The page says *layers*
and the engine says *regions*, deliberately — a visitor's word for "a thing I select and
edit separately" against what this actually is, a label per stroke. `--regions` and
`oilpaint/regions.py` keep the engine's.) Five things worth knowing:

- **it is spatial and it is still cheap**, which is the whole reason it exists here rather
  than as a post filter, and it is cheap TWICE because a stroke carries its own position at
  both of this pipeline's cheap seams. Colour is decided per STROKE (`sb.x`, `sb.y` sit
  right next to `sb.rgb`), so a colour region is one array lookup per stroke — 5 000 of them
  against a megapixel canvas — attaching at exactly the same point as the global grade, the
  end of `plan`, and moving no leaf, no radius, no angle, no random number. A **flow**
  region is the same lookup one seam earlier, in step 3 of `strokes.from_cells`: it re-combs
  its own passage's marks, and still moves no leaf, no radius, no colour, no paint order and
  no random number, because step 3 is the one step of `from_cells` that draws none. The two
  sets are `REGION_COLOR_PARAMS` and `REGION_FLOW_PARAMS`, and which is which is what
  decides where a field attaches.

- **the flow label is read from the stroke's BIRTH centre**, and that is forced rather than
  chosen: `flow_drift` moves a mark along the field, so at the moment the field has to be
  picked the drifted position does not exist yet. The colour label keeps reading
  `sb.x`/`sb.y`, which is where the mark lands and therefore where its colour is seen.
- **the swirl centres are addressed the same way**, and had to be: one global list meant a
  swirl placed in the sky was read by the field painting the ground too. `vortices` is
  `{region id: [points]}` — a bare list is still the base's — and a region without a set of
  its own inherits the base's. See `flow.centres_for`, and the *Swirl centres* tool below.
- **a stroke gets ONE label, from its centre.** A boundary therefore falls on stroke
  boundaries: no feathering, no matting, no halo, and a mask that is sloppy at the edges
  still gives an edge that reads as deliberate. It is the same argument `palette.py` makes
  for why the pigment projection is per stroke — a mark that straddles the skyline is one
  dab of one mixture, because that is what paint is.
- **the equivalence invariant is the one that matters.** Give every region the base config's
  own values and the painting comes back **bit-identical**, canvas included — the counterpart
  of handing a flow preset its own spiral back as placed vortices. It is what makes "one code
  path" a fact rather than a hope, and it is why a region that does not name its own
  reference *inherits* the base's already-fitted transport instead of refitting it over a
  subset. A region that DOES name one is fitted over its own strokes, which is the entire
  point of putting `starry-night` on a sky.
- **both parameter sets are checked, not asserted**, exactly like `RELIGHT_PARAMS`, and
  they are checked for OPPOSITE things. Every colour field, perturbed inside a region, must
  leave the stroke geometry bit-identical; every flow field, perturbed inside a region, must
  move that passage's own strokes (`x`, `y`, `theta`, `r_major`, `r_minor` — `flow_coh`
  changes only the shape and `flow_drift` only the position, so a check written against the
  angle alone would pass those two by saying nothing) and leave every stroke outside it
  bit-identical. The near misses — the fields that would want the quadtree back: `target_n`,
  `max_cell`, `kappa`, `aniso_max`, `seed` — are REFUSED rather than ignored. A wrong entry
  would not raise; it would repaint one passage from a quadtree the rest of the picture does
  not share.
- **the mask resizes NEAREST, and that is the one trap.** The foveal map's bilinear is right
  for a smooth weight field and catastrophic for labels: interpolating between region 2 and
  region 4 invents a region 3 along every boundary in the picture. Both the CLI and the
  page's `regionBytes` say so at the resample.

The mask is DATA, like the foveal map and the swirl centres — an image, not a number — so it
is an argument to `plan`/`paint` and not a `PaintConfig` field. What the page adds on top is
the workflow, and it is the one every layer-based tool has: **the panel is the editor and the
selection says where it writes.** Selecting a layer re-points the existing Palette and Flow
rows at that layer, selecting several shows `—` where they disagree and writes to all of them
at once, and selecting the Base points them back at `params` — which is the page this was
before layers existed, unchanged. There are no per-region sliders and there must not be:
those fields already have a control surface generated from `schema.json`, and seven copies of
it is seven things to keep in step with the engine. Three consequences worth knowing:

- **which fields are layer-aware is the ENGINE's answer** (`regionParams` on the `ready`
  message), never a list in the page. A page with its own list would re-point a row the
  engine then refuses, or leave a row global that the CLI can set per region — and it is
  what let the **Flow** rows join the layer model without a line of the panel changing.
- **the SELECTION is the target of every tool that acts on the picture**, now four of them:
  the Palette rows, the Flow rows, the region fill and the *Swirl centres* clicks. That last
  one is the only tool whose failure is silent — a centre placed into the base while a layer
  is selected still swirls *something*, so the picture changes and nothing looks broken — so
  `index_node.mjs` drives it and reads the owner off the wire, not a total. Adding it found
  a real bug: `setControl` wrote straight into `params`, bypassing the one branch that
  decides where a value goes, so arming a swirl field always armed the whole picture.
- **a tool whose target cannot use it says so, and offers the fix.** The swirl tool is live
  only when the field it is aimed at is swirl-kind; otherwise the button reads unavailable,
  the canvas is inert, and the bar names the field that cannot and the ones that can, with a
  *Use starry here* button. That replaced the old first-click arming, which was right while
  there was one field and under layers would flip a layer's dropdown out from under you.
  **Availability is recomputed with the PANEL, not with the bar** — it was originally
  refreshed only while the bar was open, so setting a layer to `starry` with the bar shut
  left the button dimmed: correct when it was last computed, and nothing recomputed it.
  `verify_page.py` drives both directions with the bar closed.
- **that button lives in the Flow card, not in the toolbar.** `CARD_TOOLS` in `index.html`
  is the mechanism: a picture tool attached to one schema control, built by the card builder
  underneath that control's row. The swirl centres are a property of the flow field, so they
  belong where the field is chosen — and, more usefully, that is where the dimmed state reads
  as *this field has no swirls in it* rather than as a toolbar button that happens to be off.
  Built fresh on every panel rebuild rather than moved from the toolbar, because the panel is
  cleared with `innerHTML = ''` and a node inside a card would not survive it; opening the
  tool scrolls the picture back into view, since the card is below the fold and the thing the
  tool asks for is a click on the picture.
- **a new layer is seeded from the panel, so it starts inert** — it says exactly what the
  base says, which the equivalence invariant proves is a no-op. Making a layer never changes
  the picture; editing one does, and `verify_page.py` checks that a fill alone does not even
  re-render.
- **the eye drops a layer from the wire**, so its strokes fall back to the base's grade and
  hiding the last one restores the base painting bit for bit. The Base has no eye: it is not
  a layer over anything, it IS the panel, and inventing a meaning for hiding it would be
  inventing a second way to say `palette_strength = 0`.

`verify_page.py` drives all of that through the real handlers and reads each value back off
the render message the engine received — because the model's only real failure mode is a row
writing to the wrong place, and from outside that looks exactly like a row that works.


`oilpaint/flow.py` is the same argument for **structure**, and it attaches somewhere the
skill's table does not list. (It is also per REGION now — see `regions.py` above.) Stroke placement is three things — position (quadtree + jitter),
angle (tensor), elongation (coherence) — and this takes the last two, inside **step 3 of
`strokes.from_cells`, the one step that draws no random number.** That is what makes an
otherwise expensive seam cheap: the leaves, tau, the stroke count, the radii, the per-cell
colours, the paint order and the whole `rng` stream are untouched at any setting, and the
tests say so with `np.array_equal`. It is evaluated **analytically per stroke** — seven
vortices across 5 000 strokes, not a megapixel field — which also keeps decimation,
interpolation and the float32-field dtype traps out of the port.

Three presets, each a claim its tests have to make good on: `starry` (counter-rotating
vortices, ribbons chained along the flow), `waterlily` (a horizontal weave of short dabs)
and `hatch` (Cézanne's one diagonal). Two things it is worth knowing before editing them:

- **`strength` and `coh` are separate numbers on purpose.** The first version folded the
  style's elongation into its weight in the direction blend, which quietly made *a style
  that wants short marks a weak style* — at Monet's coh 0.18 the content outweighed the
  style 3.7 : 1 and the water came out neither horizontal nor stubby. `flow_blend` now
  averages what each side asks for and cuts it by how much they agree; see the docstring.
- **the preset constants are pinned per name in the parity suite**, like the pigment
  mixture LUTs, so a typo in a preset the arms do not happen to select still fails.

One term, `flow_drift`, moves a mark instead of turning it: strokes slide along their own
axis so marks on a flow line bunch and read as a chain. It is forced to 0 for the
underpainting, which has to cover.

**Where the swirls go is an input, not only a preset.** A swirl field falls back to a
golden-angle spiral, but `plan`/`paint` take a `vortices` list of `(fx, fy)` fractions —
what a click on the page's *Swirl centres* tool produces, and what `--vortices` takes. It
follows the **foveal map's** precedent exactly: a list of points is not a scalar, so no
slider could carry it and it is not a `PaintConfig` field. Four things worth knowing:

- it is **addressed per region**, `{region id: [points]}`, and a bare list is still the
  base's set — so no caller that predates regions changed. This had to follow the per-region
  flow rather than being a nicety: with one global list, a swirl placed in the sky was read
  by the field painting the ground as well. A region with **no set of its own inherits the
  base's**, and that inheritance is *forced* by the equivalence invariant, not chosen —
  falling back to the preset's spiral instead would make "every region on the base's own
  values" a different painting. `flow.centres_for` carries the argument; an explicitly empty
  set is the spiral, which is the third state the CLI can name and the page never produces.

- an **empty** list means the spiral, not "no vortices" — clearing the markers gives the
  preset back rather than a uniform sweep;
- the centres go through **`norm_coords`, the same transform the strokes use**, so a vortex
  placed at a pixel sits where a stroke at that pixel sits. `test_core` pins this by winding
  the field around a placed centre on a deliberately **non-square** canvas, which is the
  only shape that makes a wrong conversion falsifiable;
- **click order is the handedness** (`k % 2`, as for the spiral), so neighbouring swirls
  counter-rotate. The page's markers are numbered and drawn with the arrow going the way
  that vortex actually turns — a rule you cannot see is a rule that looks like a bug.

The invariant that proves the placed and procedural paths are one path: hand a preset its
own spiral back as placed points and the painting is **bit-identical**.

Two things that will bite an unwary edit:

- **numpy's dtypes are part of the algorithm.** `image.py` is float32 but its summed-area
  tables are float64, `sample()` returns float32 which makes `ratio` float32 until a
  float64 jitter array promotes it, and `(mip_max - mip_min)` rounds to f32 before its
  `astype`. Each of those, got wrong, moves a quadtree split or a stroke colour. The JS
  says `Math.fround` wherever numpy rounds; the parity test is what proves it.
- **the RNG draw ORDER is the algorithm.** Adding, removing or reordering one `rng.*` call
  in `strokes.js` shifts the whole stream and gives a completely different painting.

`web/tune/ziggurat_tables.js` is generated by `gen_ziggurat.py`, which **extracts** the
tables from the installed numpy binary — reconstructing them analytically gets every entry
right to ~1e-10 and still changes 99.6% of normal draws.

## Where this repo came from

Split off from **`D:\oil-paint-hack`** on 2026-09-06, which remains on this machine and is
the place to look for anything missing here. That repo is the same pipeline plus three
things this one deliberately does not carry: the animation layer (`anim.py`, vortex paths,
relight sweeps, `scripts/animate.py`), the GPU video layer (`gpu.py`, `video.py`,
`scripts/paint_video.py`, `slurm/`) and the tuner page's *living painting* fold. The git
history of those features lives there, not here — this repo starts at its own first commit.

Two skills under `.claude/skills/` (*sibling-repos*, *ship-to-site*) describe a Linux
cluster (`/cluster/home/misong/...`), a self-hosted GitLab and a GitHub Pages site. **None
of those paths exist on this machine.** Read them for the reasoning — the LFS traps, the
project-subpath deploy, the page-chrome contract — and not for the paths.

## Environment

This is a **Windows 11** workstation, and several inherited assumptions do not hold on it.

- **Python is `python` on PATH, not a conda env.** There is no `4dre` env here; the base
  install is Python 3.9 with numpy 1.21 / PIL / scipy and **no cv2**. Everything under
  `oilpaint/` runs on it, as do all four suites and both tuner servers. Drop the
  `conda run -n 4dre` prefix the recipes above still carry.
- **numpy 1.21, not the 1.26 the ziggurat tables were extracted from.** The consequence is
  narrow and known: `test_rng_parity.py` fails its last stage on **1 draw in 2,000,000**, a
  last-bit difference in the ziggurat's tail branch. Every table-driven draw is exact and
  every other suite passes. Do not "fix" it in `nprandom.js` — regenerate the tables with
  `gen_ziggurat.py` against whatever numpy is installed, or install 1.26.
- **Shell is PowerShell.** `&&` and `||` are parse errors in Windows PowerShell 5.1; a Bash
  tool is available for POSIX scripts.
- **node writes UTF-8, Python reads the locale encoding.** Every Python↔node JSON bridge in
  this repo passes `encoding="utf-8"` explicitly for that reason — `JSON.stringify` does not
  escape non-ASCII, and cp1252 turns a `·` in a card name into two characters and a check
  that fails for a reason unrelated to the page. Pin it on any new bridge.
- Git remote: none configured yet. Default branch **`main`** (`.gitlab-ci.yml` keys on
  `$CI_DEFAULT_BRANCH`, so it does not care).
- Git LFS: [`.gitattributes`](.gitattributes) is broad by default — re-scope it once the
  data layout settles. `build_static.py` fails the build if an unsmudged pointer stub ever
  reaches the deploy tree.

## House rules

Inherited from 4d-relight, which states them at length:

- **Minimal change.** Touch only what is necessary; prefer editing an existing file over
  creating a new one.
- Don't add docstrings, comments or type hints to code you didn't change.
- Don't refactor working code unless asked.
- Don't delete checkpoints or experiment outputs.
