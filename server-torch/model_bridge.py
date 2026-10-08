# -*- coding: utf-8 -*-
"""BRIDGE MDE（arXiv 2509.25077）适配器。

官方 bridge/dpt.py 用法（与 深度实验 scripts/infer_bridge.py 一致）：
- 固定 input_size=518 短边保比例缩放（ensure_multiple_of 14, INTER_CUBIC）
- 必须 fp32：fp16 在 vitg 上溢出 → NaN
- 输出原生 518 级分辨率（不放大，放大交给超分阶段）
"""
import os
import sys

import numpy as np


def infer(image_np, model_root, force_cpu=False):
    import torch

    sys.path.insert(0, os.path.join(model_root, '源码'))
    from bridge.dpt import Bridge

    device = torch.device('cpu' if force_cpu else ('cuda' if torch.cuda.is_available() else 'cpu'))
    # 官方 image2tensor 接收 BGR uint8（内部 cvtColor BGR2RGB）
    img_bgr = (np.clip(image_np[:, :, ::-1], 0.0, 1.0) * 255.0).astype(np.uint8)
    pt = os.path.join(model_root, '权重', 'bridge.pth')

    model = Bridge()
    # 直接 map_location 到目标设备，避免 4.7G fp32 权重在 CPU 上的内存峰值
    model.load_state_dict(torch.load(pt, map_location=str(device)))
    model = model.to(device).eval()

    with torch.no_grad():
        image, _ = model.image2tensor(img_bgr, 518)
        image = image.to(device)
        ih, iw = image.shape[-2], image.shape[-1]
        native = model.forward(image)[0].reshape(ih, iw).cpu().numpy().astype(np.float32)
    return native, {'device': device.type, 'native_shape': [int(ih), int(iw)]}
