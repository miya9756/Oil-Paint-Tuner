# Oil Paint Hack

Turn a photograph into an oil painting with **no optimization and no network** — every stage
is a direct computation from the source pixels.

An adaptive quadtree partitions the image where the detail is; each leaf cell becomes one 2D
anisotropic Gaussian brush stroke, oriented along the image's own structure; the strokes are
composited coarse-to-fine over an opaque underpainting, with a height field lit as impasto.
It runs in milliseconds-to-seconds on a CPU, and the whole thing also runs **in the browser**
with no server, no WASM, no CDN and no weights to ship.

The contrast case is deliberate: the 2D-Gaussian image-fitting family (GaussianImage, ECCV
2024) optimizes stroke positions per image by gradient descent. A perfectly fitted Gaussian
image is a *photograph*. Abstraction is the goal here, so the reconstruction error we give up
is the product.

Full rationale, prior art and measurements: [`docs/spec-step1.md`](docs/spec-step1.md).

---

## Quick start

Everything runs locally: numpy / PIL, CPU only — no torch, no GPU, no cluster.

```bash
# one image in, one painting out
python scripts/paint.py assets/zurisee.jpg out/photo.png --max-side 700
```

> On this machine Python is plain `python` on PATH — there is no `4dre` conda env, and the
> `conda run -n 4dre` prefix the rest of this file carries is inherited. See `CLAUDE.md`,
> *Environment*, for what else differs (including one known `test_rng_parity.py` failure
> that is a numpy-version artefact, not a port bug).

Every `PaintConfig` field is exposed as a `--kebab-case` flag, so an A/B arm is a flag:

```bash
python scripts/paint.py IN.jpg OUT.png \
    --orient random --aniso-max 1.0 --no-impasto --target-n 12000
```

The CLI prints the run's stats as JSON: achieved `tau`, stroke count, coverage, `bare`, and
wall-clock per stage. Read `bare` rather than `coverage` when a passage looks blurred: it is
the fraction of pixels no layer painted, where the base layer shows through, whereas
`coverage` is a `> 0.99` threshold that also moves when the bristles modulate alpha inside a
painted pixel.

## The tuner

The parameter tuner — drop an image in, move sliders, watch the painting change, wipe it
against the source — exists in two forms, both computing **in the browser**:

```bash
# preview web/tune/ AS YOU EDIT IT, no build step            -> http://localhost:8137
python web/tune/serve_tune.py

# the same tuner as a STATIC page: build the deploy tree, then preview it  -> :8138
python web/tune/build_static.py --serve
```

`serve_tune.py` is the dev server for the source tree. What it adds over
`python -m http.server` is `schema.json`, generated from
[`oilpaint/schema.py`](oilpaint/schema.py) and therefore not on disk — without it the engine
will not start. (Its `/render` POST still runs the Python pipeline, for comparing against the
JS; the page does not call it.)

A full-resolution render is seconds of straight-line compute, so the page does not spend it
on a spinner: the engine reports the stage it is in — the same names `info.timing` uses —
and, once a render has run past ~0.6 s, streams the painting **as it is made** onto a canvas
over the result. The blurred ground first, then the strokes going down a few hundred at a
time with a count on the pill, then the light. Every frame is a real pipeline product at
that moment, not an illustration of one; the only thing missing from them is the lighting
pass, which is ~0.7 s at 1100 px and far too expensive to run per frame. Short renders —
the 360 px drafts a dragged slider fires — never show one, so nothing flickers.

A change made while a render is running **stops it**, rather than queueing behind it. That
needs the engine's help: a worker running straight-line code never reads its own message
queue, so the rasteriser is drawn in slices with a yield between them, and the yield is the
only moment it can notice that nobody wants this painting any more. Measured at 1100 px, a
superseded render lets go in ~1 ms instead of running out its remaining seconds; slicing the
composite costs nothing measurable (24 slices, inside the run-to-run noise), and
`test_raster_parity.py` requires a sliced composite to equal a whole one **exactly**.

The same work turned up a cache that never hit: the worker kept one memo slot keyed by
render size, and the page's draft → refine → draft rhythm evicted it every single time, so
`plan` paid cold price on every render. Two slots, LRU, over draft/full/draft/full/draft/full
at 1100 px: **7.48 s → 2.44 s**, the full-width plan falling from ~2.1 s to ~0.1 s. That is
also what makes cancelling worth having — `plan` is a synchronous prefix no yield can
interrupt, so a cold one put a 2 s floor under any "stop and use the new settings".

`build_static.py` assembles [`public/`](public/) — plain HTML + JS + CSS — which
is what `.gitlab-ci.yml` publishes to GitLab Pages from the default branch. 19 files,
~764 KB, of which the sample photograph is 349 KB.

There is **no build step for the page**, so `verify_page.py` is the only thing that catches a
broken edit to it. Run it after any change to `web/tune/index.html`:

```bash
python web/tune/verify_page.py
```

Every check in it is there because it caught a real defect; the header lists which.
**Restart the server after changing `serve_tune.py`** — the HTML is re-read per request, but
the Python is loaded once at process start.

## Layout

```
oilpaint/          the reference implementation, pure numpy
  image.py         load/save, luma, colour spaces, summed-area tables
  detail.py        the five detail metrics behind one interface   {var,range,grad,dct,residual}
  quadtree.py      adaptive subdivision -> leaf cells, tau solved to hit a stroke budget
  tensor.py        structure tensor -> orientation + coherence
  strokes.py       cells -> strokes: jitter, radius, anisotropy, colour, irregularity
  render.py        the rasteriser: painter's-order compositing, bristles, taper, impasto
  metrics.py       coverage, PSNR/SSIM, edge alignment
  pipeline.py      orchestration + the PaintConfig dataclass
  schema.py        the tuner's control surface, asserted against PaintConfig on import
web/tune/          the browser side
  oilpaint/*.js    a file-for-file JS port of the package above
  index.html       the tuner page
  engine.worker.js the worker that drives the port
  build_static.py  assemble the deployable tree -> public/
  verify_page.py   static checks for the page
scripts/paint.py   CLI: one image in, one painting out
scripts/extract_reference.py   measure a colour reference off a painting you have the rights to
scripts/knob_impact.py         how much each trim moves the picture, as a number
tests/             core invariants + three parity suites
docs/              the specs, and the auto-research log
```

**No animation.** This repo is the still-image tuner. It was split from `oil-paint-hack`,
which additionally carries an animation layer (vortex paths, relight sweeps, a keyframe
"living painting" fold in the page) and a GPU video pipeline; none of that is here, and
that repo is where to look if it is wanted back. The one thing that crossed over is
`RELIGHT_PARAMS` — the parameters `finish` can re-apply to an already-rasterised painting,
which is what makes a moved light slider ~42x cheaper than a re-render. It is a still-image
claim, so it lives in `pipeline.py` / `pipeline.js`, file for file.

**No scipy and no cv2 anywhere under `oilpaint/`.** Neither exists in a browser: Gaussian blur
is separable numpy, Sobel is an explicit convolution, and the eigendecomposition of a
symmetric 2×2 is closed form. `metrics.py` is exempt — it never ports. This constraint is
what makes the browser version a port rather than a rewrite.

## Python and the port

The package exists **twice**, `oilpaint/*.py` and `web/tune/oilpaint/*.js`, file for file.
That is the central risk of this architecture. Python is the source of truth; what contains
the risk is three parity suites, all torch-free and seconds long. They need `node` and they
**skip** without it (CI asserts they did not).

```bash
python tests/test_core.py             # 31 core invariants, 258 checks, synthetic
python tests/test_rng_parity.py       # numpy's RNG, bit for bit
python tests/test_raster_parity.py    # the rasteriser alone
python tests/test_pipeline_parity.py  # every stage + the real worker
```

| suite | what it pins |
| --- | --- |
| `test_rng_parity.py` | `nprandom.js` reproduces numpy's `default_rng` **bit for bit** — SeedSequence, PCG64, and the 256-level ziggurat. Run first; without it nothing else means anything. |
| `test_raster_parity.py` | `render.js` vs `render.py` on the branches one painting misses — soft falloff, glazing, off-canvas strokes, split coverage, bristles, taper, height field, lighting. |
| `test_pipeline_parity.py` | every stage, the whole painting, the cached render path, and the real `engine.worker.js`. |

Results: quadtree leaves, stroke order, pigment and tau are **exact**; geometry sits within a
documented float32 bound; **the painting is bit-identical at 8 bits**.

Two things that bite an unwary edit:

- **numpy's dtypes are part of the algorithm.** `image.py` is float32 but its summed-area
  tables are float64; `sample()` returns float32, which keeps `ratio` float32 until a float64
  jitter array promotes it; `(mip_max - mip_min)` rounds to f32 before its `astype`. Each of
  those, got wrong, moves a quadtree split or a stroke colour. The JS says `Math.fround`
  wherever numpy rounds, and the parity test is what proves it.
- **the RNG draw ORDER is the algorithm.** Adding, removing or reordering one `rng.*` call in
  `strokes.js` shifts the whole stream and gives a completely different painting.

`web/tune/ziggurat_tables.js` is generated by `gen_ziggurat.py`, which **extracts** the tables
from the installed numpy binary — reconstructing them analytically gets every entry right to
~1e-10 and still changes 99.6 % of normal draws. Regenerate only after a numpy upgrade.

## What the pipeline actually does

```
source image
   ├─▶ detail field ────── var | range | grad | dct | residual, O(1)/cell via summed-area tables
   ├─▶ foveal map ──────── optional: a hand-painted [0,1] map reweights that score per cell
   ├─▶ quadtree ────────── subdivide while detail > tau; tau binary-searched to hit a budget
   ├─▶ structure tensor ── orientation theta + coherence c per stroke centre
   ├─▶ cells -> strokes ── centre jitter, log-normal size, area-preserving elongation, median colour
   ├─▶ underpainting ───── a complete coarse layer (or a blurred base) so no gap is a black hole
   └─▶ render ──────────── painter's order `over`, hard-ellipsoid falloff, wobble/bristle/taper,
                           plus a height field lit as impasto
```

Four findings worth knowing before changing a default (all measured; see spec §10):

- **Structure-aligned orientation wins.** Edge alignment 0.980 vs 0.641 for random — and 0.641
  is the theoretical null of 2/π, which confirms the metric as much as the result. PSNR
  separates the arms by 0.4 dB, i.e. barely; fidelity is close to blind to what dominates the
  look.
- **Elongation is what makes it brushwork.** At `aniso_max = 1` the output reads as
  pointillist dots.
- **The black-hole problem is real and large.** Strokes alone cover 0.61 of the canvas at
  κ = 1.15 and 0.78 at √2, so 22–39 % would be holes without a base layer. The stroke
  underpainting takes total coverage to 0.982 and, more importantly, makes flat regions out of
  paint rather than blur.
- **Random stroke drop is not a default.** It works as designed, but at matched final stroke
  count it is neutral-to-negative: its contribution, irregular density, is largely subsumed by
  `size_sigma`. Kept as a knob (`drop_p`, default 0).

### The foveal map

Content-based allocation answers *"where is this image hard to approximate with one flat
colour"*, which is not the question a viewer's eye asks — a gravel path outscores a face,
every time. So the eye goes back in as an **input**: a coarse map, painted by hand on the
tuner page (the **Foveal map** button) or handed to the CLI as `--foveal mask.png`, where
black means *spend strokes here*.

It reweights the detail score per cell, through one summed-area lookup like every metric:

```
w      = (1 - foveal_strength) + foveal_strength · mask_mean(cell)
score' = score · w
```

`foveal_strength` is the entire control surface — it slides continuously from the content
metric alone (0) to the map alone (1). Two more knobs were tried and removed: a
`blend`/`replace` mode, which the slider already expresses, and a `foveal_coarsen` that
reached into `quadtree.build` to let unmarked regions keep strokes *above* `max_cell`. The
second worked, but moved the stroke-size distribution far too abruptly to tune — one octave
of it emptied every intermediate cell size out of the tree. `max_cell` is a hard ceiling
again.

Two things follow, and both are pinned by tests:

- **The budget is redistributed, not increased.** `solve_tau` searches against the weighted
  score, so an A/B against the unweighted tree is still at matched stroke count. Measured on
  the sample at 700 px, `target_n = 1500`: a mark covering 8.8 % of the frame takes 4.8 % of
  the strokes without the map and **17.0 %** with it, at 1475 vs 1496 strokes. The corollary
  is that if `target_n` is *already* unreachable there is nothing left to move and the map
  looks inert — the tuner's stats line says so rather than letting the brush take the blame.
- **It is inert until used.** At `foveal_strength = 0` the field is not wrapped at all and
  the painting is bit-identical; an unpainted map is a constant weight that `tau` absorbs.
  At strength 1 the weight reaches zero and the map becomes a hard **gate**: an unmarked
  cell cannot subdivide, so the budget is capped by what the mark alone can hold.

The map is **painted by hand, and is not prefilled from an automatic saliency guess.** The
page briefly had one — a centre prior times a frequency-tuned saliency [Achanta09], about
forty lines and no download — and it was removed because it produced a near-uniform map.
That is structural, not a matter of tuning: a cheap saliency field is smooth and non-zero
nearly everywhere, so its *mean over a cell* barely varies between cells, and a near-constant
weight is exactly what `solve_tau` absorbs. Anything auto-generated would have to be **sparse**
— a face box, a segmentation mask — to move this allocator at all, which is a much larger
dependency than the brush it would be saving.

### The flat-region brush

The structure tensor answers *"which way does the form run here"*, and in a sky, a gradient
ground or an out-of-focus background it has **no signal to answer from**: the local gradient is
smaller than the sensor and JPEG noise sitting on top of it. So `theta` is the arctangent of
noise and `coherence` is ~0 — and step 4 of `strokes.py` reads a coherence of 0 as *isotropic,
stay round*. The result is a field of randomly-turned round dabs, which is what those passages
were coming out as. A painter does the opposite: **the flatter the passage, the more it is laid
in with one directional sweep.**

The direction was there all along. Write the gradient as signal + noise and the outer products
average to `jxx ≈ gs² + sn²`, `jyy ≈ sn²` — the isotropic noise power lands on **both**
eigenvalues, so coherence saturates at `(gs²/(gs² + 2sn²))²` and raising `tensor_sigma` averages
the bias without removing it. Only pre-filtering the luma attacks `sn²` itself. Measured on
`assets/zurisee` over its hazy gradient band, median coherence goes **0.376 → 0.945** with an
8 px pre-blur while the region's parallelism barely moves (0.75 → 0.82): the noise was hiding a
direction, not standing in for a missing one.

So a second, coarse structure tensor is computed at `flat_sigma` — one number driving both the
pre-blur and the tensor smoothing, because they are the same statement — **on a decimated
grid**. The field it produces is band-limited to `flat_sigma` by construction, so evaluating
it at full resolution spends 16× the arithmetic to represent nothing; it is computed on a grid
decimated until its own sigma lands at 2 px and sampled back through `flat_step`. On a 12.2 Mpx
canvas that is 34.6 s → 1.2 s, and the sampled orientation moves a median 0.1°.

It is then folded into the fine field as a **vector sum at the doubled angle**, with weights
that hand over as confidence runs out:

| source | weight | where it wins |
| --- | --- | --- |
| fine tensor | `c` | a genuine edge or fold |
| coarse tensor | `flat_dir · cf · (1 − c)` | a sky, a gradient, a soft background |
| `flat_theta_deg` | `flat_dir · 0.35 · (1 − c)(1 − cf)` | a uniform ground, where there is nothing to read |

The effective coherence is the **length** of that sum, and three properties fall out of the
choice of a vector sum rather than a `max()` or a mode switch:

- at `c = 1` the other two weights vanish, so **a coherent edge cannot be disturbed** — edge
  alignment on the striped fixture is 0.9789 at full strength against 0.9795 with the brush off;
- where the scales **agree** the length adds, so a flat gradient recovers a high coherence and
  the stroke stretches along it. On a ramp-under-noise fixture the median aspect ratio goes
  1.15 → 5.01 and parallelism 0.595 → 0.923, at an identical stroke count;
- where they **disagree** the length cancels, so an ambiguous neighbourhood goes *rounder*
  rather than committing to one of two directions at random.

The budget is untouched by construction: the blend reads neither the detail field nor the tau
search, so this cannot buy or spend a stroke — an A/B against it is at matched count. `flat_dir`
is the whole control surface, and at **0 it is bit-identical** to the painting before it existed.

### What a render actually costs

Two of the three costs below were only visible at "full res", where the canvas is the source's
own resolution rather than the ~1000 px the tuner previews at — and where cost scales with area
while the things budgeting it did not. Measured on a 12.2 Mpx canvas (3024×4032), `plan()` only:

| stage | was | now | why |
| --- | --- | --- | --- |
| flat tensor | 34.6 s | 1.2 s | decimated to the scale it is band-limited to (above) |
| base layer | 25.0 s | 1.8 s | the blurred ground is piecewise constant on the block grid, so a 49-tap convolution at full resolution folds to a 5-tap one on a grid 16× coarser per axis — same weights, same value, different summation order |
| fine tensor | 13.6 s | 13.6 s | genuinely full-resolution work; nothing to reclaim |
| **plan total** | **75.0 s** | **18.5 s** | |

The third is not in `plan()` at all. The **intermediate preview** converted and transferred
the whole canvas per frame — 48.8 MB and 212 ms at 12.2 Mpx — against a *fixed* 180 ms cadence,
so the previews were asking for more than 100 % of the worker and the strokes went down in
whatever was left. Frames are now subsampled to 1024 px (the page draws them into a pane a few
hundred pixels wide either way) and gated on their own measured cost, so previews can never take
more than a quarter of a render regardless of canvas size or machine. The finished painting is
still sent whole.

One reporting defect went with it: the busy clock starts at the render *request*, so the pill
read `painting 400/11844 · 128.5s` for a raster phase that had just begun — the 128 s was
`plan()`, before a single stroke went down.

Impasto (step 2, [Hertzmann02] "Fast Paint Texture") is implemented and on by default: a
height buffer accumulated in the same pass as the colour with the same alpha blend, normals by
central difference, Phong with view-independent occlusion. It adds **nothing** to the stroke
buffer contract — the textures are closed form in stroke-local `u, v` and reuse the per-stroke
`phase` already in the buffer, so the rng draw order is untouched. Cost is +46 % end to end.
`bristle_amp = 0, taper_amp = 0, impasto = false` reproduces a step-1 painting exactly.

## CI and deploy

The remote is **`git.drz.li`**, a self-hosted **GitLab** — not GitHub. The default branch is
`master`, and CI is [`.gitlab-ci.yml`](.gitlab-ci.yml); a workflow under `.github/` would never
run. The pipeline installs `node` and numpy < 2 (matching what Pyodide
ships, so CI measures the same floats), runs the core suite, the three parity suites, the page
verification and the static build — then publishes `public/` to GitLab Pages, from the default
branch only.

## Auto-research

An optional unattended research loop: one Claude Code cycle a night on a compute node,
alternating literature scouting with pipeline experiments, committing what it finds to the
`auto-research` branch. There is no user cron and no user systemd on the cluster, so
recurrence comes from SLURM itself — each cycle ends by `sbatch`-ing its successor, which is
what makes it survive logout and the session that created it being killed.

```bash
./auto-research/launch.sh        # first cycle at the next 02:07, then nightly
./auto-research/launch.sh now    # first cycle immediately
touch auto-research/STOP         # stop: the running cycle finishes, nothing resubmits
```

Design and the cluster facts behind it: [`docs/spec-auto-research.md`](docs/spec-auto-research.md).
Findings land in [`docs/research/LOG.md`](docs/research/LOG.md) and the queue in
[`docs/research/QUESTIONS.md`](docs/research/QUESTIONS.md), on the `auto-research` branch —
`master` carries them empty.

## Prior art

Attribution matters here because the novelty is not the quadtree and not the strokes; it is
dropping optimization entirely and getting the allocation from a compression-style detail
measure, so the whole thing is feed-forward and runs in a browser. All citations were verified
against primary sources on 2026-08-15 and are reproduced in
[`oilpaint/__init__.py`](oilpaint/__init__.py), next to the code that implements them.

| work | what we take |
| --- | --- |
| **Haeberli**, *Paint By Numbers*, SIGGRAPH Computer Graphics 24(4), 1990 | the founding idea: an ordered collection of strokes sampling colour, shape, size and orientation from a source image |
| **Litwinowicz**, *Processing Images and Video for an Impressionist Effect*, SIGGRAPH 97 | orientation along isophotes, plus random perturbation of length / colour / orientation |
| **Hertzmann**, *Painterly Rendering with Curved Brush Strokes of Multiple Sizes*, SIGGRAPH 98 | the closest prior art: layers of decreasing radius, and the opaque coarse first pass |
| **Hertzmann**, *Fast Paint Texture*, NPAR 2002 | the impasto height field and its lighting |
| **Hays & Essa**, *Image and Video Based Painterly Animation*, NPAR 2004 | smoothed orientation fields rather than per-pixel angles |
| **Kang, Lee & Chui**, *Coherent Line Drawing*, NPAR 2007 | edge tangent flow — the stronger orientation field, if the structure tensor proves too noisy |
| **Samet**, *The Quadtree and Related Hierarchical Data Structures*, ACM Computing Surveys 16(2), 1984 | the subdivision structure and its variance criterion |
| **Zhang et al.**, *GaussianImage*, ECCV 2024 | the optimized counterpart — the contrast case, deliberately not followed |

The `dct` detail metric uses the standard JPEG luminance quantization table from
ITU-T T.81 / ISO-IEC 10918-1 Annex K.

## Working in this repo

[`CLAUDE.md`](CLAUDE.md) routes to the three skills that govern changes here — the
Python↔JS parity rule, the contract for publishing to the public site, and what is and is not
safely reusable from the two sibling repos. Read the parity skill before touching either side
of a ported routine.
