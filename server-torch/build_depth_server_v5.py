#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build and stage the protocol-v7 modular multi-model onedir server.

- 权重来源：papers/深度实验/模型（BRIDGE / DepthPro / DistillAnyDepth / Iris / PPD）
- DAD 仅复制 权重/（transformers 直连加载，源码 86M 不进入插件）
- Iris 源码在 staging 时打 vendor 补丁（移除 tensorboard / matplotlib 顶层导入）
- verify_exe_protocol 校验 /ping version == "7"；产出 build_manifest_v7.json
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if ROOT.name == "源码":
    PROJECT_ROOT = ROOT.parent
    SOURCE_ROOT = ROOT
else:
    PROJECT_ROOT = ROOT
    SOURCE_ROOT = ROOT / "源码" if (ROOT / "源码").is_dir() else ROOT
SPEC = SOURCE_ROOT / "depth_server_v5.spec"
CANDIDATE_DIST_ROOT = PROJECT_ROOT / "dist_v7_candidate"
CANDIDATE_BUILD_ROOT = PROJECT_ROOT / "build_v7_candidate"
DIST_DIR = CANDIDATE_DIST_ROOT / "depth_server"
# 打包成品目录（历史工作区位置）：可用 DEPTH_PLUGIN_DST 覆盖。
PLUGIN_ROOT = Path(os.environ.get("DEPTH_PLUGIN_DST",
                                  str(PROJECT_ROOT / "com.zk21.depthpro")))
# 前端素材来源：优先 DEPTH_FRONTEND_SRC；历史工作区在 com.zk21.depthpro/ 下，
# 本仓库（源码-only）把同一批前端文件放在 uxp/ 下。
FRONTEND_SRC = Path(os.environ.get(
    "DEPTH_FRONTEND_SRC",
    str(PLUGIN_ROOT if (PLUGIN_ROOT / "index.html").is_file() else PROJECT_ROOT / "uxp")))
# 权重源目录：优先环境变量 DEPTH_MODELS_SRC（普通克隆没有历史工作区，必须显式指定），
# 未设置时回落到历史工作区位置（与本脚本同级的 papers/深度实验/模型）。
MODELS_SRC = Path(os.environ.get(
    "DEPTH_MODELS_SRC", str(PROJECT_ROOT.parent / "papers" / "深度实验" / "模型")))

# 每个模型的插件内文件夹 -> (来源, 复制方式)
# 方式: whole(整个目录) | subdirs(仅列出的子目录)
MODEL_COPY = (
    ("BRIDGE", MODELS_SRC / "BRIDGE", ("权重", "源码")),
    ("DepthPro", MODELS_SRC / "DepthPro", None),
    ("DistillAnyDepth", MODELS_SRC / "DistillAnyDepth", ("权重",)),
    ("Iris", MODELS_SRC / "Iris", ("权重", "源码")),
    ("PPD", MODELS_SRC / "PPD", None),
)
FRONTEND_FILES = ("index.html", "main.js", "manifest.json", "启动深度服务.jsx", "icon.png")


def first_existing(*candidates):
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def copy_path(src: Path, dst: Path):
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def file_count_and_bytes(path: Path):
    total = 0
    count = 0
    if path.is_file():
        return 1, path.stat().st_size
    for item in path.rglob("*"):
        if item.is_file():
            count += 1
            total += item.stat().st_size
    return count, total


def sha256_file(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def patch_iris_source(iris_dir: Path):
    """移除 Iris 源码在推理路径上不需要的顶层导入（vendor 补丁，见 THIRD_PARTY_NOTICE）。"""
    pipeline = iris_dir / "源码" / "pipeline.py"
    if pipeline.is_file():
        text = pipeline.read_text(encoding="utf-8")
        if "import tensorboard" in text:
            text = text.replace("\nimport tensorboard\n", "\n")
            pipeline.write_text(text, encoding="utf-8")
            print("  ✂️  Iris pipeline.py: 移除 tensorboard 顶层导入")
    image_utils = iris_dir / "源码" / "utils" / "image_utils.py"
    if image_utils.is_file():
        text = image_utils.read_text(encoding="utf-8")
        if "import matplotlib\n" in text and "def colorize_depth_map" in text:
            text = text.replace("from PIL import Image\nimport matplotlib\n",
                                "from PIL import Image\n", 1)
            text = text.replace(
                "def colorize_depth_map(depth, mask=None, reverse_color=False):\n    cm = matplotlib.colormaps[\"Spectral\"]",
                "def colorize_depth_map(depth, mask=None, reverse_color=False):\n    import matplotlib  # lazy import: visualization-only, avoids bundling matplotlib\n    cm = matplotlib.colormaps[\"Spectral\"]",
                1,
            )
            image_utils.write_text(text, encoding="utf-8")
            print("  ✂️  Iris utils/image_utils.py: matplotlib 改为惰性导入")


def stage_models():
    staged = []
    for folder, src, parts in MODEL_COPY:
        if not src.is_dir():
            raise FileNotFoundError(f"模型权重来源缺失: {src}")
        dst = DIST_DIR / folder
        if dst.exists():
            shutil.rmtree(dst)
        if parts is None:
            shutil.copytree(src, dst)
            staged.append((folder, "whole"))
        else:
            dst.mkdir(parents=True, exist_ok=True)
            for part in parts:
                copy_path(src / part, dst / part)
            staged.append((folder, "+".join(parts)))
    # Iris vendor 补丁（staging 后立即应用）
    patch_iris_source(DIST_DIR / "Iris")
    return staged


def stage_frontend_and_docs():
    readme_src = first_existing(PROJECT_ROOT / "README.txt",
                                PROJECT_ROOT / "docs" / "README.zh-CN.txt")
    licenses_dir = first_existing(SOURCE_ROOT / "THIRD_PARTY_LICENSES",
                                  PROJECT_ROOT / "THIRD_PARTY_LICENSES")
    if not FRONTEND_SRC.is_dir():
        raise FileNotFoundError(
            f"前端素材目录缺失: {FRONTEND_SRC}（用 DEPTH_FRONTEND_SRC 指定）")
    for name in FRONTEND_FILES:
        copy_path(FRONTEND_SRC / name, DIST_DIR / name)
    copy_path(readme_src, DIST_DIR / "README.txt")
    copy_path(licenses_dir / "THIRD_PARTY_NOTICE.txt",
              DIST_DIR / "THIRD_PARTY_NOTICE.txt")
    copy_path(licenses_dir, DIST_DIR / "THIRD_PARTY_LICENSES")


def verify_dist():
    required = [DIST_DIR / "depth_server.exe"]
    for folder, src, parts in MODEL_COPY:
        markers = {
            "BRIDGE": ["权重/bridge.pth"],
            "DepthPro": ["model.safetensors", "config.json"],
            "DistillAnyDepth": ["权重/model.safetensors", "权重/config.json"],
            "Iris": ["权重/unet/diffusion_pytorch_model.safetensors",
                     "权重/text_encoder/model.safetensors"],
            "PPD": ["ppd_moge.pth", "moge2.pt"],
        }[folder]
        for marker in markers:
            required.append(DIST_DIR / folder / marker)
    for name in FRONTEND_FILES:
        required.append(DIST_DIR / name)
    required += [
        DIST_DIR / "THIRD_PARTY_NOTICE.txt",
        DIST_DIR / "THIRD_PARTY_LICENSES",
        DIST_DIR / "README.txt",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing staged runtime files:\n" + "\n".join(missing))


def verify_exe_protocol(exe_dir: Path, expected: str = "7", timeout: float = 180.0):
    """启动候选 EXE 并要求 /ping version == expected。"""
    import time
    import urllib.request

    exe = exe_dir / ("depth_server.exe" if os.name == "nt" else "depth_server")
    if not exe.exists():
        raise FileNotFoundError(f"No server executable to verify: {exe}")

    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/IM", "depth_server.exe"],
                       capture_output=True)
        time.sleep(1.0)

    proc = subprocess.Popen([str(exe)], cwd=str(exe_dir),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        info = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:8766/ping", timeout=3) as r:
                    info = json.loads(r.read().decode("utf-8"))
                    break
            except Exception:
                time.sleep(1.0)
        if info is None:
            raise RuntimeError(f"Server {exe} did not answer /ping within {timeout:.0f}s")
        got = str(info.get("version"))
        if got != expected:
            raise RuntimeError(
                f"EXE protocol mismatch: expected {expected}, got {got} from {exe}. "
                f"Do NOT publish this as protocol {expected}.")
        found = [m["key"] for m in info.get("available_models", []) if m.get("found")]
        print(f"Verified EXE protocol: /ping version={got}, models found={found}")
        return info
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/IM", "depth_server.exe"],
                           capture_output=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable,
                        help="Python interpreter used for PyInstaller build")
    parser.add_argument("--skip-pyinstaller", action="store_true",
                        help="Only restage runtime assets into an existing dist/depth_server")
    parser.add_argument("--source-dist-dir",
                        help="Existing PyInstaller dist/depth_server directory to restage from")
    parser.add_argument("--clean", action="store_true",
                        help="Remove the isolated v7 candidate build and dist directories")
    parser.add_argument("--no-verify-exe", action="store_true",
                        help="Skip live /ping protocol verification (not recommended)")
    args = parser.parse_args(argv)

    if args.clean:
        # 模型源码目录带只读 .git 对象文件，rmtree 会 PermissionError，先解除只读
        import stat
        for root in (CANDIDATE_BUILD_ROOT, CANDIDATE_DIST_ROOT):
            if not root.exists():
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                for name in filenames:
                    try:
                        os.chmod(os.path.join(dirpath, name), stat.S_IWRITE)
                    except OSError:
                        pass
        shutil.rmtree(CANDIDATE_BUILD_ROOT, ignore_errors=True)
        shutil.rmtree(CANDIDATE_DIST_ROOT, ignore_errors=True)

    if not args.skip_pyinstaller:
        command = [
            args.python, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", str(CANDIDATE_DIST_ROOT),
            "--workpath", str(CANDIDATE_BUILD_ROOT),
            str(SPEC),
        ]
        subprocess.run(command, cwd=ROOT, check=True)

    built_dist = Path(args.source_dist_dir) if args.source_dist_dir else DIST_DIR
    if not built_dist.exists():
        raise FileNotFoundError(f"PyInstaller output not found: {built_dist}")
    if built_dist != DIST_DIR:
        CANDIDATE_DIST_ROOT.mkdir(parents=True, exist_ok=True)
        if DIST_DIR.exists():
            shutil.rmtree(DIST_DIR)
        shutil.copytree(built_dist, DIST_DIR)

    staged_models = stage_models()
    stage_frontend_and_docs()
    verify_dist()

    if (args.skip_pyinstaller or args.source_dist_dir) and not args.no_verify_exe:
        verify_exe_protocol(DIST_DIR)

    source_files = [
        SOURCE_ROOT / "depth_server.py",
        SOURCE_ROOT / "models.py",
        SOURCE_ROOT / "model_bridge.py",
        SOURCE_ROOT / "model_depthpro.py",
        SOURCE_ROOT / "model_distillanydepth.py",
        SOURCE_ROOT / "model_iris.py",
        SOURCE_ROOT / "model_ppd.py",
        SOURCE_ROOT / "postprocess.py",
        SOURCE_ROOT / "img_io.py",
        SOURCE_ROOT / "histogram_eq.py",
        SPEC,
        Path(__file__).resolve(),
    ]
    artifact_files = [
        DIST_DIR / "depth_server.exe",
        DIST_DIR / "BRIDGE" / "权重" / "bridge.pth",
        DIST_DIR / "DepthPro" / "model.safetensors",
        DIST_DIR / "DistillAnyDepth" / "权重" / "model.safetensors",
        DIST_DIR / "Iris" / "权重" / "unet" / "diffusion_pytorch_model.safetensors",
        DIST_DIR / "PPD" / "ppd_moge.pth",
        DIST_DIR / "PPD" / "moge2.pt",
    ]
    manifest = {
        "dist_dir": str(DIST_DIR),
        "protocol_version": "7",
        "models": [{"folder": folder, "copy": mode} for folder, mode in staged_models],
        "source_sha256": {
            str(path.relative_to(ROOT)).replace("\\", "/"): sha256_file(path)
            for path in source_files
        },
        "artifact_sha256": {
            str(path.relative_to(DIST_DIR)).replace("\\", "/"): sha256_file(path)
            for path in artifact_files
        },
        "copied": [],
    }
    for folder, mode in staged_models:
        count, total = file_count_and_bytes(DIST_DIR / folder)
        manifest["copied"].append({"relative_path": folder, "file_count": count,
                                   "bytes": total})
    for name in FRONTEND_FILES:
        count, total = file_count_and_bytes(DIST_DIR / name)
        manifest["copied"].append({"relative_path": name, "file_count": count,
                                   "bytes": total})
    manifest_path = DIST_DIR / "build_manifest_v7.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    print(f"Staged build ready: {DIST_DIR}")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
