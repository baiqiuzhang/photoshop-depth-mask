# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for protocol-v7 ONNX-windows server (com.zk21.depthpro.windows).

- 运行时只依赖 onnxruntime-directml（含 DML + CPU 两个 EP）+ numpy/PIL/scipy/sklearn。
- 不打包 torch/transformers/diffusers 等任何重型运行时（模型为 ONNX 权重，插件根目录加载）。
"""
import glob
import os
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules, copy_metadata

root = os.path.abspath(SPECPATH)

# 服务源码模块（模型适配器在子进程内按需动态导入，需显式点名）
hiddenimports = [
    "models",
    "model_bridge",
    "model_depthpro",
    "model_distillanydepth",
    "model_iris",
    "model_ppd",
    "onnx_common",
    "postprocess",
    "upscale_sr",
    "img_io",
    "histogram_eq",
    # /ping 的 rss_mb（histogram_eq 之外的 psutil 唯一用处）
    "psutil",
    # histogram_eq 在函数体内惰性导入，逐个点名以免被静态分析漏掉：
    # 漏掉即退化为「直方图均衡化失败，回退归一化深度」（depth_server.py:312）
    "scipy",
    "scipy.ndimage",
    "scipy.stats",
    "scipy.optimize",
    "scipy.signal",
    "sklearn",
    "sklearn.cluster",
    "sklearn.mixture",
    "joblib",
    "threadpoolctl",
]
hiddenimports += collect_submodules("numpy.f2py")

datas = []
binaries = []

# onnxruntime-directml：全量收集 capi DLL（含 DirectML.dll / onnxruntime.dll /
# onnxruntime_providers_shared.dll），保持包内相对路径
binaries += collect_dynamic_libs("onnxruntime")
try:
    datas += copy_metadata("onnxruntime")
except Exception:
    pass

# 发行元数据补齐（PyInstaller 冻结环境 metadata 查询用）
for _meta in ("scipy", "scikit-learn", "Pillow", "flask", "psutil",
              "joblib", "threadpoolctl", "numpy"):
    try:
        datas += copy_metadata(_meta)
    except Exception:
        pass

# conda 环境 MKL DLL（scipy/sklearn 数值内核）
library_bin = os.path.join(os.environ.get("CONDA_PREFIX", sys.prefix), "Library", "bin")
mkl_patterns = (
    "mkl_core.2.dll", "mkl_intel_thread.2.dll", "mkl_avx2.2.dll",
    "mkl_avx512.2.dll", "mkl_def.2.dll", "mkl_mc3.2.dll",
    "mkl_vml_*.2.dll", "libiomp5md.dll",
)
for pattern in mkl_patterns:
    for source in glob.glob(os.path.join(library_bin, pattern)):
        binaries.append((source, "."))

a = Analysis(
    [os.path.join(root, "depth_server.py")],
    pathex=[root],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 本分支不携带任何 torch 系/重模型运行时（权重为 ONNX，运行时从插件目录加载）
        "torch", "torchvision", "torchaudio", "transformers", "diffusers",
        "accelerate", "peft", "timm", "safetensors", "einops", "addict",
        "utils3d", "utils3d_moge", "omegaconf", "imageio",
        "gradio", "open3d", "evo", "pycolmap", "xformers",
        "tensorboard", "tensorboardX",
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "matplotlib", "pandas", "notebook", "jupyter", "jupyterlab",
        "nbconvert", "nbclient", "ipykernel", "IPython", "streamlit",
        "tkinter", "PIL.ImageQt",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="depth_server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="depth_server",
)
