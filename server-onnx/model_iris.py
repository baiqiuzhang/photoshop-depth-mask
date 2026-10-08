# -*- coding: utf-8 -*-
"""Iris（CVPR 2026，Lotus 式两阶段确定性扩散）ONNX 适配器。

ONNX 拆为 4 个子图（与 papers/深度实验/scripts/verify_iris_onnx.py 宿主一致）：
  text_encoder(input_ids 空串 BOS+EOS (1,2)) -> emb
  vae_encoder(rgb) -> moments，取前 4 通道均值 × scaling_factor(0.18215)
  unet(t=999) -> unet(t=499)（两次调用共用一张图，无扩散循环）
  vae_decoder(final / scaling) -> rgb，/2+0.5 clip -> 通道均值 = 视差
- 固定 768×1024（unet 隐空间 96×128），非该比例的输入 letterbox 后裁剪。
- 输出取内部处理分辨率（裁剪后），放大交给超分阶段。
- 方向反转（远亮）由 models.py invert_direction 统一处理。
"""
import os

import numpy as np

BOX = (768, 1024)                                   # (th, tw) 固定子图分辨率
SCALING = 0.18215                                   # SD2-base VAE scaling_factor
BOS_EOS = np.array([[49406, 49407]], dtype=np.int64)  # 空 prompt -> BOS+EOS
TASK_EMB = np.array([[np.sin(1.0), np.sin(0.0), np.cos(1.0), np.cos(0.0)]],
                    dtype=np.float32)


def infer(image_np, model_root, force_cpu=False):
    import onnx_common

    base = model_root
    s_te, d_te, n_te = onnx_common.get_session(
        os.path.join(base, 'iris_text_encoder.onnx'), force_cpu)
    s_ve, d_ve, n_ve = onnx_common.get_session(
        os.path.join(base, 'iris_vae_encoder.onnx'), force_cpu)
    s_un, d_un, n_un = onnx_common.get_session(
        os.path.join(base, 'iris_unet.onnx'), force_cpu)
    s_vd, d_vd, n_vd = onnx_common.get_session(
        os.path.join(base, 'iris_vae_decoder.onnx'), force_cpu)

    lb, rh, rw, pad_top, pad_left = onnx_common.letterbox_float(
        image_np, BOX, pad_value=0.0)
    rgb = (lb * 2.0 - 1.0).astype(np.float32)        # [0,1] -> [-1,1]
    rgb = onnx_common.channel_last_to_batch_chw(rgb)  # (1,3,768,1024)

    emb = np.asarray(s_te.run(None, {'input_ids': BOS_EOS})[0])          # (1,2,1024)
    moments = np.asarray(s_ve.run(None, {'rgb': rgb})[0])                # (1,8,96,128)
    latents = moments[:, :4].astype(np.float32) * SCALING                # 均值 latent
    mid = np.asarray(s_un.run(None, {'sample': latents,
                                     'timestep': np.array(999, dtype=np.int64),
                                     'encoder_hidden_states': emb,
                                     'class_labels': TASK_EMB})[0])
    final = np.asarray(s_un.run(None, {'sample': mid,
                                       'timestep': np.array(499, dtype=np.int64),
                                       'encoder_hidden_states': emb,
                                       'class_labels': TASK_EMB})[0])
    img = np.asarray(s_vd.run(None, {'latent': final / SCALING})[0])     # (1,3,768,1024)
    disp = np.clip(img[0] / 2.0 + 0.5, 0.0, 1.0)                        # (3,768,1024)
    disp = disp.mean(axis=0)
    depth = onnx_common.crop_letterbox(disp, rh, rw, pad_top, pad_left)
    return depth.astype(np.float32), {
        'device': 'dml' if d_un == 'dml' else 'cpu',
        'native_shape': [int(rh), int(rw)],
        'provider': f'{n_te} | {n_ve} | {n_un} | {n_vd}',
    }
