# -*- coding: utf-8 -*-
"""Pixel-Perfect Depth（NeurIPS 2025，MoGe2 语义 + DiT 扩散）ONNX 适配器。

ONNX 拆为 2 个子图 + 宿主 Euler 采样循环（与 verify_ppd_onnx.py 逐式一致）：
  ppd_moge_sem.onnx: image -> semantics（MoGe2 语义特征）
  ppd_dit_step.onnx:  x=[latent, cond], semantics, timestep, pos0, pos1 -> velocity
- 固定 1024×768，非该比例的输入 letterbox 后裁剪。
- 初始 latent 用 numpy RandomState(0)（与参考一致；深度后处理为 min-max 归一化，
  种子只影响采样轨迹，不影响最终归一化结果）。
- timesteps [1000,750,500,249]；最后一步 nxt=-1 回退 pred_x_0（与官方 sampler 一致）。
- 输出 depth = latent + 0.5，取内部分辨率（裁剪后），放大交给超分阶段。
"""
import os

import numpy as np

BOX = (1024, 768)                                   # (th, tw) 固定子图分辨率
T = 1000.0
TIMESTEPS = [1000, 750, 500, 249]
SEED = 0


def positions_grid(hg, wg):
    """复刻 PositionGetter：cartesian_prod(arange(hg), arange(wg)) -> (1, hg*wg, 2) int64。"""
    ys, xs = np.meshgrid(np.arange(hg), np.arange(wg), indexing='ij')
    pos = np.stack([ys.reshape(-1), xs.reshape(-1)], axis=-1).astype(np.int64)
    return pos[None]


def infer(image_np, model_root, force_cpu=False):
    import onnx_common

    s_sem, d_sem, n_sem = onnx_common.get_session(
        os.path.join(model_root, 'ppd_moge_sem.onnx'), force_cpu)
    s_dit, d_dit, n_dit = onnx_common.get_session(
        os.path.join(model_root, 'ppd_dit_step.onnx'), force_cpu)

    lb, rh, rw, pad_top, pad_left = onnx_common.letterbox_float(
        image_np, BOX, pad_value=0.5)
    image = onnx_common.channel_last_to_batch_chw(lb)          # (1,3,1024,768) /255

    semantics = np.asarray(s_sem.run(None, {'image': image})[0])   # (1,3072,1024)
    H, W = BOX
    pos0 = positions_grid(H // 16, W // 16)
    pos1 = positions_grid(H // 8, W // 8)

    latent = np.random.RandomState(SEED).randn(1, 1, H, W).astype(np.float32)
    cond = image - 0.5
    for i, t in enumerate(TIMESTEPS):
        nxt = TIMESTEPS[i + 1] if i + 1 < len(TIMESTEPS) else -1   # 与 get_next_timestep 一致
        pred = np.asarray(s_dit.run(None, {
            'x': np.concatenate([latent, cond], axis=1),
            'semantics': semantics,
            'timestep': np.array([t], dtype=np.int32),
            'pos0': pos0, 'pos1': pos1,
        })[0])
        A, B = 1.0 - t / T, t / T
        x0 = latent - B * pred
        xT = latent + A * pred
        s = max(0.0, min(float(nxt), T))
        xs = (1 - s / T) * x0 + (s / T) * xT
        if nxt < 0:
            xs = x0
        elif nxt > T:
            xs = xT
        latent = xs.astype(np.float32)

    depth = (latent + 0.5)[0, 0]                               # (1024,768)
    depth = onnx_common.crop_letterbox(depth, rh, rw, pad_top, pad_left)
    return depth.astype(np.float32), {
        'device': 'dml' if d_dit == 'dml' else 'cpu',
        'native_shape': [int(rh), int(rw)],
        'provider': f'{n_sem} | {n_dit}',
    }
