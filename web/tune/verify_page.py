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
| one armed tool at a time   | the segmented TOOL control asserts that the four picture
|                            | tools are exclusive. Wiring the assertion up found that only
|                            | rgnMode released the others: arming the focus map over an
|                            | open Layers tool left two live click-to-paint canvases on
|                            | one surface, the topmost ate every click, and the tool whose
|                            | button was lit did nothing.
| a row says what it is      | every control was headed by its Python identifier, with the
|                            | prose that would rescue it hidden behind the help toggle. The
|                            | label comes from schema.py now and the identifier stays under
|                            | it -- it is what paint.py takes. Two ways to get this wrong:
|                            | a label that goes missing, and a lead line that only restates
|                            | the label ("Stroke budget. A CEILING, not a quota...").
| erase is a control         | erasing a passage was "select the Base, then click", which
|                            | loaded one selection with two meanings and said so only in
|                            | prose at the end of a strip.
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
    # `wide` puts the HOT run in the app shell and leaves the cold one stacked, so both
    # layouts are driven by the two processes that already run. The shell is where the new
    # arithmetic lives -- the picture sized from the stage's own box rather than from 62vh
    # of the viewport -- and it would otherwise never execute here at all.
    warm = dict(base, wide=True, localStorage={"oilpaint.setup": setup},
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
    # THE LOOKS STRIP. Eight paintings of the visitor's own photograph, one per named look,
    # in place of three dropdowns of words. Two failures are possible here and neither is
    # visible from outside the wire.
    # Three, not eight: a thumbnail is an honest ~0.6 s painting, so the strip is the one
    # feature whose price is linear in its length. `photo` is the off switch rather than a
    # look -- without it, leaving a look means setting three rows to `none` by hand.
    check(hot.get("looksBuilt") == ["photo", "vangogh", "monet"],
          "the looks strip offers every look, in order", str(hot.get("looksBuilt")))
    check(hot.get("looksHidden") is False,
          "and it is on screen once there is an image to paint",
          str(hot.get("looksHidden")))
    check("flow=starry" in (hot.get("lookVanGogh") or ""),
          "a look reaches the engine as a whole bundle, flow included",
          str(hot.get("lookVanGogh")))
    # THE ONE LOOK_OFF EXISTS FOR. `zorn` names a palette and nothing else, so a look that
    # set only its own fields would leave van Gogh's `starry` combing the paint underneath
    # it -- a Zorn palette in swirls, and neither look what it claimed. Every look sets
    # every field, and this is the check that says so rather than the comment.
    check("flow=none" in (hot.get("lookZorn") or ""),
          "and switching looks clears the one before it rather than layering on it",
          str(hot.get("lookZorn")))
    if hot.get("lookSelBefore") is not None:
        check(hot.get("lookSelBefore") is True and hot.get("lookSelAfter") is True,
              "a look applied over a selected layer moves the selection to the base",
              f"layer selected {hot.get('lookSelBefore')} -> base selected "
              f"{hot.get('lookSelAfter')}")
        # ...and it landed on the BASE, not on the layer it was clicked over. `base ...` is
        # the wire line the harness writes from `msg.params`, so Monet's own flow appearing
        # there is the proof: a look that had followed the selection would have put it in
        # `regionOverrides` and left the base saying whatever it said before.
        check("flow=waterlily" in (hot.get("lookBaseWire") or ""),
              "and its values go to the base, which is where the panel now points",
              str(hot.get("lookBaseWire")))

    # UNDO. Every mark-making tool wrote into a canvas whose only way back was `Clear`, so
    # the price of a misjudged stroke was the whole map and the price of a misjudged
    # `Delete all` was every layer. What is checked here is the half that can fail quietly:
    # a mask restored WITHOUT its layer list is a picture full of passages no panel row can
    # reach, and from outside that looks exactly like an undo that worked.
    check(hot.get("undoIdle") is True,
          "undo is dimmed until there is something to undo", str(hot.get("undoIdle")))
    check(hot.get("undoArmed") is True,
          "and arms itself the moment an edit is made", str(hot.get("undoArmed")))
    check(hot.get("undoLayersWiped") == ["0"]
          and hot.get("undoLayersBack") == hot.get("undoLayersBefore")
          and len(hot.get("undoLayersBefore") or []) > 1,
          "Delete all can be taken back, layer list included",
          f"{hot.get('undoLayersBefore')} -> {hot.get('undoLayersWiped')} "
          f"-> {hot.get('undoLayersBack')}")
    # A shared history can restore something made with a tool that is no longer open, so an
    # undo that said nothing would look broken exactly when it is most needed.
    check("undo" in (hot.get("undoStatus") or "").lower(),
          "and it says what came back rather than leaving it to be spotted",
          str(hot.get("undoStatus")))
    # The swirl centres are a LIST, not a canvas, and take the other arm of undoSnap
    # entirely -- so a snapshot that only ever copied pixels would pass everything above.
    back = hot.get("undoVtxBack") or ["", ""]
    check(hot.get("undoVtxPlaced") != back[0] and back[0] == back[1],
          "a placed swirl centre can be taken back too",
          f"{back[0]} -> {hot.get('undoVtxPlaced')} -> {back[1]}")

    # THE APP SHELL. On a PC the stacked layout wasted the screen three ways -- a 62vh cap
    # deciding the WIDTH of a landscape frame (measured: 420px of empty stage, 37% of it),
    # a 1180px shell cap, and the cards a page below the picture. The rail fixes all three
    # by giving the panel its own scroll context, which is the only thing the `.cards`
    # comment's objection ever depended on.
    check(cold.get("shellStamped") is False and hot.get("shellStamped") is True,
          "the shell is stamped from the threshold, and only above it",
          f"stacked {cold.get('shellStamped')} / wide {hot.get('shellStamped')}")
    # The grip writes `--rail`, and it is a custom property rather than an inline width
    # precisely so sizeWipe can read back what the layout was given.
    check(hot.get("railWider") == "440px",
          "dragging the grip toward the picture widens the rail", str(hot.get("railWider")))
    check(hot.get("railClamped") == "260px",
          "and the rail is clamped, so a drag can never leave the canvas no width",
          str(hot.get("railClamped")))
    check(hot.get("railReleased") is False,
          "letting go ends the drag", str(hot.get("railReleased")))
    # THE SIZING ITSELF, which is the point of the whole change: stacked, the picture is
    # bounded by 62% of the VIEWPORT (the cap that was deciding a landscape frame's width
    # from its height); in the shell it is bounded by the STAGE's own box, so it fills what
    # the rail and the strips left. Both are computed from the same shimmed 700x394 stage in
    # a 1440x900 window, so a shell that had quietly kept the viewport rule would report the
    # identical width -- and this is the check that would notice.
    check(cold.get("wipeWidth") and hot.get("wipeWidth")
          and cold.get("wipeWidth") != hot.get("wipeWidth"),
          "the shell sizes the picture from its pane, not from the viewport",
          f"stacked {cold.get('wipeWidth')} / shell {hot.get('wipeWidth')}")

    # THE DOCKED LAYERS PANEL. Arming Layers used to spend ~170px of canvas on a list, a
    # readout and three lines of prose -- so the tool that needs the picture most was the
    # one that shrank it most. The rail already scrolls, so the same list now costs the
    # canvas nothing; this is Photoshop's own split, tool OPTIONS beside the canvas and the
    # layer LIST in a docked panel.
    check(hot.get("lyrIdle") is False,
          "the layers panel stays away until there is a reason for it",
          str(hot.get("lyrIdle")))
    check(hot.get("lyrArmed") == [True, ""],
          "arming the tool brings it, empty", str(hot.get("lyrArmed")))
    check(hot.get("lyrWithLayer") == [True, "1"],
          "and it counts the layers, which the Base is not one of",
          str(hot.get("lyrWithLayer")))
    # THE CLAIM WORTH CHECKING. The selection re-points the Palette and Flow rows, and those
    # rows are live with no paint tool open -- so a panel that vanished with the tool would
    # take away the only control over where they write, and hide the readout that answers
    # "where is this slider going" exactly while it is still true.
    check(hot.get("lyrOutlivesTool") == [True, "1"],
          "and putting the tool away does not take the layers with it",
          str(hot.get("lyrOutlivesTool")))

    # THE THREE-COLUMN LAYOUT. `Allocation` is the most structural card on the page -- how
    # many strokes there are and where they go -- so on a wide enough window it is docked
    # opposite the other rail. The claim that can break is not that it appears but that it
    # MIGRATES: one node moved as the window crosses 1360, never copied (two control
    # surfaces for the same six fields is what schema.py exists to prevent) and never
    # rebuilt (which would detach every listener its rows carry).
    check(hot.get("lrailWide") == [True, "leftRail"],
          "a wide window docks Allocation on the far side of the picture",
          str(hot.get("lrailWide")))
    # The middle band: room for one rail and not for two. The shell must stay on and the
    # card must go home ABOVE the others, which is where Allocation sits in FRONT_GROUPS.
    # ".rail" and not "rail": the shim mints its stubs from the selector and only strips a
    # leading "#", so a class-selected element keeps its dot as an id.
    check(hot.get("lrailNarrow") == [False, True, ".rail"],
          "and a window with room for one rail keeps the shell and gives the card back",
          str(hot.get("lrailNarrow")))
    check(hot.get("lrailBack") == [True, "leftRail"],
          "...and it goes back out again, so the migration is not one-way",
          str(hot.get("lrailBack")))

    # THE PROJECT FILE, AND THE ONE CHECK THAT COULD NOT BE MADE ON EITHER SIDE ALONE. The
    # format is oilpaint/project.py's, and the whole point of that is that a file saved in
    # the browser is a file `scripts/paint.py --project` takes. So the page's own output is
    # parsed HERE, by the real Python reader -- a page writing a plausible-looking document
    # Python refuses would pass every check either side could make by itself.
    check(hot.get("projectSaved") is True, "the page writes a project file",
          str(hot.get("projectName")))
    if hot.get("projectJson"):
        from oilpaint import project as project_mod  # noqa: E402
        try:
            doc = json.loads(hot["projectJson"])
            got = project_mod.from_dict(doc)
            check(True, "and the Python reader accepts it unchanged")
            # The five parts, each read back through Python rather than looked for in the
            # text: a key spelled the way the page happens to spell it would pass a string
            # search and still be a field project.py never looks at.
            check(got.config().flow == doc["params"]["flow"],
                  "its settings arrive as a real PaintConfig", str(got.config().flow))
            check(bool(got.overrides) and all(
                      isinstance(k, int) for k in got.overrides),
                  "its layers arrive keyed by region id", str(sorted(got.overrides)))
            # The MASKS are not checked from this document, and the reason is the harness
            # rather than the page: the DOM shim has no canvas, so `toDataURL` returns an
            # empty URL and `rgnScan` finds no passages -- the page correctly writes no
            # `masks` key for a mask that does not exist. Embedded masks are covered where
            # they can be: `test_project_round_trips` in tests/test_core.py, and the
            # committed example below, which carries two real ones.
            check(got.source == doc.get("source", {}).get("name"),
                  "and it records which photograph it was made for", str(got.source))
        except Exception as e:
            check(False, "and the Python reader accepts it unchanged", f"{type(e).__name__}: {e}")
    # THE COMMITTED EXAMPLE, which is the thing a visitor actually meets. It carries all
    # five parts including two real embedded masks, so it is also the only place the mask
    # half of the format is exercised end to end -- and an example that rots into something
    # the reader refuses is worse than no example, because it is the file people copy.
    ex = os.path.join(ROOT, "examples", "two-passages.oilpaint.json")
    if os.path.exists(ex):
        from oilpaint import project as project_mod  # noqa: E402
        try:
            p = project_mod.load(ex)
            check(p.region_mask is not None and p.foveal is not None
                  and bool(p.overrides) and bool(p.vortices) and bool(p.params),
                  "the committed example still carries all five parts",
                  f"masks={p.region_mask is not None},{p.foveal is not None} "
                  f"layers={sorted(p.overrides)} swirls={list(p.vortices)}")
        except Exception as e:
            check(False, "the committed example still carries all five parts",
                  f"{type(e).__name__}: {e}")

    # THE WAY BACK IN, read off the wire: a page that moved its own panel and never told the
    # engine looks identical from the panel.
    check("flow=hatch" in (hot.get("projectImported") or ""),
          "opening a project reaches the engine, not just the panel",
          str(hot.get("projectImported")))
    check("project loaded" in (hot.get("projectStatus") or ""),
          "and it says what it took from the file", str(hot.get("projectStatus")))

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
    # `color_jitter` is PROMOTED here rather than demoted -- it is filed under Irregularity
    # with five controls that vary a stroke's shape, and it is the one that varies its
    # colour. It lands last because the group is not renamed to move it (see PROMOTE in the
    # page): the card is a place to look, the group is what the thing is, and `--color-jitter`
    # stays an irregularity flag everywhere outside this panel.
    check(front.get("Palette") == ["palette", "palette_strength", "broken_color",
                                   "reference", "reference_strength", "color_jitter"],
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

    # THE ROWS SAY WHAT THEY ARE. Every control was headed by its Python identifier in
    # monospace, and the prose that would have rescued it is display:none until the help
    # toggle is on -- so the tool's first impression was forty identifiers and no words.
    # Both halves are pinned, and the second is the one that needed arguing for: the
    # identifier STAYS, because it is what scripts/paint.py takes and what `Copy setup`
    # writes, so a tidier row must not cost the page-to-command-line path.
    rows = cold["rowMarkup"]
    named = [n for n, h in rows.items()
             if f"<code>{n}</code>" in h and re.search(r'class="name">([^<]+)<code>', h)]
    check(len(named) == len(rows),
          "every panel row is headed by a human label with its field name under it",
          str(sorted(set(rows) - set(named))[:4]))
    labels = {r["name"]: r["label"] for r in base["schema"]["schema"]}
    check(all(v and v != k for k, v in labels.items()),
          "and no label is just the field name back again",
          str([k for k, v in labels.items() if not v or v == k][:4]))
    # A LEAD THAT ONLY RESTATES THE LABEL. `target_n`'s help opens "Stroke budget." and
    # `min_cell`'s opens "Smallest stroke." -- fine under the old identifier headings, and a
    # line saying nothing under the new ones. Caught here, before the front cards shipped
    # with three rows whose one always-visible sentence was their own title.
    def _n(t):
        return re.sub(r"[^a-z0-9]", "", t.lower())
    leads = {n: (re.search(r'class="lead">(.*?)</div>', h, re.S) or [None, ""])[1]
             for n, h in rows.items()}
    # The page's own rule, restated: a lead is dropped when it is the label and NOTHING
    # else. A sentence that opens with the name and then goes on to explain is not the
    # defect -- `broken_color`'s help does exactly that and is the most useful line on the
    # card -- so the bound is on what is left after the name, not on how it starts.
    echoes = [n for n, t in leads.items()
              if t and _n(t).startswith(_n(labels.get(n, "")))
              and len(_n(t)) - len(_n(labels.get(n, ""))) <= 12]
    check(not echoes, "and no lead line is just the label back again", str(echoes))
    check(cold["cardNotes"][:3] and all(cold["cardNotes"][:3]),
          "every front card carries the note that says what the group is for",
          str(cold["cardNotes"][:3]))

    # ONE ARMED TOOL AT A TIME. The segmented control is the page ASSERTING that the
    # picture tools are exclusive, so the assertion is driven rather than trusted -- and
    # wiring it up is what found that only rgnMode released the others, leaving two live
    # click-to-paint canvases stacked on one surface with the top one eating every click.
    check(cold["toolIdle"]["none"] == ["on"] and cold["toolIdle"]["guideHidden"] is True,
          "Compare is the tool bar's off state, and it shows no guidance strip",
          str(cold["toolIdle"]))
    check(cold["toolFov"]["fov"] == ["on"] and cold["toolFov"]["none"] == []
          and cold["toolFov"]["guideHidden"] is False,
          "arming a tool lights its chip, releases Compare and puts the next step on the "
          "picture", str(cold["toolFov"]))
    check(cold["toolRgn"]["rgn"] == ["on"] and cold["toolRgn"]["fov"] == []
          and cold["toolRgn"]["fovBar"] is True,
          "and arming the next one RELEASES the first, bar and chip together",
          str(cold["toolRgn"]))
    # The swirl chip is a readout with an off switch, never a second way to arm the tool:
    # it is armed from the Flow card, where the field it belongs to is chosen.
    check(cold["toolVtxShown"] is True and cold["toolVtxRgnBar"] is True,
          "the swirl tool shows in the bar and takes the surface from the layers tool")
    check(cold["toolVtxOff"]["chip"] is True and cold["toolVtxOff"]["bar"] is True
          and cold["toolVtxOff"]["none"] == ["on"],
          "and its chip turns it off again, back to Compare", str(cold["toolVtxOff"]))

    # ERASE IS A CONTROL, NOT A CONSEQUENCE. It was "select the Base, then click the
    # picture", which loaded one selection with two meanings -- which layer receives paint,
    # and whether a click paints or wipes -- and announced the second only in a sentence at
    # the end of the strip. On the Base the chip arms itself and goes read-only, because
    # wiping is then the only thing a click can do.
    check(cold["eraseOnBase"] == {"checked": True, "disabled": True},
          "with the Base selected the erase chip is on and read-only",
          str(cold["eraseOnBase"]))
    check(cold["eraseOnLayer"] == {"checked": False, "disabled": False},
          "and on a layer it is the visitor's to set", str(cold["eraseOnLayer"]))
    check("fill <b>Layer" in (cold["fillGuide"] or "")
          and "wipe back to the base" in (cold["eraseGuide"] or ""),
          "the strip over the picture says which of the two a mark will do",
          repr(cold["fillGuide"]) + " / " + repr(cold["eraseGuide"]))
    check("wiped" in (cold["eraseStatus"] or ""),
          "and a fill with it armed goes to the base whatever is selected",
          repr(cold["eraseStatus"]))

    # THE FOCUS MAP'S STRENGTH, ON THE STRIP THAT PAINTS THE MAP. Two widgets, one number:
    # the strip writes through the panel's own set(), so what has to hold is that the value
    # reaches the ENGINE and that a move on the panel row shows up on the strip. A mirrored
    # control that drifted from the row would be the page contradicting itself about what it
    # is painting.
    check("fov=0.6" in (cold["fovStrengthWire"] or ""),
          "the strip's strength slider reaches the engine as foveal_strength",
          repr(cold["fovStrengthWire"]))
    check(cold["fovStripReadout"] == "0.25",
          "and the panel's own row writes back to the strip", repr(cold["fovStripReadout"]))
    check("wipe the map off" in (cold["fovEraseGuide"] or ""),
          "and the guidance follows the brush's erase chip", repr(cold["fovEraseGuide"]))

    # THE BRUSH. The fill is a wand: it asks the photograph where the passage ends, and has
    # nothing to say about one the photograph does not delimit -- a face against a busy
    # background, half a sky, a wall that is one gradient. Everything below the mark is
    # shared with the fill, so these check the half that is not.
    check(cold["brushOptsBefore"] == {"fill": False, "brush": True}
          and cold["brushOptsAfter"]["fill"] is True
          and cold["brushOptsAfter"]["brush"] is False
          and cold["brushOptsAfter"]["on"] == ["on"]
          and cold["brushOptsAfter"]["off"] == []
          and cold["brushOptsBack"] == {"fill": False, "brush": True},
          "Fill and Brush each show their own settings and nobody else's",
          str([cold["brushOptsBefore"], cold["brushOptsAfter"], cold["brushOptsBack"]]))
    check("Drag to paint" in (cold["brushGuide"] or ""),
          "and the picture's guidance says drag, not click", repr(cold["brushGuide"]))
    # ONE COMMIT PER STROKE. A dab is a few hundred microseconds and a render is a second;
    # a commit inside the drag would make the brush unusable, and it is invisible from
    # anywhere except a driven stroke.
    check(cold["brushMidDrag"] is True,
          "a stroke in progress does not re-render on every dab")
    check(cold["brushRendered"] is True and "painted" in (cold["brushStatus"] or ""),
          "and letting go commits it once", repr(cold["brushStatus"]))
    # The equivalence invariant, reached through the brush this time: a layer seeded from
    # the panel says exactly what the base says, so painting it changes nothing and must not
    # cost a render. The fill has had this check since layers arrived; the brush is a second
    # way in to the same promise, and a second way in is where a promise usually breaks.
    check(cold["brushInertNoRender"] is True,
          "a stroke into a layer that still agrees with the panel does not re-render")
    check("wiped" in (cold["brushEraseStatus"] or ""),
          "the brush honours the erase chip exactly as the fill does",
          repr(cold["brushEraseStatus"]))
    # THE ONE THAT IS NOT ABOUT THE UI. A canvas arc-fill antialiases its rim, which is
    # right for the focus map (a weight field) and is the defect the whole mask pipeline is
    # built to avoid for a LABEL field: a half-alpha rim pixel is dropped by regionBytes'
    # `< 128` test, and a rim pixel whose colour is a blend of two legend entries is
    # resolved by rgnNearest to whichever is nearest the average -- inventing a region 3
    # along every boundary between 2 and 4. So the dab writes pixels, and must go on doing
    # so; drawing a circle here would pass every other check in this file.
    page_js = re.search(r"<script[^>]*>(.+?)</script>",
                        open(PAGE, encoding="utf-8").read(), re.S).group(1)
    dab = re.search(r"function rgnDab\(.*?\n\}", page_js, re.S)
    check(bool(dab) and "put(4 * (y * W + x))" in dab.group(0)
          and not re.search(r"\b(arc|fill)\(", dab.group(0)),
          "the brush writes label pixels directly, never an antialiased circle",
          "missing" if not dab else "draws instead of writing")

    # SIDE BY SIDE WHILE A MASK IS PAINTED. The wipe is the instrument for judging a
    # render, not for marking up a photograph: there the picture has to be whole and still,
    # and a divider across the middle of it is something to work around. Four claims, and
    # the last two are the ones that would cost something if they broke.
    check(cold["splitOff"]["split"] is False and cold["splitOn"]["split"] is True
          and cold["splitAfter"]["split"] is False,
          "arming Layers puts the photograph and the painting side by side, and closing it "
          "gives the wipe back", str([cold["splitOff"], cold["splitOn"], cold["splitAfter"]]))
    check("+ mask" in (cold["splitOn"]["tag"] or "")
          and "+ mask" not in (cold["splitAfter"]["tag"] or ""),
          "and the left pane's tag says what is now on it", repr(cold["splitOn"]["tag"]))
    # EVERY TOOL, not just the one that asked for the view. The rule is meant to be one
    # rule -- the left pane is what you mark, the right pane is what it makes -- and a tool
    # that kept the wipe would teach it and then break it. Driven per tool rather than
    # reasoned about, because "which tools split" is exactly the sort of list that grows a
    # missing entry when a fourth tool arrives.
    every = cold["splitEveryTool"]
    check(all(every[k]["off"] is False and every[k]["split"] is True
              for k in ("fov", "rgn", "vtx")) and every["none"]["split"] is False,
          "every picture tool splits the view, and putting them away restores the wipe",
          str({k: (v.get("off"), v["split"]) for k, v in every.items()}))
    check(every["fov"]["tag"].endswith("+ focus map")
          and every["rgn"]["tag"].endswith("+ mask")
          and every["vtx"]["tag"].endswith("+ swirl centres")
          and not every["none"]["tag"].endswith("centres"),
          "and the tag names which of them is drawn on the photograph",
          str({k: v["tag"] for k, v in every.items()}))
    # The divider used to move on any click that reached the wipe -- which, while a mask is
    # being painted, is any click that misses the tool's own canvas.
    check(cold["splitClipHeld"] is True,
          "a click on the picture in that view does not slide a divider that is not there")
    check("other.jpg" in (cold["splitTagAfterUpload"] or "")
          and (cold["splitTagAfterUpload"] or "").endswith("+ mask"),
          "loading a picture with Layers open keeps both halves of that tag",
          repr(cold["splitTagAfterUpload"]))

    # THE ONE WITH A PRICE ON IT. The painting occupies half the width in split view, so it
    # is rendered at half the width; a view change that quietly doubled the cost of every
    # render would look like nothing at all until a full-res picture took twice as long.
    def _wide(ev):
        m = re.search(r"render (\d+)x", ev or "")
        return int(m.group(1)) if m else 0
    check(0 < _wide(cold["splitRender"]) < _wide(cold["splitPrevRender"]),
          "and the painting is computed for the pane, not for the whole box",
          f'{_wide(cold["splitPrevRender"])} -> {_wide(cold["splitRender"])}')

    # FULL SCREEN: A POINTER LANDS WHERE IT LOOKS LIKE IT LANDS. Reported as "the mouse
    # click is not aligned with what is painted", and it was every tool at once: they each
    # mapped a pointer as `(clientX - left) / width * bitmapWidth`, which assumes the bitmap
    # fills its element box. Full screen makes the box 100vw x 100vh and letterboxes the
    # picture inside it, so the mark landed some way from the pointer -- in exactly the mode
    # you enter in order to place something precisely.
    cw, ch = cold["fsCanvas"]
    box = 1000.0
    scale = min(box / cw, box / ch)
    want = ((250 - (box - cw * scale) / 2) / scale / cw,
            (400 - (box - ch * scale) / 2) / scale / ch)
    got = (json.loads(cold["fsSetup"] or "{}").get("vortices") or {}).get("0") or []
    check(len(got) == 1 and abs(got[0][0] - want[0]) < 1e-6
          and abs(got[0][1] - want[1]) < 1e-6,
          "a click in a letterboxed box lands on the pixel under the pointer",
          f"want {want[0]:.4f},{want[1]:.4f} got {got}")
    # The claim above is only worth making if the OLD arithmetic would have failed it: at
    # the centre of the box both formulas agree, so a check placed there would pass either
    # way and say nothing.
    check(bool(got) and abs(got[0][1] - 400 / box) > 0.02,
          "...and that is a different pixel from the one the old arithmetic chose",
          f"{got[0][1]:.4f} vs {400 / box:.4f}" if got else "nothing placed")
    check(cold["fsBarCount"] == "1",
          "while a click on the black beside the picture places nothing at all",
          repr(cold["fsBarCount"]))

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
