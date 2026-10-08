# -*- coding: utf-8 -*-
"""DistillAnyDepth（arXiv 2502.19204，transformers Hub 布局）适配器。

直接使用 DepthAnythingForDepthEstimation + DPTImageProcessor（不走 pipeline）：
- pipeline 的 postprocess 会把深度转成 256 级 PIL 灰度（实验实测 raw_unique≈256），
  直连则拿到连续 float32 深度，16-bit 蒙版质量更高
- 输入按 preprocessor_config 短边 518 保比例（keep_aspect_ratio, ensure_multiple_of 14）
- 输出取模型内部 518 级分辨率（target_sizes=内部尺寸），放大交给超分阶段
"""
import os

import numpy as np
from PIL import Image


def infer(image_np, model_root, force_cpu=False):
    import torch
    from transformers import DepthAnythingForDepthEstimation, DPTImageProcessor

    device = torch.device('cpu' if force_cpu else ('cuda' if torch.cuda.is_available() else 'cpu'))
    # 权重位于 DistillAnyDepth/权重/（transformers Hub 布局）
    weight_root = os.path.join(model_root, '权重')

    processor = DPTImageProcessor.from_pretrained(weight_root)
    model = DepthAnythingForDepthEstimation.from_pretrained(weight_root).to(device).eval()

    img_u8 = (np.clip(image_np, 0.0, 1.0) * 255.0).astype(np.uint8)
    pil = Image.fromarray(img_u8)
    inputs = processor(images=pil, return_tensors='pt')
    nh, nw = int(inputs['pixel_values'].shape[-2]), int(inputs['pixel_values'].shape[-1])
    with torch.no_grad():
        outputs = model(**inputs.to(device))
    result = processor.post_process_depth_estimation(outputs, target_sizes=[(nh, nw)])
    depth = result[0]['predicted_depth'].cpu().numpy().astype(np.float32)
    return depth, {'device': device.type, 'native_shape': [nh, nw]}
