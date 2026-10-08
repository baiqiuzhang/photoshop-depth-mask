# -*- coding: utf-8 -*-
"""Pixel-Perfect Depth（NeurIPS 2025，MoGe2 语义变体）适配器。

与 深度实验 scripts/infer_ppd.py 方案 A 一致：
- 推理目标面积 PROCESS_AREA（默认 1024×768 官方分辨率，scale 上限 1.0，小图不放大），
  monkey-patch resize_keep_aspect（transform 模块与 ppd 模块两处引用均需覆盖）
- 可用环境变量 DEPTH_PROCESS_AREA 覆盖（如 1536*1152 提质量，2048*1536 恢复原超采样）。
  注意：2048×1536 在本机（8GB 显存）峰值 VRAM 7.87/8.19GB 几乎贴顶，易触发 OOM 后
  服务器静默回退 CPU（数小时）；1024×768 时推理约 4 倍提速、显存约 2GB，稳妥。
- 输入传 BGR uint8（官方 run.py 约定）
- sampling_steps=4，set_seed(666) 固定采样可复现
- 输出取原生内部分辨率（不放大，放大交给超分阶段）
"""
import os
import sys

import numpy as np

PROCESS_AREA = int(os.environ.get('DEPTH_PROCESS_AREA', str(1024 * 768)))


def _resize_keep_aspect_hi(image):
    """替代 ppd.utils.transform.resize_keep_aspect：目标面积 PROCESS_AREA，不放大输入。"""
    import cv2
    ori_h, ori_w = image.shape[:2]
    scale = min(1.0, (PROCESS_AREA / float(ori_h * ori_w)) ** 0.5)
    rh = max(16, int(round(ori_h * scale / 16.0)) * 16)
    rw = max(16, int(round(ori_w * scale / 16.0)) * 16)
    if (rh, rw) == (ori_h, ori_w):
        return image
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    return cv2.resize(image, (rw, rh), interpolation=interp)


def infer(image_np, model_root, force_cpu=False):
    import torch

    sys.path.insert(0, os.path.join(model_root, '源码'))
    from ppd.models.ppd import PixelPerfectDepth
    from ppd.utils.set_seed import set_seed
    import ppd.utils.transform as transform_mod
    import ppd.models.ppd as ppd_mod

    set_seed(666)
    transform_mod.resize_keep_aspect = _resize_keep_aspect_hi
    ppd_mod.resize_keep_aspect = _resize_keep_aspect_hi

    device = torch.device('cpu' if force_cpu else ('cuda' if torch.cuda.is_available() else 'cpu'))
    if torch.cuda.is_available() and not force_cpu:
        torch.backends.cuda.enable_flash_sdp(True)
        torch.backends.cuda.enable_mem_efficient_sdp(True)
        torch.backends.cuda.enable_math_sdp(False)

    model = PixelPerfectDepth(
        semantics_model='MoGe2',
        semantics_pth=os.path.join(model_root, 'moge2.pt'),
        sampling_steps=4,
    )
    ckpt_path = os.path.join(model_root, 'ppd_moge.pth')
    try:
        state = torch.load(ckpt_path, map_location=str(device), weights_only=True)
    except Exception:
        # checkpoint 含非张量对象时回退旧模式（本地可信权重）
        state = torch.load(ckpt_path, map_location=str(device))
    model.load_state_dict(state, strict=False)
    model = model.to(device).eval()

    img_bgr = (np.clip(image_np[:, :, ::-1], 0.0, 1.0) * 255.0).astype(np.uint8)
    with torch.no_grad():
        depth, resize_image = model.infer_image(img_bgr)
        depth = depth.squeeze().float().cpu().numpy().astype(np.float32)
    return depth, {
        'device': device.type,
        'native_shape': list(depth.shape),
        'internal_shape': list(resize_image.shape[:2]),
    }
