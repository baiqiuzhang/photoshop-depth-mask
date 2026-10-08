#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""协议 7 测试客户端：/ping 检视 + /process 端到端回归。

用法：
  python test_client.py ping
  python test_client.py process --model depthpro --mode depth --upscale gf --radius 2 --algo S1 --image 测试例图.jpg
  python test_client.py matrix   # 5 模型 × 3 超分 × 位深 冒烟矩阵（只跑 depth 输出）

输出校验：shape / PNG 位深（16 -> I;16，8 -> L）/ dtype / min/max/mean/unique / 截断比。
"""
import argparse
import base64
import io
import json
import os
import urllib.request

import numpy as np
from PIL import Image

SERVER = 'http://127.0.0.1:8766'
ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_IMAGE = os.path.join(os.path.dirname(ROOT), '测试例图.jpg')


def ping():
    with urllib.request.urlopen(SERVER + '/ping', timeout=5) as r:
        return json.loads(r.read().decode('utf-8'))


def to_png_base64(image_path, bits=16, target=None):
    """构造 8/16-bit RGB PNG 并 base64（16-bit 用 zlib 手写 IHDR bit_depth=16）。"""
    img = Image.open(image_path).convert('RGB')
    if target is not None:
        img = img.resize(target, Image.BICUBIC)
    if bits >= 16:
        arr = np.asarray(img, dtype=np.float32)
        arr16 = (arr / 255.0 * 65535.0).astype(np.uint16)
        data = _write_png16_rgb(arr16)
    else:
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        data = buffer.getvalue()
    return base64.b64encode(data).decode('utf-8')


def _write_png16_rgb(arr):
    """手写 16-bit RGB PNG（IHDR bit_depth=16, color_type=2），无滤波器。"""
    import struct
    import zlib
    h, w, _ = arr.shape
    raw = b''.join(b'\x00' + arr[y].astype('>u2').tobytes() for y in range(h))

    def chunk(typ, payload):
        return (struct.pack('>I', len(payload)) + typ + payload
                + struct.pack('>I', zlib.crc32(typ + payload) & 0xffffffff))

    ihdr = struct.pack('>IIBBBBB', w, h, 16, 2, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
            + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def analyze_png(b64data):
    raw = base64.b64decode(b64data)
    with Image.open(io.BytesIO(raw)) as im:
        mode, size = im.mode, im.size
        bit_depth = 16 if mode in ('I;16', 'I;16L', 'I;16B') else 8
        arr = np.asarray(im, dtype=np.float64)
    return {
        'mode': mode, 'size': size, 'bit_depth': bit_depth,
        'min': float(arr.min()), 'max': float(arr.max()),
        'mean': float(arr.mean()), 'unique': int(np.unique(arr).size),
        'sat_ratio': float((arr >= arr.max() - 1e-9).mean()) if arr.max() > 0 else 0.0,
    }


def process(args):
    data = json.dumps({
        'model': args.model,
        'image': to_png_base64(args.image, bits=args.bits, target=args.target),
        'options': [args.mode],
        'original_size': args.target if args.target else None,
        'postprocess': {'algorithm': args.algo, 'params': {}},
        'upscale': {'mode': args.upscale,
                    'params': ({'radius': args.radius} if args.upscale == 'wgif'
                               else {'sigma_r': args.sigma_r} if args.upscale == 'jbu-nc'
                               else {})},
    }).encode('utf-8')
    req = urllib.request.Request(SERVER + '/process', data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=1800) as r:
        result = json.loads(r.read().decode('utf-8'))
    if 'error' in result:
        raise RuntimeError(result['error'])
    print('diagnostics:', json.dumps(result['diagnostics'], ensure_ascii=False))
    for key in ('depth', 'fog'):
        if key in result:
            stats = analyze_png(result[key])
            print(f'{key}: {stats}')
    return result


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd')
    sub.add_parser('ping')
    p = sub.add_parser('process')
    p.add_argument('--model', default='depthpro')
    p.add_argument('--mode', default='depth', choices=['depth', 'fog'])
    p.add_argument('--upscale', default='bilinear',
                   choices=['bilinear', 'wgif', 'jbu-nc', 'none'])
    p.add_argument('--radius', type=int, default=2)
    p.add_argument('--sigma-r', type=float, default=0.05)
    p.add_argument('--algo', default='S1', choices=['none', 'S1', 'S5', 'N1_KMeans', 'N1A', 'N2'])
    p.add_argument('--bits', type=int, default=16, choices=[8, 16])
    p.add_argument('--image', default=DEFAULT_IMAGE)
    p.add_argument('--target', nargs=2, type=int, default=None)
    m = sub.add_parser('matrix')
    m.add_argument('--image', default=DEFAULT_IMAGE)
    args = ap.parse_args()

    if args.cmd == 'ping':
        info = ping()
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return
    if args.cmd == 'process':
        process(args)
        return
    if args.cmd == 'matrix':
        info = ping()
        available = [m['key'] for m in info['available_models'] if m['found']]
        print('available:', available)
        for model in available:
            for up in ('bilinear', 'wgif', 'jbu-nc', 'none'):
                for bits in (16, 8):
                    a = argparse.Namespace(model=model, mode='depth', upscale=up,
                                          radius=2, algo='S1', bits=bits,
                                          image=args.image, target=(683, 1024))
                    print(f'--- {model} upscale={up} bits={bits} ---')
                    try:
                        process(a)
                    except Exception as exc:
                        print('ERROR:', exc)
    else:
        ap.print_help()


if __name__ == '__main__':
    main()
