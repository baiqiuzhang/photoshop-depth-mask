# -*- coding: utf-8 -*-
"""DistillAnyDepth（DepthAnything 蒸馏版）ONNX 适配器。

ONNX 图有三个固定形状：518² / 518×784 / 784×518，共享同一外置数据。
- 预处理与 DPTImageProcessor 一致：/255 → ImageNet 归一化。
- 非支持比例输入按宽高比选最近形状，letterbox 后裁剪（见 onnx_common）。
- 输出取模型内部 518 级分辨率（不放大，放大交给超分阶段）。
"""
import os

import numpy as np


def infer(image_np, model_root, force_cpu=False):
    import onnx_common

    h, w = image_np.shape[:2]
    ratio = w / max(1, h)
    if ratio > 784 / 518:
        box = (518, 784)                                  # 宽扁
    elif ratio < 518 / 784:
        box = (784, 518)                                  # 高瘦
    else:
        box = (518, 518)
    model_h, model_w = box
    path = os.path.join(model_root, f'distillanydepth_fp32_{model_h}x{model_w}.onnx')
    session, device, note = onnx_common.get_session(path, force_cpu)

    lb, rh, rw, pad_top, pad_left = onnx_common.letterbox_float(image_np, box, pad_value=0.5)
    x = (lb - onnx_common.IMAGENET_MEAN) / onnx_common.IMAGENET_STD
    feed = {'pixel_values': onnx_common.channel_last_to_batch_chw(x)}
    depth = np.asarray(session.run(None, feed)[0])[0]     # (th,tw)
    depth = onnx_common.crop_letterbox(depth, rh, rw, pad_top, pad_left)
    return depth.astype(np.float32), {
        'device': device,
        'native_shape': [int(rh), int(rw)],
        'provider': note,
        'onnx_shape': [model_h, model_w],
    }
