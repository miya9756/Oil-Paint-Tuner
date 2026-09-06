#!/usr/bin/env python
"""Assemble the static tuner into a directory that can be served as-is.

    conda run -n 4dre python web/tune/build_static.py --dist public
    conda run -n 4dre python web/tune/build_static.py --serve      # build + preview

The deployed page has no server, no Python and no runtime download: `web/tune/oilpaint/`
is a JS port of the `oilpaint` package, held to it stage by stage by
`tests/test_pipeline_parity.py`. So "building" is copying the page, the worker and the JS
package into one tree -- plus the one thing that cannot be copied, `schema.json`.

**The schema is generated, not written.** The control panel is defined once, in
`oilpaint/schema.py`, next to the `PaintConfig` it asserts against on import. Emitting it
here means the deployed panel cannot drift from the dataclass, and it is why this build step
exists at all rather than the page being pure static files in git.

Modelled on 4d-relight's `web/demo/build_demo.py --dist public`, and for the same reason:
GitLab Pages publishes whatever ends up in `public/`, so the tree is assembled by a script
that can be run and checked locally rather than by hand-copying in CI.
"""

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import fields

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from oilpaint.pipeline import PaintConfig  # noqa: E402
from oilpaint.project import DEFAULT_PROJECT  # noqa: E402
from oilpaint.schema import as_dict  # noqa: E402

# Page assets, copied verbatim. package.json is NOT here: it exists only so `node` treats
# the package as ES modules for the parity tests, and the browser needs no such hint.
# One page: the tuner carries the living painting inline (the animator and live pages were
# folded into it -- see CLAUDE.md), so the whole tool is index.html plus its worker.
ASSETS = ["index.html", "engine.worker.js", "session.js"]
# Binary assets, kept apart from ASSETS because the import-resolution pass below opens every
# name in ASSETS as TEXT -- a .jpg in that list is a UnicodeDecodeError, not a broken import.
# `sample.jpg` is the image the page loads on its own so the tool is usable the moment it
# opens; it is excluded from Git LFS in .gitattributes, which explains why.
MEDIA = ["sample.jpg"]
PACKAGE = "oilpaint"  # web/tune/oilpaint/*.js -- every .js in it ships
# THE SETUP THE PAGE OPENS WITH, copied out of examples/ rather than kept as a second copy
# under web/tune/. It is the same file `scripts/paint.py --project` takes and the same one
# verify_page.py checks, so the painting a visitor meets on the deployed page is one they can
# reproduce on the command line -- which is most of the point of having a project format at
# all. Renamed on the way in: the page asks for a stable name, and WHICH example is the
# opening one is a decision for this file rather than a string buried in the page.
SAMPLE_PROJECT = os.path.join(ROOT, "examples", DEFAULT_PROJECT)
SAMPLE_PROJECT_AS = "sample-project.json"


def build(dist):
    if os.path.isdir(dist):
        shutil.rmtree(dist)
    os.makedirs(os.path.join(dist, PACKAGE))

    modules = []
    src_pkg = os.path.join(HERE, PACKAGE)
    for name in sorted(os.listdir(src_pkg)):
        if not name.endswith(".js"):
            continue
        shutil.copy2(os.path.join(src_pkg, name), os.path.join(dist, PACKAGE, name))
        modules.append(name)

    for name in ASSETS + MEDIA:
        shutil.copy2(os.path.join(HERE, name), os.path.join(dist, name))
    shutil.copy2(SAMPLE_PROJECT, os.path.join(dist, SAMPLE_PROJECT_AS))

    with open(os.path.join(dist, "schema.json"), "w", encoding="utf-8") as fh:
        json.dump(as_dict(), fh, indent=1)

    return modules


def verify(dist, modules):
    """Fail the build rather than publish a tree that cannot boot.

    Each check is a way the page dies with a blank screen and a console message no visitor
    will read: an unresolvable import, a control panel that disagrees with the dataclass, or
    a root-absolute URL that 404s the moment Pages puts the site under a project subpath.
    """
    fails = []

    def check(ok, label, detail=""):
        print(("  ok   " if ok else "  FAIL ") + label + (f"  {detail}" if detail else ""))
        if not ok:
            fails.append(label)

    for name in ASSETS + MEDIA + ["schema.json", SAMPLE_PROJECT_AS]:
        p = os.path.join(dist, name)
        check(os.path.exists(p) and os.path.getsize(p) > 0, f"{name} present and non-empty")
    check(len(modules) >= 8, f"the JS package shipped ({len(modules)} modules)",
          ", ".join(modules))

    # Every relative import in every shipped file must resolve to a file that also shipped.
    # A static import that 404s takes the whole worker down, and the page just never paints.
    shipped = set(modules)
    for name in ASSETS + [os.path.join(PACKAGE, m) for m in modules]:
        src = open(os.path.join(dist, name)).read()
        base = os.path.dirname(name)
        for spec in re.findall(r"""from\s+['"](\.[^'"]+)['"]""", src):
            target = os.path.normpath(os.path.join(base, spec))
            ok = os.path.exists(os.path.join(dist, target))
            check(ok, f"{name} -> {spec} resolves", "" if ok else f"missing {target}")
        void = shipped  # noqa: F841

    # The JS pipeline restates PaintConfig's defaults, which is the one duplication the port
    # could not avoid -- the browser has to know them without importing Python. This is what
    # stops the two silently disagreeing about what 'default' means.
    js = open(os.path.join(dist, PACKAGE, "pipeline.js")).read()
    block = re.search(r"export const DEFAULTS = \{(.*?)\n\};", js, re.S)
    check(block is not None, "pipeline.js exposes a DEFAULTS block")
    if block:
        got = dict(re.findall(r"(\w+):\s*([^,\n]+?)\s*,", block.group(1)))
        want = {f.name: getattr(PaintConfig(), f.name) for f in fields(PaintConfig)}
        bad = []
        for k, v in want.items():
            if k not in got:
                bad.append(f"{k} missing")
                continue
            lit = got[k].strip().strip("'\"")
            if isinstance(v, bool):
                same = lit == ("true" if v else "false")
            elif v is None:
                same = lit == "null"
            elif isinstance(v, (int, float)):
                same = abs(float(lit) - float(v)) <= 1e-12 * max(1.0, abs(float(v)))
            else:
                same = lit == str(v)
            if not same:
                bad.append(f"{k}: js {got[k]!r} vs PaintConfig {v!r}")
        check(not bad, "pipeline.js DEFAULTS match PaintConfig", "; ".join(bad))

    schema = json.load(open(os.path.join(dist, "schema.json"), encoding="utf-8"))
    check(len(schema["schema"]) >= 20 and len(schema["defaults"]) >= 20,
          f"schema.json carries the full panel ({len(schema['schema'])} controls)")

    page = open(os.path.join(dist, "index.html")).read()
    check("./engine.worker.js" in page, "the page starts the worker by relative path")
    worker = open(os.path.join(dist, "engine.worker.js")).read()
    check("./schema.json" in worker, "the worker fetches the generated schema")
    check("pyodide" not in page.lower() and "pyodide" not in worker.lower(),
          "no Pyodide left in the deployed page")
    abs_paths = re.findall(r'(?:src|href)="(/[^/][^"]*)"', page)
    check(not abs_paths, "no root-absolute paths in the page", str(abs_paths))

    # THE WAY BACK TO THE SOURCE. The deployed page is the whole tool, so a visitor who wants
    # to know what it is doing has nowhere to go from it unless the page says. Matched as a
    # SHAPE rather than as the literal URL: pinning the address here would put the same
    # string in two files and make renaming the repository a silent half-rename.
    back = re.search(r'href="https://github\.com/[^/"]+/[^/"]+/?"', page)
    check(bool(back), "the published page links back to its source",
          back.group(0) if back else "no github link in the page")

    # .gitattributes is deliberately broad (see the sibling-repos skill), so a runner that
    # clones without git-lfs would copy 130-byte pointer stubs into the deploy tree and
    # publish a page whose modules are text files describing themselves.
    pointers = []
    for dp, _, fs in os.walk(dist):
        for f in fs:
            p = os.path.join(dp, f)
            with open(p, "rb") as fh:
                if fh.read(42).startswith(b"version https://git-lfs"):
                    pointers.append(os.path.relpath(p, dist))
    check(not pointers, "no published file is an unsmudged Git LFS pointer", str(pointers))

    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(dist) for f in fs)
    print(f"\n  {len(modules)} JS modules + {len(ASSETS)} assets + {len(MEDIA)} image"
          f" + schema.json + the opening project, {total / 1024:.0f} KB")
    print("  (nothing is fetched at runtime -- no CDN, no WASM, no Python)")
    return fails


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dist", default=os.path.join(ROOT, "public"),
                    help="output directory (GitLab Pages requires 'public')")
    ap.add_argument("--serve", action="store_true", help="serve the built tree and stop")
    ap.add_argument("--port", type=int, default=8138)
    a = ap.parse_args()

    dist = os.path.abspath(a.dist)
    print(f"building {dist}")
    modules = build(dist)
    fails = verify(dist, modules)
    if fails:
        print(f"\n{len(fails)} FAILED")
        return 1
    print("\nbuild ok")

    if a.serve:
        import functools
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

        class NoStore(SimpleHTTPRequestHandler):
            """SimpleHTTPRequestHandler, minus the browser cache.

            The bare handler sends no Cache-Control at all, so a browser falls back to
            HEURISTIC freshness and may reuse a module WITHOUT revalidating. That is
            harmless for one self-contained file and poisonous for an ES module graph,
            because the files are fetched independently and can therefore come from
            different builds: a fresh `pipeline.js` calling into a cached `strokes.js`
            fails as `strokes.coveringKappa is not a function` -- a missing-export error
            that points at source which, on disk, is perfectly correct. That cost a real
            debugging round.

            `serve_tune.py` has always sent `no-store`; this path simply did not, so the
            two preview servers disagreed about the one thing a preview server must get
            right. Nothing here is a deploy path -- GitLab Pages serves `public/` itself
            -- so there is no caching worth keeping.
            """

            def end_headers(self):
                self.send_header("Cache-Control", "no-store, must-revalidate")
                super().end_headers()

        handler = functools.partial(NoStore, directory=dist)
        print(f"\nstatic tuner  ->  http://127.0.0.1:{a.port}/   (ctrl-c to stop)")
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), handler)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
        finally:
            srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
