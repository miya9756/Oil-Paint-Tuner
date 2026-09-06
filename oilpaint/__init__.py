"""Feed-forward image -> Gaussian-stroke oil painting.

An image is partitioned by an adaptive quadtree, each leaf cell becomes one 2D
anisotropic Gaussian "brush stroke", and the strokes are composited coarse-to-fine over an
opaque base layer. No optimization, no learned component: every stage is a direct
computation from the source pixels.

How it works, and what each stage is for: see README.md.

References (verified against primary sources 2026-08-15)
--------------------------------------------------------
[Haeberli90]  P. Haeberli. "Paint By Numbers: Abstract Image Representations."
              ACM SIGGRAPH Computer Graphics 24(4), Aug 1990, pp. 207-214.
              -- strokes sampling colour/shape/size/orientation from a source image.
[Litwinowicz97] P. Litwinowicz. "Processing Images and Video for an Impressionist
              Effect." SIGGRAPH 97, pp. 407-414.
              -- stroke orientation normal to the image gradient (i.e. along isophotes),
                 plus random perturbation of length/colour/orientation for the
                 "hand-touched look".
[Hertzmann98] A. Hertzmann. "Painterly Rendering with Curved Brush Strokes of Multiple
              Sizes." SIGGRAPH 98, pp. 453-460.
              -- layers of decreasing stroke radius; strokes placed where the canvas
                 differs from the reference; the opaque coarse first pass.
[Hertzmann02] A. Hertzmann. "Fast Paint Texture." NPAR 2002.
              -- stroke texture as a height field, lit as relief: the impasto pass, and
                 the ridge a stroke's own edge leaves in the paint.
[HaysEssa04]  J. Hays and I. Essa. "Image and Video Based Painterly Animation."
              NPAR 2004, pp. 113-120.  -- smoothed orientation fields.
[Kang07]      H. Kang, S. Lee, C. K. Chui. "Coherent Line Drawing." NPAR 2007,
              doi:10.1145/1274871.1274878.  -- edge tangent flow.
[Samet84]     H. Samet. "The Quadtree and Related Hierarchical Data Structures."
              ACM Computing Surveys 16(2), 1984, pp. 187-260.
[GaussianImage24] X. Zhang, X. Ge, T. Xu, D. He, Y. Wang, H. Qin, G. Lu, J. Geng,
              J. Zhang. "GaussianImage: 1000 FPS Image Representation and Compression by
              2D Gaussian Splatting." ECCV 2024.
              -- the OPTIMIZED counterpart; the contrast case, deliberately not followed.
[T.81]        ITU-T T.81 / ISO-IEC 10918-1 Annex K -- the JPEG example luminance
              quantization table used by the `dct` detail metric.

The splat falloff in `render.py` mirrors the fragment shader of
`4d-relight/web/demo/index.html`, which is where the target look was first observed.

Portability
-----------
Nothing under `oilpaint/` may import scipy or cv2 -- this package is the reference
implementation for a WebGL/WASM port, and neither exists in a browser. See
.claude/skills/python-js-parity/SKILL.md. `metrics.py` is exempt (it never ports).
"""

from .pipeline import PaintConfig, paint

__all__ = ["PaintConfig", "paint"]
