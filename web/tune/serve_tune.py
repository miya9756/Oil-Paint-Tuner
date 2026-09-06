#!/usr/bin/env python
"""Local parameter-tuning tool: upload an image, move sliders, watch the painting change.

    conda run -n 4dre python web/tune/serve_tune.py [--port 8137] [--preview 560]

Then open http://localhost:8137/

This is the DEV server for the source tree. The page it serves computes in the browser --
`web/tune/oilpaint/*.js`, inside engine.worker.js -- so this process only hands over files,
plus the one file that is generated rather than written: `schema.json`, built from
`oilpaint/schema.py`. That is the whole reason this is not `python -m http.server`.

Use it to preview `web/tune/` as you edit it. `build_static.py --serve` assembles and
serves the DEPLOY tree instead, and is what CI publishes; run that before shipping.

The POST endpoints below still run the real `oilpaint` pipeline server-side. The page no
longer calls them (verify_page.py asserts it makes no server calls), but they are the
Python side of the parity story and cost nothing to keep -- `curl` them to compare a
Python render against the browser's.

Stdlib http.server only, matching 4d-relight's serve_static.py / serve_debug.py -- no
Flask, no new dependency in the env.
"""

import argparse
import base64
import io
import json
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from oilpaint.metrics import edge_alignment, psnr  # noqa: E402
from oilpaint.pipeline import PaintConfig, paint  # noqa: E402
from oilpaint.project import DEFAULT_PROJECT  # noqa: E402
from oilpaint.schema import as_dict as schema_dict  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# A module worker is refused outright if its script comes back with the wrong type, so the
# .js line here is load-bearing rather than cosmetic.
CTYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".json": "application/json",
    ".css": "text/css",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
}

# The control schema and its PaintConfig drift guard now live in `oilpaint/schema.py`.
# The static build imports that same module under Pyodide, and a control panel defined in
# two places is a control panel that disagrees with itself.

# One original, kept at full quality, plus a small cache of the widths we have been asked
# to paint at. The page displays the ORIGINAL as the source -- it never sees a resized copy
# -- and asks for the painting at the width the image is actually displayed at, so the two
# halves of the wipe are the same resolution and neither is upscaled into the box.
ORIGINAL = {}  # "img" -> PIL.Image (RGB, full quality)
RESIZED = {}  # width -> float32 HxWx3


def _pil_from_data_url(data_url):
    from PIL import Image

    _, _, b64 = data_url.partition(",")
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img.convert("RGB")


def _to_float(img):
    a = np.asarray(img, dtype=np.float32) / 255.0
    if a.ndim == 2:
        a = np.repeat(a[:, :, None], 3, axis=2)
    return np.ascontiguousarray(a[:, :, :3])


def image_at_width(width):
    """The original resampled to `width`, cached. Never upscales past the original."""
    from PIL import Image

    src = ORIGINAL.get("img")
    if src is None:
        return None
    width = max(32, min(int(width), src.width))
    if width not in RESIZED:
        if len(RESIZED) > 6:
            RESIZED.clear()  # a handful of widths is all a session ever touches
        h = max(1, round(src.height * width / src.width))
        img = src if width == src.width else src.resize((width, h), Image.LANCZOS)
        RESIZED[width] = _to_float(img)
    return RESIZED[width]


def encode_png(arr):
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray((np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class Handler(BaseHTTPRequestHandler):
    # The page asks in DEVICE pixels, so a 600 CSS px box on a 2x display asks for 1200.
    # Render cost scales with image AREA, and it is steep: measured on this machine at
    # ~5000 strokes, 360px is 1.1 s, 900px 5.8 s, 1200px 9.2 s and 1800px 30 s. So the
    # interactive cap is 1100 -- past that a slider stops being a slider. "full res" is
    # the deliberate, slow path. These only bite on large sources; the server never
    # upscales past the original.
    preview_side = 1100  # cap on the interactive render width
    full_side = 2000  # cap when "full res" is ticked (SLOW: tens of seconds)
    store_side = 2400  # cap on what is kept in memory

    def log_message(self, fmt, *a):  # quieter than the default one-line-per-asset
        pass

    def _send(self, code, payload, ctype="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, rel):
        full = os.path.normpath(os.path.join(HERE, rel))
        if not full.startswith(HERE + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        ctype = CTYPES.get(os.path.splitext(full)[1], "application/octet-stream")
        with open(full, "rb") as fh:
            return self._send(200, fh.read(), ctype)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._file("index.html")
        # schema.json is GENERATED -- build_static.py writes it into the deploy tree and it
        # is deliberately not in the source directory, so a plain file server pointed at
        # web/tune/ 404s it and the engine refuses to start. Synthesising it here is what
        # lets this port preview the SOURCE tree with no build step.
        if path in ("/schema.json", "/schema"):
            return self._send(200, schema_dict())
        # The opening project, for the same reason schema.json is here: it lives in
        # examples/ and build_static.py copies it into the deploy tree under this name, so a
        # plain file server pointed at web/tune/ would 404 it and this port would open on a
        # different painting from the deployed page. Read per request, like index.html, so
        # editing the example is visible on a refresh.
        if path == "/sample-project.json":
            try:
                with open(os.path.join(_ROOT, "examples", DEFAULT_PROJECT),
                          encoding="utf-8") as fh:
                    return self._send(200, json.load(fh))
            except OSError:
                # A missing example must not take the page down: it opens on the photograph
                # with the engine's own defaults, which is what it did before this existed.
                return self._send(404, {"error": "no sample project"})
        # The page is the static one now: it starts engine.worker.js and imports
        # oilpaint/*.js by relative path, and does the compute in the browser. Those have
        # to be served or the worker dies on its first import.
        return self._file(path.lstrip("/"))

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            path = self.path.split("?")[0]
            if path == "/upload":
                return self._send(200, self._upload(req))
            if path == "/render":
                return self._send(200, self._render(req))
            return self._send(404, {"error": "not found"})
        except Exception:
            traceback.print_exc()
            return self._send(500, {"error": traceback.format_exc(limit=3)})

    def _upload(self, req):
        img = _pil_from_data_url(req["data"])
        if max(img.size) > self.store_side:  # bound memory, not quality-for-display
            s = self.store_side / max(img.size)
            from PIL import Image

            img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))),
                             Image.LANCZOS)
        ORIGINAL["img"] = img
        RESIZED.clear()
        # No image is returned: the page already holds the file it just read, and shows
        # THAT as the source. Sending back a server-resized copy is what made the source
        # pane display a degraded image next to a crisp painting.
        return {"ok": True, "w": img.width, "h": img.height}

    def _render(self, req):
        want = int(req.get("width") or self.preview_side)
        want = max(64, min(want, self.full_side if req.get("full") else self.preview_side))
        img = image_at_width(want)
        if img is None:
            return {"error": "no image uploaded yet"}

        params = dict(req.get("params", {}))
        params.pop("tau", None)
        cfg = PaintConfig(**params)

        t0 = time.perf_counter()
        out, info, sb = paint(img, cfg)
        wall = time.perf_counter() - t0

        return {
            "png": encode_png(out),
            "stats": {
                "strokes": int(info["n_detail"]),
                "underpainting": int(info["n_under"]),
                "coverage_detail": round(info["coverage"], 3),
                "coverage_all": round(info["coverage_all"], 3),
                "bare": round(info["bare"], 4),
                "psnr": round(psnr(out, img), 2),
                "edge_alignment": round(edge_alignment(sb, img), 3),
                "budget_reached": info.get("budget_reachable", True),
                "ceiling": info.get("stroke_ceiling", 0),
                "tau": float(info["tau"]),
                "seconds": round(wall, 2),
                "size": f"{img.shape[1]}x{img.shape[0]}",
            },
        }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8137)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--preview", type=int, default=1100,
                    help="cap on the interactive render width (the page asks for the "
                         "width the image occupies in DEVICE pixels, up to this). "
                         "Cost scales with area: ~9s at 1200px, ~30s at 1800px")
    ap.add_argument("--full", type=int, default=2000,
                    help="cap on the render width when 'full res' is ticked (slow)")
    a = ap.parse_args()

    Handler.preview_side, Handler.full_side = a.preview, a.full
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"oil-paint tuner  ->  http://{a.host}:{a.port}/")
    print(f"render width: display size, capped at {a.preview}px "
          f"({a.full}px on 'full res')   (ctrl-c to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
