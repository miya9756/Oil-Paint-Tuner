// One working session, persisted across page loads.
//
// A page load starts from nothing, so without this a reload -- or, in the era when the
// animator was a separate document, a walk between pages -- opened on engine defaults with
// no picture and threw away the tuning. The pages have since been folded into one; the
// reload is the same handoff to a page with no memory, so the module stays, unchanged.
//
// TWO STORES, because the state has two very different sizes.
//
//   SETUP -- parameters and placed swirl centres. A few hundred bytes of numbers, wanted
//   synchronously while the control panel is being built. localStorage.
//   REGION MASK -- the passages the picture is divided into, as a PNG data URL. Small
//   (a few kilobytes of flat colour), but it is an IMAGE, and the rule this module already
//   states is that images go in IndexedDB: a mask over a complicated subject is not
//   bounded, and the failure mode of guessing wrong is losing the SETUP alongside it.
//
//   IMAGE -- the source photograph as the data URL the pages already hand to `img.src`.
//   A full-resolution phone photo is 5-8 MB of base64, which is past localStorage's ~5 MB
//   quota on every browser: storing it there does not degrade, it THROWS, and takes the
//   setup with it if they share a write. IndexedDB has no such limit and is asynchronous,
//   which is fine because an image load already is.
//
// Everything here is wrapped. Both stores throw outright in a private window and in a
// browser set to block site data, and a session that cannot be saved must degrade to the
// old behaviour -- re-drop the image, retune -- rather than taking the page down.

const SETUP_KEY = 'oilpaint.setup';
const DB_NAME = 'oilpaint';
const STORE = 'session';
const IMAGE_KEY = 'image';
const MASK_KEY = 'regionmask';

/** Parameters and placed swirl centres. Synchronous, small, best-effort. */
export function saveSetup(state) {
  try {
    localStorage.setItem(SETUP_KEY, JSON.stringify(Object.assign({ at: Date.now() }, state)));
    return true;
  } catch (e) {
    return false;                    // private window, or site data blocked
  }
}

export function loadSetup() {
  try {
    return JSON.parse(localStorage.getItem(SETUP_KEY) || 'null');
  } catch (e) {
    return null;                     // unreadable, or written by an older version
  }
}

export function clearSetup() {
  try { localStorage.removeItem(SETUP_KEY); } catch (e) { /* nothing to do */ }
}

function openDb() {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') return reject(new Error('no indexedDB'));
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error('indexedDB refused'));
    req.onblocked = () => reject(new Error('indexedDB blocked'));
  });
}

function tx(mode, fn) {
  return openDb().then(db => new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const req = fn(t.objectStore(STORE));
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
    t.oncomplete = () => db.close();
  }));
}

/**
 * The source image, as the data URL both pages already put in `img.src`.
 *
 * The URL rather than a Blob on purpose: it is what `upload()` produces on both pages and
 * what `<img>` consumes, so restoring is the same code path as loading, and there is no
 * second decode to get wrong.
 */
export async function saveImage(dataUrl, name) {
  try {
    await tx('readwrite', s => s.put({ dataUrl, name: name || 'image', at: Date.now() },
                                     IMAGE_KEY));
    return true;
  } catch (e) {
    return false;
  }
}

/** `{dataUrl, name}` or null. Never throws. */
export async function loadImage() {
  try {
    const v = await tx('readonly', s => s.get(IMAGE_KEY));
    return v && v.dataUrl ? v : null;
  } catch (e) {
    return null;
  }
}

export async function clearImage() {
  try { await tx('readwrite', s => s.delete(IMAGE_KEY)); } catch (e) { /* nothing to do */ }
}

/**
 * The region mask, as a PNG data URL, or null to forget it.
 *
 * A separate record from the image on purpose: a new photograph gets a new, empty mask (the
 * passages of one picture mean nothing on another), so the two have different lifetimes and
 * sharing a record would make clearing one a read-modify-write of the other.
 */
export async function saveRegionMask(dataUrl) {
  try {
    if (dataUrl === null) await tx('readwrite', s => s.delete(MASK_KEY));
    else await tx('readwrite', s => s.put({ dataUrl, at: Date.now() }, MASK_KEY));
    return true;
  } catch (e) {
    return false;
  }
}

/** The stored mask's data URL, or null. Never throws. */
export async function loadRegionMask() {
  try {
    const v = await tx('readonly', s => s.get(MASK_KEY));
    return v && v.dataUrl ? v.dataUrl : null;
  } catch (e) {
    return null;
  }
}
