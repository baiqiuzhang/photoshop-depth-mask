# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for protocol-v7 modular multi-model server."""
import glob
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules


root = os.path.abspath(SPECPATH)

# 服务源码模块（模型适配器在子进程内按需动态导入，需显式点名）
hiddenimports = [
    "models",
    "model_bridge",
    "model_depthpro",
    "model_distillanydepth",
    "model_iris",
    "model_ppd",
    "postprocess",
    "upscale_sr",
    "img_io",
    "histogram_eq",
]
hiddenimports += collect_submodules("numpy.f2py")

datas = []
binaries = []
# 环境依赖（含 diffusers/accelerate/peft/timm/utils3d：动态导入多，collect_all 全量收集）
for package in ("cv2", "omegaconf", "safetensors", "imageio", "einops", "addict",
                "diffusers", "accelerate", "peft", "timm", "utils3d", "utils3d_moge"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# 冻结包内 importlib.metadata 查不到 dist-info 会导致
# "No package metadata was found for 'requests'"（diffusers/hub 运行时检查）。
# requests 模块本体已被收集，这里补齐其发行元数据。
from PyInstaller.utils.hooks import copy_metadata
for _meta_pkg in ("requests",):
    datas += copy_metadata(_meta_pkg)

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
        # 重型/无关包（模型源码在插件根目录运行时加载，不进 _internal）
        "gradio", "open3d", "evo", "pycolmap", "xformers",
        "tensorboard", "tensorboardX",
        # 阻断环境无关重包把 Qt/notebook 拖进导入图
        "PyQt5", "PyQt6", "PySide2", "PySide6", "PyQt5.QtWebEngineWidgets",
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
