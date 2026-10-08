# -*- coding: utf-8 -*-
"""
模型注册表与寻址检测（协议 7 模块化多模型）
================================================
- MODEL_SPECS：5 个模型的元数据（文件夹名、权重标记、源码子目录、方向约定）。
- 权重文件夹命名沿用 papers/深度实验/模型：BRIDGE / DepthPro / DistillAnyDepth / Iris / PPD。
- 权重不直接放在插件根目录，而是放在各自的"权重名称文件夹"下（含 权重/ 源码/ 子结构，
  DepthPro 为 transformers Hub 平铺布局）。
- 方向约定统一为"远亮"（far = 高值）：bridge/distillanydepth/iris 反转，depthpro/ppd 不反转，
  固化自 深度实验 scripts/INVERT.json 的实测结论。
"""
import os

MODEL_SPECS = {
    'bridge': {
        'folder': 'BRIDGE',
        'display': 'BRIDGE MDE',
        'markers': ['权重/bridge.pth'],
        'source_dirs': ['源码'],
        'invert_direction': True,
        'resolution_note': '短边 518（fp32）',
    },
    'depthpro': {
        'folder': 'DepthPro',
        'display': 'Depth Pro',
        'markers': ['model.safetensors', 'config.json'],
        'source_dirs': [],
        'invert_direction': False,
        'resolution_note': '1536×1536',
    },
    'distillanydepth': {
        'folder': 'DistillAnyDepth',
        'display': 'DistillAnyDepth',
        'markers': ['权重/model.safetensors', '权重/config.json'],
        'source_dirs': [],
        'invert_direction': True,
        'resolution_note': '短边 518',
    },
    'iris': {
        'folder': 'Iris',
        'display': 'Iris',
        'markers': ['权重/unet/diffusion_pytorch_model.safetensors',
                    '权重/text_encoder/model.safetensors'],
        'source_dirs': ['源码'],
        'invert_direction': True,
        'resolution_note': '长边 ≤1536（fp16）',
    },
    'ppd': {
        'folder': 'PPD',
        'display': 'Pixel-Perfect Depth',
        'markers': ['ppd_moge.pth', 'moge2.pt'],
        'source_dirs': ['源码'],
        'invert_direction': False,
        'resolution_note': '面积 2048×1536',
    },
}

MODEL_KEYS = tuple(MODEL_SPECS)


def root_candidates(base_dir, source_dir):
    """候选模型根目录链：env → exe/源码目录 → 同目录下 com.zk21.depthpro → 父目录。"""
    roots = []
    env_root = os.environ.get('DEPTH_MODEL_ROOT')
    if env_root:
        roots.append(env_root)
    for item in (base_dir, source_dir):
        if item and item not in roots:
            roots.append(item)
    for item in (base_dir, source_dir):
        if item:
            nested = os.path.join(item, 'com.zk21.depthpro')
            if nested not in roots:
                roots.append(nested)
    if base_dir:
        parent = os.path.join(os.path.dirname(base_dir), 'com.zk21.depthpro')
        if parent not in roots:
            roots.append(parent)
    return roots


def _weight_ok(folder_path, markers):
    return all(os.path.isfile(os.path.join(folder_path, marker)) for marker in markers)


def find_model_root(key, base_dir, source_dir):
    """返回该模型的权重文件夹绝对路径；未找到返回 None。"""
    spec = MODEL_SPECS[key]
    for root in root_candidates(base_dir, source_dir):
        folder = os.path.join(root, spec['folder'])
        if os.path.isdir(folder) and _weight_ok(folder, spec['markers']):
            return os.path.abspath(folder)
    return None


def detect_available_models(base_dir, source_dir):
    """启动时检视插件目录，返回 {key: {...}} 供 /ping 与前端确定选项。"""
    result = {}
    for key, spec in MODEL_SPECS.items():
        root = find_model_root(key, base_dir, source_dir)
        result[key] = {
            'key': key,
            'folder': spec['folder'],
            'display': spec['display'],
            'found': root is not None,
            'weight_ok': root is not None,
            'root': root,
            'resolution_note': spec['resolution_note'],
            'invert_direction': spec['invert_direction'],
        }
    return result


def add_source_dirs(key, model_root):
    """把该模型的源码子目录加入 sys.path（仅模型子进程调用）。"""
    import sys
    for sub in MODEL_SPECS[key]['source_dirs']:
        path = os.path.join(model_root, sub)
        if path not in sys.path:
            sys.path.insert(0, path)


def run_model(key, image_path, model_root, force_cpu=False):
    """在一次性模型子进程中执行推理；返回 (depth_f32_2d, diagnostics)。"""
    import importlib
    import numpy as np
    image_np = np.load(image_path, allow_pickle=False)
    add_source_dirs(key, model_root)
    module = importlib.import_module('model_' + key)
    depth, diagnostics = module.infer(image_np, model_root, force_cpu=bool(force_cpu))
    return np.asarray(depth, dtype=np.float32), diagnostics
