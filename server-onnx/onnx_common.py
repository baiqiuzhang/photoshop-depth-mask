# -*- coding: utf-8 -*-
"""
ONNX Runtime 公共模块（协议 7，ONNX 分支）
==========================================
- provider 解析：DirectML（DmlExecutionProvider）+ CPU 回退；纯 CPU 机器只用 CPU。
- Intel 显卡适配性：实测 Intel iGPU 的 DirectML 驱动不兼容，只检测到 Intel/
  Microsoft Basic Display 类适配器时强制禁用 DirectML（本分支部署目标）。
- 手动禁用：设置环境变量 DEPTH_DML=0（或 DEPTH_FORCE_CPU=1），见 README。
- 会话缓存：按 (模型路径, force_cpu) 缓存，避免重复加载数 GB 权重。
- letterbox 工具：非支持比例输入统一"等比缩放居中 + 灰边填充 + 推理后裁剪"。
"""
import os
import subprocess

import numpy as np

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_INTEL_NAME_TOKENS = ('intel', 'microsoft basic display', 'microsoft remote display',
                      'basic render', 'standard vga', 'warpplayer')

_session_cache = {}


def _log(msg):
    try:
        print(str(msg), flush=True)
    except Exception:
        pass


# ---------- DirectML / 设备检测 ----------
def _video_controller_names():
    """枚举显卡名称（PowerShell CIM）；枚举失败返回 None（权限/环境问题）。"""
    try:
        cmd = ('Get-CimInstance Win32_VideoController '
               '| Select-Object -ExpandProperty Name')
        out = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', cmd],
            capture_output=True, text=True, timeout=30,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        lines = [line.strip() for line in (out.stdout or '').splitlines() if line.strip()]
        return lines if lines else None
    except Exception:
        return None


def _is_intel_name(name):
    low = (name or '').lower()
    return any(token in low for token in _INTEL_NAME_TOKENS)


def detect_dml_allowed():
    """返回 (allowed, reason)。

    优先级：环境变量 DEPTH_DML（0=禁用）> DEPTH_FORCE_CPU=1 > 显卡枚举。
    枚举失败时默认允许 DML（靠会话创建失败自动回退 CPU 兜底），
    需要强制 CPU 时请用 DEPTH_DML=0 手动禁用。
    """
    override = os.environ.get('DEPTH_DML')
    if override is not None:
        try:
            return int(override) != 0, f'环境变量 DEPTH_DML={override}'
        except ValueError:
            pass
    if os.environ.get('DEPTH_FORCE_CPU') == '1':
        return False, '环境变量 DEPTH_FORCE_CPU=1'
    names = _video_controller_names()
    if names is None:
        return True, '显卡枚举不可用；默认允许 DML（会话失败自动回退 CPU）'
    if not names:
        return False, '未检测到任何显卡'
    non_intel = [name for name in names if not _is_intel_name(name)]
    if not non_intel:
        return False, f'仅检测到 Intel/基础适配器: {names}'
    return True, f'检测到非 Intel 适配器: {non_intel[0]}'


def resolve_providers(force_cpu=False):
    """返回 (providers, reason)。

    DML 允许时用 'high_performance' + 'gpu' 过滤锁定独显（混合显卡/多适配器
    机器上比 device_id 稳健）；只列 DmlExecutionProvider（ORT 会自动为不支持的
    算子附加 CPU EP 兜底，实测显式列 CPU EP 反而会让部分图整体回退 CPU）。
    """
    if force_cpu:
        return ['CPUExecutionProvider'], 'force_cpu=True'
    allowed, reason = detect_dml_allowed()
    if not allowed:
        return ['CPUExecutionProvider'], reason
    return [('DmlExecutionProvider',
              {'performance_preference': 'high_performance',
               'device_filter': 'gpu'})], reason


def _session_options(optimize=True):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.log_severity_level = 3
    # allowzero/onnxscript 图编辑后 DML 会话可直接用 ORT_ENABLE_ALL；
    # 保留 optimize 参数供 CPU 路径（DML 失败回退时用全量优化提速）。
    try:
        level = (ort.GraphOptimizationLevel.ORT_ENABLE_ALL if optimize
                 else ort.GraphOptimizationLevel.ORT_DISABLE_ALL)
        options.graph_optimization_level = level
    except Exception:
        pass
    try:
        options.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
    except Exception:
        pass
    return options


def _session_uses_dml(session):
    try:
        return 'DmlExecutionProvider' in session.get_providers()
    except Exception:
        return False


def make_session(path, force_cpu=False):
    """创建 ORT 会话。DML（高性能独显）→ 默认设备 → CPU 三档尝试。

    返回 (session, device_name, note)，device_name ∈ {'dml', 'cpu'}。
    """
    import onnxruntime as ort
    providers, reason = resolve_providers(force_cpu)
    options_cpu = _session_options(optimize=True)
    if not any('Dml' in str(p) for p in providers):
        session = ort.InferenceSession(path, providers=providers,
                                       sess_options=options_cpu)
        return session, 'cpu', f'{reason}; providers={session.get_providers()}'
    attempts = []
    # 1) 高性能独显（混合显卡首选；实测 DML-only 比 [DML, CPU] 更易让 DML 接管）
    try:
        session = ort.InferenceSession(path, providers=providers,
                                       sess_options=options_cpu)
        if _session_uses_dml(session):
            return session, 'dml', f'{reason}; DML 接管'
        attempts.append(f'{providers}: DML 未接管')
    except Exception as exc:
        attempts.append(f'{providers}: {exc}')
    # 2) 默认设备（device 0）
    try:
        session = ort.InferenceSession(
            path, providers=[('DmlExecutionProvider', {'device_id': 0})],
            sess_options=options_cpu)
        if _session_uses_dml(session):
            return session, 'dml', f'{reason}; DML dev0'
        attempts.append('dev0: DML 未接管')
    except Exception as exc:
        attempts.append(f'dev0: {exc}')
    # 3) 回退 CPU
    session = ort.InferenceSession(path, providers=['CPUExecutionProvider'],
                                   sess_options=options_cpu)
    return session, 'cpu', f'{reason}; DML 不可用: {attempts}'


def get_session(path, force_cpu=False):
    """带缓存的会话工厂。"""
    key = (os.path.normcase(os.path.abspath(path)), bool(force_cpu))
    entry = _session_cache.get(key)
    if entry is None:
        entry = make_session(path, force_cpu)
        _session_cache[key] = entry
    return entry


# ---------- 图像工具 ----------
def resize_float(img_hw, target_hw, resample='bicubic'):
    """float32 (H,W[,C]) [0,1] -> 等比缩放（保持 float 精度，不经过 uint8）。"""
    from PIL import Image
    h, w = img_hw.shape[:2]
    th, tw = target_hw
    if (h, w) == (th, tw):
        return np.asarray(img_hw, dtype=np.float32)
    mapping = {'bilinear': Image.Resampling.BILINEAR,
               'bicubic': Image.Resampling.BICUBIC,
               'lanczos': Image.Resampling.LANCZOS}
    resample = mapping.get(resample, Image.Resampling.BICUBIC)
    if img_hw.ndim == 3:
        channels = [
            np.asarray(Image.fromarray(img_hw[..., c], mode='F')
                       .resize((tw, th), resample=resample), dtype=np.float32)
            for c in range(img_hw.shape[2])
        ]
        return np.stack(channels, axis=-1)
    return np.asarray(Image.fromarray(img_hw, mode='F')
                      .resize((tw, th), resample=resample), dtype=np.float32)


def letterbox_float(img_hw, target_hw, pad_value=0.5):
    """等比缩放居中 + 填充到 (th,tw) 框内。

    返回 (letterboxed (th,tw,C), rh, rw, pad_top, pad_left)。
    推理后调用 crop_letterbox 按 (rh, rw) 裁回有效区域。
    """
    h, w = img_hw.shape[:2]
    th, tw = target_hw
    scale = min(tw / max(1, w), th / max(1, h))
    rw = max(1, int(round(w * scale)))
    rh = max(1, int(round(h * scale)))
    pad_left = (tw - rw) // 2
    pad_top = (th - rh) // 2
    resized = resize_float(img_hw, (rh, rw), resample='lanczos')
    out = np.full((th, tw) + resized.shape[2:], pad_value, dtype=np.float32)
    out[pad_top:pad_top + rh, pad_left:pad_left + rw] = resized
    return out, rh, rw, pad_top, pad_left


def crop_letterbox(depth2d, rh, rw, pad_top, pad_left):
    """从 (th,tw) 深度图中裁剪出 letterbox 有效区域 (rh,rw)。"""
    return depth2d[pad_top:pad_top + rh, pad_left:pad_left + rw]


def channel_last_to_batch_chw(x):
    """(H,W,C) float32 [0,1] -> (1,C,H,W) float32。"""
    return np.transpose(x, (2, 0, 1))[None].astype(np.float32)
