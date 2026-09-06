#!/usr/bin/env python
"""Static checks for the tuner page. There is no build step, so nothing else catches these.

    conda run -n 4dre python web/tune/verify_page.py

Exit 0 = clear, 1 = at least one FAIL. Modelled on the site's own verify-site skill
(mingyang-song.github.io/.claude/skills/verify-site/), and each check below is here because
it caught a real defect -- not because it seemed prudent.

| check | the bug it caught |
| --- | --- |
| [hidden] vs author display | THE BIG ONE. `.busy-pill{display:flex}` outranks the UA's
|                            | `[hidden]{display:none}`, so `el.hidden = true` did nothing.
|                            | The busy pill never went away: a render that finished in
|                            | 4.11 s left a spinner frozen at "4.1s" for ever, and a
|                            | completed render was indistinguishable from a hang. #stats had
|                            | the same fault. Reported twice as "it gets stuck".
| JS -> DOM ids              | a stale getElementById throws at load and blanks the page --
|                            | the worst failure mode, invisible without a browser.
| JS syntax                  | same, one typo earlier in the file kills everything after it.
| CSS brace balance          | a dropped } silently kills every rule after it.
| motion vs reduced-motion   | the site's design contract requires every animation to be
|                            | switched off there; retrofitting is always forgotten.
| page-chrome trio           | Chrome and Safari disagree about the surfaces the page's own
|                            | background does not reach.
| root-absolute paths        | would 404 if this page ever moves under a project subpath.
| no server calls left       | the page is STATIC now. A surviving fetch('/render') parses
|                            | fine, passes every other check, and 404s on the deployed
|                            | site -- the tool loads and simply never paints.
| preview vs done converter  | the intermediate preview converted the WHOLE canvas per
|                            | frame against a fixed 180 ms cadence, so its cost grew with
|                            | area while its budget did not: at 12.2 Mpx one frame was
|                            | 48.8 MB and 212 ms, the previews took more of the worker than
|                            | the strokes did, and a render that had just started painting
|                            | showed "400/11844 - 128.5s". Nothing else would notice.
| module files parse         | the compute moved into engine.worker.js and the oilpaint/
|                            | JS package, which the inline-script syntax check cannot see.
|                            | A typo there is the same blank-page failure, one file out.
| index.html AT RUN TIME     | the one dynamic check, and it is here because everything
|                            | above it is static. `const s = spec()` was read one line
|                            | before its own declaration: valid syntax, ReferenceError at
|                            | run time, thrown inside an async click handler and so
|                            | swallowed as an unhandled rejection. The Render button did
|                            | nothing and said nothing, and every static check passed.
|                            | index_node.mjs shims the DOM and works the page.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PAGE = os.path.join(HERE, "index.html")
SPACK_NODE = ("/cluster/software/stacks/2024-06/spack/opt/spack/linux-ubuntu22.04-x86_64_v3/"
              "gcc-12.2.0/node-js-19.2.0-i6pplhcf7voeure6rh5oeg7lu7cmokt7/bin/node")
VOID = {"br", "img", "input", "meta", "link", "hr", "source", "area", "base", "col",
        "embed", "track", "wbr"}

FAILS = []


def check(ok, label, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (f"  {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def main():
    src = open(PAGE).read()
    css_raw = re.search(r"<style>(.*?)</style>", src, re.S).group(1)
    # `<script type="module">` since the compute moved into a worker -- a bare `<script>`
    # pattern silently matches nothing here, and every JS check below would vanish.
    # Strip CSS comments FIRST. Without this the checks match their own rationale: the
    # comment explaining the [hidden] trap contains the literal text
    # "[hidden]{display:none}", so deleting the real rule still passed. A check that is
    # green for the wrong reason is worse than no check, so this line is load-bearing.
    css = re.sub(r"/\*.*?\*/", "", css_raw, flags=re.S)
    js_m = re.search(r"<script[^>]*>(.+?)</script>", src, re.S)
    assert js_m, "no inline script found in the page"
    js = js_m.group(1)
    flat = css.replace(" ", "").replace("\n", "")

    print("hidden attribute is actually honoured")
    # The check that matters most: an author `display` beats the UA's [hidden] rule.
    has_global = "[hidden]{display:none" in flat
    check(has_global, "a global [hidden]{display:none} rule exists")
    for eid in re.findall(r'<[^>]*id="([^"]+)"[^>]*\shidden', src):
        m = (re.search(r'<[^>]*id="%s"[^>]*class="([^"]*)"' % eid, src)
             or re.search(r'<[^>]*class="([^"]*)"[^>]*id="%s"' % eid, src))
        sels = ["#" + eid] + (["." + c for c in m.group(1).split()] if m else [])
        clash = [s for s in sels
                 if re.search(re.escape(s) + r"\{[^}]*display:", css)
                 and not re.search(re.escape(s) + r"\{[^}]*display:none", css)]
        check(has_global or not clash, f"[hidden] works on #{eid}",
              f"author display via {clash}" if clash else "")

    print("javascript")
    node = shutil.which("node") or (SPACK_NODE if os.path.exists(SPACK_NODE) else None)
    if node:
        # .mjs, not .js: the page's script is a module, and so are the two files it pulls
        # in. `node --check` applies script rules to a .js file and would reject `import`.
        tmp = os.path.join(HERE, ".verify_tmp.mjs")
        open(tmp, "w").write(js)
        r = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
        os.remove(tmp)
        check(r.returncode == 0, "inline script parses", r.stderr.strip()[:200])
        # The compute lives out here now, so checking only the inline script would leave
        # the part that actually paints unverified.
        mods = ["engine.worker.js"] + [
            os.path.join("oilpaint", f) for f in sorted(os.listdir(os.path.join(HERE, "oilpaint")))
            if f.endswith(".js")]
        for name in mods:
            p = os.path.join(HERE, name)
            if not os.path.exists(p):
                check(False, f"{name} exists")
                continue
            r = subprocess.run([node, "--input-type=module", "--check"],
                               stdin=open(p), capture_output=True, text=True)
            check(r.returncode == 0, f"{name} parses", r.stderr.strip()[:200])
    else:
        print("  skip  syntax (no node)")
    # ...plus the ids the page CREATES for itself. A card tool (CARD_TOOLS) is built at
    # runtime, so its id is declared in that table rather than in the markup -- and it still
    # has to be declared somewhere this can see, or `$('#vtxBtnn')` goes unnoticed.
    ids = (set(re.findall(r'\bid="([^"]+)"', src))
           | set(re.findall(r"\bid:\s*'([A-Za-z0-9_-]+)'", src)))
    refs = (set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", src))
            | set(re.findall(r"getElementById\('([^']+)'\)", src)))
    check(not (refs - ids), "every id used in JS exists in the markup",
          f"missing {sorted(refs - ids)}" if refs - ids else f"{len(refs)} refs")

    print("css")
    check(css.count("{") == css.count("}"), "braces balanced",
          f"{css.count('{')} open / {css.count('}')} close")
    anims = set(re.findall(r"animation:\s*([a-zA-Z_-]+)", css)) - {"none"}
    rm = re.search(r"@media\(prefers-reduced-motion:reduce\)\{(.*?)\n\}", css, re.S)
    rm_body = rm.group(1).replace(" ", "") if rm else ""
    check(not anims or "animation:none" in rm_body,
          "animations are switched off under prefers-reduced-motion", f"declared {sorted(anims)}")
    check(all(k in src for k in ("theme-color", "color-scheme", "html{background")),
          "page-chrome trio present")

    print("static, no server")
    # The page used to POST to serve_tune.py. Every one of those calls had to go; one left
    # behind parses, passes every other check here, and 404s only on the deployed site.
    calls = re.findall(r"""fetch\(\s*['"](/[^'"]*)['"]""", js)
    check(not calls, "no calls to a server endpoint", str(calls))
    check("engine.worker.js" in js, "the page starts the compute worker")
    wjs = open(os.path.join(HERE, "engine.worker.js")).read()
    check("./schema.json" in wjs, "the worker fetches the generated schema")
    # The two sides of the init handshake, pinned. The worker used to post the whole
    # schema.json document under `schema`, so boot()'s `j.schema` was a non-iterable object
    # and `j.defaults` undefined; it threw on the first for..of and the page sat on
    # "starting the engine..." forever. Every other check here passed while it did.
    ready = re.search(r"postMessage\(\{[^)]*type:\s*'ready'[^)]*\)", wjs)
    sends = set(re.findall(r"(\w+):", ready.group(0))) if ready else set()
    check({"schema", "defaults"} <= sends,
          "the engine's ready message carries schema AND defaults", str(sorted(sends)))
    # `[^)]*` because boot gained a parameter (`restore`), and this check failing on a
    # signature change rather than on a real drift is a check that trains people to ignore it.
    body = re.search(r"async function boot\([^)]*\)\{(.*?)\n\}", js, re.S)
    reads = set(re.findall(r"\bj\.(\w+)", body.group(1))) if body else set()
    check(bool(reads) and reads <= sends,
          "boot() reads only fields the ready message sends", str(sorted(reads - sends)))

    # The intermediate preview must be SUBSAMPLED and the finished painting must not.
    # Swapping those two converters is the invisible defect this pins: the page still works
    # either way, it just gets slower with the square of the canvas -- at 12.2 Mpx a full
    # frame was 48.8 MB and 212 ms against a 180 ms cadence, so the previews were taking
    # more of the worker than the strokes were and a render that had barely started read as
    # one that had been crawling for two minutes.
    prev = re.search(r"type:\s*'preview'[^;]*?\}", wjs, re.S)
    prev_body = re.search(r"frame\(rgb, done, total, force = false\) \{(.*?)\n    \},",
                          wjs, re.S)
    check(bool(prev) and bool(prev_body), "the worker still has a preview frame path")
    if prev_body:
        check("toRgbaPreview" in prev_body.group(1) and "toRgba(" not in prev_body.group(1),
              "intermediate frames go through the subsampling converter")
        check("PREVIEW_BUDGET" in prev_body.group(1),
              "and are gated on their own measured cost, not only on a fixed cadence")
    done = re.search(r"type:\s*'done'", wjs)
    result = re.search(r"const pixels = (\w+)\(", wjs)
    check(bool(done) and bool(result) and result.group(1) == "toRgba",
          "the finished painting is sent whole", result.group(1) if result else "?")

    print("markup")
    doc = re.sub(r"<script[^>]*>.*?</script>|<style>.*?</style>|<!--.*?-->", "", src, flags=re.S)
    doc = re.sub(r"<!doctype[^>]*>", "", doc, flags=re.I)
    stack, bad = [], []
    for m in re.finditer(r"<(/?)([a-zA-Z0-9]+)([^>]*?)(/?)>", doc):
        close, name, _, self_close = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        if name in VOID or self_close:
            continue
        if not close:
            stack.append(name)
        elif not stack:
            bad.append(f"</{name}> with nothing open")
        else:
            opened = stack.pop()
            if opened != name:
                bad.append(f"</{name}> closes <{opened}>")
    check(not bad and not stack, "tags nest correctly", f"{bad} unclosed={stack}")
    abs_paths = re.findall(r'(?:src|href)="(/[^/][^"]*)"', src)
    check(not abs_paths, "no root-absolute paths", str(abs_paths))

    page_runtime(node)

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED")
        return 1
    print("all passed")
    return 0


def page_runtime(node):
    """RUN index.html's script and work the page. The only dynamic check in this file.

    Every other check here is static, and static is exactly what missed the bug that forced
    this kind of check: a `const` read one line before its own declaration -- valid SYNTAX,
    a ReferenceError at run time, thrown inside an async handler and swallowed as an
    unhandled rejection, so a button did nothing and said nothing while every parse passed.
    So the harness (`index_node.mjs`) shims the DOM and works the page: boot with and
    without a stored session, the automatic first render through a fake engine, and the
    tools that mark the picture.
    """
    print("\nindex.html at run time")
    if not node:
        print("  skip  (no node)")
        return
    sys.path.insert(0, ROOT)
    from oilpaint.flow import MAX_VORTICES, PRESETS  # noqa: E402
    from oilpaint.regions import LEGEND, MAX_REGIONS, REGION_PARAMS  # noqa: E402
    from oilpaint.schema import as_dict  # noqa: E402
    base = {"schema": as_dict(), "maxVortices": MAX_VORTICES,
            "flowKinds": {k: v["kind"] for k, v in PRESETS.items()},
            "maxRegions": MAX_REGIONS, "regionLegend": [list(c) for c in LEGEND],
            "regionParams": sorted(REGION_PARAMS)}
    # The stored setup carries a live hue band and a reference as well, so the panel is
    # seeded with every kind of control the schema has, not only the geometry.
    setup = json.dumps({"params": {"flow": "starry", "palette": "fauve",
                                   "hue_target": 140.0, "hue_range": 30.0,
                                   "hue_rotate": -40.0, "hue_boost": 0.25,
                                   "reference": "great-wave",
                                   "reference_strength": 0.8},
                        "vortices": [[0.5, 0.3], [0.2, 0.7]]})
    warm = dict(base, localStorage={"oilpaint.setup": setup},
                indexedDB={"image": {"dataUrl": "data:image/png;base64,AAAA",
                                     "name": "sunset.jpg"}})

    def run(job, label):
        with tempfile.TemporaryDirectory() as tmp:
            jp, op = os.path.join(tmp, "job.json"), os.path.join(tmp, "out.json")
            with open(jp, "w", encoding="utf-8") as fh:
                json.dump(job, fh)
            r = subprocess.run([node, os.path.join(HERE, "index_node.mjs"), jp, op],
                               capture_output=True, text=True)
            if r.returncode != 0 or not os.path.exists(op):
                check(False, f"the page script runs at all ({label})",
                      r.stderr.strip()[-400:])
                return None
            with open(op, encoding="utf-8") as fh:
                return json.load(fh)

    cold = run(base, "no stored session")
    hot = run(warm, "with a stored session")
    # A THIRD run against an engine whose schema predates the hue fields -- what a server
    # left running across the edit actually serves. Reported from the running tool as
    # "The band holds NaN degrees either side of NaN degrees", and the caption was the
    # mild half: `undefined !== 0` read as a live band, so every graded colour was NaN too.
    stale = run(dict(base, staleSchema=True), "an engine with a stale schema")
    if cold is None or hot is None or stale is None:
        return

    # THE SESSION, both halves: a stored setup seeds the panel and the stored image is
    # brought back and RENDERED, without anybody clicking anything. Adopting the parameters
    # and then asking for the photograph again is most of the way to not having adopted
    # anything -- the lesson the old two-page handoff taught at full price.
    check(hot["restoredImage"] is True,
          "a stored session brings the image back and shows the viewer")
    check("sunset" in (hot["sourceTag"] or ""),
          "with the restored file named on the source pane", repr(hot["sourceTag"]))
    check(any("flow=starry" in e and "v=0:2" in e for e in hot["renders"]),
          "and the first render carries the restored flow and placed centres",
          str(hot["renders"][:2]))
    check(cold["restoredImage"] is True and "sample" in (cold["sourceTag"] or ""),
          "while a fresh visit opens on the shipped sample", repr(cold["sourceTag"]))
    check(any("flow=none" in e for e in cold["renders"]),
          "rendered at engine defaults", str(cold["renders"][:1]))

    # THE REGION LAYERS, and this is the block that matters most on the page. A layer is
    # the only colour control here that is not generated from the schema, and the model --
    # "the panel edits the selection" -- has exactly one failure mode: a row that writes to
    # the wrong place. From outside that is indistinguishable from a row that works, so every
    # check below reads the value back off the RENDER MESSAGE the engine received, which is
    # what the picture would actually have been painted from.
    check(hot["rgnBarShown"] is True, "the Regions button opens its layers panel")
    check(hot["rgnBaseOnly"] == ["0"] and hot["rgnLayerIds"] == ["1", "0"],
          "the list starts as the Base alone and + Layer adds one above it",
          f"{hot['rgnBaseOnly']} -> {hot['rgnLayerIds']}")
    # A NEW LAYER IS SEEDED FROM THE PANEL, so it says exactly what the base says and
    # painting it changes nothing -- oilpaint/regions.py's equivalence invariant showing up
    # as a UI promise. Re-rendering to prove that costs a second and teaches the wrong thing
    # about which action changes the picture.
    check(hot["rgnFillInert"] is True,
          "filling a layer that still agrees with the panel does not re-render")
    check("Layer 1" in (hot["rgnEditingText"] or ""),
          "and the bar says which layer the Palette rows are now writing to",
          repr(hot["rgnEditingText"]))

    # THE CLAIM. With a layer selected, a Palette row writes to THAT LAYER and not to the
    # base -- both halves, because a row that wrote to both would pass either one alone.
    check("warm_cool=0.44" in (hot["rgnLayer1Warm"] or ""),
          "a Palette row with a layer selected writes to that layer",
          repr(hot["rgnLayer1Warm"][:40]))
    check(hot["rgnBaseWarmAfter1"] == 0,
          "...and leaves the base exactly where it was",
          f"base warm_cool = {hot['rgnBaseWarmAfter1']}")
    check("layered" in (hot["rgnRowClasses"] or []),
          "and the row is marked as pointed at a layer", str(hot["rgnRowClasses"]))

    # THE SAME CLAIM FOR THE FLOW HALF, driven rather than reasoned about. `flow` is
    # layer-aware only because the engine's `regionParams` says so, so a page that had kept
    # a list of its own would leave this row global and quietly re-comb the whole picture --
    # which, from outside, looks exactly like a row that works. It is also the other kind of
    # row: a <select>, so a different branch of the panel's builder.
    check("flow=waterlily" in (hot["rgnLayer1Flow"] or ""),
          "a Flow row with a layer selected writes to that layer",
          repr(hot["rgnLayer1Flow"][:60]))
    check(hot["rgnBaseFlowAfter"] == hot["rgnBaseFlowWas"] != "waterlily",
          "...and leaves the rest of the picture on the field it had",
          f"{hot['rgnBaseFlowWas']} -> {hot['rgnBaseFlowAfter']}")

    # THE SWIRL TOOL FOLLOWS THE SAME SELECTION. This is the fourth tool on that one rule,
    # and the only one where getting it wrong is silent: a centre placed into the base while
    # a layer is selected still swirls SOMETHING, so the picture changes and nothing looks
    # broken. So the event line counts per owner and the checks read the owner, not a total.
    check(hot["vtxTargetText"] and "Layer 1" in hot["vtxTargetText"],
          "the swirl bar names the layer the clicks are aimed at",
          repr(hot["vtxTargetText"]))
    check("v=0:2,1:1" in (hot["vtxAfterLayerPlace"] or ""),
          "a swirl placed with a layer selected joins THAT layer's set, not the picture's",
          repr(hot["vtxAfterLayerPlace"]))
    # ...and Clear takes back only that layer's, handing it back to the picture's swirls.
    check("v=0:2" in (hot["vtxAfterLayerClear"] or "")
          and "1:" not in (hot["vtxAfterLayerClear"] or "").split("v=")[-1].split(" ")[0],
          "and Clear removes only that layer's set, leaving the picture's alone",
          repr(hot["vtxAfterLayerClear"]))
    # THE GATE. A target whose flow has no swirls must not collect them, and must say so
    # rather than swallowing the click -- the defect a hidden Render button once had:
    # was reported for three times.
    check(hot["vtxOffBtnClasses"] and "off" in hot["vtxOffBtnClasses"],
          "a target that cannot hold swirls makes the button look unavailable",
          str(hot["vtxOffBtnClasses"]))
    check(hot["vtxOffRefused"] is True,
          "and a click on the picture then places nothing",
          repr(hot["vtxOffAfter"]))
    check("waterlily" in (hot["vtxOffHint"] or "") and "starry" in (hot["vtxOffHint"] or ""),
          "while the bar names the field that cannot and the one that can",
          repr((hot["vtxOffHint"] or "")[:120]))
    # THE REPORTED BUG. Availability used to be recomputed only while the bar was open, so a
    # layer set to `starry` with the bar shut left the button dimmed -- right when it was
    # last computed, and nothing recomputed it. Driven with the bar closed, both ways.
    check(hot["vtxBarClosed"] is True, "the swirl bar closes again")
    check("off" in (hot["vtxOffWhileClosed"] or []),
          "a field with no swirls dims the button even with the bar shut",
          str(hot["vtxOffWhileClosed"]))
    check("off" not in (hot["vtxLiveWhileClosed"] or []),
          "...and setting the layer back to starry undims it, still with the bar shut",
          str(hot["vtxLiveWhileClosed"]))

    # THE STATS STRIP. Off by default, because the numbers are for judging a change and the
    # page is for looking at a picture -- but a warning is the page refusing to fail in
    # silence, so it shows either way. Both halves, because either alone would pass a
    # version that got the other wrong.
    check(hot["statsHiddenByDefault"] is True,
          "the stats strip is hidden by default")
    check("strokes" in (hot["statsShownHtml"] or ""),
          "the stats toggle reveals the numbers", repr(hot["statsShownHtml"][:60]))
    check(hot["statsHiddenAgain"] is True, "...and hides them again")
    check(hot["statsWarnHidden"] is False and "ceiling" in (hot["statsWarnHtml"] or "")
          and "psnr" not in (hot["statsWarnHtml"] or ""),
          "while a WARNING shows with the toggle off, and only the warning",
          repr((hot["statsWarnHtml"] or "")[:120]))

    # RENDER FOLLOWS `auto`. With every change re-rendering there is nothing for the button
    # to do, so it is not there; with `auto` off it is the ONLY way to see a change, so it
    # cannot merely be dimmed. Both directions, because a page that hid it and kept it
    # hidden is a tuner that cannot render -- the worst version of this change.
    check(hot["renderHiddenOnAuto"] is True,
          "the Render button is absent while `auto` renders everything")
    check(hot["renderShownOffAuto"] is False,
          "and comes back the moment `auto` is turned off")
    check(hot["manualNoAutoRender"] is True,
          "with `auto` off a moved slider does NOT render on its own")
    check(hot["manualRendered"] is True, "...and the button is what renders it")
    check(hot["renderHiddenAgain"] is True, "turning `auto` back on takes it away again")
    check("layered" in (hot["rgnFlowRowClasses"] or []),
          "and that row is marked as pointed at a layer too",
          str(hot["rgnFlowRowClasses"]))

    check("warm_cool=-0.31" in (hot["rgnLayer2Warm"] or ""),
          "a second layer holds its own value", repr(hot["rgnLayer2Warm"][:40]))

    # MULTI-SELECT. Two layers that disagree read as mixed, and one write reaches both --
    # the behaviour every layer-based tool has, and the reason the dash exists at all.
    check("mixed" in (hot["rgnMixedClasses"] or []),
          "selecting two layers that disagree marks the row mixed",
          str(hot["rgnMixedClasses"]))
    check("Layer 1 + Layer 2" in (hot["rgnMultiEditing"] or ""),
          "and the bar names both", repr(hot["rgnMultiEditing"]))
    check(all("warm_cool=0.12" in e for e in (hot["rgnMultiBoth"] or [""])),
          "one write with both selected reaches both layers",
          str([e[:28] for e in hot["rgnMultiBoth"]]))
    check("mixed" not in (hot["rgnUnmixedClasses"] or []),
          "...and they stop being mixed, because they now agree",
          str(hot["rgnUnmixedClasses"]))

    # THE EYE. A hidden layer leaves the wire entirely, so its strokes fall back to the
    # base's grade -- and `params_for` returning None on an empty set is why hiding the last
    # one restores the base painting bit for bit rather than approximately.
    check("r=1/" in (hot["rgnHidden"] or "") and "r=1,2/" in (hot["rgnShown"] or ""),
          "hiding a layer takes it off the wire, and showing it puts it back",
          f"{hot['rgnHidden']} -> {hot['rgnShown']}")

    # BACK TO THE BASE. A page left pointed at a layer is the worst shape a control can
    # have: every slider still works and none of them does what the visitor expects.
    check(not (hot["rgnBaseClasses"] or []),
          "selecting the Base points the rows back at the whole picture",
          str(hot["rgnBaseClasses"]))
    check(abs(float(hot["rgnBaseWarmAfterBaseEdit"]) - 0.07) < 1e-9,
          "so a row then writes to the base again",
          f"base warm_cool = {hot['rgnBaseWarmAfterBaseEdit']}")
    check("warm_cool=0.12" in (hot["rgnLayer1AfterBaseEdit"] or ""),
          "...without disturbing what the layers hold",
          repr(hot["rgnLayer1AfterBaseEdit"][:40]))
    # A FILL THAT TAKES. Painting an inert layer over a live one still changes the picture,
    # because the live one loses those strokes -- so "did this fill change anything" is a
    # different question from "is the layer I am painting live". Answering it with the target
    # alone left a real edit unrendered, and this is the check that says so.
    check(hot["rgnStealRendered"] is True,
          "filling an inert layer over a live one still re-renders")
    check("1" not in (hot["rgnAfterDelete"] or []) and "0" in (hot["rgnAfterDelete"] or []),
          "and deleting a layer removes it from the list, leaving the Base",
          str(hot["rgnAfterDelete"]))
    # The harness itself must not fail in silence either: a throw in the drive above used to
    # end its process with exit 0, no output and no result file at all.
    check(not hot.get("driveFailed"), "nothing threw while the layers were driven",
          "; ".join(hot.get("errors", [])[:1])[:300])


    # THE CARDS. Palette had 13 rows and Flow 6, and most of those were trims measured to
    # move the picture a fraction as much as the preset above them (the table over `FINE`
    # in the page). A demoted row must land on a `<group> · fine` card under Advanced, at
    # the TOP of Advanced -- and demotion must lose nothing: every control the schema has
    # appears exactly once, or the CLI has a knob the page silently does not.
    front = {c["name"]: c["rows"] for c in cold["frontCards"]}
    adv = [c["name"] for c in cold["advCards"]]
    check(front.get("Palette") == ["palette", "palette_strength", "broken_color",
                                   "reference", "reference_strength"],
          "the front Palette card is the preset, its strength, broken colour and the "
          "reference", str(front.get("Palette")))
    check(front.get("Flow") == ["flow", "flow_strength"],
          "and the front Flow card is the preset and its strength", str(front.get("Flow")))
    # THE SWIRL TOOL LIVES ON THAT CARD, not in the toolbar: it is a property of the flow
    # field, so it belongs where the field is chosen -- and, more usefully, where its dimmed
    # state reads as "this field has no swirls in it". Read off the card the page BUILT, so
    # a table entry naming a control that no longer exists fails here rather than losing the
    # button silently.
    tools = {c["name"]: c.get("tools", []) for c in cold["frontCards"]}
    check(tools.get("Flow") == ["vtxBtn"],
          "and it carries the swirl-centre tool, which the toolbar no longer does",
          str(tools))
    check(all("vtxBtn" not in v for k, v in tools.items() if k != "Flow"),
          "...and only that card does", str(tools))
    check(adv[:2] == ["Palette · fine", "Flow · fine"],
          "the demoted trims head the Advanced fold under their own card names", str(adv[:3]))
    seen = [n for c in cold["frontCards"] + cold["advCards"] for n in c["rows"]]
    hidden = {"base", "base_cell_scale"}
    expect = sorted(r["name"] for r in base["schema"]["schema"] if r["name"] not in hidden)
    check(sorted(seen) == expect and len(seen) == len(set(seen)),
          "and every schema control not deliberately hidden is on exactly one card",
          str(sorted(set(expect) ^ set(seen))))

    # THE STALE-SCHEMA RUN. Two claims, and the second is what makes the first more than
    # cosmetic: nothing anywhere may read NaN, and the tool must go on working around the
    # hole -- a missing knob means the feature is off, never a painting full of NaN.
    blob = " ".join(str(stale.get(k) or "") for k in
                    ("bootStatus", "sourceTag", "statsShownHtml", "errText"))
    check("NaN" not in blob, "no NaN reaches the page when the schema is older than it",
          repr(blob[:90]))
    # The controls are simply ABSENT there -- the page has no second list of field names to
    # miss them against, and inventing one would be the duplicate control surface
    # schema.py exists to prevent. Absent and inert is the right way to degrade; what must
    # never happen is the tool breaking around the hole.
    check(bool(stale["renders"]) and not (stale["errText"] or ""),
          "and the painting still renders, with nothing reported as broken",
          repr(stale["renders"][:1]))
    check(not stale["errors"], "nothing throws on that path",
          "; ".join(stale["errors"]))

    # THE check. An empty list is the whole point: anything that escapes a handler is a
    # control that silently does nothing.
    check(not cold["errors"] and not hot["errors"],
          "nothing throws while the page is driven",
          "; ".join(cold["errors"] + hot["errors"]))
    check(hot["reportsErrors"] is True,
          "the page reports its own runtime errors rather than swallowing them")


if __name__ == "__main__":
    sys.exit(main())
