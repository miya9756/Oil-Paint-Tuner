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
    // `setProperty` because the layers panel sets a CSS custom property for the selected
    // layer's colour, which is what `style` is for and what a bare object is not.
    style: { setProperty() {}, removeProperty() {}, getPropertyValue: () => '' },
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
    appendChild(c) { this._kids.push(c); return c; }, remove() {},
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

const els = new Map();
const $ = sel => {
  if (!els.has(sel)) els.set(sel, makeEl(sel.replace('#', '')));
  return els.get(sel);
};

globalThis.document = {
  querySelector: $, querySelectorAll: () => [],
  getElementById: id => $('#' + id),
  createElement: () => makeEl('created'),
  body: makeEl('body'),
  addEventListener() {},
};
globalThis.window = {
  _h: {}, devicePixelRatio: 2,
  addEventListener(t, f) { (this._h[t] = this._h[t] || []).push(f); },
  prompt() { return null; },
};
globalThis.getComputedStyle = () => ({ fontFamily: 'serif' });
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
globalThis.File = class { constructor(bits, name, opts) { this.name = name; this.type = (opts && opts.type) || ''; this.size = 4; } };
globalThis.FileReader = class {
  readAsDataURL() {
    queueMicrotask(() => { this.result = 'data:image/jpeg;base64,AAAA'; this.onload && this.onload(); });
  }
};
// restoreImage() re-fetches the stored data URL and loadSample() fetches the shipped jpeg;
// both only need a blob with a size, because the bytes go straight back into the shimmed
// FileReader above.
globalThis.fetch = async () => ({ ok: true, status: 200,
                                  blob: async () => ({ size: 4, type: 'image/jpeg' }) });
// requestAnimationFrame is shimmed but never pumped: the only frame the page schedules is
// the wipe's own drag, which this harness does not start. It exists so that a page CALLING
// rAF does not throw on a runtime that has none -- which would look like a page bug.
globalThis.requestAnimationFrame = () => 0;

// --- the fake engine ---------------------------------------------------------------------
let served = 0;
globalThis.Worker = class {
  constructor() { this.onmessage = null; this._l = {}; out.events.push('worker started'); }
  addEventListener(t, f) { (this._l[t] = this._l[t] || []).push(f); }
  postMessage(msg) {
    const reply = m => queueMicrotask(() =>
      (this._l.message || []).forEach(f => f({ data: m })));
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
      out.events.push(`base warm_cool=${msg.params.warm_cool} flow=${msg.params.flow}`);
      for (const k of Object.keys(msg.regionOverrides || {})) {
        const ov = msg.regionOverrides[k];
        // TWO probe fields, one per half of the model: a colour one, which must never move
        // the geometry, and a flow one, which must move only this passage's. They are read
        // off the wire for the same reason -- a row writing to the wrong half looks exactly
        // like a row that works.
        out.events.push(`region ${k} warm_cool=${ov.warm_cool} flow=${ov.flow} `
          + `fields=${Object.keys(ov).sort().join(',')}`);
      }
      const px = new Uint8ClampedArray(msg.w * msg.h * 4).fill((served++ % 250) + 1);
      // Per OWNER, not a total: the whole claim of the per-layer swirls is that a point
      // lands in the set the selection names, and a count alone cannot tell "2 for the
      // picture" from "2 for Region 1" -- which is the one thing that can go wrong.
      // `fov` is appended rather than inserted: every check above matches this line by
      // substring, and a field in the middle would rewrite what those matches mean.
      out.events.push(`render ${msg.w}x${msg.h} flow=${msg.params.flow}`
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

// 1c. THE CARDS. Which control sits on which card is decided by FRONT_GROUPS, HIDDEN and
//     FINE in the page, and the only way to see the decision is to build the panel.
const cardList = host => host._kids.map(c => ({
  name: (c.innerHTML.match(/<h2>(.*?)<em>/) || [])[1],
  // Controls only. A card can also carry a TOOL button (see CARD_TOOLS in the page), which
  // has no data-name -- read separately below, because "which controls are on this card" and
  // "which tools are" are two different questions and one list cannot answer both.
  rows: c._kids.map(r => r.dataset.name).filter(Boolean),
  tools: c._kids.map(r => r._id).filter(x => x && x !== 'created') }));
out.frontCards = cardList($('#cards'));
out.advCards = cardList($('#advCards'));
// THE ROW MARKUP ITSELF. Every row used to be printed as its Python identifier; the label
// now leads and the identifier follows it, and the second half is the one worth pinning --
// `target_n` is what scripts/paint.py takes and what `Copy setup` writes, so a redesign
// that tidied it away would cut the path from the page to the command line.
const allCards = $('#cards')._kids.concat($('#advCards')._kids);
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
// The same brush with the erase chip on goes to the base, exactly as the fill does.
$('#rgnErase').checked = true; $('#rgnErase').fire('change', {});
stroke(4, [[150, 90], [170, 110]])();
out.brushEraseStatus = $('#status').textContent;      // synchronous, as above
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

// 6. The page must have armed its own error reporting, whatever else happened.
out.reportsErrors = (globalThis.window._h.error || []).length > 0
  && (globalThis.window._h.unhandledrejection || []).length > 0;

} catch (e) {
  out.errors.push(`drive: ${(e && e.stack) || e}`);
  out.driveFailed = true;
}

writeFileSync(outPath, JSON.stringify(out, null, 1));
