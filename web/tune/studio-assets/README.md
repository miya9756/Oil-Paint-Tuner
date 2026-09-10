The Light room credits scene design and implementation to OpenAI Codex, and the
3D furniture and chandelier to Mingyang Song under CC0. These credits are available from the expandable
label in the room, including fullscreen, alongside the Three.js and Poly Haven credits.

Mingyang Song dedicates the supplied 3D furniture and chandelier, including the repaired
models shipped as [`tea-furniture.glb`](tea-furniture.glb) and
[`chandelier.glb`](chandelier.glb), to the public domain under
[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/).
See [the model dedication](LICENSE.md). Attribution is appreciated but not required.

The tea furniture and chandelier are repaired copies of OBJ files supplied by the
project owner from their local `old_3d_assets` collection. The source files identify
Cinema 4D as the exporter; they contain no creator or license metadata, and their
referenced MTL files were not supplied. The CC0 dedication above was provided by the
project owner separately from the source-file metadata.

`scripts/prepare_studio_assets.py` ear-clips the concave polygons, fills only triangle
and quad boundary gaps, checks winding, reconstructs normals with hard-edge splits,
and repairs zero-length normals. It preserves the source files and writes full repaired
GLBs and a per-object report to `.cache/museum-review/repaired/`.

The shipped furniture combines one chair, a pedestal table, a teapot, cups, and a tiered
tea stand from `table_with_chairs_cups.obj`. Parts sharing a material are merged. The
chandelier comes from `吊灯.obj`. Both use material slots styled in `studio-furniture.js`.
The source models' scale is normalized for this room. These GLBs are ordinary Git files
so static builds do not depend on Git LFS or access to the original asset directory.

To rebuild, install the offline tooling listed in the preparation script and run:

    python scripts/prepare_studio_assets.py E:/paint/old_3d_assets --all
    python tests/test_studio_assets.py

All five repaired variants are available locally; only the two optimized assets above
are part of the website. The clockwork exhibit is generated locally by `studio-clockwork.js`.

Scanned color, normal, and roughness maps are bundled at 1K resolution from
[Poly Haven](https://polyhaven.com): [Wood Table 001](https://polyhaven.com/a/wood_table_001),
[Marble 01](https://polyhaven.com/a/marble_01), and
[Beige Wall 001](https://polyhaven.com/a/beige_wall_001). These are
[CC0 assets](https://polyhaven.com/license). `materials.json` records their original
download URLs and checksums. The running site never contacts Poly Haven.
