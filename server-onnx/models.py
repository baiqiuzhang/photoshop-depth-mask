# -*- coding: utf-8 -*-
"""
模型注册表与寻址检测（协议 7 模块化多模型，ONNX 分支）
=====================================================
- MODEL_SPECS：5 个模型的元数据（文件夹名、权重标记、方向约定）。
- 权重文件夹命名沿用 papers/深度实验/模型 的 *-onnx 导出目录：
  BRIDGE-onnx / DepthPro-onnx / DistillAnyDepth-onnx / Iris-onnx / PPD-onnx。
- 本分支只读取 ONNX 权重（.onnx + 外置 .onnx.data），不读取任何 torch 权重。
- 方向约定统一为"远亮"（far = 高值）：bridge/distillanydepth/iris 反转，
  depthpro/ppd 不反转，与 torch 版 models.py 完全一致。
"""
import os

MODEL_SPECS = {
    'bridge': {
        'folder': 'BRIDGE-onnx',
        'display': 'BRIDGE MDE',
        'markers': ['bridge_fp32_784x518.onnx',
                    'bridge_fp32_518x784.onnx',
                    'bridge_fp32.onnx_data'],
        'source_dirs': [],
        'invert_direction': True,
        'resolution_note': 'ONNX 518 级（784×518 / 518×784）',
    },
    'depthpro': {
        'folder': 'DepthPro-onnx',
        'display': 'Depth Pro',
        'markers': ['depthpro_fp32.onnx', 'depthpro_fp32.onnx.data'],
        'source_dirs': [],
        'invert_direction': False,
        'resolution_note': 'ONNX 1536×1536',
    },
    'distillanydepth': {
        'folder': 'DistillAnyDepth-onnx',
        'display': 'DistillAnyDepth',
        'markers': ['distillanydepth_fp32_518x518.onnx',
                    'distillanydepth_fp32_518x784.onnx',
                    'distillanydepth_fp32_784x518.onnx',
                    'distillanydepth_fp32.onnx_data'],
        'source_dirs': [],
        'invert_direction': True,
        'resolution_note': 'ONNX 518 级（三形状）',
    },
    'iris': {
        'folder': 'Iris-onnx',
        'display': 'Iris',
        'markers': ['iris_unet.onnx', 'iris_text_encoder.onnx',
                    'iris_vae_encoder.onnx', 'iris_vae_decoder.onnx'],
        'source_dirs': [],
        'invert_direction': True,
        'resolution_note': 'ONNX 固定 768×1024',
    },
    'ppd': {
        'folder': 'PPD-onnx',
        'display': 'Pixel-Perfect Depth',
        'markers': ['ppd_moge_sem.onnx', 'ppd_dit_step.onnx'],
        'source_dirs': [],
        'invert_direction': False,
        'resolution_note': 'ONNX 固定 1024×768',
    },
}

MODEL_KEYS = tuple(MODEL_SPECS)

# 插件目录名（嵌套/父目录候选时同时识别 torch 版与 ONNX 分支）
_PLUGIN_FOLDER_NAMES = ('com.zk21.depthpro', 'com.zk21.depthpro.windows')


def root_candidates(base_dir, source_dir):
    """候选模型根目录链：env → exe/源码目录 → 同目录插件目录 → 父目录插件目录。"""
    roots = []
    env_root = os.environ.get('DEPTH_MODEL_ROOT')
    if env_root:
        roots.append(env_root)
    for item in (base_dir, source_dir):
        if item and item not in roots:
            roots.append(item)
    for item in (base_dir, source_dir):
        if not item:
            continue
        for plugin_name in _PLUGIN_FOLDER_NAMES:
            nested = os.path.join(item, plugin_name)
            if nested not in roots:
                roots.append(nested)
    if base_dir:
        for plugin_name in _PLUGIN_FOLDER_NAMES:
            parent = os.path.join(os.path.dirname(base_dir), plugin_name)
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
    """把该模型的源码子目录加入 sys.path（ONNX 分支无模型源码，保留接口为空实现）。"""
    import sys
    for sub in MODEL_SPECS[key].get('source_dirs', []):
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
