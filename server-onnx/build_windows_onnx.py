#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build and stage the protocol-v7 ONNX-windows onedir server (com.zk21.depthpro.windows).

- 权重来源：papers/深度实验/模型 下的 *-onnx 目录（只读 ONNX，不读 torch 权重）
- 前端：从 com.zk21.depthpro 原样复制（manifest 用本分支的 manifest_windows.json）
- verify_exe_protocol 校验 /ping version == "7"；产出 build_manifest_windows.json
- --dry-run：只打印复制计划与大小，不复制、不构建
"""
import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
SPEC = ROOT / "depth_server_windows.spec"
CANDIDATE_DIST_ROOT = PROJECT_ROOT / "dist_win_candidate"
CANDIDATE_BUILD_ROOT = PROJECT_ROOT / "build_win_candidate"
DIST_DIR = CANDIDATE_DIST_ROOT / "depth_server"
PLUGIN_DST = PROJECT_ROOT / "com.zk21.depthpro.windows"
# 前端素材来源：优先 DEPTH_FRONTEND_SRC；历史工作区在 com.zk21.depthpro/ 下，
# 本仓库（源码-only）把同一批前端文件放在 uxp/ 下。
PLUGIN_SRC = Path(os.environ.get(
    "DEPTH_FRONTEND_SRC",
    str(PROJECT_ROOT / "com.zk21.depthpro")
    if (PROJECT_ROOT / "com.zk21.depthpro" / "index.html").is_file()
    else str(PROJECT_ROOT / "uxp")))


def _resolve_models_src():
    """ONNX 权重来源，按顺序尝试：
    1) 环境变量 DEPTH_MODELS_SRC（普通克隆应使用这一条）；
    2) 历史工作区位置 papers/深度实验/模型；
    3) 工作区插件目录 com.zk21.depthpro.windows 现存的 *-onnx 权重
       （2026-09-19：论文目录已删除，插件目录即唯一现存副本）。"""
    env_src = os.environ.get("DEPTH_MODELS_SRC")
    candidates = ([Path(env_src)] if env_src else []) + [
        PROJECT_ROOT.parent / "papers" / "深度实验" / "模型",
        PROJECT_ROOT / "com.zk21.depthpro.windows",
    ]
    for candidate in candidates:
        if (candidate / "BRIDGE-onnx").is_dir():
            return candidate
    raise FileNotFoundError(
        "ONNX 权重来源缺失（已尝试: " + "; ".join(str(c) for c in candidates)
        + "）；请用环境变量 DEPTH_MODELS_SRC 指向含 *-onnx 目录的路径。")


MODELS_SRC = _resolve_models_src()

# 每个模型的插件内文件夹 -> 来源（整个 -onnx 目录）
MODEL_COPY = (
    ("BRIDGE-onnx", "BRIDGE-onnx"),
    ("DepthPro-onnx", "DepthPro-onnx"),
    ("DistillAnyDepth-onnx", "DistillAnyDepth-onnx"),
    ("Iris-onnx", "Iris-onnx"),
    ("PPD-onnx", "PPD-onnx"),
)
FRONTEND_FILES = ("index.html", "main.js", "common.js", "png_codec.js",
                  "mix_core.js", "mix_extract.jsx", "启动深度服务.jsx", "icon.png")
MARKERS = {
    "BRIDGE-onnx": ["bridge_fp32_784x518.onnx", "bridge_fp32_518x784.onnx",
                    "bridge_fp32.onnx_data"],
    "DepthPro-onnx": ["depthpro_fp32.onnx", "depthpro_fp32.onnx.data"],
    "DistillAnyDepth-onnx": ["distillanydepth_fp32_518x518.onnx",
                             "distillanydepth_fp32_518x784.onnx",
                             "distillanydepth_fp32_784x518.onnx",
                             "distillanydepth_fp32.onnx_data"],
    "Iris-onnx": ["iris_unet.onnx", "iris_text_encoder.onnx",
                  "iris_vae_encoder.onnx", "iris_vae_decoder.onnx"],
    "PPD-onnx": ["ppd_moge_sem.onnx", "ppd_dit_step.onnx"],
}


def sha256_file(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_count_and_bytes(path: Path):
    if path.is_file():
        return 1, path.stat().st_size
    total, count = 0, 0
    for item in path.rglob("*"):
        if item.is_file():
            count += 1
            total += item.stat().st_size
    return count, total


def copy_path(src: Path, dst: Path):
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def stage_models(dry_run=False):
    staged = []
    for folder, src_name in MODEL_COPY:
        src = MODELS_SRC / src_name
        if not src.is_dir():
            raise FileNotFoundError(f"ONNX 权重来源缺失: {src}")
        dst = DIST_DIR / folder
        if dry_run:
            count, total = file_count_and_bytes(src)
            print(f"  [dry] 复制 {src_name} -> {dst.name}  ({count} 文件, {total/2**20:.0f}MB)")
            staged.append((folder, "whole"))
            continue
        copy_path(src, dst)
        staged.append((folder, "whole"))
    return staged


def stage_frontend_and_docs(dry_run=False):
    for name in FRONTEND_FILES:
        src = PLUGIN_SRC / name
        if not src.exists():
            raise FileNotFoundError(f"前端文件缺失: {src}")
        if dry_run:
            print(f"  [dry] 复制前端 {name}")
            continue
        copy_path(src, DIST_DIR / name)
    # 分支 manifest（id = com.zk21.depthpro.windows）
    manifest_src = ROOT / "manifest_windows.json"
    if not manifest_src.is_file():
        manifest_src = PLUGIN_SRC / "manifest.onnx.json"
    readme_src = ROOT / "README.txt"
    if not readme_src.is_file():
        readme_src = PROJECT_ROOT / "docs" / "README.zh-CN.txt"
    notice_src = ROOT / "THIRD_PARTY_NOTICE.txt"
    if not notice_src.is_file():
        notice_src = PROJECT_ROOT / "THIRD_PARTY_NOTICE.txt"
    if dry_run:
        # dry-run 不得写盘：这里只报告，不复制（vendor/manifest/README/NOTICE）。
        print("  [dry] 复制前端 vendor/ + manifest.json + README.txt + THIRD_PARTY_NOTICE.txt")
        return
    copy_path(PLUGIN_SRC / "vendor", DIST_DIR / "vendor")
    copy_path(manifest_src, DIST_DIR / "manifest.json")
    copy_path(readme_src, DIST_DIR / "README.txt")
    copy_path(notice_src, DIST_DIR / "THIRD_PARTY_NOTICE.txt")


def verify_dist():
    required = [DIST_DIR / "depth_server.exe", DIST_DIR / "manifest.json",
                DIST_DIR / "README.txt", DIST_DIR / "THIRD_PARTY_NOTICE.txt"]
    for folder, _src in MODEL_COPY:
        for marker in MARKERS[folder]:
            required.append(DIST_DIR / folder / marker)
    for name in FRONTEND_FILES:
        required.append(DIST_DIR / name)
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError("Missing staged runtime files:\n" + "\n".join(missing))


def verify_exe_protocol(exe_dir: Path, expected: str = "7", timeout: float = 300.0):
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
                f"EXE protocol mismatch: expected {expected}, got {got}. "
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
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印复制计划与大小，不复制不构建")
    parser.add_argument("--skip-pyinstaller", action="store_true",
                        help="Only restage runtime assets into an existing dist")
    parser.add_argument("--skip-copy-to-plugin", action="store_true",
                        help="Don't copy dist to com.zk21.depthpro.windows")
    parser.add_argument("--clean", action="store_true",
                        help="Remove candidate build and dist directories")
    parser.add_argument("--no-verify-exe", action="store_true")
    args = parser.parse_args(argv)

    if args.dry_run:
        print(f"dry-run: 目标插件目录 {PLUGIN_DST}")
        print("模型复制计划：")
        stage_models(dry_run=True)
        print("前端/文档复制计划：")
        stage_frontend_and_docs(dry_run=True)
        print("dry-run 完成（未复制/未构建）")
        return 0

    if args.clean:
        for root_dir in (CANDIDATE_BUILD_ROOT, CANDIDATE_DIST_ROOT):
            if not root_dir.exists():
                continue
            for dirpath, _dirnames, filenames in os.walk(root_dir):
                for name in filenames:
                    try:
                        os.chmod(os.path.join(dirpath, name), stat.S_IWRITE)
                    except OSError:
                        pass
            shutil.rmtree(root_dir, ignore_errors=True)

    if not args.skip_pyinstaller:
        command = [
            args.python, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", str(CANDIDATE_DIST_ROOT),
            "--workpath", str(CANDIDATE_BUILD_ROOT),
            str(SPEC),
        ]
        subprocess.run(command, cwd=ROOT, check=True)

    if not DIST_DIR.exists():
        raise FileNotFoundError(f"PyInstaller output not found: {DIST_DIR}")

    staged_models = stage_models()
    stage_frontend_and_docs()
    verify_dist()
    if not args.no_verify_exe:
        verify_exe_protocol(DIST_DIR)

    source_files = [ROOT / "depth_server.py", ROOT / "models.py",
                    ROOT / "onnx_common.py", ROOT / "model_bridge.py",
                    ROOT / "model_depthpro.py", ROOT / "model_distillanydepth.py",
                    ROOT / "model_iris.py", ROOT / "model_ppd.py",
                    ROOT / "postprocess.py", ROOT / "img_io.py",
                    ROOT / "histogram_eq.py", SPEC, Path(__file__).resolve(),
                    ROOT / "manifest_windows.json", ROOT / "README.txt",
                    ROOT / "THIRD_PARTY_NOTICE.txt"]
    artifact_files = [DIST_DIR / "depth_server.exe",
                      DIST_DIR / "BRIDGE-onnx" / "bridge_fp32.onnx_data",
                      DIST_DIR / "DepthPro-onnx" / "depthpro_fp32.onnx.data",
                      DIST_DIR / "DistillAnyDepth-onnx" / "distillanydepth_fp32.onnx_data",
                      DIST_DIR / "Iris-onnx" / "iris_unet.onnx.data",
                      DIST_DIR / "PPD-onnx" / "ppd_dit_step.onnx"]
    manifest = {
        "dist_dir": str(DIST_DIR),
        "plugin_dir": str(PLUGIN_DST),
        "protocol_version": "7",
        "runtime": "onnxruntime-directml (DML + CPU EP)",
        "models": [{"folder": folder, "copy": mode} for folder, mode in staged_models],
        "source_sha256": {
            str(p.relative_to(PROJECT_ROOT)).replace("\\", "/"): sha256_file(p)
            for p in source_files
        },
        "artifact_sha256": {
            str(p.relative_to(DIST_DIR)).replace("\\", "/"): sha256_file(p)
            for p in artifact_files
        },
        "copied": [],
    }
    for folder, _mode in staged_models:
        count, total = file_count_and_bytes(DIST_DIR / folder)
        manifest["copied"].append({"relative_path": folder, "file_count": count,
                                   "bytes": total})
    for name in FRONTEND_FILES + ("manifest.json", "README.txt", "THIRD_PARTY_NOTICE.txt"):
        count, total = file_count_and_bytes(DIST_DIR / name)
        manifest["copied"].append({"relative_path": name, "file_count": count,
                                   "bytes": total})
    (DIST_DIR / "build_manifest_windows.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not args.skip_copy_to_plugin:
        if PLUGIN_DST.exists():
            shutil.rmtree(PLUGIN_DST)
        shutil.copytree(DIST_DIR, PLUGIN_DST)
        print(f"Copied dist -> {PLUGIN_DST}")
    print(f"Staged build ready: {DIST_DIR}")
    print(f"Manifest: {DIST_DIR / 'build_manifest_windows.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
