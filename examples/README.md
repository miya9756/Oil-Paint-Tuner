# Example projects

A **project** is one JSON file holding a whole painting setup: every `PaintConfig` field,
the per-layer overrides, the placed swirl centres, and both masks. It is what the tuner's
**Save project** button writes and what `scripts/paint.py --project` reads, so a setup moves
between the browser and the command line without being retyped.

The format is defined in [`oilpaint/project.py`](../oilpaint/project.py) — read that for the
reasoning. The short version is below.

## Try it

```bash
# over the photograph it was made for (DIV2K 0201) ...
python scripts/paint.py assets/0201.png out.png \
    --project examples/mountain-valley.oilpaint.json --max-side 900

# ... or over the one in this repo. That is not a fallback, it is the point: a project is
# a recipe, so it runs over any photograph and the passages land where the mask puts them.
python scripts/paint.py web/tune/sample.jpg out.png \
    --project examples/mountain-valley.oilpaint.json --max-side 900
```

Any flag still wins over the file, so a project is a starting point rather than a fixed
recipe:

```bash
python scripts/paint.py web/tune/sample.jpg out.png \
    --project examples/mountain-valley.oilpaint.json --target-n 12000 --seed 7
```

Or drop the `.json` straight onto the tuner page.

## What is in a project

```jsonc
{
  "format": "oilpaint-project",
  "version": 1,
  "note":   "free text; an example can explain itself",
  "source": { "name": "sample.jpg" },     // a NOTE, not the pixels — see below
  "params": { "target_n": 6000, ... },    // any PaintConfig field
  "regions": {                            // per-layer overrides, by region id
    "1": { "flow": "starry", "palette": "nocturne" },
    "2": { "flow": "waterlily", "hue_rotate": -12 }
  },
  "vortices": { "1": [[0.28, 0.12], [0.62, 0.09]] },   // swirl centres, as fractions
  "masks": {
    "regions": "data:image/png;base64,...",   // or a path: "sky-mask.png"
    "foveal":  "data:image/png;base64,..."
  }
}
```

**The photograph is not in it.** A project is a recipe, not the ingredients — a
full-resolution phone photo is 5–8 MB, which is 8–11 MB of base64 and not a thing to commit
beside code. The image stays the first argument to `paint.py`; the project records only its
name so a file found later can say what it was made for. The useful consequence: running the
same project over a *different* photograph is a perfectly sensible thing to do.

**Everything is optional.** A hand-written project naming three fields means "these, and the
defaults for the rest". The tuner writes all of them, because a file it saves is meant to be
an exact record.

## Masks: embedded or referenced

Both forms work, and they are for different jobs.

- **Embedded** (`"data:image/png;base64,..."`) makes the file self-contained, which is what
  an example in a repository has to be. It also keeps the whole setup out of Git LFS —
  `.gitattributes` sends every `*.png` to LFS, and a `.json` stays diffable and viewable on
  GitHub.
- **Referenced** (`"regions": "sky-mask.png"`, resolved next to the project file) makes the
  mask an ordinary PNG you can open in any image editor, paint in, and save back. For a
  complicated boundary — a figure against a busy background — an image editor is a far
  better tool than the tuner's flood fill.

Either way the conventions are the CLI's, so a mask is a mask wherever it came from:

| mask | format | convention |
| --- | --- | --- |
| `regions` | RGB PNG | black is the base; each cube corner (red, green, blue, yellow, magenta, cyan, white) is one passage — the legend in [`oilpaint/regions.py`](../oilpaint/regions.py) |
| `foveal` | grey PNG | **black means spend strokes here**, the same as `--foveal` |

A mask painted elsewhere goes in through the tuner's **Load mask** button, or through
`--regions` on the command line — they take the same file.

## The files here

| file | what it shows |
| --- | --- |
| `mountain-valley.oilpaint.json` | three passages over one mountain valley — a van Gogh sky with four placed swirl centres, a Monet foreground of short woven dabs, and the limestone between them left on the base grade. Plus a foveal map that spends the budget on the valley mouth rather than on the fern texture in the corners. Both masks embedded, 24 KB. |

Its photograph is [`assets/0201.png`](../assets/0201.png) — **DIV2K 0201**, from the public
super-resolution dataset, and a sibling of the `assets/0465.png` already in this repo. It
lives in Git LFS like every other source photograph here, so a clone with LFS installed (or
`git lfs pull`) is what fetches it.

You do not need it to use the project, and nothing in CI does either — the workflow checks
out with `lfs: false`, on the premise that nothing *published* is LFS-tracked. The project
file is 24 KB of JSON with both masks inside it.

The masks were not painted by hand. They were classified from the photograph on `g − b`,
green against blue, which separates all three passages cleanly — sky −0.10, limestone +0.09,
sunlit fern +0.22. Worth knowing if you build one the same way: the obvious test, *green >
red*, selects almost nothing here, because sunlit autumn fern is **red-dominant** (r 0.449,
g 0.411). That is the kind of thing you find by measuring rather than by assuming.

`verify_page.py` loads every example and fails if one stops carrying what it claims, because
an example that has rotted is worse than none: it is the file people copy.
