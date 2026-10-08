# -*- coding: utf-8 -*-
"""
新流水线后处理（协议 7）
========================
最终算法处理过程：单目深度估计神经网络 → 离群值裁切 → 超分辨率 →
直方图均衡化 → 雾气深度变换 → 归一化 → 采样到 16bit/8bit 位深度返回 PS。

本模块提供除"直方图均衡化"（histogram_eq.py）外的各阶段：
- sanitize_and_clip：无效像素中值替换 + 0.025–99.925 对称分位裁切 + 方向归一化（远亮）
- upscale：超分辨率（bilinear / wgif / jbu-nc / none=留给 PS 处理）
- fog_transform：雾气深度变换（robust 相对线性雾映射）
- normalize / quantize_png：归一化与 16/8bit 位深采样
"""
import numpy as np

OUTLIER_LO_PCT = 0.025
OUTLIER_HI_PCT = 99.925
MAX_CLIP_PCT = 99.925
GF_EPS = 1e-2
GF_DEFAULT_RADIUS = 2
GF_RADIUS_MIN = 1
GF_RADIUS_MAX = 32
# 边界拉直 σ 基准系数（×k，封顶见 upscale_sr.gated_smooth 的 sigma_max=8）。
# 由消融定稿（papers/超分实验/scripts/_dyn_*.png + 法向剖面数据）：
# 过强平滑会把过渡带拉宽、被 S1 直方图均衡压成平行灰带；wgif 自身细化强可更小。
STRAIGHTEN_SIGMA_SCALE = {'bilinear': 2.0, 'wgif': 1.2, 'jbu-nc': 1.6}


def _bilinear_aa(bil, k):
    """纯双线性结果 + 高斯抗锯齿低通（σ≈k/2；k≤1 无插值伪差，不平滑）。

    wgif/jbu-nc 的回退路径共用。"""
    if k <= 1.0:
        return bil
    import upscale_sr
    return upscale_sr.aa_blur(bil, max(0.5, k / 2.0))


def sanitize_and_clip(depth, invert_direction=False):
    """无效像素中值替换 + 方向归一化到远亮（远=1，近=0）。

    数值范围用有效像素的实际 min/max 全量程归一化，与实验基准
    （scripts/common.py norm_to_uint16，全量程、无分位裁切）一致。
    之前的 0.025/99.925 分位裁切会在翻转后整体拉伸动态范围，
    使亮部（雾气/天空高光）被推白过曝，与基准输出不一致
    （实测 深度实验/白色中性灰样例，参考=65535-raw 全量程反色）。
    极端单像素的防 Wolver 公益由 valid 掩码（非有限值/≤1e-8 → 中值替换）兜底。
    """
    depth = np.asarray(depth, dtype=np.float32).squeeze()
    if depth.ndim != 2 or depth.size == 0:
        raise ValueError('深度图必须是非空二维数组')
    # 0 是反差(disparity)语义下的合法值（0=无穷远→归一化后为纯白），
    # 不可当作无效值做中值替换（否则大片远区被错误灰化）
    valid = np.isfinite(depth) & (depth >= 0)
    samples = depth[valid]
    if samples.size < 64:
        raise ValueError('深度图有效像素不足')
    result = depth.copy()
    replacement = float(np.median(samples))
    result[~valid] = replacement
    lo = float(samples.min())
    hi = float(samples.max())
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= max(lo, 1e-8):
        result.fill(replacement)
        return result.astype(np.float32, copy=False)
    result = (result - lo) / (hi - lo)
    if invert_direction:
        result = 1.0 - result
    return result.astype(np.float32, copy=False)


def normalize_minmax(depth):
    """最小-最大归一化到 [0,1]。"""
    depth = np.asarray(depth, dtype=np.float32)
    low, high = float(np.min(depth)), float(np.max(depth))
    if high > low + 1e-8:
        return (depth - low) / (high - low)
    return np.zeros_like(depth, dtype=np.float32)


def _dynamic_radii(native_hw, target_hw, r_user):
    """按上采样倍率 k 动态推导各类半径（数学推导见 upscale 文档字符串）。

    k = 长边比（目标/原生）。k ≤ 1（缩小或原尺寸）时无插值伪差，直接返回零平滑。
    返回 (k, n_smooth, r_eff, r_lp)：
      n_smooth  双线性 3×3 二项式平滑遍数 = round(k²/2.92)，clamp [1,8]
                （σ_target=k/2，单遍 σ≈0.855px，n=(σ/0.855)²）
      r_eff     GF 引导滤波半径 = round(r_user·k/2)，clamp [1,32]
                （原生 1px 深度边缘放大后过渡带宽 ≈ k，默认 r_user=2 → r_eff≈k）
      r_lp      引导图低通盒式半径 = round(k)，clamp [2,32]
                （滤除目标域中周期 < ~k 的纹理，即原生分辨率以上的内容）
    """
    native_m = max(int(native_hw[0]), int(native_hw[1]))
    target_m = max(int(target_hw[0]), int(target_hw[1]))
    k = target_m / max(1, native_m)
    if k <= 1.0:
        return k, 0, max(1, int(r_user)), 2
    n_smooth = int(min(8, max(1, round(k * k / 2.92))))
    r_eff = int(min(32, max(1, round(r_user * k / 2.0))))
    r_lp = int(min(32, max(2, round(k))))
    return k, n_smooth, r_eff, r_lp


def upscale(depth, target_size, mode='bilinear', params=None, guide=None):
    """超分辨率。

    depth: float32 (H,W) 模型原生深度（已裁切，值域 [0,1]）
    target_size: (h,w) 目标尺寸
    mode: 'none'（保持原生尺寸，留给 PS 处理）| 'bilinear' | 'wgif' | 'jbu-nc'
    params: wgif 时 {'radius': r}（UI"半径 r"滑块，为基准柔化系数，实际半径按
            k 动态放大：r_eff = r·k/2，见 _dynamic_radii）
            jbu-nc 时 {'sigma_r': v}（范围容差 0.02–0.10）
    guide: float32 RGB (target 尺寸) 引导图（三模式均使用其明度做边界门控）
    返回 (depth, diagnostics)；wgif/jbu-nc 失败自动回退 bilinear。

    抗锯齿推导：
      双线性插值把原生网格每个样本展宽为 ~k 目标像素的分段线性平台，插值伪差
      集中在目标奈奎斯特频率 1/(2k) 附近；保留原生带宽需低通截止 ~1/(2k)，
      对应高斯 σ ≈ k/2（目标像素域）。bilinear 模式直接用该 σ 的高斯
      （σ 无 clamp；k≤1 无插值伪差，不平滑）。GF/WGIF 的边缘过渡带与纹理
      截止同理随 k 放大（r_eff / r_lp）。
    台阶锯齿（audit_edge 2026-09-19）：
      LR 深度的边界台阶是宽带位置锯齿，σ≈k/2 低通只能压约 3×；天空-地面边界
      处 RGB 引导无对比，细化算法无信息去重排边缘。故三模式在归一化前统一做
      gated_smooth：引导无真实边处加重各向同性平滑拉直边界，有边处原样保留。
    """
    params = dict(params or {})
    target_h, target_w = map(int, target_size)
    depth = np.asarray(depth, dtype=np.float32)
    if depth.shape == (target_h, target_w) or mode == 'none':
        applied = depth.shape == (target_h, target_w)
        return depth.astype(np.float32, copy=False), {'mode': mode, 'applied': applied}

    r_user = int(params.get('radius', GF_DEFAULT_RADIUS))
    r_user = min(32, max(GF_RADIUS_MIN, r_user))
    k, n_smooth, r_eff, r_lp = _dynamic_radii(depth.shape, (target_h, target_w), r_user)

    from PIL import Image
    bil = np.asarray(
        Image.fromarray(depth, mode='F').resize((target_w, target_h),
                                                Image.Resampling.BILINEAR),
        dtype=np.float32,
    )

    gray = None
    if guide is not None and guide.shape[:2] == (target_h, target_w):
        gray = (0.2126 * guide[..., 0] + 0.7152 * guide[..., 1]
                + 0.0722 * guide[..., 2]).astype(np.float32)

    def _finish(out, diag):
        """边界拉直后处理（引导可用时）；σ 基准系数按模式定稿、随 k 缩放。"""
        if gray is not None:
            import upscale_sr
            out, sdiag = upscale_sr.gated_smooth(
                out, gray, k, sigma_scale=STRAIGHTEN_SIGMA_SCALE.get(mode, 2.0))
            diag['straighten'] = sdiag
        return out, diag

    if mode == 'bilinear':
        out = bil
        diag = {'mode': 'bilinear', 'applied': True, 'k': round(k, 3)}
        if k > 1.0:
            import upscale_sr
            sigma_aa = max(0.5, k / 2.0)
            out = upscale_sr.aa_blur(out, sigma_aa)
            diag['smoothed'] = True
            diag['sigma_aa'] = round(float(sigma_aa), 2)
        out, diag = _finish(out, diag)
        return out.astype(np.float32, copy=False), diag

    if mode in ('wgif', 'jbu-nc'):
        try:
            if gray is None:
                out = _bilinear_aa(bil, k)
                return out, {'mode': mode, 'applied': True, 'radius': r_eff,
                             'fallback': 'guide_unavailable'}
            import upscale_sr
            if mode == 'wgif':
                # 引导图低通：抑制原图纹理的高频（周期 < ~k），只留大结构边缘
                guide_lp = upscale_sr.box_blur(gray, r_lp)
                out = upscale_sr.wgif(guide_lp, bil, r_eff, GF_EPS)
                diag = {'mode': 'wgif', 'applied': True, 'radius': r_eff,
                        'guide_lowpass': r_lp, 'k': round(k, 3)}
            else:
                sigma_r = float(params.get('sigma_r', upscale_sr.JBU_SIGMA_R_DEFAULT))
                sigma_r = min(upscale_sr.JBU_SIGMA_R_MAX,
                              max(upscale_sr.JBU_SIGMA_R_MIN, sigma_r))
                out = upscale_sr.jbu_nc_lr(gray, depth, (target_h, target_w),
                                           r=upscale_sr.JBU2_R,
                                           sigma_s=upscale_sr.JBU2_SIGMA_S,
                                           sigma_r=sigma_r)
                diag = {'mode': 'jbu-nc', 'applied': True, 'radius': upscale_sr.JBU2_R,
                        'sigma_r': sigma_r, 'k': round(k, 3)}
            out, diag = _finish(out, diag)
            return out.astype(np.float32, copy=False), diag
        except Exception as exc:
            out = _bilinear_aa(bil, k)
            return out, {'mode': mode, 'applied': True,
                         'radius': r_eff, 'k': round(k, 3),
                         'fallback': mode + '_failed:' + str(exc)}

    raise ValueError('未知超分模式: ' + str(mode))


def fog_transform(depth01):
    """雾气深度变换：robust 相对线性雾映射，输出 [0,1]。"""
    depth01 = np.asarray(depth01, dtype=np.float32)
    valid = np.isfinite(depth01) & (depth01 > 1e-8)
    samples = depth01[valid] if valid.any() else depth01
    if samples.size == 0:
        return np.zeros_like(depth01, dtype=np.float32)
    low, high = np.percentile(samples, [OUTLIER_LO_PCT, OUTLIER_HI_PCT])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low + 1e-8:
        low, high = float(samples.min()), float(samples.max())
    if high <= low + 1e-8:
        return np.zeros_like(depth01, dtype=np.float32)
    fog = np.clip(depth01, low, high)
    return (fog - low) / (high - low)


def quantize_png(depth01, bits):
    """归一化深度采样到指定位深并返回 PIL Image（16 -> I;16，8 -> L）。"""
    from PIL import Image
    depth01 = np.clip(depth01, 0.0, 1.0)
    if bits >= 16:
        arr = np.round(depth01 * 65535.0).astype(np.uint16)
        return Image.fromarray(arr, mode='I;16')
    arr = np.round(depth01 * 255.0).astype(np.uint8)
    return Image.fromarray(arr, mode='L')
