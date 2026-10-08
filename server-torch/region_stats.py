#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""区域统计诊断：调用 /process，保存深度 PNG，输出全图/上1/4/下1/4 区域统计。
用于复现"S1 + DistillAnyDepth 下原本纯白区域变中性灰"问题。"""
import argparse
import base64
import io
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_client as tc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', required=True)
    ap.add_argument('--upscale', default='bilinear')
    ap.add_argument('--algo', default='S1')
    ap.add_argument('--image', default=tc.DEFAULT_IMAGE)
    ap.add_argument('--target', nargs=2, type=int, default=None)
    ap.add_argument('--bits', type=int, default=16)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()

    data = json.dumps({
        'model': a.model,
        'image': tc.to_png_base64(a.image, bits=a.bits, target=a.target),
        'options': ['depth'],
        'original_size': a.target if a.target else None,
        'postprocess': {'algorithm': a.algo, 'params': {}},
        'upscale': {'mode': a.upscale, 'params': {}},
    }).encode('utf-8')
    req = urllib_req()
    import urllib.request
    req = urllib.request.Request(tc.SERVER + '/process', data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=1800) as r:
        result = json.loads(r.read().decode('utf-8'))
    if 'error' in result:
        raise RuntimeError(result['error'])
    print('diagnostics:', json.dumps(result['diagnostics'], ensure_ascii=False))

    raw = base64.b64decode(result['depth'])
    im = Image.open(io.BytesIO(raw))
    arr = np.asarray(im, dtype=np.float64)
    im.save(a.out)
    h = arr.shape[0]
    top = arr[:h // 4, :]
    mid = arr[h // 4: 3 * h // 4, :]
    bot = arr[3 * h // 4:, :]
    print(json.dumps({
        'full': {'min': float(arr.min()), 'max': float(arr.max()),
                 'mean': float(arr.mean()), 'unique': int(np.unique(arr).size)},
        'top25': {'mean': float(top.mean()), 'max': float(top.max()),
                  'pct_near_white': float((top > 0.9 * arr.max()).mean())},
        'mid': {'mean': float(mid.mean())},
        'bottom25': {'mean': float(bot.mean()), 'max': float(bot.max())},
    }, default=float))


def urllib_req():
    return None


if __name__ == '__main__':
    main()
