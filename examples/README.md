# Example projects

A **project** is one JSON file holding a whole painting setup: every `PaintConfig` field,
the per-layer overrides, the placed swirl centres, and both masks. It is what the tuner's
**Save project** button writes and what `scripts/paint.py --project` reads, so a setup moves
between the browser and the command line without being retyped.

The format is defined in [`oilpaint/project.py`](../oilpaint/project.py) — read that for the
reasoning. The short version is below.

## Try it

```bash
python scripts/paint.py web/tune/sample.jpg out.png \
    --project examples/two-passages.oilpaint.json --max-side 900
```

Any flag still wins over the file, so a project is a starting point rather than a fixed
recipe:

```bash
python scripts/paint.py web/tune/sample.jpg out.png \
    --project examples/two-passages.oilpaint.json --target-n 12000 --seed 3
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
| `two-passages.oilpaint.json` | two painted passages over an impressionist base — a starry sky and a woven foreground — with three placed swirl centres and a foveal map. Both masks embedded. |

`verify_page.py` loads every example and fails if one stops carrying what it claims, because
an example that has rotted is worse than none: it is the file people copy.
