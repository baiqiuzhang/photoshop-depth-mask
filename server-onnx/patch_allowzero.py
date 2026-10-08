# -*- coding: utf-8 -*-
"""把 ONNX 图内 Reshape 的 allowzero 属性从 1 改为 0（DML 兼容修复）。

背景（已调研+实测）：DML EP 对 `allowzero=1` 且 shape 含 -1 的 Reshape 创建
kernel 时返回 E_INVALIDARG（0x80070057），导致 depthpro/distillanydepth/iris
图在 DirectML 上失败。这些 shape 均无零维，allowzero=1 与 0 数学完全等价，
改为 0 后输出逐位不变（DAD 已实测 maxdiff=0.0）。

本脚本只改小图文件（.onnx），不触碰外置权重文件（*.onnx.data）；
编辑前把 .onnx 备份到 <脚本目录>/onnx_originals/，并记录编辑前后 SHA-256。

用法：python patch_allowzero.py
"""
import hashlib
import os
import shutil
import sys
from pathlib import Path

import onnx

ROOT = Path(__file__).resolve().parent
MODELS_SRC = ROOT.parent.parent / 'papers' / '深度实验' / '模型'
BACKUP_DIR = ROOT / 'onnx_originals'

# 待处理图（按 模型目录/图文件）
TARGETS = [
    ('DepthPro-onnx', 'depthpro_fp32.onnx'),
    ('DistillAnyDepth-onnx', 'distillanydepth_fp32_518x518.onnx'),
    ('DistillAnyDepth-onnx', 'distillanydepth_fp32_518x784.onnx'),
    ('DistillAnyDepth-onnx', 'distillanydepth_fp32_784x518.onnx'),
    ('Iris-onnx', 'iris_unet.onnx'),
    ('Iris-onnx', 'iris_text_encoder.onnx'),
    ('Iris-onnx', 'iris_vae_encoder.onnx'),
    ('Iris-onnx', 'iris_vae_decoder.onnx'),
    ('BRIDGE-onnx', 'bridge_fp32_784x518.onnx'),
    ('BRIDGE-onnx', 'bridge_fp32_518x784.onnx'),
    ('PPD-onnx', 'ppd_moge_sem.onnx'),
    ('PPD-onnx', 'ppd_dit_step.onnx'),
]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def patch_graph(path):
    """加载图 → 内联模型本地函数（消除 onnxscript 自定义域）→ 全部 Reshape allowzero 置 0。

    返回 (patched_count, inlined_count, model)。allowzero 可能同时出现在顶层与
    函数体内，内联后再统一处理。
    """
    model = onnx.load(str(path), load_external_data=False)
    inlined = 0
    try:
        from onnx.inliner import inline_local_functions
        before = len(model.functions)
        model = inline_local_functions(model, convert_version=True)
        inlined = before - len(model.functions)
    except Exception as exc:
        print(f'  ⚠ 内联失败（继续 patch allowzero）: {exc}')
    patched = 0
    for node in model.graph.node:
        if node.op_type == 'Reshape':
            for attr in node.attribute:
                if attr.name == 'allowzero' and attr.i == 1:
                    attr.i = 0
                    patched += 1
    return patched, inlined, model


def main():
    BACKUP_DIR.mkdir(exist_ok=True)
    summary = []
    for folder, fname in TARGETS:
        src = MODELS_SRC / folder / fname
        if not src.is_file():
            print(f'  ✗ 缺失: {folder}/{fname}')
            continue
        before = sha256(src)
        patched, inlined, model = patch_graph(src)
        # 备份原始小图文件
        backup = BACKUP_DIR / folder / fname
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, backup)
        if patched == 0 and inlined == 0:
            print(f'  - {folder}/{fname}: allowzero=1 数量 0、无函数体，跳过')
            continue
        onnx.save(model, str(src))
        after = sha256(src)
        # 校验外置数据引用未被改动
        reloaded = onnx.load(str(src), load_external_data=False)
        refs = []
        for init in reloaded.graph.initializer:
            for entry in init.external_data:
                if entry.key == 'location':
                    refs.append(entry.value)
        print(f'  ✓ {folder}/{fname}: 内联函数 {inlined} 个 + 改 allowzero {patched} 处 '
              f'{before[:12]} -> {after[:12]} | 外置引用={sorted(set(refs))} | 残留自定义域节点='
              f'{sum(1 for n in reloaded.graph.node if n.domain)}')
        summary.append({'graph': f'{folder}/{fname}', 'patched': patched,
                        'inlined': inlined, 'sha_before': before, 'sha_after': after})
    print(f'\n备份目录: {BACKUP_DIR}')
    print(f'共处理 {len(summary)} 个图，修改 {sum(s["patched"] for s in summary)} 处 allowzero')


if __name__ == '__main__':
    sys.exit(main())
