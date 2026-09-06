---
name: sibling-repos
description: The two sibling repos this project sits between — 4d-relight (the Python-to-WebGL research pipeline whose architecture this one follows) and mingyang-song.github.io (the public site where it will likely be published) — including what is safely reusable, and the remote/branch/CI/LFS differences that silently break a change copied from one to the other. Read BEFORE porting code, copying config, setting up CI, or deciding where a piece of work belongs.
---

# The sibling repos

This project does not stand alone. Two existing repos on this machine already solved
most of what it is about to do, and both are worth reading before inventing anything.

| | path | what it is |
| --- | --- | --- |
| **4d-relight** | `/cluster/home/misong/4d-relight` | Research codebase: 4D Gaussian Splatting, *SmoothMotionVectors* (SIGGRAPH 2026). PyTorch + CUDA, config-first, trains on SLURM. Compresses trained scenes and **ships them to WebGL2 browser players**. |
| **the site** | `/cluster/home/misong/mingyang-song.github.io` | The public personal site. Hand-written CSS, no build step. Hosts project pages (`smv`, `spdef`, `grain`) and a **digital museum** hanging oil paintings as Gaussian splats. |

**Why this project is between them.** Its stated shape — Python prototype first, then
WebGL/WASM in the browser — *is* 4d-relight's shape. And its subject, oil paint, is
already what the site's museum hangs. So the architecture comes from the left column and
the publishing contract from the right.

Each has its own `CLAUDE.md`; both are worth reading in full before non-trivial work.
The site's is ~1800 lines and unusually dense with earned rationale — do not skim it and
assume, grep it.

## What is reusable, and what only looks reusable

**Genuinely reusable:**

- **The Python-reference / JS-mirror discipline.** 4d-relight's single most load-bearing
  rule, and the one this project will live or die by. It has its own skill here —
  [python-js-parity](../python-js-parity/SKILL.md). Read that before writing either side.
- **The parity-test harness shape.** `tests/test_traj_parity.py` and
  `tests/test_knot_parity.py` in 4d-relight are working, non-obvious templates: build a
  synthetic bundle from random numbers, run the *real* JS under `node`, compare every
  intermediate stage rather than only the output. No GPU, no trained model, seconds to run.
- **The design contract for a project page.** The site's `.claude/skills/site-design/SKILL.md`
  is the spec a new page must match. Do not re-derive it.
- **The static-verification harness.** The site's `.claude/skills/verify-site/verify.py`
  catches the failure classes that have no build step to catch them. If this project grows
  a browser page in-repo, adapt that script rather than writing a new one.
- **WebGL2 splat-renderer technique** (`RGBA32UI` texture packing, bucket sort, EWA
  footprints, premultiplied-alpha clear). If this project ever renders splats, that work
  is done — see the museum entry in the site's `CLAUDE.md`.

**Looks reusable, is not:**

- **CSS tokens between pages.** The site's pages are *separate design systems that rhyme*.
  They share only the page-chrome trio (see [ship-to-site](../ship-to-site/SKILL.md)).
  Copying `--grad` or a palette wholesale is explicitly against that contract.
- **`.gitattributes` LFS globs.** See the LFS section below — the two repos disagree on
  purpose, and this repo's config is broader than either.
- **CI config.** The two siblings run on *different hosts* with different branch names.
  See the table below.
- **4d-relight's `web/player_*` pages as a starting scaffold.** They decode a very
  specific bundle format. Take the technique, not the file.

## Differences that silently break a copied change

| | 4d-relight | the site | **this repo** |
| --- | --- | --- | --- |
| host | `git.drz.li` (self-hosted **GitLab**) | `github.com/miya9756` | `git.drz.li` (**GitLab**) |
| branch | `master` | `main` | `master` |
| CI file | `.gitlab-ci.yml` | `.github/workflows/*.yml` | → use `.gitlab-ci.yml` |
| `gh` CLI | **does not apply** | applies | **does not apply** |
| deploy | GitLab Pages | GitHub Pages, **project subpath** | GitLab Pages if any |

Three traps in that table:

1. **`gh` does not work against `git.drz.li`.** It is GitLab, not GitHub. There are no PRs
   here — merge requests, through the GitLab UI or API.
2. **4d-relight carries a `.github/workflows/web-demo.yml` that does not run**, because
   GitLab ignores `.github/`. It only executes if that repo is separately mirrored to
   GitHub. Do not copy it here and expect a pipeline.
3. **The branch names differ** (`master` here and in 4d-relight, `main` on the site). A
   CI rule copied from the site that keys on `main` will never fire here.

## Git LFS — the three repos deliberately disagree

The site's `CLAUDE.md` documents a real, quantified lesson worth inheriting:

> **LFS pays off for a few large files, not for many small ones.** The site keeps 214
> `.bin` files averaging 23 KB (6.7 MB total) as *plain git objects*, on purpose. In LFS
> they would be 214 objects re-downloaded on every CI checkout against a monthly bandwidth
> quota, for no packing benefit. A single 1.37 MB file is likewise under the threshold
> where LFS buys anything.

So the site tracks only `*.mkv` and `*.npz` — narrow, and its `*.wasm` is a plain binary
git object, not LFS.

**This repo's [.gitattributes](../../../.gitattributes) is deliberately broader** — it is a
fresh repo and does not yet know its own shape, so the common binary formats are pre-wired.
That is the right default *and* a standing hazard: an oil-paint simulation naturally
produces many small per-stroke or per-tile arrays, which is exactly the case the site
learned not to put in LFS. **When a data layout settles, re-scope the globs to the paths
that actually hold large files.** `*.npy` and `*.bin` are the two to watch.

Also: **GitLab LFS is quota'd server-side and must be enabled for the project.** A push of
a large asset to `git.drz.li` fails with a confusing error if it is not. Check before the
first big commit, not after.

## Execution environment (inherited from 4d-relight — verify before relying on it)

4d-relight's `CLAUDE.md` states, as a hard rule: **do not run Python on the local
workstation — it segfaults.** Work is edited locally and run on the **SLURM cluster**
(ETH Euler), under conda (`smv` for that repo, `4dre` appears in the site's build commands).

Whether that constraint binds *this* project depends on what it imports. A CPU-only
numpy/PIL paint prototype may well be fine locally where a torch+CUDA job is not. **Do not
assume either way — try a trivial run first, and if it segfaults, move to `sbatch` and
follow `slurm/` in 4d-relight for the canonical batch scripts and module loads.**

## Where a piece of work belongs

- **Simulation, solver, reference implementation** → here, in Python.
- **The browser port** → here, alongside its Python reference, with a parity test.
- **A public project page** → eventually the site, under `projects/<name>/`. Read
  [ship-to-site](../ship-to-site/SKILL.md) first. The page is *ported*, not symlinked or
  submoduled — that is how `smv`, `spdef` and the museum all got there.
- **Anything Gaussian-splat** → probably already exists in 4d-relight. Look before building.

One direction rule, learned from 4d-relight's mirror problem: **when code exists in two
places, one of them is the source of truth and it is written down.** That repo's viewer
code is authored in `web/player_browser/` and *synced outward* to its public mirror, never
edited on the mirror and synced back. Apply the same rule to anything this project ships
to the site: **author here, port there.**
