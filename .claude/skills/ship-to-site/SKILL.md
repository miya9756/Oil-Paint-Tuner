---
name: ship-to-site
description: The contract for publishing this project as a page on mingyang-song.github.io — the project-subpath deploy that breaks root-absolute paths, the page-chrome trio every page must declare, the design bones a new project page inherits, the copyright and attribution lines, and the LFS and verification steps. Read BEFORE writing any browser page intended for the public site, and before porting one there.
---

# Shipping to the public site

The likely endgame for this project is a page on
`/cluster/home/misong/mingyang-song.github.io`, alongside `smv`, `spdef` and `grain`. That
site has a written design contract and a static verifier, and both are strict. **Read the
site's own two skills before writing the page**, not after:

- `mingyang-song.github.io/.claude/skills/site-design/SKILL.md` — the design contract.
- `mingyang-song.github.io/.claude/skills/verify-site/SKILL.md` — the checks, and the real
  bug each one caught.

This skill is the *bridge*: what to know while the work is still here, so the port is a
move rather than a rewrite.

## Author here, port there

The site is not a build target of this repo. A page gets **copied** into
`projects/<name>/`, the way `projects/smv/` was copied out of 4d-relight's
`web/player_browser/`. That repo learned the direction rule the hard way and wrote it down:
the source of truth is where the code is *authored*, and edits go outward, never back.

So: build the page here, port it there, and if it later needs changing, change it **here**
first. Otherwise the two drift and the public one silently wins.

## Two constraints to design around from day one

Both are cheap to honour now and expensive to retrofit.

### 1. No root-absolute paths, ever

The site deploys as a GitHub **project site** — `miya9756.github.io/mingyang-song.github.io/`,
not a domain root. So `src="/assets/x.png"` resolves fine on a local `http.server` and
**404s in production**. Every path must be relative. `verify.py` fails the build on this.

Write the page with relative paths from the start. A page developed against a local root
accumulates absolute paths that all break at once on the port.

### 2. Every page declares the page-chrome trio

Three declarations, on every page including any new one. `verify.py` FAILs without them:

```html
<meta name="theme-color" content="#f4f2ec">   <!-- or one per scheme, with media= -->
```
```css
:root{ color-scheme: light }        /* `light dark` if the page is themed */
html { background: var(--bg) }      /* not just body */
```

They cover the surfaces the page's own background does not reach — the UA canvas,
scrollbars, form controls, iOS toolbars, Safari's overscroll rubber-band. Chrome infers
those from the body background; Safari leaves them white. The site's landing page rendered
warm in Chrome and pure white in Safari with byte-identical CSS until these were added.

One corollary from the same section, which matters for a paint project more than most:
**keep off-whites off white.** The site's light `--bg` was `#fbfaf8`, three units from
white — inside the noise floor of sRGB → display-profile conversion on a wide-gamut screen,
so the warmth survived in one browser and not the other. It is now `#f4f2ec`. *A tint that
is meant to be seen needs to be a colour, not a rounding error.*

## The design bones a new project page inherits

The site's pages are **separate design systems that rhyme** — do not share tokens between
them, and do not copy another page's palette. A new project page picks its own temperature
and accent. What it *does* inherit, per the design contract, is the bones of the two most
recent project pages:

- Fraunces display serif for `h1`/`h2`/`h3`
- the same back pill, card radius and section rhythm
- section order: **work → citation → datasets → footer**

> Follow that for the next one; only the temperature and the accent should change.

This project has an obvious palette temptation and it is worth naming: the site's museum
already hangs oil paintings on warm paper. Rhyming with that is legitimate; copying its
tokens is not.

Two more rules from the contract that a canvas-heavy page will hit:

- **Motion goes in the `prefers-reduced-motion: reduce` block in the same edit.**
  Retrofitting is always forgotten. For a simulation this means deciding what the reduced
  state *is* — the museum's easter egg, for instance, keeps the same duration but widens
  the wavefront to the whole cloud so the change arrives everywhere at once, rather than
  being disabled outright.
- **Images carry real `width`/`height` and real `alt`.** `verify.py` does not check this.
  Prefer transparent PNG for figures — a baked-in white background forces a choice between
  a glaring band in dark mode and a fake plate to hide it.

## Attribution and licensing — non-optional

Every page on that site ends:

```
© 2026 Mingyang Song · All rights reserved.
```

using that page's own `footer{…}` rule. Nothing on the site is offered under an open
licence, and `/LICENSE` at the repo root carries the scope.

**The claim covers Mingyang's own text, artwork and code only.** So every project page that
uses third-party work also carries *Built with* and *Datasets & credits* blocks, and the
line "Third-party code and datasets remain under their own licences". Those attributions
are load-bearing — the site's own note is blunt about why: *the reserved-rights line is
only honest next to them.*

For this project, that means tracking dependencies and any reference imagery **as you go**,
here, so the blocks can be written truthfully at port time. A paint prototype accretes
borrowed pigment data, brush textures and reference photographs quickly, and reconstructing
their provenance later is guesswork.

Two related habits from the museum work, which are about honesty rather than law:

- **Do not upgrade an inference into a citation.** When the museum could not source a
  work's catalogue record, the page carries `refs:[]` and a note reading *Transcribed from
  the gallery label* — rather than a plausible-looking accession number no primary source
  confirms. Same rule for any pigment constant, historical recipe or measured spectrum this
  project cites.
- **Say what a thing actually is.** The museum's `CLAUDE.md` corrects "photographs" to "one
  video pass" because that is what the capture was. If a result is a simulation, an
  approximation, or a fit, the page says so.

## LFS on the site is narrow — check before adding assets

The site tracks only `*.mkv` and `*.npz` in LFS; its `*.wasm` is a plain binary git object.
That is deliberate, and the reasoning transfers directly to anything this project ships:

> LFS pays off for a few large files, not many small ones. 214 files averaging 23 KB stay
> as plain git objects — in LFS they would be 214 objects re-downloaded on every CI checkout
> against a monthly bandwidth quota, for no packing benefit.

So before porting assets: if the page ships **a few large binaries**, add a scoped LFS glob
on the site. If it ships **many small ones**, do not. Scope any new rule to the path, never
a bare `*.bin`-style glob — the site's note calls that out explicitly as too generic.

Note this repo's own `.gitattributes` is much broader (a fresh-repo default). Do not copy it
across. See [sibling-repos](../sibling-repos/SKILL.md).

## Port checklist

- [ ] Page built and working here, all paths relative.
- [ ] Page-chrome trio present: `theme-color`, `color-scheme`, `html{background}`.
- [ ] `theme-color` hex actually appears in the page CSS (`verify.py` WARNs otherwise).
- [ ] Own palette, not another page's tokens; Fraunces headings, standard section order.
- [ ] Every transition also handled in the `prefers-reduced-motion` block.
- [ ] Copyright line, *Built with*, and *Datasets & credits* blocks written and truthful.
- [ ] Assets: scoped LFS glob only if they are few and large.
- [ ] Copy into `projects/<name>/` on the site — remember it deploys from **`main`**, while
      this repo is on `master`.
- [ ] `python3 .claude/skills/verify-site/verify.py --serve` on the site, exit 0, before
      calling it done.
