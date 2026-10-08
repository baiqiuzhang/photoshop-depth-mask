# -*- coding: utf-8 -*-
"""
深度图超分细化算法（0.2.1 Torch 变体）
======================================
从超分实验（papers/超分实验，36 组 × 全指标评选）胜出的两个保边细化算法：
- wgif   加权引导滤波（Li TIP 2015）
- jbu-nc 非凸联合双边上采样（Lu MTAP 2018 Cauchy 范围核）。实装为 LR 网格制
  （jbu_nc_lr，2026-09-19 实机修正）；旧 HR 网格制（jbu_nc）保留供实验对账，
  两者在大 k（>4）下行为差异见 jbu_nc_lr 文档字符串。

两算法都在"部署同款纯 PIL 双线性"的输出上做细化（与实验 build_ctx 的 base 口径
一致），输入输出均为 float32 (H,W)。异常由调用方（postprocess.upscale）捕获并
回退 bilinear+抗锯齿，本模块只负责抛出 ValueError，不做静默回退。
"""
import cv2
import numpy as np

JBU_R = 4            # jbu-nc（旧 HR 网格实现，保留供对账）空间窗口半径
JBU_SIGMA_S = 2.0    # jbu-nc 空间权重
JBU_SIGMA_R_DEFAULT = 0.05
JBU_SIGMA_R_MIN = 0.02
JBU_SIGMA_R_MAX = 0.10
JBU_TRUNC = 1.0      # Cauchy 核截断：Δ² > trunc*8σ² 的邻居直接置零

# LR 网格制实现（2026-09-19 实机修正，见 jbu_nc_lr 文档字符串）
JBU2_R = 4           # 空间窗口半径（LR 像素单位）
JBU2_SIGMA_S = 2.0   # 空间权重（LR 像素单位）


def _check_2d_finite(a, name):
    a = np.asarray(a, dtype=np.float32)
    if a.ndim != 2 or a.shape[0] < 1 or a.shape[1] < 1:
        raise ValueError(name + ' 必须是非空二维数组')
    if not np.isfinite(a).all():
        raise ValueError(name + ' 含非有限值')
    return a


def box_blur(a, radius):
    """(2r+1)² 盒式均值，反射边界。与实验 sr_algo._box 同一 cv2 实现。"""
    a = _check_2d_finite(a, 'box_blur input')
    r = int(radius)
    if r <= 0:
        return a
    return cv2.boxFilter(a, -1, (2 * r + 1, 2 * r + 1),
                         borderType=cv2.BORDER_REFLECT)


def aa_blur(a, sigma):
    """精确高斯抗锯齿低通（替代 3×3 级联：σ 无 clamp，可分离，更快）。"""
    a = _check_2d_finite(a, 'aa_blur input')
    sigma = float(sigma)
    if sigma <= 0:
        return a
    return cv2.GaussianBlur(a, (0, 0), sigma)


def gated_smooth(depth01, gray01, k, sigma_scale=2.0, gate_percentile=70.0,
                 sigma_max=8.0):
    """引导无边处加重各向同性平滑（拉直天空-地面类边界的台阶锯齿）。

    根因（papers/超分实验 audit_edge 系列）：LR 深度的边界台阶是宽带位置锯齿，
    σ≈k/2 的各向同性低通只能压约 3×；而天空-地面边界处 RGB 引导无对比，
    wgif/jbu-nc 没有信息去重排边缘，台阶全部保留。
    本函数在"引导梯度低"（无真实 RGB 边）处用高斯 σ=sigma_scale·k 加重平滑，
    引导有真实边缘处原样保留（保护物体轮廓）；门控场经 σ≈k 高斯羽化防接缝。
    σ 按倍率 k 动态缩放并封顶 sigma_max（第二/三轮修订：过强的平滑会把过渡带
    拉宽，反而被 S1 直方图均衡压成"灰带"——σ 与过渡带宽度需随 k 同步收敛，
    各模式基准系数由消融定稿：bilinear 2.0 / wgif 1.2 / jbu-nc 1.6，见
    postprocess.upscale 的调用处）。
    代价：引导无边处的深度边缘过渡带变宽（远场边界可接受）。
    返回 (out, diag)。
    """
    d = _check_2d_finite(depth01, 'gated_smooth src')
    g = _check_2d_finite(gray01, 'gated_smooth guide')
    if g.shape != d.shape:
        raise ValueError('gated_smooth src/guide 形状不一致')
    k = max(1.0, float(k))
    kw = int(max(2, round(k))) * 2 + 1
    gmag = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0, 3),
                         cv2.Sobel(g, cv2.CV_32F, 0, 1, 3))
    gmag = cv2.GaussianBlur(gmag, (kw, kw), 0)
    thr = float(np.percentile(gmag, gate_percentile))
    gate = np.clip(1.0 - np.clip(gmag / max(thr, 1e-9), 0.0, 1.0),
                   0.0, 1.0).astype(np.float32)
    gate = np.clip(cv2.GaussianBlur(gate, (kw, kw), 0), 0.0, 1.0)
    sigma = min(sigma_max, max(1.0, sigma_scale * k))
    blur = cv2.GaussianBlur(d, (0, 0), sigma)
    out = d * (1.0 - gate) + blur * gate
    return out.astype(np.float32), {"sigma": round(float(sigma), 2),
                                    "gate_mean": round(float(gate.mean()), 4)}


def wgif(guide_lp, base, r_eff, eps):
    """加权引导滤波：He 式引导滤波，有效 eps 为 eps/w_i（w_i 在引导方差大处
    更大）→ 边缘区更保真、抑制 halo。guide_lp/base 同形状 (H,W) float32。"""
    g = _check_2d_finite(guide_lp, 'wgif guide')
    p = _check_2d_finite(base, 'wgif src')
    if g.shape != p.shape:
        raise ValueError('wgif guide/src 形状不一致')
    r = int(r_eff)
    if r < 1:
        raise ValueError('wgif 半径必须 >= 1')
    if not (eps > 0):
        raise ValueError('wgif eps 必须为正')

    mi, mp = box_blur(g, r), box_blur(p, r)
    var = np.maximum(box_blur(g * g, r) - mi * mi, 0.0)
    cov = box_blur(g * p, r) - mi * mp
    w = (var + eps) * float(np.mean(1.0 / (var + eps)))
    a = cov / (var + eps / np.maximum(w, 1e-6))
    b = mp - a * mi
    return (box_blur(a, r) * g + box_blur(b, r)).astype(np.float32)


def jbu_nc(base, gray, r=JBU_R, sigma_s=JBU_SIGMA_S, sigma_r=JBU_SIGMA_R_DEFAULT,
           trunc=JBU_TRUNC):
    """非凸联合双边上采样：范围核用 Cauchy 型 1/(1+Δ²/σ²) 并加截断。

    非凸核对"深度-彩色边不一致"处的权重衰减比高斯更快，缓解纹理拷贝与
    边缘模糊。base/gray 同形状 (H,W) float32。
    """
    p = _check_2d_finite(base, 'jbu-nc src')
    g = _check_2d_finite(gray, 'jbu-nc guide')
    if p.shape != g.shape:
        raise ValueError('jbu-nc src/guide 形状不一致')
    if not (sigma_r > 0) or not (sigma_s > 0):
        raise ValueError('jbu-nc sigma 必须为正')
    H, W = p.shape
    r = int(r)
    gp, pp = np.pad(g, r, mode='edge'), np.pad(p, r, mode='edge')
    out = np.zeros((H, W), np.float32)
    wsum = np.zeros((H, W), np.float32)
    inv = 1.0 / (sigma_r * sigma_r)
    # 预分配缓冲复用，避免 81 次全图循环反复分配临时数组
    buf = np.empty((H, W), np.float32)
    contrib = np.empty((H, W), np.float32)
    keep = np.empty((H, W), bool)            # Cauchy 截断掩码（d2 < trunc*8σ²）
    spatial_lut = [float(np.exp(-(dx * dx + dy * dy) / (2.0 * sigma_s * sigma_s)))
                   for dy in range(-r, r + 1) for dx in range(-r, r + 1)]
    i = 0
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            gs = gp[r + dy:r + dy + H, r + dx:r + dx + W]
            ps = pp[r + dy:r + dy + H, r + dx:r + dx + W]
            # Cauchy 范围核：1/(1+Δ²/σ²)，并截断 Δ² ≥ trunc*8σ² 的邻居（权重置 0）
            np.subtract(g, gs, out=buf)
            np.multiply(buf, buf, out=buf)
            np.multiply(buf, inv, out=buf)          # buf = Δ²/σ²
            np.less(buf, trunc * 8.0, out=keep)
            np.add(buf, 1.0, out=buf)
            np.reciprocal(buf, out=buf)             # buf = Cauchy 权重
            np.multiply(buf, keep, out=buf)         # 截断处置 0
            np.multiply(buf, spatial_lut[i], out=buf)
            np.multiply(buf, ps, out=contrib)
            out += contrib
            wsum += buf
            i += 1
def jbu_nc_lr(gray, depth, target_hw, r=JBU2_R, sigma_s=JBU2_SIGMA_S,
              sigma_r=JBU_SIGMA_R_DEFAULT):
    """LR 网格制非凸联合双边上采样（2026-09-19 实机修正版）。

    旧 HR 网格实现（jbu_nc，r=4 目标像素）的实机退化根因（papers/超分实验
    diag_jbunc_deploy.py，2026-09-19）：空间窗口 9px 固定，k 大时（实机 4608 档
    k≈6–10.5）覆盖不足一个 LR 采样间距，范围核看不到深度过渡带两侧的 LR 样本
    ——输出与双线性基线梯度相关 0.88–0.98、与 RGB 引导相关 ≈0，σr 全程仅
    0.1–2.6% 像素变化超 8-bit LSB（调参无感、边缘退化为"深度锯齿+局部 RGB
    拖拽"的混合样式）。实验（k 1.35–3.95）恰落在 r=4≈1–2 个 LR 间距的有效区，
    故实验指标无法外推到大 k。

    本实现把空间核搬回 LR 网格（r/sigma_s 以 LR 像素为单位，每 tap 聚合一个
    真实 LR 样本）：
      - 数据项 = LR 深度的双线性展开（连续 ramp，消除 NEAREST 块展开的
        k 周期条纹；选边能力由范围核承担）；
      - 范围核 = Cauchy 1/(1+Δ²/σ²)，Δ = HR 明度 − 该 LR 样本引导（LR 明度
        面积降采样后双线性上采样的平移场），引导信息限带于原生分辨率
        （纹理拷贝抑制）；
      - 空间核 = 高斯 exp(−d²/2σs²)，d 为连续分数距离（LR 单位），按行/列
        可分离计算（无逐 tap 全图 exp）；
      - **不做 Cauchy 截断**：全帧实测（_DSC9662 4096）截断掩码在纹理引导上
        逐像素闪烁 → 权重集突变 → 黑色斑点噪声（0.45% 像素暗于局部均值 2%），
        关闭后纹理区干净、全帧指标不变。非凸性由 Cauchy 核本身保留。
    与实验 sr_algo.py 的 jbu-nc 口径不同（网格/数据项/截断三处），不再逐位一致；
    差异动机与验证数据见 checkpoint ckpt-20260919-jbunc。

    gray: HR 引导明度 (H,W) float32；depth: LR 深度 (h,w) float01；
    k = H/h（允许非整数，平移取整）。k ≤ 1 时返回双线性展开（无插值伪差）。
    """
    g = _check_2d_finite(gray, 'jbu-nc guide')
    d_lr = _check_2d_finite(depth, 'jbu-nc lr depth')
    if not (sigma_r > 0) or not (sigma_s > 0):
        raise ValueError('jbu-nc sigma 必须为正')
    H, W = int(target_hw[0]), int(target_hw[1])
    h, w = d_lr.shape
    ky, kx = H / float(h), W / float(w)
    if ky <= 1.0 and kx <= 1.0:
        return cv2.resize(d_lr, (W, H), interpolation=cv2.INTER_LINEAR)
    r = int(r)
    # 数据项：LR 深度双线性展开（连续场）；引导场：LR 明度（面积降采样限带）
    # 双线性上采样，平移即取该 LR 样本的引导
    P = cv2.resize(d_lr, (W, H), interpolation=cv2.INTER_LINEAR)
    g_small = cv2.resize(g, (w, h), interpolation=cv2.INTER_AREA)
    G = cv2.resize(g_small, (W, H), interpolation=cv2.INTER_LINEAR)
    pad_y = int(np.ceil(r * ky)) + 1
    pad_x = int(np.ceil(r * kx)) + 1
    Gp = np.pad(G, ((pad_y, pad_y), (pad_x, pad_x)), mode='edge')
    Pp = np.pad(P, ((pad_y, pad_y), (pad_x, pad_x)), mode='edge')
    # 连续分数距离（LR 单位）：u=(x+0.5)/k−0.5，frac∈[0,1)
    uy = (np.arange(H, dtype=np.float32) + 0.5) / ky - 0.5
    ux = (np.arange(W, dtype=np.float32) + 0.5) / kx - 0.5
    fy = uy - np.floor(uy)
    fx = ux - np.floor(ux)
    out = np.zeros((H, W), np.float32)
    wsum = np.zeros((H, W), np.float32)
    inv = 1.0 / (sigma_r * sigma_r)
    buf = np.empty((H, W), np.float32)
    for dy in range(-r, r + 1):
        sy = int(round(dy * ky))
        wy = np.exp(-((fy - dy) ** 2) / (2.0 * sigma_s * sigma_s)).astype(np.float32)
        for dx in range(-r, r + 1):
            sx = int(round(dx * kx))
            wx = np.exp(-((fx - dx) ** 2) / (2.0 * sigma_s * sigma_s)).astype(np.float32)
            Gs = Gp[pad_y + sy:pad_y + sy + H, pad_x + sx:pad_x + sx + W]
            Ps = Pp[pad_y + sy:pad_y + sy + H, pad_x + sx:pad_x + sx + W]
            # Cauchy 范围核（无截断）
            np.subtract(g, Gs, out=buf)
            np.multiply(buf, buf, out=buf)
            np.multiply(buf, inv, out=buf)
            np.add(buf, 1.0, out=buf)
            np.reciprocal(buf, out=buf)
            np.multiply(buf, wy[:, None], out=buf)
            np.multiply(buf, wx[None, :], out=buf)
            out += buf * Ps
            wsum += buf
    return (out / np.maximum(wsum, 1e-6)).astype(np.float32)
