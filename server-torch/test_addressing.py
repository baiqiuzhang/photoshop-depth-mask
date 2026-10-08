# -*- coding: utf-8 -*-
"""寻址鲁棒性纯函数验证：4 种布局 + env 覆盖 + 阴性用例（不加载模型，仅 os.path 检查）。"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import models


def make_weights(root, folder):
    d = os.path.join(root, folder)
    markers = {
        'BRIDGE': ['权重/bridge.pth'],
        'DepthPro': ['model.safetensors', 'config.json'],
        'DistillAnyDepth': ['权重/model.safetensors', '权重/config.json'],
        'Iris': ['权重/unet/diffusion_pytorch_model.safetensors',
                 '权重/text_encoder/model.safetensors'],
        'PPD': ['ppd_moge.pth', 'moge2.pt'],
    }[folder]
    for m in markers:
        p = os.path.join(d, m)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'w').close()


def clear_env():
    os.environ.pop('DEPTH_MODEL_ROOT', None)


tmp = tempfile.mkdtemp(prefix='addr_test_')
try:
    results = []
    # 布局1：模型与 exe 同目录
    clear_env()
    for spec in models.MODEL_SPECS.values():
        make_weights(tmp, spec['folder'])
    snap = models.detect_available_models(tmp, os.path.join(tmp, 'nosrc'))
    results.append(('布局1 exe旁', all(v['found'] for v in snap.values())))

    # 布局2：模型在 base_dir/com.zk21.depthpro/ 下
    clear_env()
    shutil.rmtree(tmp)
    os.makedirs(tmp)
    nested = os.path.join(tmp, 'com.zk21.depthpro')
    for spec in models.MODEL_SPECS.values():
        make_weights(nested, spec['folder'])
    snap = models.detect_available_models(tmp, tmp)
    results.append(('布局2 嵌套com.zk21', all(v['found'] for v in snap.values())))

    # 布局3：模型在父目录的 com.zk21.depthpro 下（源码运行场景）
    clear_env()
    shutil.rmtree(tmp)
    os.makedirs(tmp)
    parent = os.path.join(tmp, 'sub')
    os.makedirs(parent)
    outer = os.path.join(tmp, 'com.zk21.depthpro')
    for spec in models.MODEL_SPECS.values():
        make_weights(outer, spec['folder'])
    snap = models.detect_available_models(parent, parent)
    results.append(('布局3 父目录com.zk21', all(v['found'] for v in snap.values())))

    # 布局4：env DEPTH_MODEL_ROOT 覆盖
    shutil.rmtree(tmp)
    os.makedirs(tmp)
    env_root = os.path.join(tmp, 'models')
    for spec in models.MODEL_SPECS.values():
        make_weights(env_root, spec['folder'])
    os.environ['DEPTH_MODEL_ROOT'] = env_root
    snap = models.detect_available_models(os.path.join(tmp, 'elsewhere'),
                                          os.path.join(tmp, 'elsewhere'))
    results.append(('布局4 env覆盖', all(v['found'] for v in snap.values())))

    # 阴性：无任何模型
    clear_env()
    shutil.rmtree(tmp)
    os.makedirs(tmp)
    snap = models.detect_available_models(tmp, tmp)
    results.append(('阴性 无模型', all(not v['found'] for v in snap.values())))

    for name, ok in results:
        print(f'{name}: {"PASS" if ok else "FAIL"}')
finally:
    shutil.rmtree(tmp, ignore_errors=True)
