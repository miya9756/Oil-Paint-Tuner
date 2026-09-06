"""Image I/O, colour, and summed-area tables.

Summed-area tables are what keep the quadtree build O(pixels) rather than
O(pixels * depth): every cell statistic in detail.py is an O(1) lookup regardless of how
large the cell is.
"""

import numpy as np

# Rec. 709 luma. The detail metrics run on luminance only -- chroma-only edges are rare
# enough in practice not to justify tripling the cost of every metric (spec §4.1).
LUMA_709 = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def load_image(path):
    """Load to float32 HxWx3 in [0,1], sRGB. Alpha is dropped, greyscale is expanded."""
    from PIL import Image

    img = Image.open(path)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    a = np.asarray(img, dtype=np.float32) / 255.0
    if a.ndim == 2:
        a = np.repeat(a[:, :, None], 3, axis=2)
    return np.ascontiguousarray(a[:, :, :3])


def save_image(path, img):
    from PIL import Image

    a = np.clip(img, 0.0, 1.0)
    Image.fromarray((a * 255.0 + 0.5).astype(np.uint8)).save(path)


def luma(rgb):
    return rgb @ LUMA_709


def srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1.0 / 2.4) - 0.055)


def summed_area(a):
    """SAT with a zero first row/column, so a box sum is 4 lookups and no edge cases.

    Accumulated in float64: a 4k image of squared luma sums to ~1e7, and float32 loses
    the low-order bits of a difference of two large partial sums -- which is exactly what
    a variance query computes.
    """
    s = np.zeros((a.shape[0] + 1, a.shape[1] + 1), dtype=np.float64)
    np.cumsum(np.cumsum(a, axis=0, dtype=np.float64), axis=1, out=s[1:, 1:])
    return s


def sat_box(sat, y0, x0, y1, x1):
    """Sum over [y0,y1) x [x0,x1), clipped to the image. Vectorized over cell arrays.

    Cells may hang off the edge of the image (the tree lives on a padded square), so the
    clip is not optional. Returns (sums, areas); areas can be 0 for a fully-outside cell
    and every caller must guard the division.
    """
    h, w = sat.shape[0] - 1, sat.shape[1] - 1
    y0 = np.clip(y0, 0, h)
    y1 = np.clip(y1, 0, h)
    x0 = np.clip(x0, 0, w)
    x1 = np.clip(x1, 0, w)
    total = sat[y1, x1] - sat[y0, x1] - sat[y1, x0] + sat[y0, x0]
    return total, (y1 - y0).astype(np.float64) * (x1 - x0)


def gaussian_kernel1d(sigma, truncate=3.0):
    r = max(1, int(truncate * sigma + 0.5))
    x = np.arange(-r, r + 1, dtype=np.float32)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    return k / k.sum()


def convolve1d(a, k, axis):
    """Separable convolution with edge padding.

    Hand-rolled rather than scipy.ndimage: this file has to port to the browser, where
    scipy does not exist (see the package docstring).
    """
    r = len(k) // 2
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r, r)
    ap = np.pad(a, pad, mode="edge")
    out = np.zeros_like(a, dtype=np.float32)
    for i, wgt in enumerate(k):
        sl = [slice(None)] * a.ndim
        sl[axis] = slice(i, i + a.shape[axis])
        out += np.float32(wgt) * ap[tuple(sl)]
    return out


def gaussian_blur(a, sigma):
    if sigma <= 0:
        return a.astype(np.float32, copy=True)
    k = gaussian_kernel1d(sigma)
    return convolve1d(convolve1d(a, k, 0), k, 1)


def decimate(a, step):
    """Block-mean downsample of a 2-D array by an integer factor, edge-replicating the
    ragged last block so no sample is invented and none is dropped.

    A step x step box IS the anti-alias filter a decimation needs, so this is one operation
    rather than "filter, then subsample". Accumulated in float64 and rounded once at the
    end: the reduction order of numpy's float32 pairwise sum over a 4-D reshape is not
    reproducible from outside the library, and a port that sums naively in float64 then
    rounds lands on the same float32 -- which is what makes this cheap to hold to parity.
    """
    step = max(1, int(step))
    if step == 1:
        return a.astype(np.float32, copy=True)
    h, w = a.shape
    hs, ws = -(-h // step), -(-w // step)
    p = np.pad(a, ((0, hs * step - h), (0, ws * step - w)), mode="edge")
    return p.reshape(hs, step, ws, step).mean(axis=(1, 3), dtype=np.float64).astype(np.float32)


def pad_to_square(a, size):
    """Edge-replicate to size x size, anchored at the top-left (the tree's origin)."""
    ph, pw = size - a.shape[0], size - a.shape[1]
    pad = [(0, ph), (0, pw)] + [(0, 0)] * (a.ndim - 2)
    return np.pad(a, pad, mode="edge")
