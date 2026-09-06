# Open questions — the auto-research queue

Ordered by what is most worth doing next. Each cycle takes **one** item, settles it as far
as it can, and rewrites this file: strike what is settled, add what got opened. Tag each
item `[lit]` or `[exp]` so a cycle in either mode can find work.

Seeded 2026-08-16 from `docs/spec-step1.md` §8 and §11. Nothing below has been attempted by
the loop yet.

---

1. `[exp]` **Is 8×8 `dct` usable as the detail metric under a multi-scale tree at all, or
   does the block grid print through?** (spec §11.2 — Experiment 1 answers it.) A negative
   is a legitimate finding and should be recorded as one.

2. `[exp]` **Does `residual` justify its sequential cost over `var`?** (spec §11.3.) They are
   close relatives; measure both quality and wall-clock on the same image at the same
   `target_n`, and say plainly whether the difference is visible.

3. ~~`[lit]` **Impasto — the height field from stroke overlap, lit by a moving light.**~~
   **SETTLED 2026-08-17, by hand rather than by the loop.** [Hertzmann02] "Fast Paint
   Texture" (NPAR 2002) is the method and it is now implemented in `render.py` / `render.js`:
   a second height buffer accumulated in the same pass as the colour, composited with the
   same alpha blend (**not** summed — the paper tried that and buried strokes surfaced),
   plus a per-stroke offset proportional to the strokes already drawn, then normals by
   central difference and Phong. It adds **nothing** to the stroke buffer contract (spec §9):
   the textures are closed form in stroke-local `u, v`, and the per-stroke variation reuses
   the `phase` already in the buffer, so the rng draw order in `strokes.py` is untouched.
   Cost is +46% end to end (2.42s → 3.53s at 700px). Open sub-question left behind: the
   lighting is applied to display values, matching the paper's GPU bump-map; **in linear
   light would be more defensible and is a one-line move — is it visibly better?**

   Two terms were added beyond the paper, and the split between them is the useful part:
   **ambient occlusion is view-INdependent** (a crease sees less of the room whatever the
   eye does, so it multiplies the ambient term only, from a band-passed cavity map), while
   **the specular is the view-dependent term** — `view_deg` / `view_elev_deg` now exist and
   default to straight-on, which is the seam an interactive page drives from the pointer.
   A failed attempt worth not repeating: normalising the specular against a flat surface,
   to stop an oblique eye glaring the canvas white. At the mirror direction the flat lobe
   is *maximal*, so the subtraction deletes the signal exactly where the relief has most to
   say, and the view slider measurably did nothing.

   The occlusion then read as a plastic **drop shadow**, and the cause was not the
   occlusion: it was that the height field started at 0, so every stroke was a 1.0-tall
   plateau on an abyss and both the bevel and the cavity map traced its silhouette. The
   base layer is PAINT, so it needs a height — and putting it just *below* the strokes is
   not enough, because every stroke is then still on the same side of it and still gets a
   shadow. `GROUND_AT = 0.5` puts the ground at the MEAN paint level, half the strokes above
   and half below, and the halo goes. Lesson, and it is the third instance of the same
   shape: **a term that looks like a shading artefact is usually a geometry artefact.**

   That fix is what makes `base='blur'` usable as the default (it is now), since blur halves
   coverage to 0.55 — with 45% of the canvas showing ground, the height the ground sits at
   *is* the look.

   **Next increment for view-dependence, not attempted:** parallax — at a grazing view a
   raised lip should occlude the crevice behind it, which needs the occlusion sampled along
   an offset proportional to height. That is the one remaining view-dependent effect a
   near-planar painting actually has, and it is what would make the museum's "walk past the
   canvas" read as geometry rather than as a shifting highlight.

   A lesson worth carrying into any further texture work, because it cost three attempts on
   the bristles and two on the fringe: **any texture feature needs its scale pinned to
   pixels, not to the stroke.** `u, v` are in units of sigma, so anything written naively in
   them is a fixed fraction of the mark — which is why a 130 px blob got a 130 px "wobble"
   and read as a smooth vector shape however high `wobble_amp` went. Both the SIZE and the
   DEPTH of a feature have to be anchored separately: pinning only the size gave sea-urchin
   spikes, pinning only the depth gave corduroy.

   The survey that preceded it is worth not repeating: no stroke-texture set was found that
   is both commercially usable and of known provenance. Stylized Neural Painting is CC
   BY-NC-SA in its README (and CC0 in its LICENSE file — they contradict) with assets named
   `brush_fromweb2_*`; Im2Oil is CC0 but its `brush/` art is unattributed; the IEEE DataPort
   "ArtStroke" set (520 real strokes) is subscription-gated. Clean but not directly usable:
   MyPaint's brush packs are genuinely CC0, though they are *engine settings*, so an atlas
   would have to be baked through libmypaint (ISC). Closed form avoids all of it and keeps
   the deployed page asset-free.

4. `[lit]` **Kubelka-Munk subtractive mixing.** Second step-2 candidate. §4.8 notes real
   paint mixes subtractively and neither current colour model captures it. What is the
   minimum viable K-M that runs per-stroke in a browser, and is the visual gain worth the
   two extra channels?

5. `[lit]` **Where does the browser port draw the CPU/GPU line?** (spec §11.4.) Tree on CPU
   in WASM with strokes on GPU, or the whole thing in a fragment pass? The stroke buffer is
   the seam either way. Deferred until the Python numbers exist — check whether they do now.

6. `[lit]` **What has stroke-based rendering published since 2024** that is feed-forward,
   needs no weights, and would fit this pipeline? Recent work exists (e.g. MambaPainter,
   arXiv 2410.12524, single-step neural SBR) but most of the field is optimization- or
   network-based, which §1 rejects on purpose. Record the rejects and the reason, so later
   cycles do not re-scout the same ground.
