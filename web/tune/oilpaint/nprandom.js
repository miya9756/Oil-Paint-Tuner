// numpy's `default_rng(seed)`, reimplemented exactly.
//
// This is the load-bearing piece of the browser port. `oilpaint/strokes.py` draws every
// jitter, every log-normal size, every colour perturbation and the paint-order shuffle from
// `np.random.default_rng(seed)`. If this file is merely a good random number generator
// rather than THE one numpy uses, the seed in the control panel stops meaning anything: the
// same seed would give a different painting in the browser than in the Python, and every
// downstream parity test would have to be weakened from "same pixels" to "similar
// statistics" (.claude/skills/python-js-parity/SKILL.md rule 2).
//
// So it is bit-exact, and tests/test_rng_parity.py proves it against numpy for every method
// the pipeline uses. Three pieces have to match, not one:
//
//   1. SeedSequence -- how an integer seed becomes 128 bits of PCG state and increment.
//   2. PCG64 (XSL-RR 128/64) -- the bit generator itself, in 128-bit arithmetic.
//   3. The distribution methods -- uniform, random, standard_normal (ziggurat).
//
// 128-bit arithmetic is done with BigInt. It is the only correct option in JS (doubles lose
// bits above 2^53) and it is not the bottleneck: a full render draws on the order of 10^4
// values, which costs single-digit milliseconds.

import { KI, WI, FI, ZIGGURAT_NOR_R, ZIGGURAT_NOR_INV_R } from './ziggurat_tables.js';

const M64 = (1n << 64n) - 1n;
const M128 = (1n << 128n) - 1n;
const M32 = 0xffffffff;

// ---- SeedSequence ------------------------------------------------------------------
// numpy/random/bit_generator.pyx. All arithmetic is uint32, which in JS means Math.imul for
// the multiplies and `>>> 0` to get back to unsigned after every step.
const XSHIFT = 16;
const INIT_A = 0x43b0d7e5;
const MULT_A = 0x931e8875;
const INIT_B = 0x8b51f9dd;
const MULT_B = 0x58f38ded;
const MIX_MULT_L = 0xca01f9dd;
const MIX_MULT_R = 0x4973f715;
const POOL_SIZE = 4;

function hashmix(value, hc) {
  value = (value ^ hc.a) >>> 0;
  hc.a = Math.imul(hc.a, MULT_A) >>> 0;
  value = Math.imul(value, hc.a) >>> 0;
  value = (value ^ (value >>> XSHIFT)) >>> 0;
  return value;
}

function mix(x, y) {
  // `MIX_MULT_L * x - MIX_MULT_R * y` in uint32, then an xorshift.
  let r = (Math.imul(MIX_MULT_L, x) - Math.imul(MIX_MULT_R, y)) >>> 0;
  r = (r ^ (r >>> XSHIFT)) >>> 0;
  return r;
}

// numpy's `_coerce_to_uint32_array`: a non-negative integer becomes its little-endian
// uint32 words, with a single zero word for 0.
function coerceEntropy(seed) {
  let v = BigInt(seed);
  if (v < 0n) throw new Error('seed must be non-negative');
  const words = [];
  while (v > 0n) {
    words.push(Number(v & 0xffffffffn) >>> 0);
    v >>= 32n;
  }
  return words.length ? words : [0];
}

function mixEntropy(entropy) {
  const hc = { a: INIT_A };
  const pool = new Uint32Array(POOL_SIZE);
  for (let i = 0; i < POOL_SIZE; i++) {
    pool[i] = hashmix(i < entropy.length ? entropy[i] : 0, hc);
  }
  for (let src = 0; src < POOL_SIZE; src++) {
    for (let dst = 0; dst < POOL_SIZE; dst++) {
      if (src !== dst) pool[dst] = mix(pool[dst], hashmix(pool[src], hc));
    }
  }
  // Entropy beyond the pool size is folded in afterwards. A plain integer seed never
  // reaches this, but a large one does, and silently ignoring the high words would make
  // two different seeds collide.
  for (let src = POOL_SIZE; src < entropy.length; src++) {
    for (let dst = 0; dst < POOL_SIZE; dst++) {
      pool[dst] = mix(pool[dst], hashmix(entropy[src], hc));
    }
  }
  return pool;
}

/** numpy's `SeedSequence(seed).generate_state(nWords, uint64)`. */
export function generateState64(seed, nWords) {
  const pool = mixEntropy(coerceEntropy(seed));
  const n32 = nWords * 2;
  const out32 = new Uint32Array(n32);
  let hashConst = INIT_B;
  for (let i = 0; i < n32; i++) {
    let v = pool[i % POOL_SIZE];
    v = (v ^ hashConst) >>> 0;
    hashConst = Math.imul(hashConst, MULT_B) >>> 0;
    v = Math.imul(v, hashConst) >>> 0;
    v = (v ^ (v >>> XSHIFT)) >>> 0;
    out32[i] = v;
  }
  // `state.view(uint64)` on a little-endian machine: low word first.
  const out = new Array(nWords);
  for (let i = 0; i < nWords; i++) {
    out[i] = (BigInt(out32[2 * i + 1]) << 32n) | BigInt(out32[2 * i]);
  }
  return out;
}

// ---- PCG64 (XSL-RR 128/64) ---------------------------------------------------------
const PCG_MULT = 0x2360ed051fc65da44385df649fccf645n;

class PCG64 {
  constructor(seed) {
    // generate_state(4, uint64) -> [initstate_hi, initstate_lo, initseq_hi, initseq_lo]
    const s = generateState64(seed, 4);
    const initstate = ((s[0] << 64n) | s[1]) & M128;
    const initseq = ((s[2] << 64n) | s[3]) & M128;
    this.inc = ((initseq << 1n) | 1n) & M128;
    this.state = 0n;
    this.step();
    this.state = (this.state + initstate) & M128;
    this.step();
    this.hasCached = false;
  }

  step() {
    this.state = (this.state * PCG_MULT + this.inc) & M128;
  }

  /** One uint64, as a BigInt. */
  nextUint64() {
    this.step();
    const s = this.state;
    // XSL-RR output: fold the halves together, then rotate by the top 6 bits.
    const xored = ((s >> 64n) ^ s) & M64;
    const rot = Number(s >> 122n);
    if (rot === 0) return xored;
    return ((xored >> BigInt(rot)) | (xored << BigInt(64 - rot))) & M64;
  }

  /** numpy's `next_double`: the top 53 bits scaled into [0, 1). */
  nextDouble() {
    return Number(this.nextUint64() >> 11n) * (1.0 / 9007199254740992.0);
  }
}

// ---- Generator ---------------------------------------------------------------------
export class Generator {
  constructor(seed) {
    this.bg = new PCG64(seed);
  }

  /** `rng.random(n)` -- n doubles in [0, 1). */
  random(n) {
    const out = new Float64Array(n);
    for (let i = 0; i < n; i++) out[i] = this.bg.nextDouble();
    return out;
  }

  /** `rng.uniform(low, high, n)`. numpy computes `low + range * next_double`. */
  uniform(low, high, n) {
    const out = new Float64Array(n);
    const range = high - low;
    for (let i = 0; i < n; i++) out[i] = low + range * this.bg.nextDouble();
    return out;
  }

  /**
   * `rng.standard_normal(n)` -- numpy's `random_standard_normal`, the 256-level ziggurat.
   *
   * Transcribed from numpy/random/src/distributions/distributions.c. The rejection
   * branches are hit ~1% of the time and the tail ~0.3%, and both call `exp`/`log1p`,
   * where a JS engine and CPython's libm may differ in the last bit. A divergence needs
   * that last bit to also straddle the comparison, which the parity test measures rather
   * than assumes: over 2 million draws it finds none.
   */
  standardNormal(n) {
    const out = new Float64Array(n);
    for (let i = 0; i < n; i++) out[i] = this.nextNormal();
    return out;
  }

  nextNormal() {
    for (;;) {
      const r = this.bg.nextUint64();
      const idx = Number(r & 0xffn);
      const r8 = r >> 8n;
      const sign = Number(r8 & 0x1n);
      const rabsBig = (r8 >> 1n) & 0x000fffffffffffffn;
      const rabs = Number(rabsBig);
      let x = rabs * WI[idx];
      if (sign) x = -x;
      if (rabs < KI[idx]) return x;

      if (idx === 0) {
        for (;;) {
          // 1.0 - U rather than U, to avoid log(0) -- numpy GH 13361.
          const xx = -ZIGGURAT_NOR_INV_R * Math.log1p(-this.bg.nextDouble());
          const yy = -Math.log1p(-this.bg.nextDouble());
          if (yy + yy > xx * xx) {
            return ((rabsBig >> 8n) & 0x1n)
              ? -(ZIGGURAT_NOR_R + xx)
              : ZIGGURAT_NOR_R + xx;
          }
        }
      } else {
        if ((FI[idx - 1] - FI[idx]) * this.bg.nextDouble() + FI[idx]
            < Math.exp(-0.5 * x * x)) {
          return x;
        }
      }
    }
  }
}

/** `np.random.default_rng(seed)`. */
export function defaultRng(seed) {
  return new Generator(seed);
}
