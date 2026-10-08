# -*- coding: utf-8 -*-
"""ONNX 分支数值门：5 个模型宿主流水线 vs 参考夹具 *_onnx_ref/*.npz（CPU EP）。

运行环境：onnxruntime-directml（或 onnxruntime）+ onnx + numpy + PIL。
用法：
  set DEPTH_MODELS_SRC=<ONNX 权重目录>
  set DEPTH_ONNX_REF_DIR=<参考夹具目录>
  python gate_onnx_models.py [--models depthpro bridge distillanydepth iris ppd]
判据：逐阶段/最终深度 cosine >= 0.99；结果写入 gate_results.json。
本脚本只读参考数据与模型权重，不修改任何文件（gate_*.json 为中间产物）。
"""
import argparse
import json
import os
import sys

SRC_WIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC_WIN)

# 参考夹具目录（*.npz）与 ONNX 权重源目录。两者都不随仓库发布，必须自己指定：
# 命令行 --ref-dir / --models-src 优先，其次环境变量，最后回落到仓库内的同层目录。
REF_DIR = os.environ.get(
    'DEPTH_ONNX_REF_DIR', os.path.normpath(os.path.join(SRC_WIN, os.pardir, 'onnx_ref')))
MODELS_SRC = os.environ.get(
    'DEPTH_MODELS_SRC', os.path.normpath(os.path.join(SRC_WIN, os.pardir, 'models')))

import numpy as np  # noqa: E402
import onnx_common  # noqa: E402
from PIL import Image  # noqa: E402


def cos(a, b):
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    if a.shape != b.shape:
        return {'shape_mismatch': [list(a.shape), list(b.shape)]}
    return round(float(np.sum(a * b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12)), 8)


def sess(rel):
    return onnx_common.get_session(os.path.join(MODELS_SRC, rel), force_cpu=True)[0]


def gate_depthpro(out):
    """社区 fp32 图输出为已烘焙后处理的度量深度（1536²），对照 torch 参考
    post_process 后的 depth_hw（原尺寸，bilinear 缩回后比较）。"""
    s = sess('DepthPro-onnx/depthpro_fp32.onnx')
    for key in ('_DSC5383', '_DSC0975'):
        z = np.load(os.path.join(REF_DIR, f'depthpro_{key}.npz'))
        pred = s.run(['predicted_depth'], {'pixel_values': z['pixel_values']})[0][0]
        H, W = int(z['h']), int(z['w'])
        resized = np.asarray(
            Image.fromarray(pred, mode='F').resize((W, H), Image.Resampling.BILINEAR),
            dtype=np.float32)
        out[f'depthpro:{key}'] = {
            'depth_hw_cos': cos(resized, z['depth_hw']),
            'mean': round(float(pred.mean()), 3),
            'max': round(float(pred.max()), 3),
        }


def gate_bridge(out):
    for key, shape in (('_DSC5383', '784x518'), ('_DSC0975', '518x784')):
        s = sess(f'BRIDGE-onnx/bridge_fp32_{shape}.onnx')
        z = np.load(os.path.join(REF_DIR, f'bridge_{key}.npz'))
        raw = s.run(None, {'pixel_values': z['pixel_values']})[0]
        out[f'bridge:{key}'] = {'predicted_depth_cos': cos(raw, z['depth_int'])}


def gate_dad(out):
    for key, shape in (('_DSC5383', '784x518'), ('_DSC0975', '518x784'),
                       ('_DSC4194', '518x518')):
        s = sess(f'DistillAnyDepth-onnx/distillanydepth_fp32_{shape}.onnx')
        z = np.load(os.path.join(REF_DIR, f'distillanydepth_{key}.npz'))
        raw = s.run(None, {'pixel_values': z['pixel_values']})[0]
        out[f'dad:{key}'] = {'predicted_depth_cos': cos(raw, z['predicted_depth_int'])}


def gate_iris(out):
    base = 'Iris-onnx'
    s_te = sess(f'{base}/iris_text_encoder.onnx')
    s_ve = sess(f'{base}/iris_vae_encoder.onnx')
    s_un = sess(f'{base}/iris_unet.onnx')
    s_vd = sess(f'{base}/iris_vae_decoder.onnx')
    scaling = 0.18215
    for key in ('_DSC5383', '_DSC0975'):
        z = np.load(os.path.join(REF_DIR, f'iris_{key}.npz'))
        emb = s_te.run(None, {'input_ids': z['input_ids']})[0]
        moments = s_ve.run(None, {'rgb': z['rgb']})[0]
        latents = moments[:, :4].astype(np.float32) * scaling
        mid = s_un.run(None, {'sample': latents,
                              'timestep': np.array(999, dtype=np.int64),
                              'encoder_hidden_states': emb,
                              'class_labels': z['task_emb']})[0]
        final = s_un.run(None, {'sample': mid,
                                'timestep': np.array(499, dtype=np.int64),
                                'encoder_hidden_states': emb,
                                'class_labels': z['task_emb']})[0]
        img = s_vd.run(None, {'latent': final / scaling})[0]
        disp = np.clip(img[0] / 2.0 + 0.5, 0.0, 1.0).mean(axis=0)
        r = {
            'text_emb': cos(emb, z['prompt_embeds']),
            'latents': cos(latents, z['latents']),
            'mid': cos(mid, z['mid']),
            'final': cos(final, z['final']),
            'img': cos(img, z['img']),
        }
        # 参考 disparity 为 nearest 放大回原尺寸；这里同法放大后再比
        H0, W0 = int(z['disparity'].shape[0]), int(z['disparity'].shape[1])
        disp_orig = np.asarray(
            Image.fromarray(disp, mode='F').resize((W0, H0), Image.Resampling.NEAREST),
            dtype=np.float32)
        r['disparity'] = cos(disp_orig, z['disparity'])
        out[f'iris:{key}'] = r


def positions_grid(hg, wg):
    ys, xs = np.meshgrid(np.arange(hg), np.arange(wg), indexing='ij')
    return np.stack([ys.reshape(-1), xs.reshape(-1)], axis=-1).astype(np.int64)[None]


def gate_ppd(out):
    base = 'PPD-onnx'
    s_sem = sess(f'{base}/ppd_moge_sem.onnx')
    s_dit = sess(f'{base}/ppd_dit_step.onnx')
    z = np.load(os.path.join(REF_DIR, 'ppd__DSC5383.npz'))
    image = z['image']
    H, W = int(image.shape[2]), int(image.shape[3])
    ts = [int(t) for t in z['timesteps'].tolist()]
    semantics = s_sem.run(None, {'image': image})[0]
    pos0 = positions_grid(H // 16, W // 16)
    pos1 = positions_grid(H // 8, W // 8)
    latent = z['latent0'].copy()
    cond = image - 0.5
    r = {'semantics': cos(semantics, z['semantics'])}
    for i, t in enumerate(ts):
        nxt = ts[i + 1] if i + 1 < len(ts) else -1
        pred = s_dit.run(None, {'x': np.concatenate([latent, cond], axis=1),
                                'semantics': semantics,
                                'timestep': np.array([t], dtype=np.int32),
                                'pos0': pos0, 'pos1': pos1})[0]
        r[f'pred{i}_t{t}'] = cos(pred, z['preds'][i])
        A, B = 1.0 - t / 1000.0, t / 1000.0
        x0 = latent - B * pred
        xT = latent + A * pred
        s = max(0.0, min(float(nxt), 1000.0))
        xs = (1 - s / 1000.0) * x0 + (s / 1000.0) * xT
        if nxt < 0:
            xs = x0
        elif nxt > 1000.0:
            xs = xT
        latent = xs.astype(np.float32)
        r[f'lat{i}_t{t}'] = cos(latent, z['lats'][i + 1])
    depth = latent + 0.5
    r['depth'] = cos(depth, z['depth'])
    out['ppd:_DSC5383'] = r


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', nargs='*', default=None,
                        help='只跑指定模型（默认全部）')
    parser.add_argument('--worker', action='store_true',
                        help='内部参数：在子进程中执行单模型门（父进程每模型起一个进程，隔离内存）')
    parser.add_argument('--ref-dir', default=None,
                        help='参考夹具目录（默认取 DEPTH_ONNX_REF_DIR，其次 ../onnx_ref）')
    parser.add_argument('--models-src', default=None,
                        help='ONNX 权重源目录（默认取 DEPTH_MODELS_SRC，其次 ../models）')
    args = parser.parse_args()

    global REF_DIR, MODELS_SRC
    if args.ref_dir:
        REF_DIR = args.ref_dir
    if args.models_src:
        MODELS_SRC = args.models_src
    for label, path in (('参考夹具目录', REF_DIR), ('ONNX 权重源目录', MODELS_SRC)):
        if not os.path.isdir(path):
            raise SystemExit(
                f'{label}不存在: {path}\n'
                '本仓库不附带参考夹具与 ONNX 权重，请用 --ref-dir / --models-src '
                '或环境变量 DEPTH_ONNX_REF_DIR / DEPTH_MODELS_SRC 指定。')

    runners = {'depthpro': gate_depthpro, 'bridge': gate_bridge,
               'distillanydepth': gate_dad, 'iris': gate_iris, 'ppd': gate_ppd}

    if args.worker:
        # 子进程：只跑一个模型，避免多模型会话同时驻留内存（数 GB 权重 × N）
        name = os.environ['GATE_WORKER_MODEL']
        out = {}
        try:
            runners[name](out)
        except Exception as exc:
            import traceback
            out[name] = {'gate_error': f'{type(exc).__name__}: {exc}'}
            traceback.print_exc()
        with open(os.path.join(SRC_WIN, f'gate_{name}.json'), 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        return 0

    import subprocess as sp
    models = list(runners) if not args.models else args.models
    out = {}
    for name in models:
        # 把解析后的两个目录透传给 worker，否则子进程会回落到默认值。
        env = dict(os.environ, GATE_WORKER_MODEL=name,
                   DEPTH_ONNX_REF_DIR=REF_DIR, DEPTH_MODELS_SRC=MODELS_SRC)
        print(f'== gating {name} (subprocess) ...', flush=True)
        code = sp.call([sys.executable, os.path.abspath(__file__), '--worker'],
                       env=env, cwd=SRC_WIN)
        path = os.path.join(SRC_WIN, f'gate_{name}.json')
        if os.path.isfile(path):
            with open(path, encoding='utf-8') as f:
                out.update(json.load(f))
            os.remove(path)
        if code != 0:
            out.setdefault(name, {})['gate_error'] = f'worker exit code {code}'

    report_path = os.path.join(SRC_WIN, 'gate_results.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'saved: {report_path}')

    def check(entry, path):
        if isinstance(entry, dict) and 'cos' not in entry and not entry.get('gate_error'):
            for k2, v2 in entry.items():
                check(v2, path + '/' + k2)
            return
        name = path
        if isinstance(entry, dict) and 'shape_mismatch' in entry:
            print(f'  ✗ {name}: SHAPE MISMATCH {entry["shape_mismatch"]}')
            return
        val = entry if isinstance(entry, (int, float)) else None
        if val is None and isinstance(entry, dict):
            val = next((v for v in entry.values() if isinstance(v, (int, float))), None)
        status = '✓' if (val is not None and val >= 0.99) else '✗'
        print(f'  {status} {name}: cos={val}')

    ok = True
    for group, entries in out.items():
        if isinstance(entries, dict) and entries.get('gate_error'):
            print(f'  ✗ {group}: GATE ERROR {entries["gate_error"]}')
            ok = False
            continue
        for key, entry in entries.items():
            if isinstance(entry, dict) and 'gate_error' in entry:
                print(f'  ✗ {group}:{key}: GATE ERROR')
                ok = False
                continue
            check(entry, f'{group}:{key}')
            for sub in (entry.values() if isinstance(entry, dict) else []):
                if isinstance(sub, dict) and ('shape_mismatch' in sub or
                                              (sub.get('cos') is not None and sub['cos'] < 0.99)):
                    ok = False
    print('GATE:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
