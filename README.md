# Oil Paint Tuner

Turn a photograph into an oil painting — in your browser, with the sliders in your hands.

![A photograph of sunflowers beside the same photograph rendered as an oil painting, the brush strokes following the curve of each petal](assets/readme-before-after.jpg)

Drop a picture in, move a slider, watch the painting change. Nothing is uploaded and
nothing is downloaded: the whole painter runs inside the page, on your machine, offline.
There is no account, no server, no upload limit, and no queue.

---

## Try it

The tuner is one HTML file and a folder of JavaScript. To run it locally:

```bash
python web/tune/serve_tune.py        # then open http://localhost:8137
```

That is the whole setup. You need Python with **numpy** and **Pillow** installed, and
nothing else — no build step, no `npm install`, no GPU.

The page opens on a sample photograph so there is something to play with immediately.
Drop your own in at any time, or use the **Clear** button to start over.

## What it actually does

It is not a filter, and there is no neural network in it.

The tool *looks* at your photograph and decides where a painter would spend their effort —
lots of small strokes where the detail is, a few big ones across a plain sky. Then it works
out which way the form runs at each of those places, and lays down one brush stroke there,
turned to follow it. Strokes go down coarse first and fine last, the way a painting is
actually built up, over an underpainting so no gap shows through.

Every mark is a real brush stroke, not a texture: it has a direction, a length, a colour
mixed from a palette, bristle streaks, a tapered end, and a thickness that catches the
light. You can move all of that.

Because nothing is optimised or learned, a change is instant to explain and instant to
undo. A slider does one thing, and the same settings always give you the same painting.

## The palette

The colour a painter would have *mixed*, rather than the one the camera recorded. Each
preset stands for a real set of tubes, so it changes what the painting is able to say:

![The same sunflower painting rendered four times under different palettes: the photograph's own colour, impressionist, zorn, and nocturne](assets/readme-palettes.jpg)

There are six palettes, and every one of them has trims under it — warmth, chroma, how far
the colour departs from the photograph — so a preset is a starting point rather than a
destination.

You can also point the painting at a **famous picture** instead: *Starry Night*, *The Great
Wave*, *Water Lilies*, *Sunflowers*, *The Scream*, or a Vermeer. That moves your
photograph's colours onto the masterpiece's, and it composes with the palette — the
reference sets the mood, the palette sets the tubes.

## The panel

Seven groups of controls. The ones you will reach for most are on the front cards; the fine
trims fold away under **Advanced** so the panel stays readable.

| group | what it changes |
| --- | --- |
| **Allocation** | How many strokes, how big they can get, and where they go. This is the single biggest lever on how abstract the painting looks. |
| **Palette** | The colour: which pigments, how far from the photograph, warmth, and the optional famous-painting reference. |
| **Flow** | Which way the marks run. Leave it off and they follow the photograph's own structure; turn it on and they follow a field you choose — swirling, rippling, or hatched. |
| **Geometry** | The shape of a stroke: how long and thin it gets, how far it may wander from where it was placed, and how strongly it commits to a direction. |
| **Irregularity** | The hand. Size variation, wobble, colour drift, bristle streaks, tapered ends — the things that stop it looking machine-made. |
| **Paint** | The underpainting, the edge hardness of a mark, and how thickly the paint covers. |
| **Impasto** | Thickness and light. The paint is built as a real height field and then lit, so you can rake a light across the picture and watch the ridges catch it. |

Turn on the **help** toggle in the toolbar and every control explains itself in place.

**Stats** shows the numbers behind the current render — stroke count, coverage — for when
you want to know *why* a change did what it did. It is off by default, because the page is
for looking at a picture. Warnings always show, whether stats are on or not.

## Three ways to point at part of the picture

Sliders change the whole painting. These three tools let you aim at one passage of it.

**Foveal map** — paint roughly over the part of the picture you care about, and the stroke
budget moves there. A photograph's own detail is not the same as what your eye cares about:
a gravel path scores higher than a face, every time. This is how you tell it otherwise.
The budget is *redistributed*, never increased, so the rest of the picture goes broader as
the marked part goes finer.

**Layers** — click to fill regions, then give each one its own palette and its own flow.
Van Gogh's sky over Monet's water, in one picture. Regions you do not paint keep whatever
the base is set to.

**Swirl centres** — when a swirling flow field is on, click to place the centres of the
swirls by hand instead of taking the default arrangement. Positions are stored as fractions
of the image, so they land in the same place whatever size you render at.

## Rendering at full size

The preview renders at about 1100 pixels across, so that dragging a slider feels immediate.
When you have the look you want, turn on **full res** and press **Render**: that paints at
your photograph's own resolution, which on a phone photo is seconds to a minute of work.
Then **Download** writes the PNG.

A big render tells you what it is doing rather than showing a spinner — it names the stage
it is in, and once it has been going for more than half a second it streams the painting
onto the canvas as it is made. The ground first, then the strokes going down a few hundred
at a time, then the light. Change your mind mid-render and it stops immediately rather than
making you wait it out.

Your image and your settings are remembered between visits, so closing the tab does not
lose your work.

## From the command line

The same painter, without the browser:

```bash
# one image in, one painting out
python scripts/paint.py photo.jpg painting.png --max-side 1400
```

Every control on the panel is also a flag:

```bash
python scripts/paint.py photo.jpg painting.png \
    --palette impressionist --palette-strength 0.8 --broken-color 0.3

python scripts/paint.py photo.jpg painting.png \
    --flow starry --flow-strength 0.85 --palette nocturne

python scripts/paint.py photo.jpg painting.png \
    --reference starry-night --reference-strength 0.75
```

The masks the page's tools produce work here too — `--foveal mask.png` and
`--regions mask.png` — so you can aim a look on the page and then batch it over a folder.
The page's **Copy setup** button puts the current settings on your clipboard.

You can also measure a colour reference off a painting you have the rights to:

```bash
python scripts/extract_reference.py painting.jpg --name my-reference
```

## Running and developing

```bash
python web/tune/serve_tune.py           # the tuner, live from source    -> :8137
python web/tune/build_static.py --serve # the deployable page, previewed -> :8138
```

`serve_tune.py` is the one to use while editing. The only thing it adds over a plain static
file server is `schema.json`, which is generated from
[`oilpaint/schema.py`](oilpaint/schema.py) so the control panel can never drift from the
parameters it is supposed to expose.

`build_static.py` assembles [`public/`](public/) — plain HTML, JS and CSS, nothing fetched
at runtime — which is what gets published.

### Layout

```
oilpaint/          the painter, in Python
  image.py         load/save, colour spaces, the fast area lookups
  detail.py        five ways of measuring "how much is going on here"
  quadtree.py      dividing the picture up, and hitting a stroke budget
  tensor.py        which way the form runs at each point
  strokes.py       turning cells into brush strokes
  render.py        laying the paint down, and lighting it
  palette.py       the pigment sets and the colour grade
  reference.py     matching a famous painting's colours
  flow.py          the artistic direction fields
  regions.py       aiming a look at one passage
  pipeline.py      the orchestration, and every parameter's default
  schema.py        the control panel, generated from the above
web/tune/          the browser side
  oilpaint/*.js    the same painter, ported to JavaScript file for file
  index.html       the tuner page
  engine.worker.js the worker that does the painting off the UI thread
scripts/           the command line, and two smaller tools
tests/             the suites that hold the two implementations together
```

### The one thing to know before changing anything

The painter exists **twice** — once in Python, once in JavaScript — because the browser
version has to run with no server behind it. Python is the source of truth. Four test
suites hold the two together, and they take seconds:

```bash
python tests/test_core.py             # the painter's own invariants
python tests/test_rng_parity.py       # the random numbers, bit for bit
python tests/test_raster_parity.py    # the brush, stroke for stroke
python tests/test_pipeline_parity.py  # every stage, and the real worker
python web/tune/verify_page.py        # the page, driven end to end
```

The parity suites need `node`. The result they protect is that the browser and the command
line produce **the same painting, byte for byte at 8 bits** — so a look you find on the
page is a look you can render at full size from a script.

There is no build step for the page, which means `verify_page.py` is the only thing that
catches a broken edit to it. Run it after touching `web/tune/index.html`.

## Built on

The technique is a descendant of a long line of work in non-photorealistic rendering. The
citations are reproduced in [`oilpaint/__init__.py`](oilpaint/__init__.py), beside the code
that implements each one.

| work | what this takes from it |
| --- | --- |
| Haeberli, *Paint By Numbers* (1990) | the founding idea: a painting as an ordered collection of strokes that sample the photograph |
| Litwinowicz, *Processing Images and Video for an Impressionist Effect* (1997) | turning strokes to follow the form, and perturbing them so they read as handmade |
| Hertzmann, *Painterly Rendering with Curved Brush Strokes of Multiple Sizes* (1998) | the closest ancestor: coarse strokes first, fine ones last, over an opaque first pass |
| Hertzmann, *Fast Paint Texture* (2002) | thick paint as a height field, lit |
| Hays & Essa, *Image and Video Based Painterly Animation* (2004) | smoothing the direction field instead of trusting it pixel by pixel |
| Kang, Lee & Chui, *Coherent Line Drawing* (2007) | a stronger way of reading direction out of an image |
| Samet, *The Quadtree and Related Hierarchical Data Structures* (1984) | the structure that decides where the strokes go |
| Zhang et al., *GaussianImage* (2024) | the optimised counterpart — deliberately not followed, because a perfectly fitted result is a photograph again |

---

© 2026 Mingyang Song. All rights reserved.
