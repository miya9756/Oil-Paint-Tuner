// node harness for tests/test_rng_parity.py. Runs the REAL nprandom.js.
//
//     node web/tune/rng_node.mjs job.json out.json

import { readFileSync, writeFileSync } from 'node:fs';
import { generateState64, defaultRng } from './oilpaint/nprandom.js';

const [, , jobPath, outPath] = process.argv;
const job = JSON.parse(readFileSync(jobPath, 'utf8'));
const result = {};

// stage 1: SeedSequence, as hex so 64-bit values survive JSON
result.seedState = {};
for (const seed of job.seeds) {
  result.seedState[seed] = generateState64(seed, 4).map(v => v.toString(16));
}

// stage 2: the PCG64 stream itself, and the derived doubles
result.uint64 = {};
result.random = {};
for (const seed of job.seeds) {
  const g = defaultRng(seed);
  result.uint64[seed] = Array.from({ length: job.n_uint64 }, () => g.bg.nextUint64().toString(16));
  result.random[seed] = Array.from(defaultRng(seed).random(job.n_random));
}

// stage 3+: the distribution methods the pipeline actually calls
result.uniform = {};
result.normal = {};
result.mixed = {};
for (const seed of job.seeds) {
  result.uniform[seed] = Array.from(defaultRng(seed).uniform(job.uniform.low, job.uniform.high, job.n_random));
  result.normal[seed] = Array.from(defaultRng(seed).standardNormal(job.n_normal));
  // Interleaved exactly as strokes.from_cells draws them: consuming one method must leave
  // the stream positioned correctly for the next, which separate streams cannot catch.
  const g = defaultRng(seed);
  result.mixed[seed] = [
    Array.from(g.uniform(-1, 1, 5)),
    Array.from(g.random(5)),
    Array.from(g.standardNormal(5)),
    Array.from(g.uniform(0, 2 * Math.PI, 5)),
    Array.from(g.standardNormal(5)),
  ];
}

// stage 6: a long normal run, where a single last-bit disagreement in exp/log1p would show
result.normalBulk = Array.from(defaultRng(job.bulk_seed).standardNormal(job.n_bulk));

writeFileSync(outPath, JSON.stringify(result));
