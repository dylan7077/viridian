"""Export the grading model to ONNX + int8-quantize it for fast CPU inference on the VPS.

The grader runs on the VPS CPU (no GPU). ONNX Runtime is much faster than PyTorch on CPU,
and dynamic int8 quantization shrinks the model ~4x and speeds it up further with negligible
accuracy loss for this regression head. Outputs grader.onnx (fp32) + grader_int8.onnx, and
benchmarks torch-fp32 vs ort-fp32 vs ort-int8 latency so we know what we're deploying.

Usage: python scripts/quantize_grader.py
"""
from __future__ import annotations

import os
import sys
import time

import glob

import cv2
import numpy as np
import torch
import torch.nn as nn
import onnxruntime as ort
from onnxruntime.quantization import quantize_static, CalibrationDataReader, QuantFormat, QuantType
from torchvision import models

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

SIZE = 96
ASPECTS = 4
D = config.DATA_DIR / "training"
PT = D / "grader.pt"
ONNX = D / "grader.onnx"
ONNX_I8 = D / "grader_int8.onnx"


_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)


def _prep(path):
    img = cv2.cvtColor(cv2.resize(cv2.imread(path), (SIZE, SIZE)), cv2.COLOR_BGR2RGB)
    x = (img.astype(np.float32) / 255.0 - _MEAN) / _STD
    return x.transpose(2, 0, 1)[None]


class Calib(CalibrationDataReader):
    """Feeds ~100 real synth images so static quantization picks good int8 ranges."""
    def __init__(self, n=120):
        files = glob.glob(str(config.DATA_DIR / "training" / "images" / "synth_*.jpg"))[:n]
        self.data = iter([{"x": _prep(f)} for f in files])

    def get_next(self):
        return next(self.data, None)


def build_model():
    m = models.mobilenet_v3_small()
    m.classifier[-1] = nn.Linear(m.classifier[-1].in_features, ASPECTS)
    return m


def mb(p):
    return os.path.getsize(p) / 1e6


def bench(fn, n=60):
    fn()  # warmup
    t = time.time()
    for _ in range(n):
        fn()
    return (time.time() - t) / n * 1000  # ms/inference


def main():
    model = build_model()
    model.load_state_dict(torch.load(PT, map_location="cpu"))
    model.eval()
    dummy = torch.randn(1, 3, SIZE, SIZE)

    # 1) export fp32 ONNX
    torch.onnx.export(model, dummy, str(ONNX), input_names=["x"], output_names=["grades"],
                      dynamic_axes={"x": {0: "batch"}}, opset_version=17)
    print(f"exported {ONNX.name}  ({mb(ONNX):.1f} MB)", flush=True)

    # 2) STATIC int8 quantization (QDQ) — proper int8 convs, supported on ORT CPU
    quantize_static(str(ONNX), str(ONNX_I8), Calib(), quant_format=QuantFormat.QDQ,
                    per_channel=True, weight_type=QuantType.QInt8, activation_type=QuantType.QInt8)
    print(f"quantized {ONNX_I8.name}  ({mb(ONNX_I8):.1f} MB)", flush=True)

    # 3) benchmark on CPU (batch 1, the production case)
    x = dummy.numpy().astype(np.float32)
    so = ort.SessionOptions(); so.intra_op_num_threads = os.cpu_count() or 4
    sess32 = ort.InferenceSession(str(ONNX), so, providers=["CPUExecutionProvider"])
    sess8 = ort.InferenceSession(str(ONNX_I8), so, providers=["CPUExecutionProvider"])

    with torch.no_grad():
        t_torch = bench(lambda: model(dummy))
    t_o32 = bench(lambda: sess32.run(None, {"x": x}))
    t_o8 = bench(lambda: sess8.run(None, {"x": x}))

    # accuracy sanity: int8 output vs fp32
    o32 = sess32.run(None, {"x": x})[0][0]
    o8 = sess8.run(None, {"x": x})[0][0]
    drift = float(np.abs(o32 - o8).mean())

    print("\n=== CPU inference latency (ms / card, batch 1) ===")
    print(f"  PyTorch fp32 : {t_torch:6.1f} ms")
    print(f"  ONNX fp32    : {t_o32:6.1f} ms   ({t_torch/t_o32:.1f}x vs torch)")
    print(f"  ONNX int8    : {t_o8:6.1f} ms   ({t_torch/t_o8:.1f}x vs torch)")
    print(f"\n  size: {mb(PT):.1f}MB pt -> {mb(ONNX):.1f}MB onnx -> {mb(ONNX_I8):.1f}MB int8")
    print(f"  int8 vs fp32 output drift: {drift:.3f} grade points (negligible if <0.2)")
    print("\n  -> deploy grader_int8.onnx on the VPS with onnxruntime (CPUExecutionProvider)")


if __name__ == "__main__":
    main()
