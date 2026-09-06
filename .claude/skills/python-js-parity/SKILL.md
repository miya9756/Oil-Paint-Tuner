---
name: python-js-parity
description: The rule that governs this project's Python-prototype-to-browser architecture — the Python implementation is the source of truth, the JS/WASM port must mirror it to float epsilon, and a parity test running the real JS under node proves it on synthetic data. Read BEFORE writing the browser port of any Python routine, before changing either side of an already-ported routine, and before deciding what a WASM build is allowed to change.
---

# Python ↔ JS/WASM parity

This project is a Python prototype that becomes a browser program. That means every
numerical routine will end up existing **twice** — once in Python, once in JS or in WASM
compiled from C/C++/Rust. Two implementations of the same math is the central risk of the
whole architecture, and the sibling repo already paid for the lesson.

## The failure mode, stated plainly

From 4d-relight's `CLAUDE.md`, about its browser player:

> Its JS decode path **mirrors `compression/decoder.py` to float epsilon** — keep them in
> sync; a divergence silently corrupts rendered geometry.

And from its point-trajectory parity test:

> The page renders geometry it computes *itself*. If that math drifts from the Python the
> bundle was written with, the demo silently shows deformations the model never predicted.

**Silently** is the operative word, and it is worse for paint than for geometry. A drifted
splat decode produces visibly wrong shapes. A drifted pigment-mixing or paint-transport
routine produces an image that is merely *a bit different* — plausible, pretty, and wrong.
Nothing about looking at it will tell you. Only a test will.

## The three rules

**1. Python is the source of truth.** The reference implementation is the readable,
unoptimised, f64 Python one. The browser side is a *port of it*. When they disagree, the
Python is right by definition and the port has the bug — unless the Python is what changed,
in which case the port is stale and must follow. Never resolve a disagreement by adjusting
the Python to match what the browser happens to render.

**2. Every ported routine gets a parity test before it is trusted.** Not after it looks
right on screen. Looking right is exactly the evidence this failure mode is immune to.

**3. The port may be faster, but not different.** Vectorising, tiling, restructuring loops,
moving to a shader — all fine. Changing the order of a float reduction, dropping a clamp,
substituting a cheaper approximation of a transcendental — **not** fine without measuring
the divergence and writing down the tolerance it justifies. If a fast path genuinely needs
a different algorithm, that is a deliberate decision with a documented error bound, not a
silent one.

## The harness shape

4d-relight's `tests/test_traj_parity.py` and `tests/test_knot_parity.py` are the working
templates. Read one before writing the first parity test here. The shape:

```
build synthetic input from random numbers   (no trained model, no GPU, no real asset)
  ↓
run the REAL browser-side code under `node`  (not a reimplementation of it)
  ↓
compare EVERY intermediate stage             (not just the final output)
```

Four things about that shape are load-bearing:

- **Synthetic input, seeded.** `np.random.default_rng(seed)`. The test must not need a
  trained model, a capture, or a large asset — that is what keeps it in CI at seconds-scale
  and keeps it runnable while the real data pipeline is still broken.
- **Run the real JS.** The test shells out to `node` and imports the actual shipped module.
  A test against a Python transcription of the JS proves nothing; the transcription is the
  thing that drifts.
- **Compare intermediate stages.** The trajectory test compares knot bracketing, then the
  curve basis, then final positions — three checkpoints, so a failure localises instead of
  just saying "the numbers differ". Pick the equivalent seams here: quantisation, one
  simulation step, colour-space conversion, the composite.
- **Skip cleanly when `node` is missing, exit 0.** But see the CI note below — a skip that
  nobody notices is how a suite goes green while checking nothing.

## Tolerance: f32 vs f64 is the expected difference

The browser computes in `Float32Array` and stores f32; Python stays in f64. That is a real,
expected divergence, not a bug — so the tolerance encodes it explicitly. 4d-relight uses:

```python
FLOAT32_SLACK = 1e-5   # JS dequantises into Float32Array and stores f32 results,
                       # Python stays in f64.
```

Name the constant and comment *why*, as above. A bare `atol=1e-5` in an assert is a number
nobody can later evaluate. And a tolerance that gets loosened to make a test pass has
deleted the test — if a comparison needs more slack than f32 explains, find out what else
is different first.

**For an iterative simulation, per-step tolerance is not enough.** Paint transport, wet
blending and any relaxation loop *accumulate* f32 error over steps, so a per-step bound of
1e-5 says nothing about frame 500. Test both: one step at f32 slack, and a long run against
a bound you have actually measured and written down. If the long run diverges, that is
information about the algorithm's conditioning, not a reason to widen the number.

## Make the bundle self-checking too

Beyond the test, 4d-relight ships a runtime guard worth copying:

> every bundle carries a torch-computed `parity` block and the page **refuses to render**
> if it disagrees.

That catches the case the test cannot: a data file produced by an *older* version of the
Python, loaded by a newer page. If this project serialises anything from Python for the
browser to read, put a small computed check in the file and have the page fail loudly
rather than render something subtly wrong.

## CI

Parity tests belong in CI precisely because they are cheap — torch-free, GPU-free, no
asset. 4d-relight scopes its GitLab pipeline to exactly these, and installs `node`
explicitly with this reasoning:

> Both suites SKIP those checks when node is missing, which would leave the job green while
> checking almost nothing — so node is installed and its **presence asserted**, not hoped for.

Do the same here: install `node`, run `node --version` as an assertion, and only then run
the suite. A conditional skip is correct locally and dangerous in CI.

This repo pushes to GitLab, so the pipeline file is `.gitlab-ci.yml` — see
[sibling-repos](../sibling-repos/SKILL.md) for why a copied GitHub workflow will not run.

## When the port is WASM, not JS

Everything above holds, with the boundary moved: the parity test compares Python against
**the compiled artifact**, driven from `node` via its JS glue. Do not test the C/C++/Rust
source with a native test and call the browser covered — the compile is part of what you
are verifying.

Two additional hazards specific to WASM:

- **Build flags change results.** `-ffast-math` (and Rust's equivalent relaxations) permit
  reassociation, drop NaN/Inf guarantees, and will move your numbers. If a fast-math build
  is wanted, the parity test is what tells you the cost — run it both ways before choosing.
- **SIMD reduction order differs from scalar.** A vectorised sum of floats is a different
  number from a sequential one. Expect it, measure it, and if a routine's result is
  order-sensitive, either fix the order or widen that specific bound with a comment saying
  which SIMD width it assumes.

## Practical checklist

- [ ] Python reference exists, is readable, and is the one that gets edited first.
- [ ] Ported routine has a parity test with seeded synthetic input.
- [ ] The test runs the real shipped JS/WASM under `node`, not a transcription.
- [ ] Intermediate stages compared, not just the final result.
- [ ] Tolerance is a named constant with a comment explaining the source of the difference.
- [ ] Iterative code tested for accumulated drift over a long run, not only one step.
- [ ] CI installs node and asserts its presence.
- [ ] Serialised data carries a parity block; the page refuses to render on mismatch.
