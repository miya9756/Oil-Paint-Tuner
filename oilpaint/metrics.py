"""Instrumentation. None of these are objectives -- see the warning on `psnr`.

This module is EXEMPT from the no-scipy rule in the package docstring: it never ports to
the browser. Everything else under oilpaint/ must stay portable.

Spec §7.
"""

import numpy as np

from .image import luma
from .tensor import sample, sample_angle, structure_tensor


def psnr(a, b):
    """Fidelity against the source.

    A DIAGNOSTIC OF THE ALLOCATOR, NOT A GOAL. At a fixed stroke budget, higher means
    detail went where detail was. But a method that maximizes this is converging on a
    photograph, which is the failure mode of the whole project -- always read it next to
    the image, never on its own.
    """
    mse = float(np.mean((np.clip(a, 0, 1) - np.clip(b, 0, 1)) ** 2))
    return float("inf") if mse == 0 else 10.0 * np.log10(1.0 / mse)


def coverage(cover, thresh=0.99):
    """Fraction of pixels fully painted by strokes alone -- the black-hole measure.

    Only meaningful with the base layer OFF, since the base makes it 1.0 by construction.
    """
    return float((cover > thresh).mean())


def edge_alignment(strokes, rgb, sigma=2.0):
    """Mean |cos(theta_stroke - theta_isophote)|, weighted by structure coherence.

    This is what makes spec experiment 2 objective instead of a matter of taste.
    Expected: ~1.0 for orient='structure', and ~2/pi ~ 0.637 for orient='random', which
    is the mean of |cos| over uniformly distributed angles. A structure-aligned run that
    scores near 0.637 has a bug, not a style.
    """
    theta_f, coh = structure_tensor(rgb, sigma)
    ref = sample_angle(theta_f, strokes.y, strokes.x)
    w = sample(coh, strokes.y, strokes.x)
    # Orientations are pi-periodic, so compare through the doubled angle.
    c = np.abs(np.cos(strokes.theta - ref))
    tot = float(w.sum())
    return float((c * w).sum() / tot) if tot > 0 else float(c.mean())


RANDOM_ALIGNMENT = 2.0 / np.pi  # 0.6366 -- the null hypothesis for edge_alignment


def stroke_stats(strokes):
    r = 0.5 * (strokes.r_major + strokes.r_minor)
    return {
        "n": int(len(strokes)),
        "r_mean": float(r.mean()),
        "r_min": float(r.min()),
        "r_max": float(r.max()),
        "aniso_mean": float((strokes.r_major / np.maximum(strokes.r_minor, 1e-6)).mean()),
    }


def report(out, src, cover, strokes):
    return {
        "psnr": round(psnr(out, src), 2),
        "coverage": round(coverage(cover), 4),
        "edge_alignment": round(edge_alignment(strokes, src), 4),
        **stroke_stats(strokes),
    }
