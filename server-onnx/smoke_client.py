# -*- coding: utf-8 -*-
"""/process 冒烟客户端：构造 8bit/16bit PNG 请求，校验输出位深与统计。

用法：
  python smoke_client.py --server http://127.0.0.1:8766 \
      --model depthpro --algo none --bits 8 [--image path/to/img.png] [--jpg path/img.jpg]
输出：写入 smoke_out/<model>_<algo>_<bits>.png + 打印诊断与统计。
统计纪律：shape/dtype/位深/min/max/mean/std/唯一值数/截断比例。
"""
import argparse
import base64
import io
import json
import os
import sys
import urllib.request

import numpy as np

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'smoke_out')
SAMPLE_JPG = r'D:\depth_pro_photoshop_jsx\0.2.1\测试样图\_DSC5383-编辑-拷贝.jpg'


def load_8bit_png(path):
    from PIL import Image
    with Image.open(path) as img:
        img = img.convert('RGB')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()


def _png_chunk(ctype, payload):
    import struct
    import zlib
    return (struct.pack('>I', len(payload)) + ctype + payload
            + struct.pack('>I', zlib.crc32(ctype + payload) & 0xFFFFFFFF))


def make_16bit_png(w=1024, h=1024, seed=7):
    """合成 16-bit RGB PNG（渐变 + 噪点 + 圆形物体），手工编码保证 bit_depth=16。"""
    import struct
    import zlib
    rng = np.random.RandomState(seed)
    base = np.zeros((h, w, 3), dtype=np.uint16)
    y, x = np.mgrid[0:h, 0:w]
    base[..., 0] = (x * 65535 // w).astype(np.uint16)
    base[..., 1] = (y * 65535 // h).astype(np.uint16)
    base[..., 2] = ((x + y) * 65535 // (w + h)).astype(np.uint16)
    cy, cx = h // 3, w // 2
    mask = (x - cx) ** 2 + (y - cy) ** 2 < (h // 5) ** 2
    base[..., 0][mask] = 40000
    base[..., 1][mask] = 5000
    base[..., 2][mask] = 30000
    noise = rng.randint(0, 300, size=(h, w, 3)).astype(np.uint16)
    base = np.clip(base.astype(np.int32) + noise, 0, 65535).astype(np.uint16)
    # RGB 16-bit：每行 filter 0 + 每像素 6 字节大端
    rows = base.reshape(h, w * 3).astype('>u2').tobytes()
    raw = b''.join(b'\x00' + rows[y * w * 6:(y + 1) * w * 6] for y in range(h))
    ihdr = struct.pack('>IIBBBBB', w, h, 16, 2, 0, 0, 0)  # bit_depth=16, color_type=2
    return (b'\x89PNG\r\n\x1a\n' + _png_chunk(b'IHDR', ihdr)
            + _png_chunk(b'IDAT', zlib.compress(raw, 6)) + _png_chunk(b'IEND', b''))


def arr_stats(a):
    a = np.asarray(a)
    return {
        'shape': list(a.shape), 'dtype': str(a.dtype),
        'min': float(a.min()), 'max': float(a.max()),
        'mean': round(float(a.mean()), 3), 'std': round(float(a.std()), 3),
        'unique': int(len(np.unique(a))),
    }


def png_stats(png_bytes):
    from PIL import Image
    with Image.open(io.BytesIO(png_bytes)) as img:
        mode = img.mode
        arr = np.asarray(img)
    return mode, arr_stats(arr)


def request(server, model, image_png, bits, algo, upscale_mode, radius=2):
    payload = {
        'model': model,
        'image': base64.b64encode(image_png).decode('ascii'),
        'options': ['depth'],
        'original_size': None,
        'postprocess': {'algorithm': algo, 'params': {}},
        'upscale': {'mode': upscale_mode,
                    'params': {'radius': radius} if upscale_mode == 'gf' else {}},
    }
    if bits == 16:
        payload['original_size'] = [1024, 1024]
    req = urllib.request.Request(
        server + '/process', data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=1800) as resp:
        return json.loads(resp.read().decode('utf-8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--server', default='http://127.0.0.1:8766')
    parser.add_argument('--model', default='depthpro')
    parser.add_argument('--algo', default='none', choices=['none', 'S1', 'S5'])
    parser.add_argument('--bits', type=int, default=8, choices=[8, 16])
    parser.add_argument('--upscale', default='bilinear', choices=['bilinear', 'gf', 'none'])
    parser.add_argument('--image', default=None, help='8bit PNG 路径（默认用样图 JPG 转换）')
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    if args.bits == 16:
        png = make_16bit_png()
        src_desc = '合成 16bit PNG (1024²)'
    elif args.image:
        with open(args.image, 'rb') as f:
            png = f.read()
        src_desc = os.path.basename(args.image)
    else:
        if not os.path.isfile(SAMPLE_JPG):
            raise SystemExit(f'样图不存在: {SAMPLE_JPG}')
        from PIL import Image
        with Image.open(SAMPLE_JPG) as img:
            img = img.convert('RGB')
            if max(img.size) > 2048:
                img.thumbnail((2048, 2048))
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            png = buf.getvalue()
        src_desc = f'{os.path.basename(SAMPLE_JPG)} -> PNG(≤2048)'

    print(f'== {args.model} algo={args.algo} bits={args.bits} upscale={args.upscale} | {src_desc}')
    import time
    t0 = time.time()
    result = request(args.server, args.model, png, args.bits, args.algo,
                     args.upscale)
    elapsed = time.time() - t0
    if 'error' in result:
        print(f'  ✗ ERROR: {result["error"]}')
        return 1

    mode, stats = png_stats(base64.b64decode(result['depth']))
    print(f'  elapsed={elapsed:.1f}s')
    print(f'  depth PNG mode={mode}')
    for k, v in stats.items():
        print(f'    {k}: {v}')
    diag = result.get('diagnostics', {})
    print(f'  diagnostics: model={diag.get("model")} native={diag.get("native_shape")} '
          f'device={diag.get("model_device")} input_bits={diag.get("input_bit_depth")} '
          f'output_bits={diag.get("output_bit_depth")} hist={diag.get("histogram")}')
    expected_mode = 'I;16' if args.bits == 16 else 'L'
    if mode != expected_mode:
        print(f'  ✗ 位深不符: 期望 {expected_mode}，实际 {mode}')
        return 1
    out = os.path.join(OUT_DIR, f'{args.model}_{args.algo}_{args.bits}bit_{args.upscale}.png')
    with open(out, 'wb') as f:
        f.write(base64.b64decode(result['depth']))
    print(f'  saved: {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
