# -*- coding: utf-8 -*-
"""把 ONNX 图内 rank-0（标量）Expand 改写为 Reshape+Tile 子图（DML 兼容修复，第二轮）。

背景（已实测）：DML EP 对 rank-0 Expand 创建 kernel 报 E_INVALIDARG
（0x80070057，如 iris_vae_encoder 的 node_Expand_666），导致 iris/depthpro 图
在 DirectML 上运行失败。标量 Expand(x, shape=[N]) 数学上等价于
Reshape(x,[1]) 后按 shape Tile 成 [N]（纯索引复制，无浮点运算，逐位一致）。
rank>=1 的 Expand DML 可正常跑（iris_text 93 个 rank-4 实测），保持不变。

本脚本只改小图文件（.onnx），不触碰外置权重（*.onnx.data）；编辑前备份到
<脚本目录>/onnx_originals/，记录编辑前后 SHA-256。

用法：python patch_expand_tile.py
"""
import hashlib
import os
import shutil
import sys
from pathlib import Path

import onnx
from onnx import helper

ROOT = Path(__file__).resolve().parent
MODELS_SRC = ROOT.parent.parent / 'papers' / '深度实验' / '模型'
BACKUP_DIR = ROOT / 'onnx_originals'

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


def _unique_name(base, taken):
    name = base
    i = 0
    while name in taken:
        i += 1
        name = f'{base}_{i}'
    taken.add(name)
    return name


def rewrite_expand_to_tile(model):
    """Expand(x, shape) 改写为 Add(x, ConstantOfShape(shape))。

    实测 DML 对 rank-0（标量）Expand 创建 kernel 报 E_INVALIDARG（node_Expand_666
    等），rank>=1 的 Expand 正常。任意秩的 Expand 数学上恒等于"目标形状的零张量
    + x"（numpy 广播语义与 Expand 一致，加 0 为恒等）。ConstantOfShape 与 Add
    均为 DML 原生算子。仅改写 rank-0 的 Expand，其余保留。
    """
    from onnx import shape_inference
    try:
        inf = shape_inference.infer_shapes(model)
        vi = {v.name: v for v in inf.graph.value_info}
    except Exception:
        vi = {}
    taken = set()
    for node in model.graph.node:
        for name in list(node.input) + list(node.output):
            if name:
                taken.add(name)
    for init in model.graph.initializer:
        taken.add(init.name)
    new_nodes = []
    changed = 0
    for node in model.graph.node:
        if node.op_type != 'Expand':
            new_nodes.append(node)
            continue
        x, shape = node.input[0], node.input[1]
        out = node.output[0]
        xv = vi.get(x)
        rank = len(xv.type.tensor_type.shape.dim) if xv else -1
        if rank != 0:
            new_nodes.append(node)          # 非标量 Expand 保留（DML 可跑）
            continue
        zeros = _unique_name(f'{out}_dml_zeros', taken)
        new_nodes.append(helper.make_node('ConstantOfShape', [shape], [zeros]))
        new_nodes.append(helper.make_node('Add', [x, zeros], [out]))
        changed += 1
    if changed:
        del model.graph.node[:]
        model.graph.node.extend(new_nodes)
    return changed


def main():
    BACKUP_DIR.mkdir(exist_ok=True)
    summary = []
    for folder, fname in TARGETS:
        src = MODELS_SRC / folder / fname
        if not src.is_file():
            print(f'  ✗ 缺失: {folder}/{fname}')
            continue
        before = sha256(src)
        model = onnx.load(str(src), load_external_data=False)
        changed = rewrite_expand_to_tile(model)
        if changed == 0:
            print(f'  - {folder}/{fname}: 无 Expand，跳过')
            continue
        backup = BACKUP_DIR / folder / fname
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, backup)
        onnx.save(model, str(src))
        after = sha256(src)
        reloaded = onnx.load(str(src), load_external_data=False)
        refs = sorted({e.value for init in reloaded.graph.initializer
                       for e in init.external_data if e.key == 'location'})
        tiles = sum(1 for n in reloaded.graph.node if n.op_type == 'Tile')
        expands = sum(1 for n in reloaded.graph.node if n.op_type == 'Expand')
        print(f'  ✓ {folder}/{fname}: Expand 改写 {changed} 处 '
              f'{before[:12]} -> {after[:12]} | 外置引用={refs} | 剩余 Expand={expands} Tile={tiles}')
        summary.append({'graph': f'{folder}/{fname}', 'expanded': changed,
                        'sha_before': before, 'sha_after': after})
    print(f'\n备份目录: {BACKUP_DIR}')
    print(f'共改写 {sum(s["expanded"] for s in summary)} 处 Expand')


if __name__ == '__main__':
    sys.exit(main())
