# Step 1 — Feed-forward image → Gaussian-stroke oil painting

Status: **accepted, in implementation.** Written 2026-08-15 from the proposal in-thread;
the three changes in §1 were reviewed and adopted on the same day. Assessment first, then
the build. All citations in §2 were verified against primary sources on 2026-08-15.

---

## 1. Assessment of the proposal

### What is right, and is worth committing to now

**The core claim holds.** "A stroke is a vectorized region of pixels" is exactly the premise
of stroke-based rendering, a line of work going back to Haeberli's *Paint by Numbers*
(SIGGRAPH 1990). The observation from `4d-relight/web/demo` — hard-edged ellipsoids at
inflated radius abstract the image while preserving structure — is a real effect and a
sound visual target. We are not guessing whether the look exists; we have seen it.

**Feed-forward with no optimization and no network is the right call, and it is not a
compromise.** The obvious alternative (GaussianImage, Zhang et al. 2024, and the 2D-Gaussian
image-fitting family) optimizes positions per image by gradient descent. That buys fidelity
we explicitly do not want — a perfectly fitted Gaussian image is a *photograph*, not a
painting. Abstraction is the goal, so the reconstruction error we give up is the product.
Feed-forward also runs in milliseconds, in-browser, with no weights to ship. Keep it.

**Cell edge length as the radius surrogate is right**, and the coarse-to-fine allocation is
essentially Hertzmann's (SIGGRAPH 1998) multi-scale brush idea reached independently.

**Radius inflation does hide gaps**, and the amount needed is derivable rather than tuned —
see §4.4.

### Three things I think need changing

These are the places where I expect the proposal as written to underdeliver, in descending
order of how much I believe it.

---

**(A) Random rotation will look like confetti. Orient strokes along image structure instead.**

Proposal step 4 says *randomly perturb rotation to make it look random and natural*. Random
orientation **alone** does not read as natural — it reads as noise. What makes painterly
rendering look hand-made is that strokes **follow the form**: they run along a cheek, with
the grain of cloth, around the curve of a cloud. Litwinowicz (SIGGRAPH 1997) oriented strokes
normal to the image gradient — i.e. **along isophotes** — and Hays & Essa (2004) refined that
into smoothed orientation fields.

*Correction to an earlier version of this section, which overstated the case.* Litwinowicz
1997 explicitly **does** randomly perturb stroke length, colour **and orientation**, for
exactly the reason the proposal gives — "to enhance the hand-touched look". So the proposal's
instinct is not merely defensible, it is the canonical method. The one substantive change is
that the perturbation is applied **around a structure-derived base angle** rather than around
nothing. Randomness stays; it acquires a mean.

Concretely: build the **structure tensor**, take its *minor* eigenvector (the direction
along the edge, not across it), and use that as stroke angle. Then add bounded jitter
(±15–25°) on top. Randomness is a seasoning, not the signal.

This also solves anisotropy for free (see B), and costs one Gaussian blur.

*Keep pure-random as an A/B arm.* I am confident enough to make structure-aligned the
default, and the comparison is nearly free to run — see §6.

---

**(B) Isotropic blobs will not read as brushwork. Elongation should come from structure too.**

The proposal perturbs the x and y radii randomly. That gives randomly-stretched blobs.
A brush stroke is elongated *in a direction that means something*. The structure tensor
already gives it: its **coherence** measures how strongly oriented the local neighbourhood
is. Strongly oriented region (an edge, a fold in cloth) → elongate along it. Isotropic
region (open sky) → stay round. That is one line of arithmetic once (A) is in.

Then jitter the ratio, per the proposal.

Note the cost, stated honestly: **elongation reintroduces the gaps that radius inflation was
meant to close**, because an area-preserving stretch pulls the ellipse away from the cell's
short axis. Which is why (C) matters more than it looks.

---

**(C) Do not fight black holes with radius alone. Put an opaque base layer underneath.**

Proposal step 5 inflates radii until holes are covered. This works, but it couples two
things that want to be independent: *how big the strokes look* and *whether the canvas shows
through*. Push radius up far enough to guarantee coverage under anisotropy and jitter, and
the image is mush.

The decoupled fix is Hertzmann's first pass and it is nearly free: **render a coarse, fully
opaque approximation first** — the quadtree's own coarse levels, or a heavily downsampled
image upscaled — then paint strokes over it in coarse-to-fine order. Any gap now shows an
approximately-correct colour instead of black.

That buys back the whole radius budget for aesthetics. Inflation becomes a look control
(overlap, impasto crowding) rather than a coverage constraint. **I consider this the single
highest-value addition to the proposal.**

---

### Two smaller gaps in the proposal

**Stroke colour is unspecified**, and the obvious choice is a trap. A cell's *mean* colour,
where the cell straddles an edge, is a muddy intermediate that appears in the painting as
blur — precisely at the locations the subdivision worked hardest to find. Use a **per-channel
median** (robust to the minority side of an edge) as the default. Cheap A/B against mean and
against centre-pixel sampling (Haeberli's original).

**Stroke count is unbounded.** A pure threshold gives a different stroke count on every
image, which makes A/B comparisons meaningless — you would be comparing allocation
strategies at different budgets. Expose a **target stroke count** and binary-search the
threshold to hit it (§4.6). Non-negotiable for the experiments in §6 to mean anything.

---

### The main open risk, stated up front

Quadtree cell sizes are powers of two. Even with jitter, stroke sizes come in discrete
octaves, and cell centres sit on a lattice. **The risk is that the result reads as a mosaic
filter rather than a painting.** Three mitigations are in the spec — centre jitter within
the cell (§4.3), continuous radius jitter across the octave boundary (§4.4), and
structure-aligned elongation (§4.5) — and I believe they are sufficient. But this is the
assumption most likely to be wrong, and it is visible in the first render, so we will know
early. If it survives, alternatives are Poisson-disk / Voronoi seeding or SLIC superpixels,
both of which drop the lattice entirely at higher cost. Not step 1.

### Verdict

The proposal is sound and well-matched to the goal. Build it, with (A), (B) and (C) folded
in. Steps 1→5 as written survive; what changes is that "random" becomes
"structure-aligned + random", and coverage moves from the radius to a base layer.

---

## 2. Prior art

Cited so the eventual page and any write-up can attribute honestly, per
[ship-to-site](../.claude/skills/ship-to-site/SKILL.md). **Verified against primary sources
on 2026-08-15**; the same table is reproduced in `oilpaint/__init__.py` so the citations sit
next to the code that implements them.

| work | what we take |
| --- | --- |
| **Haeberli**, *Paint By Numbers: Abstract Image Representations*, ACM SIGGRAPH Computer Graphics **24**(4), Aug 1990, pp. 207–214 | the founding idea: an ordered collection of strokes sampling colour, shape, size and orientation from a source image |
| **Litwinowicz**, *Processing Images and Video for an Impressionist Effect*, SIGGRAPH 97, pp. 407–414 | stroke orientation normal to the gradient (along isophotes), **and** random perturbation of length / colour / orientation "to enhance the hand-touched look" |
| **Hertzmann**, *Painterly Rendering with Curved Brush Strokes of Multiple Sizes*, SIGGRAPH 98, pp. 453–460 | **the closest prior art.** Layers of decreasing stroke radius; strokes applied only where the canvas differs from the reference; the opaque coarse first pass |
| **Hays & Essa**, *Image and Video Based Painterly Animation*, NPAR 2004, pp. 113–120 | smoothed orientation fields rather than per-pixel angles |
| **Kang, Lee & Chui**, *Coherent Line Drawing*, NPAR 2007, doi:10.1145/1274871.1274878 | edge tangent flow — the stronger orientation field to reach for if §4.5's structure tensor proves too noisy |
| **Samet**, *The Quadtree and Related Hierarchical Data Structures*, ACM Computing Surveys **16**(2), 1984, pp. 187–260 | the subdivision structure and its classic variance criterion |
| **Zhang, Ge, Xu, He, Wang, Qin, Lu, Geng & Zhang**, *GaussianImage: 1000 FPS Image Representation and Compression by 2D Gaussian Splatting*, ECCV 2024 | the **optimized** 2D-Gaussian counterpart — the contrast case, and what we deliberately do not do |

The JPEG luminance quantization table used by the `dct` detail metric (§4.1) is the standard
example table from ITU-T T.81 / ISO-IEC 10918-1, Annex K.

Honest framing for later: the novelty here is not the quadtree and not the strokes. It is
**dropping optimization entirely** and getting the allocation from a compression-style
detail measure, so the whole thing is feed-forward and runs in a browser.

---

## 3. Pipeline

```
source image
   │
   ├─▶ [1] preprocess ────── luma, integral images, optional linear-light conversion
   │
   ├─▶ [2] detail field ──── one of {var, range, grad, dct, residual}          §4.1
   │
   ├─▶ [3] quadtree ──────── subdivide while detail > τ, depth ∈ [dmin, dmax]  §4.2
   │         └── τ chosen by binary search to hit a stroke budget             §4.6
   │
   ├─▶ [4] structure tensor  orientation θ + coherence c per cell              §4.5
   │
   ├─▶ [5] cells → strokes   centre jitter, radius, elongation, rotation,      §4.3-4.5
   │                         colour; sort coarse→fine
   │
   ├─▶ [6] base layer ────── opaque coarse approximation                       §4.7
   │
   └─▶ [7] render ────────── painter's order, back-to-front `over`             §5
                             soft or hard-ellipsoid falloff
```

Stages 2–5 are pure geometry and are the part that must port to the browser. Stage 7 mirrors
a WebGL fragment shader exactly (§5).

---

## 4. Stage specifications

### 4.1 Detail field

The question each cell asks is: *can one flat colour stand in for this region?* Five metrics
implement it, behind one interface, all computed in **O(1) per cell via summed-area tables**
so the tree build is O(pixels) regardless of depth.

| id | metric | note |
| --- | --- | --- |
| `var` | luminance variance in the cell | classic quadtree criterion; **default** |
| `range` | 0.5/99.5-percentile luminance spread | robust to salt-and-pepper; needs no SAT |
| `grad` | mean Sobel magnitude | fires on edges, ignores smooth gradients — different bias |
| `dct` | AC energy of the 8×8 DCT-II, weighted by the JPEG luminance quantization table | the proposal's "use JPEG" idea, made concrete |
| `residual` | error of the current coarse approximation vs source | Hertzmann's criterion |

Two honest notes. **`var` and `residual` are close relatives** — a flat fill's squared error
*is* the variance — differing only in that `residual` measures against what has actually been
painted so far, including neighbour bleed. They will likely rank similarly; that is a finding,
not a failure. And **`dct` is block-quantized to 8×8**, which sits awkwardly under a
multi-scale tree and is tuned for perceptual *compression* rather than stroke allocation.
It is in the A/B because it was asked for and it is cheap, but `var` is the one I expect to win.

All metrics are computed on **luminance** (Rec. 709 luma). Chroma-only edges are rare in
practice and cost a 3× metric.

### 4.2 Quadtree

Standard adaptive subdivision. A cell splits into 4 if `detail(cell) > τ` and
`depth < dmax`; every cell subdivides at least to `dmin`.

- `dmin` (default 2) prevents a single stroke covering a quarter of a flat sky.
- `dmax` (default 7) caps stroke count at 4^7 = 16384 in the limit.
- Non-square images: subdivide within a square padded to `2^dmax`, discard cells whose centre
  falls outside the image. Simpler than a rectangular tree, and the discarded work is bounded.

Output is a flat list of **leaf cells**: `(x0, y0, size, depth)` in pixels.

### 4.3 Centre jitter — required, not optional

Cell centres sit on a regular lattice, which is the mosaic risk of §1. Displace each centre
uniformly within `±jitter_c × size/2` of the cell centre, with `jitter_c` default **0.35**.

This is stratified (jittered-grid) sampling: it destroys the lattice while keeping the
density that the tree worked out. Cheap, and load-bearing for the look.

### 4.4 Radius, and the coverage arithmetic

Let the cell edge be `e`. Define the stroke's **half-extent** `R` — the hard-ellipsoid
boundary, in pixels.

A circle inscribed in the cell (`R = e/2`) covers `π/4 ≈ 78.5 %` of it; the missing 21.5 %
is the four corners, and *that is the black hole*. Full single-cell coverage needs the
half-diagonal:

```
R = κ · e/2        κ = √2 ≈ 1.414  → exactly covers the square cell
                   κ ∈ [1.0, 1.6]  → useful range; > √2 is deliberate overlap
```

So the proposal's "increase the radius a bit" has a principled floor: **κ = √2 is where holes
close, not a tuned constant.** Default `κ = 1.15` *because* the base layer (§4.7) removes the
obligation to reach √2 — this is the trade §1(C) buys.

Then jitter `R` by `±jitter_r` (default 0.15) **continuously**, which also smears the
power-of-two size banding across octave boundaries.

Relation to the Gaussian: with the falloff of §5, `d` is measured in standard deviations, so

```
σ = R / hard_r          hard_r = the hard-edge cutoff in σ (default 1.5)
```

### 4.5 Orientation and elongation, from the structure tensor

```
Ix, Iy          Sobel derivatives of luma
J   = gaussian_blur( [[Ix², IxIy], [IxIy, Iy²]], ρ )      ρ default 2.0 px
λ1 ≥ λ2         eigenvalues of J
θ   = angle of the eigenvector of λ2          # minor axis = ALONG the edge
c   = ((λ1 − λ2) / (λ1 + λ2 + ε))²            # coherence, ∈ [0,1]
```

Sample `θ` and `c` at the (jittered) stroke centre. Then:

```
θ_stroke = θ + uniform(−jitter_θ, +jitter_θ)         jitter_θ default 20°
ratio    = 1 + (aniso_max − 1) · c                   aniso_max default 6.0
ratio   *= 1 + uniform(−jitter_a, +jitter_a)         jitter_a default 0.2
R_major  = R · √ratio                                # area-preserving
R_minor  = R / √ratio
```

Area preservation means elongation changes stroke *shape* without changing the painted area,
so it does not silently alter coverage statistics between A/B arms.

`aniso_max = 1.0` collapses this to round blobs, and `orient = random` swaps `θ` for a uniform
angle — those are the two ablation arms of §6.

### 4.6 Stroke budget

Given `target_n`, binary-search `τ` over ~20 iterations (leaf count is monotone decreasing in
`τ`) until the leaf count is within `±2 %`. Report the achieved `τ` and count.

This exists so A/B arms are compared at equal stroke count. Without it the comparison is
confounded and worthless.

### 4.7 Base layer

Render, fully opaque, beneath all strokes: the source image **box-downsampled to `2^dmin`
and nearest-upsampled**. Cost is negligible, and every gap now shows an approximately correct
colour.

Alternative arm: the coarse quadtree levels drawn as opaque rectangles. Slightly better
colour, slightly more visible as blocks where it shows through.

`--no-base` disables it, which is the arm that shows how bad the holes actually are — worth
rendering once for the record.

### 4.8 Colour

Per-channel **median** over the cell's pixels, default. `mean` and `centre` are A/B arms.

Colour space: work in float32 sRGB [0,1] by default. `--linear` converts to linear light for
averaging and compositing and back for output, which is physically correct; for paint it is
not obviously better, so it is an arm rather than the default. Note that **oil paint mixes
subtractively** and neither of these models that — Kubelka-Munk is explicitly step 2 (§8).

---

## 5. Renderer — mirrors the demo shader

The Python renderer is the reference implementation, and it is written to mirror what the
WebGL port will do, per [python-js-parity](../.claude/skills/python-js-parity/SKILL.md). The
falloff is taken verbatim from `4d-relight/web/demo/index.html`, so the visual target is
reproduced rather than approximated:

```glsl
d2 = dot(vPos, vPos)                 // vPos in units of σ; quad spans ±2σ
soft: A = exp(-d2)
hard: A = 1.0 - smoothstep(hard_r - aa, hard_r + aa, sqrt(d2))
discard when d2 > 4.0
```

In Python, `aa` (the shader's `fwidth(d)`) becomes the per-pixel σ-space step, `1/σ` along
each axis — an exact analytic stand-in for the GPU's screen-space derivative on an
axis-aligned pixel grid, and the one place the two sides are *deliberately* not
bit-identical. Document the resulting tolerance in the parity test rather than hiding it.

**Compositing: back-to-front `over`, painter's algorithm.** Strokes are sorted coarse→fine
(large first), with a seeded shuffle within each depth level so equal-size strokes do not
overlap in scan order. The demo uses front-to-back `under` because it is a 3D depth-sorted
renderer; in 2D with an explicit paint order, `over` is equivalent and simpler on both sides.
Array order **is** paint order — sort once at build time so the browser draws the buffer
straight through.

Per-stroke `alpha` (default 1.0) is a knob for glaze-like build-up; at 1.0 the hard-ellipsoid
mode is pure painter's algorithm.

---

## 6. Experiments

All at **matched stroke budget** (§4.6). `scripts/ab.py` runs a sweep and writes a contact
sheet.

| # | question | arms |
| --- | --- | --- |
| 1 | which detail metric allocates best? | `var` · `range` · `grad` · `dct` · `residual` |
| 2 | **does structure-aligned orientation beat random?** (§1A) | `structure` · `random` · `fixed` |
| 3 | **does structure-driven elongation help?** (§1B) | `aniso_max` ∈ {1.0, 2.0, 3.0, 5.0} |
| 4 | **how much does the base layer buy?** (§1C) | base × κ ∈ {1.0, 1.15, 1.41, 1.7} |
| 5 | does centre jitter kill the mosaic look? (§1 risk) | `jitter_c` ∈ {0, 0.2, 0.35, 0.5} |
| 6 | colour rule | `median` · `mean` · `centre` |
| 7 | budget scaling | `target_n` ∈ {1k, 4k, 16k, 64k} |
| 8 | soft vs hard falloff | `hard_r` ∈ {soft, 1.0, 1.5, 2.0} |

Experiments 2, 3 and 4 test the three changes I proposed in §1. If 2 and 3 come out neutral,
the proposal was right and I was wrong — revert to random, it is simpler.

**Test images must include the hard cases**: an open sky (does it stay flat, or waste
strokes?), fabric or hair (does orientation follow it?), a face (does the median colour rule
avoid muddy skin?), text or a hard graphic edge (where every method looks worst).

---

## 7. Metrics

Instrumentation, not objectives.

- **coverage** — fraction of pixels with accumulated α > 0.99 *with the base layer off*.
  Directly measures the black-hole problem, and is the number §4.4's arithmetic predicts.
- **PSNR / SSIM vs source** — a *diagnostic of the allocator*, *not the goal*. At fixed
  budget, higher means detail went where detail was. A method that maximizes it is
  approaching a photograph, which is the failure mode, so read it alongside the image.
- **edge alignment** — mean `|cos(θ_stroke − θ_isophote)|` weighted by coherence. Should be
  near 1.0 for `structure`, ~0.64 (the mean of |cos| over uniform angles) for `random`.
  This is the number that makes experiment 2 objective rather than a matter of taste.
- **stroke count**, achieved τ, and **wall-clock** per stage.

---

## 8. Explicitly out of scope for step 1

Named so they are not smuggled in: Kubelka-Munk subtractive pigment mixing · impasto height
field and relighting · curved / splined strokes (Hertzmann's actual contribution) · bristle
and canvas textures · temporal coherence for video · palette quantization to a finite set of
pigments · **any optimization or learned component**.

Step 2 candidates, in the order I would take them: **impasto** (a height field from stroke
overlap, lit by a moving light — and the relight machinery already exists in
`4d-relight/web/demo`), then **Kubelka-Munk**, then **curved strokes**.

---

## 9. Module layout

Config-first with dataclasses + YAML, mirroring 4d-relight so the two read alike.

```
oilpaint/
  image.py      load/save, luma, colour spaces, integral images
  detail.py     the five metrics behind one interface          §4.1
  quadtree.py   adaptive subdivision → leaf cells               §4.2
  tensor.py     structure tensor → orientation + coherence      §4.5
  strokes.py    cells → strokes: jitter, radius, aniso, colour  §4.3-4.8
  budget.py     threshold search for a target stroke count      §4.6
  render.py     2D Gaussian rasterizer, painter's order         §5
  metrics.py    coverage, psnr/ssim, edge alignment             §7
  pipeline.py   orchestration + PaintConfig dataclass
scripts/
  paint.py      CLI: one image in, one painting out
  ab.py         sweep runner → contact sheet
tests/
  test_quadtree.py   coverage/partition invariants
  test_render.py     falloff against closed form
  test_budget.py     binary search converges
configs/
  default.yaml
```

**No scipy, no cv2 in `oilpaint/` core.** Both are absent in a browser. Gaussian blur is
separable-numpy; Sobel is an explicit convolution; the eigendecomposition of a symmetric 2×2
is a closed form. `scipy`/`cv2` may be used in `metrics.py` and in tests, which do not port.
This constraint is what makes step 2 a port rather than a rewrite.

### The stroke buffer — the cross-language contract

Struct-of-arrays, so the browser uploads typed arrays directly and the paint order is the
array order:

```
x, y          float32[N]   centre, pixels
r_major       float32[N]   half-extent along θ, pixels
r_minor       float32[N]   half-extent across θ, pixels
theta         float32[N]   radians
rgb           float32[N,3] colour
alpha         float32[N]
```

Serialized as `.npz` for Python and as a flat binary + JSON sidecar for the web. Per the
parity skill, the sidecar carries a **parity block** (a checksum plus a few reference
rasterized pixels) and the page refuses to render on mismatch.

---

## 10. Findings from the first implementation (2026-08-15)

Recorded as they were measured, including the two places the spec itself was wrong.

**The lattice risk of §1 did not materialise.** With `jitter_centre = 0.35` the result reads
as strokes, not as a mosaic. This was the assumption most likely to be wrong and it survived.

**Experiment 2 is settled, and structure-aligned orientation wins.** At a matched budget of
5000 strokes on `data/samples/photo.jpg`:

| arm | PSNR | coverage | **edge alignment** | mean aniso |
| --- | --- | --- | --- | --- |
| structure, aniso 3 | 24.44 | 0.696 | **0.9800** | 1.50 |
| random, aniso 3 | 24.03 | 0.666 | 0.6413 | 2.00 |
| structure, aniso 1 | 24.22 | 0.725 | 0.9800 | 1.00 |
| random, aniso 1 | 24.18 | 0.725 | 0.6413 | 1.00 |

The random arms land on **0.641 against the theoretical null of 2/π = 0.6366**, which
confirms the metric as much as the result. Visually the structure arm follows the hair, the
jacket edge and the diagonal of the snow bank; the random arm reads as confetti. The
`aniso = 1` rows read as pointillist dots, so **elongation is what makes it brushwork** —
change (B) earns its place independently of (A).

PSNR separates the arms by only 0.4 dB, i.e. barely. That is the expected outcome and the
reason `edge_alignment` exists: fidelity is close to blind to the thing that dominates the
look.

**Coverage confirms the black-hole problem is real and large.** Strokes alone cover
0.61 of the canvas at `κ = 1.15`, 0.78 at `κ = 1.41`. So **22–39 % of the image would be
holes without the base layer** — change (C) is not a refinement, it is load-bearing.

### Two errors in the first draft of this spec

- **§4.2's `dmin` was expressed as a depth, and the default was backwards.** A depth is
  relative to the *padded tree size*, so `dmin = 2` silently meant 256 px strokes on a
  1024 tree, and flat regions came out as enormous blobs. Stroke scale in **pixels** is
  what the look depends on, so `max_cell` / `min_cell` are now pixel counts and `dmin` is
  derived. Related: **below ~8 px a stroke stops reading as a stroke** and the output
  collapses into a slightly-degraded photograph.
- **§4.7's base layer was specified as box-downsample + nearest-upsample, which printed
  its own 2^k rectangles straight through the gaps** — a worse artefact than the holes it
  was added to fix. It is now downsample → upsample → blur, which leaves no grid.

### Irregularity: why the first output read as "artifacts" (2026-08-15, second pass)

The v1 result was recognisably synthetic. Five separate causes, only one of which is about
randomness:

1. **Exactly one stroke per cell** — density perfectly uniform within a depth level.
2. **Size quantized to powers of two** — `jitter_radius` is bounded and cannot leave its
   octave, so within a flat region every stroke was the same size.
3. **Every stroke a perfect ellipse**, same falloff, no exceptions.
4. **Flat regions were blurred base layer, not paint at all.**
5. **Uniform alpha and flat per-stroke colour** — no glazing, no pigment variation.

Fixes, ablated cumulatively at a matched budget on `grain_example`:

| arm | strokes | coverage (detail) | coverage (all) |
| --- | --- | --- | --- |
| 1 baseline: blurred base, no irregularity | 3287 | 0.756 | 0.756 |
| 2 **+ stroke underpainting** | 3302 | 0.756 | **0.982** |
| 3 + `size_sigma` 0.30 (log-normal) | 3302 | 0.805 | 0.990 |
| 4 + `wobble_amp` 0.18 | 3302 | 0.806 | 0.990 |
| 5 + `color_jitter` 0.03 | 3302 | 0.807 | 0.990 |
| 6 + `drop_p` 0.35 | 2220 | 0.799 | 0.989 |

**The stroke underpainting is the big one** (cause 4): a *complete* canvas of coarse strokes
painted first, which is Hertzmann's actual first pass. It takes total coverage from 0.756 to
0.982 and, more importantly, means flat regions are made of paint instead of blur. Every
pixel is now under 2+ strokes at different scales, which is where painterly depth comes from.

`size_sigma` (log-normal, multiplicative, has a tail) is the fix for cause 2 and the second
biggest. `wobble_amp` modulates the hard edge by two harmonics of the local angle with a
per-stroke phase — closed-form, no texture, so the fragment shader evaluates the identical
expression.

**Random drop + area-compensating expansion was tested and is not adopted as a default.**
The proposal was to drop a fraction `p` of strokes and expand the rest by `1/sqrt(1-p)` to
preserve painted area. It works exactly as designed. But compared at *matched final stroke
count* — which arm 6 above is not — it is neutral to slightly negative:

| arm | final strokes | coverage | PSNR |
| --- | --- | --- | --- |
| `drop_p` 0.00, budget 2200 | 2195 | 0.825 | 15.77 |
| `drop_p` 0.35, budget 3400 | 2205 | 0.799 | 15.52 |
| `drop_p` 0.60, budget 5500 | 1362 | 0.793 | 14.79 |

The reason is that its contribution — irregular *density* — is largely **subsumed by
`size_sigma`**, which did not exist when the idea was proposed. Getting the same looseness
by asking for fewer strokes with more size spread is cheaper and keeps more detail. Drop is
also content-blind: it can delete a stroke carrying a highlight, and the neighbours that
expand into the gap bring their own colour. Kept as a knob (`drop_p`, default 0) since it
does read looser and this is one image at one budget.

**One artefact introduced and fixed in the same pass:** with the stroke underpainting the
canvas was left initialised to black, so the coarse layer's ~1.8 % of uncovered pixels
showed as a scatter of hard black specks. The blurred base now sits *underneath* the stroke
underpainting rather than being replaced by it — never seen, costs one blur.

### Correction to §1(A)

Stated in §1 already, repeated here because it is the one place the assessment was unfair to
the proposal: Litwinowicz 1997 **does** randomly perturb orientation, exactly as proposed.
The change is that the perturbation now has a structure-derived mean, not that randomness
was wrong.

---

## 11. Open questions

1. **Does the lattice survive jitter?** (§1 risk.) Visible in the first render.
2. **Is 8×8 `dct` usable under a multi-scale tree at all**, or does the block grid print
   through? Experiment 1 answers it; a negative is a legitimate finding.
3. **Does `residual` justify its sequential cost** over `var`, given they are close relatives?
4. **Where does the browser port draw the CPU/GPU line** — tree on CPU in WASM and strokes on
   GPU, or the whole thing in a compute-ish fragment pass? Deferred until the Python numbers
   exist, but the §9 stroke buffer is the seam either way.
