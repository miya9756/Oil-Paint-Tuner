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

## Masks: embedded, or a bundle

Both forms work, they load through the same reader, and they are for different jobs.

**A bundle** is what the tuner's **Save project** writes and what you want while a mask is
still being worked on — three files that travel together, named after the project itself:

```
my-painting.oilpaint.json     the recipe
my-painting.regions.png       RGB through the legend; black is the base
my-painting.foveal.png        grey; BLACK MEANS SPEND STROKES HERE
```

```jsonc
"masks": {
  "regions": "my-painting.regions.png",   // resolved beside the .json
  "foveal":  "my-painting.foveal.png"
}
```

The names come from the project's own base name, so one folder holds several projects
without their masks colliding, and a bundle stays a bundle when the folder is renamed. The
point of it is that a mask is then an **ordinary PNG**: paint it in any image editor, over
the photograph, and the same project picks the change up. For a complicated boundary — a
figure against a busy background — an editor is a far better tool than a flood fill.

**Embedded** (`"data:image/png;base64,..."`) makes one self-contained file, which is what an
example committed to a repository has to be: nothing to lose track of, and it keeps the
setup out of Git LFS, since `.gitattributes` sends every `*.png` there while a `.json` stays
diffable on GitHub. It is also the one form an image editor cannot open.

Moving between them is one call, and it is how you start repainting a committed example:

```python
from oilpaint import project
doc = json.load(open("examples/mountain-valley.oilpaint.json"))
project.save_bundle("work/valley.oilpaint.json", doc)   # -> .json + the two .png files
```

`save_bundle` leaves an already-referenced mask alone, so calling it on a bundle rewrites
only the document.

Either way the conventions are the CLI's, so a mask is a mask wherever it came from:

| mask | format | convention |
| --- | --- | --- |
| `regions` | RGB PNG | black is the base; each cube corner (red, green, blue, yellow, magenta, cyan, white) is one passage — the legend in [`oilpaint/regions.py`](../oilpaint/regions.py) |
| `foveal` | grey PNG | **black means spend strokes here**, the same as `--foveal` |

A mask painted elsewhere goes in through the tuner's **Load mask** and **Load map** buttons,
or through `--regions` / `--foveal` on the command line — they take the same files. To open
a bundle in the tuner, select or drop **the `.json` and its `.png` files together**: a
browser is handed files, never a folder, so it has nothing to resolve a relative name
against. Open one without its masks and the page says which files it wanted.

One thing worth knowing about sizes. Masks leave the page at the **photograph's own pixel
size**, so they line up when you open them over it in an editor — but the tuner paints its
masks internally at 512px on the long side, and that is the resolution it previews at. A
mask you loaded from a file is written back out **unchanged** until you paint on it, so
detail finer than 512px survives a save; the moment you brush or fill, the canvas becomes
the mask. `scripts/paint.py` always reads the file at its full size.

## The files here

| file | what it shows |
| --- | --- |
| `sample.oilpaint.json` | **the project the tuner opens on**, over the sample photograph it ships with — so what a visitor meets on the deployed page is a painting they can reproduce here with one command. Three passages: a van Gogh sky (starry flow, fauve palette, *Starry Night*'s colours, three swirl centres placed by hand), a hatched foreground under *The Great Wave*, and the limestone between them left on the base grade — a waterlily weave under *Water Lilies*. Three references in one picture, and all of flow, palette and reference shown acting per passage and globally at once. Its mask was **painted by hand at the photograph's own 1400 × 1054**, so it lines up pixel for pixel; `project.save_bundle` explodes it back to an editable PNG. The README builds this file up one setting at a time, with a picture per step. |
| `mountain-valley.oilpaint.json` | three passages over one mountain valley — a van Gogh sky with four placed swirl centres, a Monet foreground of short woven dabs, and the limestone between them left on the base grade. Plus a foveal map that spends the budget on the valley mouth rather than on the fern texture in the corners. Both masks embedded, 24 KB. The one example here with a **focus map**, which the opening project has no need of. |

`mountain-valley`'s photograph is [`assets/0201.png`](../assets/0201.png) — **DIV2K 0201**, from the public
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
