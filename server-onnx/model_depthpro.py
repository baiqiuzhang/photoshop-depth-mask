# -*- coding: utf-8 -*-
"""Depth Pro（Apple）ONNX 适配器（固定 1536×1536，fp32，CPU/DML 双可用）。

权重来源：onnx-community/DepthPro-ONNX 的 fp32 导出（opset 14 纯标准算子，
图内已烘焙 HF post_process_depth_estimation 的 fov 换算 + 1/clamp），
`predicted_depth` 输出即度量深度（远=高值），无需外部后处理。

预处理与 torch 版一致：bicubic 缩放到 1536² → /255 → (x-0.5)/0.5。
方向约定：depthpro 不反转（远亮），由 models.py invert_direction 统一处理。
"""
import os

import numpy as np


def infer(image_np, model_root, force_cpu=False):
    import onnx_common

    path = os.path.join(model_root, 'depthpro_fp32.onnx')
    session, device, note = onnx_common.get_session(path, force_cpu)

    x = onnx_common.resize_float(image_np, (1536, 1536), resample='bicubic')
    x = (x - 0.5) / 0.5
    feed = {'pixel_values': onnx_common.channel_last_to_batch_chw(x)}
    # 图内已烘焙 fov 换算 + 1/clamp，predicted_depth 即度量深度（直接使用）
    depth = np.asarray(session.run(['predicted_depth'], feed))[0]
    return depth.astype(np.float32), {
        'device': device,
        'native_shape': [1536, 1536],
        'provider': note,
        'model_source': 'onnx-community/DepthPro-ONNX fp32 (图内烘焙后处理)',
    }
