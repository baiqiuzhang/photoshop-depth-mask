# -*- coding: utf-8 -*-
"""Iris（CVPR 2026，diffusers DirectDiffusionPipeline 系）适配器。

与 深度实验 scripts/infer_iris.py 一致：
- IrisPipeline.from_pretrained(权重目录, torch_dtype=fp16)（实验最终跑 fp16）
- 预处理：RGB float -> /127.5-1，task_emb=[1,0] sin/cos 位置编码
- 推理：num_inference_steps=1, timesteps=[499,999], match_input_res=True
  （输出放大回输入分辨率，disparity 空间）
- CUDA OOM 时 processing_res 1536→1024→768 逐档降级
- 输出方向反转（远亮）由 models.py invert_direction 统一处理

注意：交付版 Iris/源码 已打 vendor 补丁（移除 tensorboard / matplotlib 顶层导入），
见 THIRD_PARTY_NOTICE.txt。
"""
import os
import sys

import numpy as np


def infer(image_np, model_root, force_cpu=False):
    import torch

    sys.path.insert(0, os.path.join(model_root, '源码'))
    from pipeline import IrisPipeline

    device = torch.device('cpu' if force_cpu else ('cuda' if torch.cuda.is_available() else 'cpu'))
    pipe = IrisPipeline.from_pretrained(
        os.path.join(model_root, '权重'), torch_dtype=torch.float16
    )
    pipe = pipe.to(device)
    pipe.set_progress_bar_config(disable=True)

    rgb = np.clip(image_np, 0.0, 1.0)
    rgb_t = torch.tensor((rgb * 255.0).astype(np.float32)).permute(2, 0, 1).unsqueeze(0)
    rgb_t = (rgb_t / 127.5 - 1.0).to(device)
    task_emb = torch.tensor([1, 0]).float().unsqueeze(0)
    task_emb = torch.cat([torch.sin(task_emb), torch.cos(task_emb)], dim=-1).to(device)

    diagnostics = {'device': device.type, 'attempts': []}
    for processing_res in (1536, 1024, 768):
        attempt = {'processing_res': processing_res}
        try:
            with torch.no_grad(), torch.autocast(device.type):
                out_final, _ = pipe(
                    rgb_in=rgb_t, prompt='', num_inference_steps=1, generator=None,
                    output_type='np', timesteps=[499, 999], task_emb=task_emb,
                    processing_res=processing_res, match_input_res=True,
                    resample_method='bilinear', return_intermediates=True, return_dict=True,
                )
            depth = np.asarray(out_final.images[0].mean(axis=-1), dtype=np.float32)
            attempt['ok'] = True
            diagnostics['attempts'].append(attempt)
            diagnostics['processing_res'] = processing_res
            diagnostics['native_shape'] = list(depth.shape)
            return depth, diagnostics
        except torch.cuda.OutOfMemoryError:
            attempt['ok'] = False
            attempt['error'] = 'OOM'
            diagnostics['attempts'].append(attempt)
            if processing_res > 768 and torch.cuda.is_available():
                torch.cuda.empty_cache()
                continue
            raise
        except Exception as exc:
            attempt['ok'] = False
            attempt['error'] = f'{type(exc).__name__}: {exc}'
            diagnostics['attempts'].append(attempt)
            lowered = ('out of memory' in str(exc).lower() or 'cuda' in str(exc).lower())
            if lowered and processing_res > 768 and torch.cuda.is_available():
                torch.cuda.empty_cache()
                continue
            raise
    raise RuntimeError('Iris 推理失败：所有 processing_res 档位均未成功')
