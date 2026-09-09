// Drives index.html's inline module under node, with a DOM shim, and WORKS THE PAGE:
// boot with and without a stored session, a render through a fake engine, and the tools
// that mark the picture -- the foveal map, the region layers, the swirl centres.
//
//     node web/tune/index_node.mjs job.json out.json
//
// WHY THIS EXISTS. `node --check` PARSES the page script, and a parse cannot see a
// temporal-dead-zone error -- reading a `const` one line before its own declaration is
// valid syntax and a ReferenceError at run time, thrown inside an async handler and
// swallowed as an unhandled rejection. That exact bug has shipped here once already: a
// button that did nothing and said nothing, with every static check green.
// The shim is deliberately thin: anything it does not answer shows up as a thrown error,
// which is exactly what this file is looking for.
//
// The fake engine answers `init` with the REAL schema (handed in from Python) and `render`
// with pixels that differ per frame, in the shapes engine.worker.js ships -- so what is
// under test is the page's half of each contract.

import { readFileSync, writeFileSync, unlinkSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const [, , jobPath, outPath] = process.argv;
const job = JSON.parse(readFileSync(jobPath, 'utf8'));
/** `{id: points}` -> "0:2,1:3", ascending, so an event line says WHOSE swirls these are. */
const vtxTally = v => Object.keys(v || {}).map(Number).sort((a, b) => a - b)
  .map(k => `${k}:${v[k].length}`).join(',');
const vtxCount = v => Object.keys(v || {}).reduce((n, k) => n + v[k].length, 0);

const out = { events: [], errors: [] };

// --- the DOM shim ------------------------------------------------------------------------
let ellipses = 0, drawImages = 0;
// A 2D context that REMEMBERS. `getImageData` used to hand back a fresh zeroed buffer every
// call, which made the region mask amnesiac: the page reads the canvas, edits it, writes it
// back, and reads it again to work out which passages exist. With no memory the fill could
// be driven but never observed, and the one thing it has to get right -- that painting over
// a LIVE layer re-renders even when the layer being painted is inert -- was untestable.
//
// One buffer per element per rect, so a full-canvas read/modify/write round-trips while two
// different rects (or two different canvases) stay independent, as they do in a browser.
// `putImageData` is then a no-op: the page mutated the array this handed it.
function makeCtx(el) {
  const bufs = new Map();
  return new Proxy({}, {
    get(_, k) {
      if (k === 'getImageData') {
        return (x, y, w, h) => {
          const key = `${x},${y},${w},${h}`;
          if (!bufs.has(key)) bufs.set(key, new Uint8ClampedArray(Math.max(1, w * h * 4)));
          return { data: bufs.get(key), width: w, height: h };
        };
      }
      // A REAL BUFFER, because the page writes into one. The proxy's catch-all returns
      // undefined for anything it does not name, so `createImageData(...).data` threw --
      // which looked exactly like a page bug and was a gap here. Both mask paths of the
      // project format go through this: the foveal map is imported by writing alpha and
      // exported by writing grey.
      if (k === 'createImageData') {
        return (w, h) => ({ data: new Uint8ClampedArray(Math.max(1, w * h * 4)),
                            width: w, height: h });
      }
      if (k === 'clearRect') return () => bufs.forEach(b => b.fill(0));
      if (k === 'ellipse') return () => { ellipses++; };
      if (k === 'drawImage') return () => { drawImages++; };
      if (k === 'createRadialGradient') return () => ({ addColorStop() {} });
      if (k === 'measureText') return () => ({ width: 10 });
      return () => {};
    },
    set() { return true; },
  });
}

function makeEl(id) {
  const el = {
    _id: id, _h: {}, hidden: false, open: false, value: '', textContent: '',
    // Children are KEPT, and setting innerHTML drops them, which is enough of the DOM for
    // the harness to read the panel back: which card each control landed on.
    _html: '', _kids: [],
    set innerHTML(v) { this._html = v; this._kids = []; },
    get innerHTML() { return this._html; },
    _src: '', onload: null, onerror: null, checked: false, disabled: false, title: '',
    width: 0, height: 0, naturalWidth: 0, naturalHeight: 0,
    // THE LAID-OUT BOX. Without these, sizeWipe read `undefined` for the stage and produced
    // NaN -- which never threw, and so never once exercised the arithmetic that decides how
    // big the picture is. Both numbers are pinned by a constraint rather than picked:
    //
    //   560 wide, because `sourceNote` warns when a source is shown at more than 1.15x and
    //   the shimmed devicePixelRatio is 2 against a 1000px source. At the 700 that matches
    //   getBoundingClientRect that is 1.4x -- a REAL warning, correctly raised, which keeps
    //   #stats on screen and fails the two checks that ask whether it hides. 560 gives
    //   1.12x. (dpr stays 2: renderWidth caps at PREVIEW_CAP either way, and dropping it to
    //   1 would move every `render 1100x617` expectation in this file.)
    //
    //   260 tall, so the two layouts' height bounds actually DIFFER. Stacked bounds the
    //   picture at 62% of a 900px window (558) and the width wins; the shell bounds it at
    //   the stage's own box. At 394 the shell's bound would not bind either and both
    //   layouts would report the same width -- so the check that says the shell sizes from
    //   its pane would pass without ever having been true.
    clientWidth: 560, clientHeight: 260,
    // `setProperty` because the layers panel sets a CSS custom property for the selected
    // layer's colour, which is what `style` is for and what a bare object is not.
    // setProperty RECORDS. The rail's width is a custom property and nothing else, so a
    // no-op here would make the grip drag unobservable -- and "the layout number the
    // arithmetic reads back" is exactly the half worth checking.
    style: { _p: {}, setProperty(k, v) { this._p[k] = v; }, removeProperty(k) { delete this._p[k]; },
             getPropertyValue(k) { return this._p[k] || ''; } },
    dataset: {},
    classList: {
      _s: new Set(),
      add(x) { this._s.add(x); }, remove(x) { this._s.delete(x); },
      toggle(x, on) { if (on === undefined) { this._s.has(x) ? this._s.delete(x) : this._s.add(x); } else if (on) this._s.add(x); else this._s.delete(x); },
      contains(x) { return this._s.has(x); },
    },
    // Assigning `src` fires `onload` AND reports a size: upload() awaits the load and then
    // reads naturalWidth for the aspect, and a stub that stayed 0x0 would quietly skip the
    // whole render path this harness exists to drive.
    set src(v) {
      this._src = v; this.naturalWidth = 1000; this.naturalHeight = 561;
      queueMicrotask(() => this.onload && this.onload());
    },
    get src() { return this._src || ''; },
    addEventListener(t, f) { (this._h[t] = this._h[t] || []).push(f); },
    removeEventListener() {},
    setAttribute() {}, removeAttribute() {}, focus() {}, click() { this.fire('click', {}); },
    setPointerCapture() {}, releasePointerCapture() {}, hasPointerCapture() { return false; },
    scrollIntoView() {},
    // Track parentage as the page builds its controls and layer rows.
    parentNode: null,
    appendChild(c) { this._kids.push(c); c.parentNode = this; return c; },
    insertBefore(c, ref) {
      const i = this._kids.indexOf(ref);
      if (i < 0) this._kids.push(c); else this._kids.splice(i, 0, c);
      c.parentNode = this;
      return c;
    },
    remove() {},
    querySelector() { return makeEl('inner'); },
    querySelectorAll() { return []; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 700, height: 394 }; },
    getContext() { return (this._ctx = this._ctx || makeCtx(this)); },
    toDataURL() { return 'data:,'; },
    fire(type, ev) { (this._h[type] || []).forEach(f => f(Object.assign({
      preventDefault() {}, stopPropagation() {}, target: this,
    }, ev))); },
  };
  // An element that is GIVEN an id becomes findable by it. The panel's controls are built
  // with createElement and so were unreachable from here: the harness could read the shape
  // of a card back but could not move a slider -- which is exactly where the layer model's
  // risk lives, because a row that writes to the wrong place looks identical from outside.
  Object.defineProperty(el, 'id', {
    get() { return this._id; },
    set(v) { this._id = v; els.set('#' + v, this); },
  });
  return el;
}

const made = [];
const els = new Map();
const $ = sel => {
  if (!els.has(sel)) els.set(sel, makeEl(sel.replace('#', '')));
  return els.get(sel);
};

globalThis.document = {
  querySelector: $, querySelectorAll: () => [],
  getElementById: id => $('#' + id),
  // EVERY created element is remembered. `Save project` writes its file by making an
  // anchor, setting a data: URL on it and clicking it -- there is no other way for a page
  // to hand over a file -- so the only way to read back what it wrote is to find that
  // anchor afterwards. Used by nothing else; the list is cheap and the alternative is a
  // download the harness cannot see.
  createElement: () => { const e = makeEl('created'); made.push(e); return e; },
  body: makeEl('body'),
  // The rail's width lives as a custom property on the root element, so the grip needs a
  // documentElement with a style object to write it to.
  documentElement: makeEl('html'),
  addEventListener() {},
};
globalThis.window = {
  _h: {}, devicePixelRatio: 2,
  // Sizes the page reads directly. The stacked layout bounds the picture at 62vh of this,
  // so a window with no height at all made sizeWipe produce NaN -- harmless, and it also
  // meant the arithmetic was never once exercised.
  innerWidth: job.wide ? 1440 : 800, innerHeight: job.wide ? 900 : 500,
  // WHICH LAYOUT THE PAGE IS IN, from a shimmed viewport rather than a real window. The
  // shell is stamped by `shellSync` off these queries, so without them the harness could
  // only ever drive the stacked page -- and the shell is the half with the new arithmetic.
  //
  // Evaluate the query against the viewport so docked and stacked sizing are both driven.
  matchMedia(q) {
    const need = (re, v) => { const m = re.exec(q); return !m || v >= parseFloat(m[1]); };
    const matches = need(/min-width:\s*(\d+)/, globalThis.window.innerWidth)
                 && need(/min-height:\s*(\d+)/, globalThis.window.innerHeight);
    return { matches, media: q,
             addEventListener() {}, removeEventListener() {}, addListener() {} };
  },
  addEventListener(t, f) { (this._h[t] = this._h[t] || []).push(f); },
  prompt() { return null; },
};
// `--rail` is read back through this before a grip drag, so it has to answer with something
// parseable or the drag starts from NaN and the rail jumps to its clamp on the first move.
globalThis.getComputedStyle = () => ({ fontFamily: 'serif',
                                       getPropertyValue: () => '340px' });
// defineProperty rather than assignment: node ships its own `navigator` as a getter-only
// accessor on globalThis (18+), so a plain assignment throws TypeError at module scope and
// takes the whole harness down before a single check runs -- which reads as "the page script
// does not run at all" and says nothing about the page.
// The clipboard REMEMBERS. `Copy setup` is the page's own record of what is on screen, and
// it carries the placed swirl centres as fractions -- which makes it the only way to read
// back WHERE a click landed, as opposed to that one did.
let lastCopy = '';
Object.defineProperty(globalThis, 'navigator', {
  value: { clipboard: { writeText: async t => { lastCopy = t; } } },
  configurable: true, writable: true,
});
globalThis.localStorage = {
  _m: new Map(job.localStorage ? Object.entries(job.localStorage) : []),
  getItem(k) { return this._m.has(k) ? this._m.get(k) : null; },
  setItem(k, v) { this._m.set(k, v); },
};
const idbData = new Map(job.indexedDB ? Object.entries(job.indexedDB) : []);
const fireReq = (obj, ok, val) => queueMicrotask(() => {
  if (ok) { obj.result = val; obj.onsuccess && obj.onsuccess(); }
  else { obj.onerror && obj.onerror(); }
});
globalThis.indexedDB = {
  open() {
    const req = { result: null, onsuccess: null, onerror: null, onupgradeneeded: null };
    queueMicrotask(() => {
      req.result = {
        objectStoreNames: { contains: () => true },
        createObjectStore: () => {},
        close() {},
        transaction() {
          const t = { oncomplete: null };
          queueMicrotask(() => t.oncomplete && t.oncomplete());
          return { objectStore: () => ({
            get(k) { const r = {}; fireReq(r, true, idbData.get(k)); return r; },
            put(v, k) { idbData.set(k, v); const r = {}; fireReq(r, true, undefined); return r; },
            delete(k) { idbData.delete(k); const r = {}; fireReq(r, true, undefined); return r; },
          }), ...t };
        },
      };
      req.onsuccess && req.onsuccess();
    });
    return req;
  },
};
globalThis.ImageData = class {
  // Both signatures the page uses: (data, w, h) and the bare (w, h) the atlas tinting
  // allocates with.
  constructor(a, b, c) {
    if (typeof a === 'number') { this.width = a; this.height = b; this.data = new Uint8ClampedArray(a * b * 4); }
    else { this.data = a; this.width = b; this.height = c; }
  }
};
// A File KEEPS its bits when they are text, so the harness can hand the page a project
// file. Everything else about it is still a stub -- the image path only ever needs a name
// and a size.
globalThis.File = class {
  constructor(bits, name, opts) {
    this.name = name; this.type = (opts && opts.type) || ''; this.size = 4;
    this._text = (bits || []).filter(b => typeof b === 'string').join('');
  }
};
globalThis.FileReader = class {
  readAsDataURL() {
    queueMicrotask(() => { this.result = 'data:image/jpeg;base64,AAAA'; this.onload && this.onload(); });
  }
  // The project path reads TEXT, not a data URL, and a shim with only the latter made
  // importing a project silently do nothing at all.
  readAsText(f) {
    queueMicrotask(() => { this.result = (f && f._text) || ''; this.onload && this.onload(); });
  }
};
// restoreImage() re-fetches the stored data URL and loadSample() fetches the shipped jpeg;
// both only need a blob with a size, because the bytes go straight back into the shimmed
// FileReader above.
// ...and the OPENING PROJECT, which is JSON rather than an image and is the one fetch whose
// content the page actually reads. Served from the job so the harness can drive both halves
// of the rule: `sampleProject` present is a deployed page with its example, absent is an
// older build (or a 404), which must degrade to the photograph and the engine's defaults.
globalThis.fetch = async (url) => {
  if (String(url).includes('sample-project.json')) {
    const doc = job.sampleProject;
    return { ok: !!doc, status: doc ? 200 : 404, json: async () => doc,
             blob: async () => ({ size: 4, type: 'application/json' }) };
  }
  return { ok: true, status: 200,
           blob: async () => ({ size: 4, type: 'image/jpeg' }) };
};
// requestAnimationFrame is shimmed but never pumped: the only frame the page schedules is
// the wipe's own drag, which this harness does not start. It exists so that a page CALLING
// rAF does not throw on a runtime that has none -- which would look like a page bug.
globalThis.requestAnimationFrame = () => 0;

// --- the fake engine ---------------------------------------------------------------------
let served = 0, engines = 0;
globalThis.Worker = class {
  // ONLY THE FIRST ENGINE IS ON THE RECORD. The page runs two: the painting engine, and a
  // second one that paints the looks strip's thumbnails in the background. Both are the
  // same module and both arrive here, but every check in verify_page.py reads `out.events`
  // to ask what THE PAGE ASKED FOR -- "did that fill re-render", "did that click place a
  // swirl" -- and a thumbnail is neither. Recording both made two checks fail against a
  // page that was working correctly, which is the worst kind of harness defect: it accuses
  // the code. Ordering is the discriminator rather than a flag on the message, because the
  // page must not have to declare to a test which of its workers is the real one.
  constructor() {
    this.onmessage = null; this._l = {};
    this.aux = engines++ > 0;
    if (!this.aux) out.events.push('worker started');
  }
  addEventListener(t, f) { (this._l[t] = this._l[t] || []).push(f); }
  postMessage(msg) {
    const reply = m => queueMicrotask(() =>
      (this._l.message || []).forEach(f => f({ data: m })));
    // The auxiliary engine still ANSWERS -- the strip has to paint while the page is
    // driven, or the harness would be exercising a feature switched off -- it simply says
    // nothing about it.
    const push = e => { if (!this.aux) out.events.push(e); };
    if (msg.type === 'init') {
      // `staleSchema` serves the schema a server started BEFORE the hue fields existed
      // would serve: the rows and the defaults both missing. That is the configuration
      // that printed "the band holds NaN degrees either side of NaN degrees" and, worse,
      // graded every colour to NaN -- so it is a job flag rather than a story.
      let schema = job.schema.schema, defaults = job.schema.defaults;
      if (job.staleSchema) {
        const drop = n => n.startsWith('hue_');
        schema = schema.filter(c => !drop(c.name));
        defaults = Object.fromEntries(
          Object.entries(defaults).filter(([k]) => !drop(k)));
      }
      reply({ id: msg.id, type: 'ready', schema, defaults,
              groupNotes: job.schema.groupNotes, pigments: {},
              flowKinds: job.flowKinds, maxVortices: job.maxVortices,
              maxRegions: job.maxRegions, regionLegend: job.regionLegend,
              regionParams: job.regionParams });
      return;
    }
    if (msg.type === 'render') {
      // WHERE EACH VALUE LANDED, recorded by the side that received it. The layer model's
      // only real failure mode is a row writing to the wrong place, and from outside that
      // looks exactly like a row that works -- so a probe field is read off the wire for
      // the base and for every layer, and the checks compare them.
      push(`base warm_cool=${msg.params.warm_cool} flow=${msg.params.flow}`);
      for (const k of Object.keys(msg.regionOverrides || {})) {
        const ov = msg.regionOverrides[k];
        // TWO probe fields, one per half of the model: a colour one, which must never move
        // the geometry, and a flow one, which must move only this passage's. They are read
        // off the wire for the same reason -- a row writing to the wrong half looks exactly
        // like a row that works.
        push(`region ${k} warm_cool=${ov.warm_cool} flow=${ov.flow} `
          + `fields=${Object.keys(ov).sort().join(',')}`);
      }
      const px = new Uint8ClampedArray(msg.w * msg.h * 4).fill((served++ % 250) + 1);
      // Per OWNER, not a total: the whole claim of the per-layer swirls is that a point
      // lands in the set the selection names, and a count alone cannot tell "2 for the
      // picture" from "2 for Region 1" -- which is the one thing that can go wrong.
      // `fov` is appended rather than inserted: every check above matches this line by
      // substring, and a field in the middle would rewrite what those matches mean.
      push(`render ${msg.w}x${msg.h} flow=${msg.params.flow}`
        + ` fov=${msg.params.foveal_strength}`
        + (msg.vortices ? ` v=${vtxTally(msg.vortices)}` : '')
        + (msg.regionLabels ? ` r=${Object.keys(msg.regionOverrides || {}).sort()}`
                            + `/${msg.regionKey}` : ''));
      reply({ id: msg.id, type: 'done', pixels: px.buffer, w: msg.w, h: msg.h,
              stats: { strokes: 1, underpainting: 0, coverage_detail: 1, coverage_all: 1,
                       bare: 0, psnr: 30, edge_alignment: 0.6,
                       // Driven by a real control so the harness can ask for a WARNING:
                       // the stats strip is off by default and a warning has to appear
                       // anyway, which is the half of that toggle worth pinning.
                       budget_reached: msg.params.target_n < 30000,
                       foveal: false, palette: false, flow: msg.params.flow !== 'none',
                       regions: Object.keys(msg.regionOverrides || {}).length,
                       region_strokes: msg.regionOverrides
                         ? Object.fromEntries(Object.keys(msg.regionOverrides)
                             .map((k, i) => [k, i === 0 ? 0 : 40 + i])) : null,
                       vortices: vtxCount(msg.vortices), ceiling: 0, tau: 1e-4,
                       seconds: 0.01, size: `${msg.w}x${msg.h}` } });
      return;
    }
  }
};

process.on('unhandledRejection', e => out.errors.push(String((e && e.message) || e)));
process.on('uncaughtException', e => out.errors.push(String((e && e.message) || e)));

const html = readFileSync(join(HERE, 'index.html'), 'utf8');
// Seed the stubs from the MARKUP's own attributes -- `#wipe` starts hidden there, and "the
// viewer is showing" is the restore claim, so a stub that defaulted to visible would report
// a successful restore with nothing in the store.
for (const m of html.matchAll(/<[a-zA-Z][^>]*>/g)) {
  const tag = m[0];
  const id = /\bid="([^"]+)"/.exec(tag);
  if (!id) continue;
  if (/\shidden(\s|>|=)/.test(tag)) $('#' + id[1]).hidden = true;
  if (/\schecked(\s|>|=)/.test(tag)) $('#' + id[1]).checked = true;
  const cls = /\bclass="([^"]*)"/.exec(tag);
  if (cls) cls[1].split(/\s+/).filter(Boolean).forEach(c => $('#' + id[1]).classList.add(c));
  const val = /\bvalue="([^"]*)"/.exec(tag);
  if (val) $('#' + id[1]).value = val[1];
}

const src = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
const tmp = join(HERE, '.index_node_tmp.mjs');
writeFileSync(tmp, src);
try {
  await import(pathToFileURL(tmp).href);
} finally {
  unlinkSync(tmp);
}
// Let the boot settle: init handshake, session restore, and the automatic first
// render, which is what everything below reads.
await new Promise(r => setTimeout(r, 320));

const fireWin = t => (globalThis.window._h[t] || []).forEach(f => f({ preventDefault() {} }));

// Everything below is wrapped: a throw in the drive used to end this process with exit 0,
// no output and no out.json, so verify_page could report that the harness had failed and
// nothing whatever about why. The rule the page follows applies to the thing testing it.
try {
// --- drive it ----------------------------------------------------------------------------
// 1. Boot. The engine started, the panel built, and the stored (or sample) image came back
//    and was rendered without anybody clicking anything.
out.bootStatus = $('#status').textContent;
out.restoredImage = !$('#wipe').hidden;
out.sourceTag = $('#tagL').textContent;
out.renders = out.events.filter(e => e.startsWith('render'));
out.errText = $('#err').textContent;
// 1b. THE OPENING PROJECT. A page whose first frame is a photograph with default settings
//     undersells the tool; a page that overwrote a returning visitor's tuning with an
//     example would be far worse. Both halves are read from the FIRST render of the
//     session, because that is the frame the argument is about -- and the two runs of this
//     harness already are the two cases: cold has no stored setup and gets the sample plus
//     its project, warm has one and must keep it.
out.openingWire = (out.renders[0] || '');
out.openingLayers = ($('#rgnLayers')._kids || []).map(r => r.dataset.id);
out.openingStatus = $('#status').textContent;

// 1c. THE CARDS. Which control sits on which card is decided by FRONT_GROUPS, HIDDEN and
//     FINE in the page, and the only way to see the decision is to build the panel.
const cardList = host => host._kids.map(c => ({
  name: (c.innerHTML.match(/<h2>(.*?)<em>/) || [])[1],
  // Controls only. A card can also carry a TOOL button (see CARD_TOOLS in the page), which
  // has no data-name -- read separately below, because "which controls are on this card" and
  // "which tools are" are two different questions and one list cannot answer both.
  rows: c._kids.map(r => r.dataset.name).filter(Boolean),
  tools: c._kids.map(r => r._id).filter(x => x && x !== 'created') }));
// Read every inspector, including its folded adjustments. Hidden tabs still own live
// controls; switching tabs must never rebuild them or duplicate their values.
const primaryIds = ['leftCards', 'cards', 'flowCards', 'lightCards'];
const fineIds = ['colorFine', 'flowFine', 'strokeFine', 'lightFine'];
out.frontCards = primaryIds.flatMap(id => cardList($('#' + id)));
out.advCards = fineIds.flatMap(id => cardList($('#' + id)));
out.inspectorCards = Object.fromEntries([
  ['color', 'cards', 'colorFine'], ['strokes', 'leftCards', 'strokeFine'],
  ['flow', 'flowCards', 'flowFine'], ['light', 'lightCards', 'lightFine']
].map(([name, main, fine]) => [name, cardList($('#' + main)).concat(cardList($('#' + fine))).map(c => c.name)]));
// THE ROW MARKUP ITSELF. Every row used to be printed as its Python identifier; the label
// now leads and the identifier follows it, and the second half is the one worth pinning --
// `target_n` is what scripts/paint.py takes and what `Copy setup` writes, so a redesign
// that tidied it away would cut the path from the page to the command line.
const allCards = primaryIds.concat(fineIds).flatMap(id => $('#' + id)._kids);
out.rowMarkup = {};
for (const c of allCards) {
  for (const r of c._kids) if (r.dataset.name) out.rowMarkup[r.dataset.name] = r.innerHTML;
}
out.cardNotes = allCards.map(c => (c.innerHTML.match(/<p class="csub">(.*?)<\/p>/) || [])[1]);

// 4b. THE REGION LAYERS. The model is "the panel edits the selection", and its only real
//     failure mode is a row writing to the wrong place -- which, from outside, looks exactly
//     like a row that works. So this drives it end to end and reads back WHERE each value
//     landed, off the render message the fake engine actually received.
const settle = ms => new Promise(r => setTimeout(r, ms));
// Undo's resting state, read HERE and not in section 5c: by the time the drive reaches
// that section it has painted, filled and placed for real, so an empty stack is only
// observable before any of it.
out.undoIdle = $('#rgnUndo').disabled;
const renders = () => out.events.filter(e => e.startsWith('render'));
// The base's own value, read off the last render the engine RECEIVED rather than out of the
// page's module scope: the wire is what the picture is actually painted from, so it is the
// only place worth asking.
const baseWarmNow = () => {
  const e = out.events.filter(x => x.startsWith('base warm_cool=')).pop() || '';
  return parseFloat(e.split('=')[1]);
};
// The panel's rows are reachable by id now (see `control()` in the page). Fire the row's own
// handler rather than reaching into the module: the wiring is what is under test.
const moveRow = (name, v) =>
  $('#ctl-' + name).fire('input', { target: { value: String(v) } });
// A choice row is a <select> and fires 'change', not 'input'. Same principle: fire the row's
// own handler rather than reaching into the module, because the wiring is what is under test.
const pickRow = (name, v) => $('#ctl-' + name).fire('change', { target: { value: v } });
const rowCls = name => [...$(`.row[data-name="${name}"]`).classList._s];
const baseFlowNow = () => {
  const e = out.events.filter(x => x.startsWith('base warm_cool=')).pop() || '';
  return (e.split('flow=')[1] || '').trim();
};

$('#rgnBtn').fire('click', {});
out.rgnBarShown = $('#rgnBar').hidden === false;
const rgnCanvas = $('#rgnCanvas');
const lyrRow = id => ($('#rgnLayers')._kids || []).find(r => r.dataset.id === String(id));
out.rgnBaseOnly = ($('#rgnLayers')._kids || []).map(r => r.dataset.id);

// A layer, then some paint in it. A NEW layer is seeded from the panel, so it says exactly
// what the base says, and painting it must not re-render -- the equivalence invariant of
// oilpaint/regions.py, showing up here as a UI promise.
$('#rgnAdd').fire('click', {});
const beforeFill = renders().length;
rgnCanvas.fire('pointerdown', { clientX: 300, clientY: 150 });
await settle(80);
out.rgnFillInert = renders().length === beforeFill;
out.rgnLayerIds = ($('#rgnLayers')._kids || []).map(r => r.dataset.id);
out.rgnEditingText = $('#rgnEditing').innerHTML;

// THE CLAIM: with layer 1 selected, a Palette row writes to layer 1 and NOT to the base.
const baseWarm = baseWarmNow();
moveRow('warm_cool', 0.44);
await settle(900);
out.rgnBaseWarmAfter1 = baseWarmNow();
out.rgnLayer1Warm = (out.events.filter(e => e.startsWith('region 1 ')).pop() || '');
out.rgnRowClasses = rowCls('warm_cool');

// THE SAME CLAIM FOR THE OTHER HALF, and it is the one worth driving rather than reasoning
// about: `flow` is layer-aware only because the ENGINE says so on the ready message, so a
// page that had kept its own list of colour fields would leave this row global and quietly
// re-comb the whole picture. A <select>, not a range, so it is a different code path in the
// row builder too.
// A field the base is NOT already on -- this path boots from a stored session whose flow is
// 'starry', and picking that would have proved nothing about where the value went.
const baseFlow = baseFlowNow();
pickRow('flow', baseFlow === 'waterlily' ? 'hatch' : 'waterlily');
await settle(900);
out.rgnBaseFlowAfter = baseFlowNow();
out.rgnLayer1Flow = (out.events.filter(e => e.startsWith('region 1 ')).pop() || '');
out.rgnFlowRowClasses = rowCls('flow');
out.rgnBaseFlowWas = baseFlow;

// 4c. THE SWIRL TOOL ON THE SAME SELECTION. The fourth tool on the one rule, and the only
//     one whose failure is silent: a centre placed into the base while a layer is selected
//     still swirls SOMETHING, so the picture changes and nothing looks broken. Layer 1 is
//     on `waterlily` from the probe above, which is exactly the state the gate is for.
const vtxCanvas = $('#vtxCanvas');
const vtxClick = (x, y) => {
  vtxCanvas.fire('pointerdown', { clientX: x, clientY: y });
  vtxCanvas.fire('pointerup', { clientX: x, clientY: y });
};
$('#vtxBtn').fire('click', {});
out.vtxTargetText = $('#vtxFor').innerHTML;
out.vtxOffBtnClasses = [...$('#vtxBtn').classList._s];
out.vtxOffHint = $('#vtxHint').innerHTML;
// THE GATE: a click on a target that cannot hold swirls must place nothing at all. Read off
// the wire rather than out of the page -- a refusal that still sent a point would look
// identical from inside.
const beforeVtx = renders().length;
vtxClick(300, 150);
await settle(900);
out.vtxOffAfter = renders().pop() || '';
out.vtxOffRefused = renders().length === beforeVtx;
// THE FIX, as a button rather than as advice, and routed through the panel's own writer so
// it arms the LAYER rather than the picture.
$('#vtxArm').fire('click', {});
await settle(900);
vtxClick(300, 150);
await settle(900);
out.vtxAfterLayerPlace = renders().pop() || '';
$('#vtxClear').fire('click', {});
await settle(900);
out.vtxAfterLayerClear = renders().pop() || '';
$('#vtxBtn').fire('click', {});            // close the bar again
out.vtxBarClosed = $('#vtxBar').hidden;
// AVAILABILITY IS RECOMPUTED WITH THE PANEL, NOT WITH THE BAR. This is the reported bug: a
// layer set to `starry` while the swirl bar was closed left the button dimmed, because the
// only thing that recomputed it ran when the bar was open. Driven with the bar SHUT.
pickRow('flow', 'waterlily');
await settle(50);
out.vtxOffWhileClosed = [...$('#vtxBtn').classList._s];
pickRow('flow', 'starry');
await settle(900);
out.vtxLiveWhileClosed = [...$('#vtxBtn').classList._s];

// 4d. THE STATS STRIP. Off by default -- the numbers are for judging a change -- but a
//     warning is not a number, and the page must never fail in silence.
out.statsHiddenByDefault = $('#stats').hidden;
$('#showStats').checked = true; $('#showStats').fire('change', {});
out.statsShownHtml = $('#stats').innerHTML;
$('#showStats').checked = false; $('#showStats').fire('change', {});
out.statsHiddenAgain = $('#stats').hidden;
moveRow('target_n', 40000);                // the fake engine then reports a ceiling
await settle(900);
out.statsWarnHidden = $('#stats').hidden;
out.statsWarnHtml = $('#stats').innerHTML;

// 4e. RENDER FOLLOWS `auto`. The button is hidden while every change re-renders -- a button
//     with nothing to do, in the busiest part of the bar -- and has to come BACK when auto
//     is off, because then it is the only way to see a change. A page that hid it and kept
//     it hidden would be a tuner that cannot render, which is the worst version of this.
out.renderHiddenOnAuto = $('#render').hidden;
$('#auto').checked = false; $('#auto').fire('change', {});
out.renderShownOffAuto = $('#render').hidden;
const beforeManual = renders().length;
moveRow('aniso_max', 4.5);                 // a change that must NOT render on its own
await settle(600);
out.manualNoAutoRender = renders().length === beforeManual;
$('#render').fire('click', {});
await settle(900);
out.manualRendered = renders().length > beforeManual;
$('#auto').checked = true; $('#auto').fire('change', {});
await settle(900);
out.renderHiddenAgain = $('#render').hidden;

// A second layer, given a different value, so the two disagree.
$('#rgnAdd').fire('click', {});
rgnCanvas.fire('pointerdown', { clientX: 120, clientY: 60 });
await settle(80);
moveRow('warm_cool', -0.31);
await settle(900);
out.rgnLayer2Warm = (out.events.filter(e => e.startsWith('region 2 ')).pop() || '');

// MULTI-SELECT. Two layers that disagree must read as mixed, and one write must reach both.
lyrRow(1).fire('click', {});
lyrRow(2).fire('click', { shiftKey: true });
out.rgnMixedClasses = rowCls('warm_cool');
out.rgnMultiEditing = $('#rgnEditing').innerHTML;
moveRow('warm_cool', 0.12);
await settle(900);
out.rgnMultiBoth = ['1', '2'].map(
  id => (out.events.filter(e => e.startsWith(`region ${id} `)).pop() || ''));
out.rgnUnmixedClasses = rowCls('warm_cool');

// THE EYE. A hidden layer leaves the wire entirely, so its strokes fall back to the base.
lyrRow(2).fire('click', {});
lyrRow(2)._kids[0].fire('click', {});      // the eye
await settle(900);
out.rgnHidden = renders().pop();
lyrRow(2)._kids[0].fire('click', {});
await settle(900);
out.rgnShown = renders().pop();

// BACK TO THE BASE. Selecting it must point the rows at `params` again. A page left pointed
// at a layer is the worst shape a control can have: every slider still works, and none of
// them does what the visitor expects.
lyrRow(0).fire('click', {});
out.rgnBaseClasses = rowCls('warm_cool');
moveRow('warm_cool', baseWarm + 0.07);
await settle(900);
out.rgnBaseWarmAfterBaseEdit = baseWarmNow();
out.rgnLayer1AfterBaseEdit = (out.events.filter(e => e.startsWith('region 1 ')).pop() || '');

// A FILL THAT TAKES. Painting an INERT layer over a live one still changes the picture --
// the live layer loses those strokes -- so "did this fill change anything" is not the same
// question as "is the layer I am painting live". Answering it with the target alone left a
// real edit unrendered, which is why this is here.
$('#rgnAdd').fire('click', {});
const beforeSteal = renders().length;
rgnCanvas.fire('pointerdown', { clientX: 200, clientY: 100 });
await settle(900);
out.rgnStealRendered = renders().length > beforeSteal;

// DELETE. The layer goes, and so do its pixels.
lyrRow(1).fire('click', {});
lyrRow(1)._kids[4].fire('click', {});      // the ×
await settle(900);
out.rgnAfterDelete = ($('#rgnLayers')._kids || []).map(r => r.dataset.id);

// 5. THE TOOLBAR'S TOOL STATE. The three picture tools are mutually exclusive in the code,
//    and the segmented control is the page ASSERTING that -- so the assertion has to be
//    driven. It is not cosmetic: wiring it up found that only rgnMode released the others,
//    so arming the focus map over an open Layers tool left two live click-to-paint canvases
//    on one surface, the topmost ate every click, and the tool whose button was lit did
//    nothing at all.
const cls = sel => [...$(sel).classList._s];
$('#toolNone').fire('click', {});
out.toolIdle = { none: cls('#toolNone'), fov: cls('#fovBtn'), rgn: cls('#rgnBtn'),
                 guideHidden: $('#guide').hidden };
$('#fovBtn').fire('click', {});
out.toolFov = { none: cls('#toolNone'), fov: cls('#fovBtn'),
                guide: $('#guide').innerHTML, guideHidden: $('#guide').hidden };
// ARMING ONE RELEASES THE OTHER, read off the bar rather than off the button: a chip that
// looked right over a canvas that was still live is exactly the bug this pins.
$('#rgnBtn').fire('click', {});
out.toolRgn = { fov: cls('#fovBtn'), rgn: cls('#rgnBtn'), fovBar: $('#fovBar').hidden,
                guide: $('#guide').innerHTML };
// The swirl chip APPEARS while that tool is on and only ever turns it off -- it is armed
// from the Flow card, and a second way to arm it here would undo the reason it lives there.
$('#vtxBtn').fire('click', {});
out.toolVtxShown = !$('#toolVtx').hidden;
out.toolVtxRgnBar = $('#rgnBar').hidden;
$('#toolVtx').fire('click', {});
out.toolVtxOff = { chip: $('#toolVtx').hidden, bar: $('#vtxBar').hidden,
                   none: cls('#toolNone') };

// 5b. ERASE IS A CONTROL NOW. It was "select the Base, then click", which loaded one
//     selection with two meanings and announced the second only in prose. On the Base the
//     chip arms itself and goes read-only, because wiping is then the only thing a click
//     can do; on a layer it is the visitor's to set, and a fill with it on must go to
//     region 0 whatever is selected.
if ($('#rgnBar').hidden) $('#rgnBtn').fire('click', {});
const anyLayer = ($('#rgnLayers')._kids || []).map(r => r.dataset.id).find(x => x !== '0');
lyrRow(0).fire('click', {});
out.eraseOnBase = { checked: $('#rgnErase').checked, disabled: $('#rgnErase').disabled };
lyrRow(Number(anyLayer)).fire('click', {});
out.eraseOnLayer = { checked: $('#rgnErase').checked, disabled: $('#rgnErase').disabled };
out.fillGuide = $('#guide').innerHTML;
$('#rgnErase').checked = true; $('#rgnErase').fire('change', {});
out.eraseGuide = $('#guide').innerHTML;
rgnCanvas.fire('pointerdown', { clientX: 210, clientY: 110 });
await settle(120);
out.eraseStatus = $('#status').textContent;

// 5c. THE FOCUS MAP'S STRENGTH, IN THE STRIP THAT PAINTS THE MAP. Two widgets, one number:
//     the strip writes through the panel's own `set()`, so what has to be proven is that
//     the value reaches the ENGINE, and that a move on the panel row shows in the strip.
$('#fovBtn').fire('click', {});
$('#fovStrength').fire('input', { target: { value: '0.6' } });
await settle(900);
out.fovStrengthWire = renders().pop() || '';
$('#ctl-foveal_strength').fire('input', { target: { value: '0.25' } });
await settle(50);
out.fovStripReadout = $('#fovStrengthV').textContent;
// The strip's own erase chip changes what the picture's guidance says, because that is the
// question a hand asks before any of the settings matter.
$('#fovErase').checked = true; $('#fovErase').fire('change', {});
out.fovEraseGuide = $('#guide').innerHTML;

// 5d. SIDE BY SIDE WHILE A MASK IS PAINTED. The wipe answers "how does the painting differ
//     from the photograph", which is not the question you ask while marking up a passage --
//     there the photograph has to be whole and still, and the divider moves whenever a click
//     lands on it. So Layers splits the box; the tag, the divider and the render width all
//     have to follow, and the last of those is the one that costs money if it does not.
$('#toolNone').fire('click', {});
const clipBefore = $('#top').style.clipPath;
const isSplit = () => [...$('#wipe').classList._s].includes('split');
out.splitOff = { split: isSplit(), tag: $('#tagL').textContent };
// A render taken with NO tool armed. Every tool splits the box now, so the last render in
// the log was very likely a split one already -- and comparing a split render against a
// split render would say nothing while looking like it said something.
moveRow('target_n', 8000);
await settle(900);
const beforeSplitRender = renders().pop() || '';
$('#rgnBtn').fire('click', {});
out.splitOn = { split: isSplit(), tag: $('#tagL').textContent };
// THE DIVIDER STANDS DOWN. A click on the photograph outside a tool's own canvas is exactly
// what used to slide a bar across the thing being marked up.
$('#wipe').fire('pointerdown', { clientX: 400, pointerId: 1 });
out.splitClipHeld = $('#top').style.clipPath === clipBefore;
// ...and the painting is computed for the PANE. Half the width on screen must not mean a
// full-width render: that is double the cost of every render for no visible pixel.
moveRow('target_n', 9000);
await settle(900);
out.splitRender = renders().pop() || '';
out.splitPrevRender = beforeSplitRender;
// ONE WRITER FOR THE TAG. The upload names the file and the split view says what is drawn
// over it, and two authors appending to whatever they find there is how a tag ends up with
// the suffix twice, or with the file name gone. Loading a picture with Layers open is the
// case that showed it.
$('#file').fire('change',
  { target: { files: [new File([], 'other.jpg', { type: 'image/jpeg' })] } });
await settle(320);
out.splitTagAfterUpload = $('#tagL').textContent;
$('#rgnBtn').fire('click', {});
out.splitAfter = { split: isSplit(), tag: $('#tagL').textContent };

// EVERY TOOL, NOT JUST LAYERS. The rule a visitor should have to learn is one rule -- the
// left pane is what you mark, the right pane is what it makes -- so a tool that kept the
// wipe would teach that rule and then break it. The tag names what is on the left pane,
// which is the only part of the view that differs between the three.
const armed = {};
for (const [name, btn] of [['fov', '#fovBtn'], ['rgn', '#rgnBtn'], ['vtx', '#vtxBtn']]) {
  $('#toolNone').fire('click', {});
  const off = isSplit();
  $(btn).fire('click', {});
  armed[name] = { off, split: isSplit(), tag: $('#tagL').textContent };
}
$('#toolNone').fire('click', {});
armed.none = { split: isSplit(), tag: $('#tagL').textContent };
out.splitEveryTool = armed;

// 5e. THE BRUSH. The fill asks the photograph where a passage ends, which is no question at
//     all for a face against a busy background or half a sky, so a layer can also be painted
//     by hand. Everything below the mark is shared with the fill -- the target, the erase
//     chip, the per-pixel writer, the commit -- so what is driven here is the half that is
//     not: the mode switch, the stroke lifecycle, and one commit per stroke rather than one
//     per dab.
$('#rgnBtn').fire('click', {});
// A layer of its own rather than whichever one survived the blocks above: 5d reloads the
// picture, and a drive that depends on what a previous drive left behind is a drive that
// fails for a reason unrelated to what it is testing.
$('#rgnAdd').fire('click', {});
$('#rgnErase').checked = false; $('#rgnErase').fire('change', {});
out.brushOptsBefore = { fill: $('#rgnFillOpts').hidden, brush: $('#rgnBrushOpts').hidden };
$('#rgnModeBrush').fire('click', {});
out.brushOptsAfter = { fill: $('#rgnFillOpts').hidden, brush: $('#rgnBrushOpts').hidden,
                       on: [...$('#rgnModeBrush').classList._s],
                       off: [...$('#rgnModeFill').classList._s] };
out.brushGuide = $('#guide').innerHTML;
// A STROKE, not a click: down, two moves, up. The count in the status line is the whole
// stroke's, which is what says the dabs accumulated into one edit rather than replacing
// each other.
const stroke = (id, pts) => {
  rgnCanvas.fire('pointerdown', { clientX: pts[0][0], clientY: pts[0][1], pointerId: id });
  for (const [x, y] of pts.slice(1))
    rgnCanvas.fire('pointermove', { clientX: x, clientY: y, pointerId: id });
  return () => rgnCanvas.fire('pointerup',
    { clientX: pts[pts.length - 1][0], clientY: pts[pts.length - 1][1], pointerId: id });
};
// FIRST, ON A LAYER THAT IS STILL INERT. A new layer is seeded from the panel, so until one
// of its rows moves it grades exactly what the base does -- provably, by the equivalence
// invariant of oilpaint/regions.py -- and painting it must not re-render. The fill has this
// check already; the brush is a second way in to the same promise.
const beforeInert = renders().length;
stroke(2, [[120, 80], [180, 100], [240, 130]])();
await settle(900);
out.brushInertNoRender = renders().length === beforeInert;

// NOW WITH THE LAYER LIVE. Moving one of its rows makes it disagree with the panel, which
// is what "live" means, and a stroke must then reach the engine.
moveRow('warm_cool', 0.22);
await settle(900);
const beforeBrush = renders().length;
const release = stroke(3, [[300, 150], [340, 170], [380, 200]]);
// ONE COMMIT PER STROKE. A dab is microseconds and a render is a second, so a mid-drag
// commit would make the brush unusable -- and it would be invisible from anywhere but here.
out.brushMidDrag = renders().length === beforeBrush;
release();
// READ BEFORE THE RENDER OVERWRITES IT. rgnCommit sets the status synchronously and then
// schedules; the render's own "draft - refining..." lands on the same line a moment later,
// so waiting first reads the wrong sentence and fails for a reason unrelated to the brush.
out.brushStatus = $('#status').textContent;
await settle(900);
out.brushRendered = renders().length > beforeBrush;
// The same brush with the erase chip on wipes, exactly as the fill does -- and wipes only
// what the SELECTION owns. Erasing used to write the base over every pixel under the brush
// whatever layer it belonged to, so tidying one layer's edge silently ate the layer
// underneath, and the only way to find out was to go and look at a layer you were not
// working on. Both halves are driven: the selected layer's pixels go...
$('#rgnErase').checked = true; $('#rgnErase').fire('change', {});
stroke(4, [[150, 90], [170, 110]])();
out.brushEraseStatus = $('#status').textContent;      // synchronous, as above
await settle(400);

// ...and the same place, erased with a DIFFERENT layer selected, gives nothing up. Paint
// into layer 1 somewhere fresh, add a second layer, select it, and wipe over layer 1's
// pixels: the status has to say nothing was taken rather than report the pixels the brush
// walked over -- the disc of a dab is what the caller counts, and it is no longer what an
// erase writes.
$('#rgnErase').checked = false; $('#rgnErase').fire('change', {});
lyrRow(1).fire('click', {});
stroke(5, [[152, 92], [168, 108]])();
await settle(400);
out.eraseSetup = $('#status').textContent;
$('#rgnAdd').fire('click', {});
await settle(140);
const other = ($('#rgnLayers')._kids || []).map(r => Number(r.dataset.id))
                                           .filter(id => id && id !== 1)[0];
out.eraseOtherLayerId = other ?? null;
if(other){
  lyrRow(other).fire('click', {});
  $('#rgnErase').checked = true; $('#rgnErase').fire('change', {});
  stroke(6, [[152, 92], [168, 108]])();
  out.eraseOtherLayer = $('#status').textContent;
  await settle(400);
}
await settle(400);
$('#rgnModeFill').fire('click', {});
out.brushOptsBack = { fill: $('#rgnFillOpts').hidden, brush: $('#rgnBrushOpts').hidden };
$('#rgnBtn').fire('click', {});

// 5f. FULL SCREEN: A POINTER LANDS WHERE IT LOOKS LIKE IT LANDS.
//     Every tool used to map a pointer as `(clientX - left) / width * bitmapWidth`, which
//     says the bitmap fills its element box. In the window it does; in full screen the box
//     is 100vw x 100vh and the picture is LETTERBOXED inside it, so the mark appeared some
//     way from the pointer -- in all three tools, and only in the mode you enter to place
//     something precisely. The shim's rect is the same for every element, so the letterbox
//     is made here rather than waited for: a square box around a wide picture is the case
//     that separates the two formulas.
$('#toolNone').fire('click', {});
pickRow('flow', 'starry');
await settle(900);
$('#vtxBtn').fire('click', {});
$('#vtxClear').fire('click', {});
await settle(600);
const vc = $('#vtxCanvas');
out.fsCanvas = [vc.width, vc.height];
vc.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 1000 });
const vClick = (x, y, id) => {
  vc.fire('pointerdown', { clientX: x, clientY: y, pointerId: id });
  vc.fire('pointerup', { clientX: x, clientY: y, pointerId: id });
};
vClick(250, 400, 7);
await settle(900);
$('#setupBtn').fire('click', {});
await settle(120);
out.fsSetup = lastCopy;
// ...and the black beside the picture is not somewhere a mark can be made. It used to be
// clamped to the nearest edge pixel and quietly acted on.
vClick(250, 100, 8);
await settle(600);
out.fsBarCount = $('#vtxCount').textContent;
$('#vtxBtn').fire('click', {});

// 5b. THE LOOKS STRIP. Two things can go wrong here and neither is visible from outside:
// a look that writes past the layer selection (so the rows the visitor is looking at are
// not the rows that moved), and a look that sets only the fields it cares about (so the
// previous look's flow keeps running under it). Both are read off the WIRE, because that
// is the only place that says what was actually painted.
const lkBtns = () => ($('#lkRow')._kids || []);
const lkClick = k => { const b = lkBtns().find(x => x.dataset.look === k); if (b) b.fire('click', {}); };
out.looksBuilt = lkBtns().map(b => b.dataset.look);
out.looksHidden = $('#looks').hidden;

lkClick('vangogh');
await settle(700);
out.lookVanGogh = renders().pop() || '';

// THE INVARIANT LOOK_OFF EXISTS FOR. `photo` names nothing of its own -- its `set` is
// `lookSet({})` -- so if a look carried only the fields it declared, van Gogh's `starry`
// would still be combing the paint underneath it. It must arrive with flow=none.
lkClick('photo');
await settle(700);
out.lookZorn = renders().pop() || '';

// A look is a whole-picture decision, so it goes to the BASE and moves the selection there
// to say so -- rather than writing past a layer selection that then disagrees with the
// rows on screen.
const someLayer = ($('#rgnLayers')._kids || []).map(r => r.dataset.id).find(x => x !== '0');
if (someLayer) {
  lyrRow(Number(someLayer)).fire('click', {});
  out.lookSelBefore = [...lyrRow(Number(someLayer)).classList._s].includes('on');
  lkClick('monet');
  await settle(700);
  out.lookSelAfter = [...lyrRow(0).classList._s].includes('on');
  out.lookBaseWire = (out.events.filter(e => e.startsWith('base ')).pop() || '');
}

// 5c. UNDO. Its failure mode is silent in the one way that matters: an undo that restores
// the mask PIXELS but not the layer LIST leaves a picture full of passages no panel row can
// reach, and from outside it looks exactly like an undo that worked. So the list is what is
// read back, and `Delete all` -- the one click on this page that can throw away an
// afternoon -- is what it is read back from.
$('#rgnBtn').fire('click', {});
await settle(120);
$('#rgnAdd').fire('click', {});
await settle(200);
out.undoLayersBefore = ($('#rgnLayers')._kids || []).map(r => r.dataset.id);
out.undoArmed = $('#rgnUndo').disabled === false;
$('#rgnClear').fire('click', {});
await settle(300);
out.undoLayersWiped = ($('#rgnLayers')._kids || []).map(r => r.dataset.id);
$('#rgnUndo').fire('click', {});
// Read SYNCHRONOUSLY, for the reason brushStatus is: `schedule()` puts a render 160 ms
// behind this, and 'draft...' then owns the line. The message is for the moment of the
// click, and that is the moment to read it in.
out.undoStatus = $('#status').textContent;
await settle(400);
out.undoLayersBack = ($('#rgnLayers')._kids || []).map(r => r.dataset.id);
$('#rgnBtn').fire('click', {});
await settle(120);

// ...and the swirl centres, which are a list rather than a canvas and so take the other
// arm of undoSnap entirely.
$('#vtxBtn').fire('click', {});
await settle(200);
// The tool is live only when its target carries a swirl field, and section 5b left the
// panel on a look whose flow is `none` -- so a click here would be correctly inert and the
// undo below would pop an entry belonging to another tool. The Van Gogh look is the
// shortest way to a swirl field on the BASE, and this drive has already proven that path.
lkClick('vangogh');
await settle(700);
const vtxBefore = $('#vtxCount').textContent;
// (300, 500) AND NOT (300, 200), which is the letterbox. The full-screen section above
// replaces this canvas's getBoundingClientRect with a 1000x1000 SQUARE box around a
// 1000x561 picture and never puts it back, so the picture occupies y 219.5..780.5 and
// everything outside that is black the tools correctly refuse to mark. A click there
// places nothing, which would leave the undo below popping another tool's entry.
vClick(300, 500, 21);
await settle(500);
out.undoVtxPlaced = $('#vtxCount').textContent;
$('#vtxUndo').fire('click', {});
await settle(500);
out.undoVtxBack = [vtxBefore, $('#vtxCount').textContent];
$('#vtxBtn').fire('click', {});
await settle(120);

// 5d. THE APP SHELL. `shellSync` is the single owner of the threshold -- there is no media
// query for it -- so what has to be true is that the stamp follows the query, and that the
// grip writes the one number the layout AND sizeWipe both read.
// `document.body` and `document.documentElement` are their own elements in this shim, NOT
// the ones `$('body')` / `$('html')` would mint -- `$` caches by selector string and has
// never seen either. Reading the wrong one gives a page that looks unstamped while working
// perfectly, which is a harness bug that accuses the code.
out.shellStamped = [...globalThis.document.body.classList._s].includes('shell');
// WHERE THE PICTURE'S HEIGHT BOUND CAME FROM, as a number. Stacked, it is 62% of the
// viewport (900 -> 558) and the 700px stage width wins; in the shell it is the stage's own
// box (394 - 28 = 366) and the height wins instead. The two layouts must NOT agree here --
// that they differ is the whole change.
out.wipeWidth = $('#wipe').style.width;
const railNow = () => globalThis.document.documentElement.style.getPropertyValue('--rail');
const grip = $('#railGrip');
grip.fire('pointerdown', { clientX: 1000, pointerId: 41 });
// Dragging LEFT widens: the handle is on the rail's left edge and the rail is anchored to
// the right of the window, so the delta is subtracted.
grip.fire('pointermove', { clientX: 900, pointerId: 41 });
out.railWider = railNow();
// ...and it is clamped, or a drag past the pane would leave a canvas with no width.
grip.fire('pointermove', { clientX: 2000, pointerId: 41 });
out.railClamped = railNow();
grip.fire('pointerup', { clientX: 2000, pointerId: 41 });
await settle(200);
out.railReleased = [...grip.classList._s].includes('on');

// 5e. THE DOCKED LAYERS PANEL. The list, the readout and the prose used to sit in the bar
// above the canvas -- ~170px of the one thing the page is for, spent by arming the tool
// that needs the picture most. In the rail they cost the canvas nothing, and the panel can
// then outlive the tool, which is the half with a real claim in it: the selection is what
// re-points the Palette and Flow rows, and those rows are live whether or not a paint tool
// happens to be open.
const lyrShown = () => $('#lyrPanel').hidden === false;
// Start from nothing: arm, wipe, close.
$('#rgnBtn').fire('click', {});
await settle(150);
$('#rgnClear').fire('click', {});
await settle(300);
$('#toolNone').fire('click', {});
await settle(150);
out.lyrIdle = lyrShown();
$('#rgnBtn').fire('click', {});
await settle(150);
out.lyrArmed = [lyrShown(), $('#lyrCount').textContent];
$('#rgnAdd').fire('click', {});
await settle(250);
out.lyrWithLayer = [lyrShown(), $('#lyrCount').textContent];
// THE CLAIM. Putting the tool away must not take the layer list with it.
$('#toolNone').fire('click', {});
await settle(200);
out.lyrOutlivesTool = [lyrShown(), $('#lyrCount').textContent];

// Switching inspector tabs preserves the exact control nodes and keeps one panel active.
const paletteCard = $('#cards')._kids[0];
$('#tab-flow').fire('click', {});
out.inspectorFlow = ['color','strokes','flow','light'].map(k => $('#panel-' + k).hidden);
$('#tab-flow').fire('keydown', {key:'Home'});
out.inspectorHome = ['color','strokes','flow','light'].map(k => $('#panel-' + k).hidden);
out.inspectorStable = paletteCard === $('#cards')._kids[0];

// 5g. THE PROJECT FILE. The format belongs to oilpaint/project.py, and the whole value of
// that is that a file saved here is a file `scripts/paint.py --project` takes. So the page's
// own output is handed back to verify_page.py, which parses it with the REAL Python reader
// -- a page that wrote a plausible-looking document Python refused would otherwise pass
// every check either side could make on its own.
// A MASK PAINTED SOMEWHERE ELSE, which is the workflow the bundle exists for: drawn in an
// image editor at the photograph's own size, loaded here, and -- the part with a trap in it
// -- handed BACK OUT unharmed. This page paints its masks at 512px, so re-exporting one
// from the canvas would silently throw away everything finer than that. The shimmed
// FileReader hands back `data:image/jpeg;base64,AAAA` while a canvas gives `data:,`, so the
// two sources are told apart by their bytes rather than by trusting the flag.
$('#rgnFile').fire('change', { target: { files: [new File([], 'hand.png',
                                                          { type: 'image/png' })], value: '' } });
await settle(160);
out.rgnFileLoaded = $('#status').textContent;
$('#fovFile').fire('change', { target: { files: [new File([], 'hand-focus.png',
                                                          { type: 'image/png' })], value: '' } });
await settle(160);
out.fovFileLoaded = $('#status').textContent;

// SAVE PROJECT WRITES A BUNDLE: the recipe, and each mask beside it as an ordinary PNG.
// Snapshot `made` first -- it accumulates every element the run has ever created, so a
// filter over the whole list would pick up anchors from earlier sections and could not tell
// how many files THIS click wrote, which is the thing being checked.
const beforeSave = made.length;
$('#projSave').fire('click', {});
await settle(200);
const wrote = made.slice(beforeSave).filter(e => typeof e.download === 'string' && e.download);
out.bundleFiles = wrote.map(e => e.download);
// The bytes each sidecar was written FROM: the loaded file, or this page's canvas.
out.bundleFrom = wrote.filter(e => /\.png$/i.test(e.download))
                      .map(e => [e.download, String(e._data).slice(0, 24)]);
const dl = wrote.find(e => e.download.endsWith('.oilpaint.json'));
out.projectSaved = !!dl;
out.projectName = dl ? dl.download : '';
out.projectJson = dl ? decodeURIComponent(String(dl._data).split(',').slice(1).join(',')) : '';

// ...and back in. A project names some fields and leaves the rest alone, so the check is
// that a named one MOVES and reaches the engine -- reading it off the wire, because a page
// that updated its own panel and never told the worker looks identical from the panel.
let imported = null;
try{
  const doc = JSON.parse(out.projectJson);
  doc.params.flow = 'hatch';
  doc.params.target_n = 4321;
  imported = JSON.stringify(doc);
}catch(e){ out.errors.push('project reparse: ' + e.message); }
if(imported){
  const pf = $('#projFile');
  const pngs = out.bundleFiles.filter(n => /\.png$/i.test(n));
  out.bundleRefs = (() => { try{ return JSON.parse(out.projectJson).masks || null; }
                            catch(e){ return null; } })();

  // THE SIDECAR THAT DID NOT COME WITH IT, first. A bundle whose PNGs were left behind
  // must SAY which file it wanted: the alternative is a project that loads its layers with
  // no pixels, which on screen is indistinguishable from a mask that was empty. Driven
  // before the good case so the run ends in the loaded state rather than this one.
  pf.fire('change', { target: { files: [new File([imported], 'y.oilpaint.json')], value: '' } });
  await settle(120);
  out.bundleMissing = $('#status').textContent;

  // ...and now the whole bundle, the .json and its masks selected together, which is the
  // only way a browser can resolve a relative name: it is handed files, never a folder.
  pf.fire('change', { target: { files: [new File([imported], 'x.oilpaint.json'),
                                        ...pngs.map(n => new File([], n, { type: 'image/png' }))],
                                value: '' } });
  // The status line first, and briefly: importing ends in `schedule()`, which puts a render
  // 160 ms behind it, and 'draft...' then owns the line. Same transient as every other edit
  // on this page -- see brushStatus.
  await settle(80);
  out.projectStatus = $('#status').textContent;
  await settle(900);
  out.projectImported = renders().pop() || '';
}

// PAINT OVER IT and the loaded bytes must be let go: from the first dab the canvas is the
// mask, and handing the old file back would export a picture the page is no longer showing.
rgnCanvas.fire('pointerdown', { clientX: 260, clientY: 130 });
await settle(160);
const beforeRe = made.length;
$('#projSave').fire('click', {});
await settle(200);
out.bundleAfterPaint = made.slice(beforeRe)
                           .filter(e => typeof e.download === 'string'
                                     && /\.png$/i.test(e.download))
                           .map(e => [e.download, String(e._data).slice(0, 24)]);

// 5h. COPY SETUP HAS TO WRITE SOMETHING THAT OPENS. It used to write {params, vortices,
// regions} -- a project's three keys with no `format` and no `version`, refused by both
// readers -- and to drop both masks without saying so. Both halves are checked: the text
// is handed to the real Python reader by verify_page.py, and the status line has to name
// what the clipboard could not carry.
$('#setupBtn').fire('click', {});
await settle(60);
out.copySetup = lastCopy;
out.copyStatus = $('#status').textContent;

// 5i. THE FOCUS MAP'S OWN DOOR, both ways -- the pair the region mask has always had.
const beforeFov = made.length;
$('#fovSave').fire('click', {});
await settle(60);
out.fovSaved = made.slice(beforeFov)
                   .filter(e => typeof e.download === 'string' && e.download)
                   .map(e => e.download);
$('#fovFile').fire('change', { target: { files: [new File([], 'hand-painted.png',
                                                          { type: 'image/png' })], value: '' } });
await settle(160);
out.fovLoaded = $('#status').textContent;

// 6. The page must have armed its own error reporting, whatever else happened.
out.reportsErrors = (globalThis.window._h.error || []).length > 0
  && (globalThis.window._h.unhandledrejection || []).length > 0;

} catch (e) {
  out.errors.push(`drive: ${(e && e.stack) || e}`);
  out.driveFailed = true;
}

writeFileSync(outPath, JSON.stringify(out, null, 1));
