"""Run the trained grading CNN via ONNX Runtime — fast CPU inference for the VPS.

Loaded lazily and defensively: if onnxruntime isn't installed or the model file is missing,
``available()`` is False and ``grade()`` returns None, so importing this can never break the
grading pipeline. The CNN's prediction is surfaced ALONGSIDE the existing grade (not replacing
it) until the model is good enough to trust — corners/edges still lag, centering is already
handled better by reference alignment.

ONNX fp32 runs ~2.4ms/card on CPU (≈40× faster than PyTorch) — negligible next to the ~7s
detect+identify pipeline.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

import config

try:
    import onnxruntime as ort
except Exception:                # onnxruntime not installed -> CNN path stays inert
    ort = None

ASPECTS = ["centering", "corners", "edges", "surface"]
SIZE = 96
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)
_MODEL = config.DATA_DIR / "training" / "grader.onnx"
_sess = None
_tried = False


def _session():
    global _sess, _tried
    if _sess is None and not _tried:
        _tried = True
        if ort is not None and _MODEL.exists():
            try:
                so = ort.SessionOptions()
                so.intra_op_num_threads = 2          # plenty; inference is tiny
                _sess = ort.InferenceSession(str(_MODEL), so,
                                             providers=["CPUExecutionProvider"])
            except Exception:
                _sess = None
    return _sess


def available() -> bool:
    return _session() is not None


def grade(card: np.ndarray) -> Optional[dict]:
    """Per-aspect CNN grades for a warped card (BGR). None if the model isn't available."""
    s = _session()
    if s is None or card is None:
        return None
    try:
        img = cv2.cvtColor(cv2.resize(card, (SIZE, SIZE)), cv2.COLOR_BGR2RGB)
        x = ((img.astype(np.float32) / 255.0 - _MEAN) / _STD).transpose(2, 0, 1)[None]
        out = s.run(None, {"x": x})[0][0]
        return {a: round(float(np.clip(out[i], 1, 10)), 1) for i, a in enumerate(ASPECTS)}
    except Exception:
        return None
