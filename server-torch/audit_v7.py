#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""协议 7 流水线审计：GF r 柔化对比 + SR×直方图×位深矩阵（服务需已启动）。

用法：python audit_v7.py --server http://127.0.0.1:8766 --image 测试例图.jpg
输出：每用例 diagnostics + 输出 PNG 统计；GF 各 r 的边缘锐度对比。
"""
import argparse
import base64
import io
import json
import os
import sys
import urllib.request

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_client


def edge_sharpness(arr01):
    """强边缘(梯度>P95)像素的梯度均值，越高越锐利（GF r 增大应降低）。"""
    gy, gx = np.gradient(arr01.astype(np.float64))
    g = np.hypot(gx, gy)
    m = g > np.percentile(g, 95)
    return float(g[m].mean()) if m.sum() else 0.0


def decode_depth(b64data):
    with Image.open(io.BytesIO(base64.b64decode(b64data))) as im:
        arr = np.asarray(im, dtype=np.float64)
        return arr, im.mode


def run_case(args, model, upscale, radius, algo, bits):
    a = argparse.Namespace(model=model, mode='depth', upscale=upscale, radius=radius,
                           algo=algo, bits=bits, image=args.image, target=(1024, 683))
    result = test_client.process(a)
    stats = test_client.analyze_png(result['depth'])
    arr, mode = decode_depth(result['depth'])
    arr01 = arr / (65535.0 if mode in ('I;16', 'I;16L', 'I;16B') else 255.0)
    stats['sharpness'] = edge_sharpness(arr01)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--server', default='http://127.0.0.1:8766')
    ap.add_argument('--image', default=test_client.DEFAULT_IMAGE)
    ap.add_argument('--model', default='depthpro')
    args = ap.parse_args()
    test_client.SERVER = args.server

    print('=== GF r 柔化对比（depthpro, S1, 16bit）===')
    sharp = {}
    for r in (1, 2, 4, 8):
        s = run_case(args, args.model, 'gf', r, 'S1', 16)
        sharp[r] = s['sharpness']
        print(f'r={r}: sharpness={s["sharpness"]:.3f} unique={s["unique"]} '
              f'mode={s["mode"]} mean={s["mean"]:.2f}')
    base = sharp.get(2, 1.0)
    print('锐度随 r 单调递减:', all(sharp[a] > sharp[b] for a, b in
                                   [(1, 2), (2, 4), (4, 8)]))

    print('=== 矩阵（depthpro）===')
    for up in ('bilinear', 'gf', 'none'):
        for algo in ('S1', 'none'):
            for bits in (16, 8):
                try:
                    s = run_case(args, args.model, up, 2, algo, bits)
                    print(f'{up:8s} {algo:4s} {bits:2d}bit -> mode={s["mode"]} '
                          f'size={s["size"]} unique={s["unique"]} sat={s["sat_ratio"]:.4f}')
                except Exception as exc:
                    print(f'{up:8s} {algo:4s} {bits:2d}bit -> ERROR {exc}')


if __name__ == '__main__':
    main()
