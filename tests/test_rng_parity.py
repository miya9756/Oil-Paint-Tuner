"""Parity test: web/tune/nprandom.js vs numpy's `default_rng` (CPU, needs node).

    conda run -n 4dre python tests/test_rng_parity.py

`oilpaint/strokes.py` draws every jitter, size, colour perturbation and the paint order from
`np.random.default_rng(seed)`. The browser port therefore needs numpy's generator, not a
generator -- otherwise the seed in the control panel means one painting in Python and a
different one in the browser, and every downstream parity test collapses from "same pixels"
to "similar statistics".

This is the test that makes the rest possible, so it is deliberately unforgiving: the
comparisons below are EXACT, not tolerance-based. A float64 that round-trips through numpy
and through JS must be the same float64, bit for bit -- there is no f32/f64 story here to
justify slack, and a random stream that is nearly right is simply a different stream.

Stages, so a failure localises:
  1. SeedSequence      -- seed -> the four uint64 words of PCG state and increment
  2. PCG64             -- the raw uint64 stream
  3. random()          -- next_double
  4. uniform(lo, hi)   -- low + range * next_double
  5. standard_normal() -- the 256-level ziggurat, tables included
  6. interleaved       -- the draw order strokes.from_cells actually uses
  7. bulk normals      -- 2e6 draws, which is where a last-bit exp/log1p difference lands
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np  # noqa: E402

TUNE_DIR = os.path.join(_REPO_ROOT, "web", "tune")
SPACK_NODE = ("/cluster/software/stacks/2024-06/spack/opt/spack/linux-ubuntu22.04-x86_64_v3/"
              "gcc-12.2.0/node-js-19.2.0-i6pplhcf7voeure6rh5oeg7lu7cmokt7/bin/node")

SEEDS = [0, 1, 3, 42, 9973, 12345, 2 ** 31, 2 ** 64 + 7]
N_UINT64 = 32
N_RANDOM = 500
N_NORMAL = 500
N_BULK = 2_000_000
BULK_SEED = 7

FAILS = []


def find_node():
    return shutil.which("node") or (SPACK_NODE if os.path.exists(SPACK_NODE) else None)


def check(ok, label, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (f"  {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def exact(js, py, label):
    """Bit-for-bit equality. Deliberately not np.allclose -- see the module docstring."""
    a = np.asarray(js, dtype=np.float64).ravel()
    b = np.asarray(py, dtype=np.float64).ravel()
    if a.size != b.size:
        return check(False, label, f"size {a.size} != {b.size}")
    bad = int(np.count_nonzero(a != b))
    if bad:
        i = int(np.flatnonzero(a != b)[0])
        return check(False, label,
                     f"{bad}/{a.size} differ; first at {i}: js {a[i]!r} vs py {b[i]!r}")
    check(True, label, f"{a.size} values identical")


def run_node(node, job):
    with tempfile.TemporaryDirectory() as tmp:
        jp, op = os.path.join(tmp, "job.json"), os.path.join(tmp, "out.json")
        with open(jp, "w", encoding="utf-8") as fh:
            json.dump(job, fh)
        r = subprocess.run([node, "--max-old-space-size=4096",
                            os.path.join(TUNE_DIR, "rng_node.mjs"), jp, op],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError(f"node failed:\n{r.stderr[-2000:]}")
        with open(op, encoding="utf-8") as fh:
            return json.load(fh)


def main():
    node = find_node()
    if node is None:
        print("SKIP  no node on PATH (and no spack node-js) -- nprandom.js not checked")
        return 0
    print(f"node {subprocess.run([node, '--version'], capture_output=True, text=True).stdout.strip()}")
    print(f"numpy {np.__version__}\n")

    # Seeds cross as STRINGS. A seed above 2^53 does not survive JSON -> JS Number, and
    # 2**64 + 7 silently became ...552000 -- which is itself a valid seed, so the test would
    # have compared two different streams and blamed the generator.
    job = {"seeds": [str(s) for s in SEEDS], "n_uint64": N_UINT64, "n_random": N_RANDOM,
           "n_normal": N_NORMAL, "n_bulk": N_BULK, "bulk_seed": BULK_SEED,
           "uniform": {"low": -1.0, "high": 1.0}}
    got = run_node(node, job)

    print("stage 1 -- SeedSequence: seed -> PCG state and increment")
    for seed in SEEDS:
        want = [int(v) for v in np.random.SeedSequence(seed).generate_state(4, dtype=np.uint64)]
        have = [int(v, 16) for v in got["seedState"][str(seed)]]
        check(have == want, f"seed {seed}: generate_state(4, uint64)",
              "" if have == want else f"{[hex(v) for v in have]} != {[hex(v) for v in want]}")

    print("\nstage 2 -- PCG64 raw uint64 stream")
    for seed in SEEDS:
        want = np.random.default_rng(seed).integers(
            0, 2 ** 64, N_UINT64, dtype=np.uint64, endpoint=False)
        have = [int(v, 16) for v in got["uint64"][str(seed)]]
        ok = have == [int(v) for v in want]
        check(ok, f"seed {seed}: first {N_UINT64} uint64",
              "" if ok else f"first mismatch at {next(i for i, (a, b) in enumerate(zip(have, want)) if a != int(b))}")

    print("\nstage 3 -- random()")
    for seed in SEEDS:
        exact(got["random"][str(seed)], np.random.default_rng(seed).random(N_RANDOM),
              f"seed {seed}: random({N_RANDOM})")

    print("\nstage 4 -- uniform(low, high)")
    for seed in SEEDS:
        exact(got["uniform"][str(seed)],
              np.random.default_rng(seed).uniform(-1.0, 1.0, N_RANDOM),
              f"seed {seed}: uniform(-1, 1, {N_RANDOM})")

    print("\nstage 5 -- standard_normal() (the ziggurat, tables included)")
    for seed in SEEDS:
        exact(got["normal"][str(seed)],
              np.random.default_rng(seed).standard_normal(N_NORMAL),
              f"seed {seed}: standard_normal({N_NORMAL})")

    print("\nstage 6 -- interleaved draws, in strokes.from_cells order")
    for seed in SEEDS:
        rng = np.random.default_rng(seed)
        want = [rng.uniform(-1, 1, 5), rng.random(5), rng.standard_normal(5),
                rng.uniform(0, 2 * np.pi, 5), rng.standard_normal(5)]
        have = got["mixed"][str(seed)]
        for k, (h, w) in enumerate(zip(have, want)):
            exact(h, w, f"seed {seed}: interleaved call {k + 1}")

    print(f"\nstage 7 -- {N_BULK:,} normals, hunting a last-bit exp/log1p divergence")
    want = np.random.default_rng(BULK_SEED).standard_normal(N_BULK)
    have = np.asarray(got["normalBulk"], dtype=np.float64)
    exact(have, want, f"standard_normal({N_BULK})")
    # The rejection branches are what could differ, so say how often they were exercised --
    # a bulk run that never left the fast path would prove much less than it appears to.
    tail = int(np.count_nonzero(np.abs(want) > 3.6541528853610088))
    print(f"         (tail branch taken {tail:,} times, {tail / N_BULK:.3%} of draws)")

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED")
        return 1
    print("nprandom.js reproduces numpy's default_rng exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
