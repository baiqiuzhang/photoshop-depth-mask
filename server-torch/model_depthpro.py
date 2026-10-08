# -*- coding: utf-8 -*-
"""Depth Pro（Apple，transformers Hub 布局）适配器。

沿用 v6 已验证的加载/预处理/后处理算术：
- 输入 bicubic 缩放到 1536×1536，归一化 (x-0.5)/0.5
- post_process_depth_estimation 返回 1/clamp(pred)（米制，远=高值），
  以 target_sizes=(1536,1536) 取原生输出（不放大，放大交给超分阶段）
"""
import os

import numpy as np


def infer(image_np, model_root, force_cpu=False):
    import torch
    import torch.nn.functional as F
    from transformers import DepthProImageProcessor, DepthProForDepthEstimation

    device = torch.device('cpu' if force_cpu else ('cuda' if torch.cuda.is_available() else 'cpu'))
    if device.type == 'cpu':
        try:
            torch.set_num_threads(max(1, os.cpu_count() or 1))
            torch.set_num_interop_threads(1)
        except Exception:
            pass

    processor = DepthProImageProcessor.from_pretrained(model_root)
    model = DepthProForDepthEstimation.from_pretrained(
        model_root, torch_dtype=torch.float32
    ).to(device).eval()

    x = torch.from_numpy(image_np).permute(2, 0, 1).unsqueeze(0).float()
    if tuple(image_np.shape[:2]) != (1536, 1536):
        x = F.interpolate(x, size=(1536, 1536), mode='bicubic',
                          antialias=False, align_corners=False)
    x = (x - 0.5) / 0.5
    with torch.no_grad():
        outputs = model(pixel_values=x.to(device))
    for field in ('predicted_depth', 'field_of_view'):
        value = getattr(outputs, field, None)
        if isinstance(value, torch.Tensor):
            setattr(outputs, field, value.float())
    result = processor.post_process_depth_estimation(
        outputs, target_sizes=[(1536, 1536)])
    depth = result[0]['predicted_depth'].cpu().numpy().astype(np.float32)
    return depth, {'device': device.type, 'native_shape': list(depth.shape)}
