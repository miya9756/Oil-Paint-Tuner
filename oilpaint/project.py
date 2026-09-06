"""One painting, as one file.

A finished setup in this tool is FIVE things, and until now they lived in five places: the
`PaintConfig` fields (forty flags, or the tuner's panel), the per-layer overrides (`--region
1:palette=...`), the swirl centres (`--vortices`), the region mask (a PNG on disk) and the
foveal map (another PNG). Reproducing somebody's painting meant collecting all five by hand,
and posting one meant posting a command line with a paragraph of caveats. This is the
document that holds them together.

WHAT IT DELIBERATELY DOES NOT HOLD is the photograph. A project is a RECIPE, not a copy of
the ingredients: a full-resolution phone photo is 5-8 MB, and base64 in a JSON file is 8-11.
That is not a thing to put in a repository beside the code, and it is not a thing to paste
into an issue. The image stays the first argument to `scripts/paint.py`, and the project
records only its name so a file found later can say what it was made for. The consequence
worth stating: the same project over a different photograph is a perfectly sensible thing to
do, and is most of why the format is worth having.

THE MASKS ARE EITHER EMBEDDED OR REFERENCED, and both are first-class. A `data:` URL makes
the file self-contained, which is what an example committed to a repository has to be; a
plain relative path (`"regions": "sky-mask.png"`) makes the mask an ordinary PNG a person
can open in any editor, paint in, and save back -- which is the whole of "make a mask
somewhere else and import it". `load` resolves a path against the project file's own
directory, so a project and its masks move as a folder.

`to_dict` writes the embedded form and `save_bundle` writes the referenced one, from the
same document: three files that travel together, named after the project itself.

    sky.oilpaint.json      the recipe
    sky.regions.png        RGB through regions.LEGEND, black is the base
    sky.foveal.png         grey, BLACK MEANS SPEND STROKES HERE

Both forms load through the same `load`, and the tuner writes and reads both, so which one
a setup is in is a choice about how you want to WORK on it rather than a fork in the format.

ONE CONVENTION PER MASK, and it is the CLI's, not the browser's:

  * the REGION mask is an RGB PNG read through `regions.LEGEND` -- black is the base and
    each cube corner is a passage. Exactly what `--regions` takes and what the tuner's
    `Save mask` writes, so a mask is a mask wherever it came from.
  * the FOVEAL map is a grayscale PNG where BLACK MEANS SPEND STROKES HERE. That is
    `--foveal`'s convention and it is the one the format follows, even though the tuner
    holds the same map the other way up internally (its canvas alpha is the weight, because
    the alpha both shows the dab and records it). The page inverts on export. Two
    conventions for one image is how a map ends up applied backwards by whichever side
    happened to be written second, so there is one, and it is the one already written down
    in `scripts/paint.py`.

PARTIAL IS VALID. `dump` writes every `PaintConfig` field, because a project's job is to be
a complete and exact record. `load` requires none of them: a hand-written example may name
three fields and mean "these, and the defaults for the rest". So the format is comfortable
to write by hand and exact when written by the tool, which are both things it has to be.
"""

import base64
import io
import json
import os
from dataclasses import fields

from .pipeline import PaintConfig
from .regions import MAX_REGIONS, REGION_PARAMS

FORMAT = "oilpaint-project"
VERSION = 1

# The example the tuner OPENS ON, named once. It is copied into the deploy tree by
# build_static.py, synthesised by serve_tune.py and handed to the harness by
# verify_page.py -- three readers of one file, which is three chances for the deployed
# page to open on something the tests never looked at.
DEFAULT_PROJECT = "sample.oilpaint.json"

# A field whose default is None is OPTIONAL and float when set -- `tau` is the only one,
# and it means "search for it unless told". `type(None)` would make it unwritable, so this
# mirrors the same special case `scripts/paint.py` makes when it builds the flag, and for
# the same reason. Kept as a rule about None rather than a name, so a second such field
# needs no edit here.
_FIELD_TYPES = {f.name: (float if f.default is None else type(f.default))
                for f in fields(PaintConfig)}
_OPTIONAL = {f.name for f in fields(PaintConfig) if f.default is None}


class ProjectError(ValueError):
    """A project file that cannot be read, with a sentence saying why.

    Its own type because `scripts/paint.py` turns it into `ap.error` -- a usage message
    rather than a traceback. A bad project file is a typo, not a crash.
    """


def _coerce(name, value, where):
    """One JSON value -> the type `PaintConfig` declares for that field."""
    # `null` round-trips as the field's own "unset". `to_dict` writes every field, so an
    # optional one that was never set is written as null and has to come back as None
    # rather than as 0.0 -- which is not the same painting: `tau` at 0.0 is a threshold, and
    # `tau` unset is a search.
    if value is None:
        if name in _OPTIONAL:
            return None
        raise ProjectError(f"{where}: {name!r} cannot be null")
    kind = _FIELD_TYPES[name]
    if kind is bool:
        # JSON has real booleans, but a hand-written file may say "true" or 1, and refusing
        # those buys nothing.
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if kind is str:
        return str(value)
    try:
        return kind(value)
    except (TypeError, ValueError):
        raise ProjectError(f"{where}: {value!r} is not a {kind.__name__} for {name!r}")


def _points(raw, where):
    """`[[x, y], ...]` -> `[(x, y), ...]`, checked."""
    out = []
    for q in raw or []:
        if not isinstance(q, (list, tuple)) or len(q) != 2:
            raise ProjectError(f"{where}: {q!r} is not an [x, y] pair")
        try:
            out.append((float(q[0]), float(q[1])))
        except (TypeError, ValueError):
            raise ProjectError(f"{where}: {q!r} is not a pair of numbers")
    return out


def _read_mask(value, base_dir, mode, where):
    """A `data:` URL or a relative path -> a PIL image in `mode`, or None.

    Both forms exist for different jobs -- see the module docstring -- and this is the one
    place that stops the rest of the code caring which it was given.
    """
    if not value:
        return None
    from PIL import Image
    if isinstance(value, str) and value.startswith("data:"):
        _, _, b64 = value.partition(",")
        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception:
            raise ProjectError(f"{where}: the embedded image is not valid base64")
        try:
            return Image.open(io.BytesIO(raw)).convert(mode)
        except Exception as e:
            raise ProjectError(f"{where}: the embedded image will not decode ({e})")
    path = value if os.path.isabs(value) else os.path.join(base_dir or "", value)
    if not os.path.exists(path):
        raise ProjectError(f"{where}: no such mask file {value!r} (looked in {base_dir!r})")
    try:
        return Image.open(path).convert(mode)
    except Exception as e:
        raise ProjectError(f"{where}: {value!r} will not open ({e})")


class Project(object):
    """A loaded project. Masks come back at their OWN size, deliberately.

    Resampling belongs to the caller because the caller is the one that knows what it is
    painting: `scripts/paint.py` has already applied `--max-side` / `--min-side` by then,
    and the two masks do not even resample the same way -- NEAREST for the region labels,
    because interpolating between region 2 and region 4 invents a region 3 along every
    boundary in the picture, and BILINEAR for the foveal map, which is a smooth weight
    field. Doing it here would mean guessing the size and picking one filter for both.
    """

    def __init__(self, params, overrides, vortices, region_mask, foveal, source, note):
        self.params = params            # {field: value}, only fields the file named
        self.overrides = overrides      # {region id: {field: value}}
        self.vortices = vortices        # None, [(x, y)...], or {region id: [(x, y)...]}
        self.region_mask = region_mask  # PIL RGB image, or None
        self.foveal = foveal            # PIL L image, BLACK = spend strokes here, or None
        self.source = source            # the photograph's name, as a note. Never the pixels.
        self.note = note                # free text, for an example file to explain itself

    def config(self, over=None):
        """A `PaintConfig` with this project's fields, then `over` on top.

        `over` is how the CLI keeps flags winning over the file: a project is a starting
        point, and `--project x.json --target-n 9000` has to mean what it looks like.
        """
        vals = dict(self.params)
        vals.update({k: v for k, v in (over or {}).items() if v is not None})
        return PaintConfig(**vals)


def load(path):
    """Read a project file. Raises `ProjectError` with a sentence a user can act on."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except OSError as e:
        raise ProjectError(f"cannot open {path!r}: {e}")
    except ValueError as e:
        raise ProjectError(f"{path!r} is not valid JSON: {e}")
    return from_dict(doc, base_dir=os.path.dirname(os.path.abspath(path)))


def from_dict(doc, base_dir=""):
    """The parser proper, split out so the page's own output can be tested without a file."""
    if not isinstance(doc, dict):
        raise ProjectError("a project must be a JSON object")
    if doc.get("format") != FORMAT:
        raise ProjectError(f"not an {FORMAT} file (found format={doc.get('format')!r})")
    # A LOUD FAILURE ON A NEWER FILE, and a silent pass on an older one. Reading a version
    # this code has never heard of and painting something plausible anyway is the worst
    # outcome available: the file asked for something and got a different picture with no
    # warning. Older versions stay readable by construction -- every field below is
    # optional -- which is what makes the version number a ceiling rather than a match.
    ver = doc.get("version", VERSION)
    if not isinstance(ver, int) or ver > VERSION:
        raise ProjectError(
            f"this file is version {ver!r} and this build reads up to {VERSION}; "
            "update the tool, or edit the version down if you know the fields are the same")

    params = {}
    unknown = []
    for k, v in (doc.get("params") or {}).items():
        if k not in _FIELD_TYPES:
            unknown.append(k)
            continue
        params[k] = _coerce(k, v, "params")
    # Reported, not raised. A field dropped from PaintConfig should not make every project
    # written before it went away unreadable -- but a silent drop is how a painting quietly
    # stops matching the file that describes it.
    if unknown:
        import sys
        print(f"note: project has {len(unknown)} field(s) this build does not know, "
              f"ignored: {', '.join(sorted(unknown)[:6])}", file=sys.stderr)

    overrides = {}
    for k, ov in (doc.get("regions") or {}).items():
        try:
            rid = int(k)
        except (TypeError, ValueError):
            raise ProjectError(f"regions: {k!r} is not a region id")
        if not 0 <= rid < MAX_REGIONS:
            raise ProjectError(f"regions: id {rid} outside 0..{MAX_REGIONS - 1}")
        if not isinstance(ov, dict):
            raise ProjectError(f"regions: {k!r} must map field names to values")
        out = {}
        for f, v in ov.items():
            f = f.replace("-", "_")
            # The SAME refusal the CLI makes, and for the same reason: a field outside
            # REGION_PARAMS would not raise in the engine, it would repaint one passage
            # from a quadtree the rest of the picture does not share.
            if f not in REGION_PARAMS:
                raise ProjectError(
                    f"regions[{k}]: {f!r} is not a region colour or flow field; "
                    f"one of {sorted(REGION_PARAMS)}")
            out[f] = _coerce(f, v, f"regions[{k}]")
        overrides[rid] = out

    raw_v = doc.get("vortices")
    vortices = None
    if isinstance(raw_v, dict):
        vortices = {}
        for k, pts in raw_v.items():
            try:
                rid = int(k)
            except (TypeError, ValueError):
                raise ProjectError(f"vortices: {k!r} is not a region id")
            if not 0 <= rid < MAX_REGIONS:
                raise ProjectError(f"vortices: id {rid} outside 0..{MAX_REGIONS - 1}")
            vortices[rid] = _points(pts, f"vortices[{k}]")
    elif raw_v:
        vortices = _points(raw_v, "vortices")

    masks = doc.get("masks") or {}
    region_mask = _read_mask(masks.get("regions"), base_dir, "RGB", "masks.regions")
    foveal = _read_mask(masks.get("foveal"), base_dir, "L", "masks.foveal")
    src = doc.get("source") or {}

    return Project(params, overrides, vortices, region_mask, foveal,
                   src.get("name") if isinstance(src, dict) else None,
                   doc.get("note"))


def to_dict(cfg, overrides=None, vortices=None, region_png=None, foveal_png=None,
            source=None, note=None):
    """Build the document. `*_png` are raw PNG bytes, embedded as `data:` URLs.

    Every `PaintConfig` field is written, not only the ones that differ from the defaults.
    A project's job is to be an exact record, and "the rest were default" is only true
    until the defaults move -- at which point a file that leant on them silently describes
    a different painting. `load` still accepts a partial file, so writing one by hand
    stays easy; that is a property of the reader, not a reason to thin out the writer.
    """
    doc = {
        "format": FORMAT,
        "version": VERSION,
        "params": {f.name: getattr(cfg, f.name) for f in fields(PaintConfig)},
    }
    if note:
        doc["note"] = note
    if source:
        doc["source"] = {"name": source}
    if overrides:
        doc["regions"] = {str(k): dict(v) for k, v in sorted(overrides.items())}
    if vortices:
        doc["vortices"] = ({str(k): [list(p) for p in v]
                            for k, v in sorted(vortices.items())}
                           if isinstance(vortices, dict)
                           else [list(p) for p in vortices])
    masks = {}
    if region_png:
        masks["regions"] = "data:image/png;base64," + base64.b64encode(region_png).decode()
    if foveal_png:
        masks["foveal"] = "data:image/png;base64," + base64.b64encode(foveal_png).decode()
    if masks:
        doc["masks"] = masks
    return doc


def save(path, doc):
    """Write a document, with a trailing newline so it is a well-formed text file."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=False)
        fh.write("\n")


# The sidecar names a BUNDLE uses, derived from the project's own base name so that one
# folder can hold several projects without their masks colliding -- `sky.oilpaint.json`
# takes `sky.regions.png`, not a `regions.png` the next project would overwrite.
#
# This rule exists TWICE, here and in the tuner (`maskNames` in index.html), because a
# browser cannot import this module. That is the drift this repo spends its test budget on,
# so `verify_page.py` reads the page's chosen names and compares them against this function
# rather than against a string it also had to type.
MASK_SUFFIX = {"regions": ".regions.png", "foveal": ".foveal.png"}


def mask_names(project_path):
    """`a/b/sky.oilpaint.json` -> `{"regions": "sky.regions.png", ...}`. Basenames only.

    Basenames rather than paths because this is what goes INTO the document, and a
    project's masks live beside it -- `load` resolves them against the project's own
    directory, so a bundle stays a bundle when the folder is moved or renamed.
    """
    base = os.path.basename(project_path)
    for ext in (".oilpaint.json", ".json"):
        if base.lower().endswith(ext):
            base = base[:-len(ext)]
            break
    base = base or "painting"
    return {k: base + suffix for k, suffix in MASK_SUFFIX.items()}


def embed_masks(doc, base_dir=""):
    """The inverse of `save_bundle`: a document whose masks are files -> a self-contained one.

    Needed because what a PAGE fetches has to be a single URL -- the tuner's opening project
    is one request, with no folder to go looking in -- and because a mask committed beside
    code is a mask in Git LFS, which `.gitattributes` sends every `*.png` to and which a CI
    checkout does not smudge. A mask already embedded is left alone, so this is idempotent
    and safe on a document of either form. The caller's document is not mutated.
    """
    doc = dict(doc)
    masks = dict(doc.get("masks") or {})
    for key in ("regions", "foveal"):
        value = masks.get(key)
        if not value or (isinstance(value, str) and value.startswith("data:")):
            continue
        path = value if os.path.isabs(value) else os.path.join(base_dir or "", value)
        if not os.path.exists(path):
            raise ProjectError(
                f"masks.{key}: no such mask file {value!r} (looked in {base_dir!r})")
        with open(path, "rb") as fh:
            masks[key] = "data:image/png;base64," + base64.b64encode(fh.read()).decode()
    if masks:
        doc["masks"] = masks
    return doc


def save_bundle(path, doc):
    """Write a project AND its masks as separate files. Returns the paths written.

    THE POINT OF THE BUNDLE is that a mask stays an ordinary PNG you can open in an image
    editor, paint on, and save back without going through the tool at all. An embedded
    `data:` URL cannot do that: it is the right form for an example committed to a
    repository, and the wrong one for a mask still being worked on.

    Any mask already given as a path is left exactly as it is, so calling this on a bundle
    is a no-op for the masks and rewrites only the document. Any mask given as a `data:`
    URL is written out beside the project and the reference replaced -- which makes this
    the way to EXPLODE an embedded project into an editable one:

        doc = json.load(open("examples/mountain-valley.oilpaint.json"))
        project.save_bundle("work/valley.oilpaint.json", doc)

    The caller's document is not mutated; `load` reads what this writes.
    """
    doc = dict(doc)
    masks = dict(doc.get("masks") or {})
    names = mask_names(path)
    where = os.path.dirname(os.path.abspath(path))
    written = []
    for key in ("regions", "foveal"):
        value = masks.get(key)
        if not value:
            continue
        if not (isinstance(value, str) and value.startswith("data:")):
            continue                    # already a sidecar; leave the reference alone
        _, _, b64 = value.partition(",")
        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception:
            raise ProjectError(f"masks.{key}: the embedded image is not valid base64")
        out = os.path.join(where, names[key])
        with open(out, "wb") as fh:
            fh.write(raw)
        masks[key] = names[key]
        written.append(out)
    if masks:
        doc["masks"] = masks
    save(path, doc)
    return [os.path.abspath(path)] + written
