#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
直方图均衡化后处理模块
提供五种算法：S1, S5, N1_KMeans, N1A, N2
所有函数输入输出均为 np.ndarray float32 [0,1]
"""
# 顶部仅保留轻量导入（如有必要），重型库全部在函数内导入
import numpy as np  # numpy 相对轻量，可以保留，但也可延迟，这里保留以便类型提示等

# ---------- 内部工具函数 ----------
BINS_EXACT = 65536

def _get_histogram(arr, bins=BINS_EXACT):
    hist, bin_edges = np.histogram(arr, bins=bins, range=(0, 1))
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    return hist.astype(np.float64), bin_centers

def _apply_strength(original, processed, strength):
    if strength >= 1.0:
        return np.clip(processed, 0, 1)
    mixed = (1 - strength) * original + strength * processed
    return np.clip(mixed, 0, 1)

def _get_histogram_smooth(arr, bins=1024, sigma=0.02):
    from scipy.ndimage import gaussian_filter1d
    hist, bin_edges = np.histogram(arr, bins=bins, range=(0, 1))
    hist = hist.astype(np.float64)
    if sigma > 0:
        hist = gaussian_filter1d(hist, sigma=sigma * bins)
    hist += 1e-12
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    return hist, bin_centers

# ---------- 算法实现 ----------

def s1_cdf_equalization(arr, strength=1.0):
    """S1: 全局 CDF 均衡化"""
    orig = arr.copy()
    hist, centers = _get_histogram(arr)
    cdf = np.cumsum(hist) / hist.sum()
    flat = arr.ravel()
    mapped_flat = np.interp(flat, centers, cdf)
    processed = mapped_flat.reshape(arr.shape)
    return _apply_strength(orig, processed, strength)

def s5_gmm_partition(arr, K_GMM=5, min_segment_ratio=0.001, strength=1.0):
    """S5: GMM 拟合 + 分区直方图均衡化"""
    from scipy.stats import norm
    from scipy.optimize import minimize_scalar
    from sklearn.mixture import GaussianMixture
    orig = arr.copy()
    X = arr.ravel().reshape(-1, 1)
    gmm = GaussianMixture(n_components=K_GMM, random_state=42)
    gmm.fit(X)
    means = gmm.means_.flatten()
    covs = gmm.covariances_.flatten()
    pis = gmm.weights_

    def mixture_pdf(x):
        return sum(pi * norm.pdf(x, loc=mu, scale=np.sqrt(cov)) for pi, mu, cov in zip(pis, means, covs))

    split_points = []
    sorted_indices = np.argsort(means)
    for i in range(len(sorted_indices)-1):
        idx1, idx2 = sorted_indices[i], sorted_indices[i+1]
        mu1, mu2 = means[idx1], means[idx2]
        if mu2 - mu1 < 1e-6:
            continue
        res = minimize_scalar(lambda x: -mixture_pdf(x), bounds=(mu1, mu2), method='bounded')
        if res.success:
            split_points.append(res.x)
    split_points = sorted(split_points)
    boundaries = [0.0] + split_points + [1.0]
    boundaries = np.unique(np.clip(boundaries, 0, 1))
    if len(boundaries) < 2:
        return s1_cdf_equalization(arr, strength)

    flat = arr.ravel()
    total_pixels = flat.size
    min_pixels = int(total_pixels * min_segment_ratio)
    processed_flat = flat.copy()

    for i in range(len(boundaries)-1):
        low, high = boundaries[i], boundaries[i+1]
        mask = (flat >= low) & (flat < high)
        if not np.any(mask):
            continue
        if np.sum(mask) < min_pixels:
            continue
        vals = flat[mask]
        hist_seg, edges_seg = np.histogram(vals, bins=BINS_EXACT, range=(0,1))
        cdf_seg = np.cumsum(hist_seg) / hist_seg.sum()
        centers_seg = (edges_seg[:-1] + edges_seg[1:]) / 2
        mapped = low + np.interp(vals, centers_seg, cdf_seg) * (high - low)
        processed_flat[mask] = np.clip(mapped, low, high)

    processed = processed_flat.reshape(arr.shape)
    return _apply_strength(orig, processed, strength)

def n1_kmeans_linear(arr, K=5, strength=1.0):
    """N1: KMeans 聚类分段线性映射"""
    from sklearn.cluster import KMeans
    orig = arr.copy()
    hist, centers = _get_histogram(arr)
    non_zero = hist > 0
    X = centers[non_zero].reshape(-1, 1)
    weights = hist[non_zero]

    clusterer = KMeans(n_clusters=K, random_state=42)
    labels = clusterer.fit_predict(X, sample_weight=weights)

    unique_labels = set(labels)
    if not unique_labels:
        return s1_cdf_equalization(arr, strength)

    clusters = {}
    for lab in unique_labels:
        idx = (labels == lab)
        centers_cluster = X[idx].flatten()
        weights_cluster = weights[idx]
        clusters[lab] = {
            'min': centers_cluster.min(),
            'max': centers_cluster.max(),
            'count': weights_cluster.sum()
        }
    sorted_labs = sorted(clusters.keys(), key=lambda l: clusters[l]['min'])
    total_pixels = hist.sum()
    allocated_ranges = []
    cum_offset = 0.0
    for lab in sorted_labs:
        cnt = clusters[lab]['count']
        width = cnt / total_pixels
        allocated_ranges.append((clusters[lab]['min'], clusters[lab]['max'], cum_offset, cum_offset + width))
        cum_offset += width

    flat = arr.ravel()
    mapped = np.zeros_like(flat)
    covered = np.zeros(flat.shape, dtype=bool)

    for (low, high, start, end) in allocated_ranges:
        mask = (flat >= low) & (flat < high)
        if not np.any(mask):
            continue
        vals = flat[mask]
        if high - low > 1e-8:
            mapped[mask] = start + (vals - low) / (high - low) * (end - start)
        else:
            mapped[mask] = (start + end) / 2
        covered |= mask

    # 各簇区间彼此不相邻（簇 min/max 之间可能留空），未覆盖像素单独回填。
    # 覆盖判定用显式掩码：旧写法 (mapped == 0) 会把首簇中"合法映射到 0"的
    # 像素（值恰等于首簇 min）一并误判成未覆盖，再用全局 CDF 覆盖掉。
    unassigned = ~covered & (flat > 0)
    if np.any(unassigned):
        hist_all, centers_all = _get_histogram(arr)
        cdf_all = np.cumsum(hist_all) / hist_all.sum()
        mapped[unassigned] = np.interp(flat[unassigned], centers_all, cdf_all)

    processed = mapped.reshape(arr.shape)
    return _apply_strength(orig, processed, strength)

def n1_auto_peak(arr, smooth_sigma=0.02, height_ratio=0.01, strength=1.0):
    """N1A: 自动峰值分段（无需指定类别数）"""
    from scipy.signal import find_peaks
    orig = arr.copy()
    hist_smooth, centers_smooth = _get_histogram_smooth(arr, bins=1024, sigma=smooth_sigma)
    peaks, _ = find_peaks(hist_smooth, height=height_ratio * hist_smooth.max())
    if len(peaks) == 0:
        return s1_cdf_equalization(arr, strength)
    peak_vals = centers_smooth[peaks]
    sorted_peaks = np.sort(peak_vals)
    boundaries = [0.0]
    for i in range(len(sorted_peaks)-1):
        boundaries.append((sorted_peaks[i] + sorted_peaks[i+1]) / 2)
    boundaries.append(1.0)
    boundaries = np.unique(np.clip(boundaries, 0, 1))

    flat = arr.ravel()
    segment_counts = []
    for i in range(len(boundaries)-1):
        low, high = boundaries[i], boundaries[i+1]
        cnt = np.sum((flat >= low) & (flat < high))
        segment_counts.append(cnt)
    total = flat.size
    allocated = []
    cum = 0.0
    for cnt in segment_counts:
        width = cnt / total
        allocated.append((cum, cum + width))
        cum += width

    mapped = np.zeros_like(flat)
    for i, (low, high) in enumerate(zip(boundaries[:-1], boundaries[1:])):
        mask = (flat >= low) & (flat < high)
        if not np.any(mask):
            continue
        vals = flat[mask]
        start, end = allocated[i]
        if high - low > 1e-8:
            mapped[mask] = start + (vals - low) / (high - low) * (end - start)
        else:
            mapped[mask] = (start + end) / 2

    # 段区间左闭右开，而末段上界恰为 1.0：归一化深度里"远区/天空"是一整片
    # 恰好 1.0 的平台（sanitize_and_clip 全量程归一化的结果），它不属于任何
    # 段；mapped 初始化为 zeros，整片平台于是被写成 0，即输出上的纯黑块。
    # 这里把未覆盖值显式归到映射值域的最亮端（cum），顺带兜住越界值，
    # 杜绝"未覆盖即静默归零"这一类缺陷复发。
    uncovered = flat >= boundaries[-1]
    if np.any(uncovered):
        mapped[uncovered] = cum

    processed = mapped.reshape(arr.shape)
    return _apply_strength(orig, processed, strength)

def n2_ideal_specification(arr, model='auto', smooth_sigma=2.0, strength=1.0):
    """N2: 理想分布直方图规定化"""
    from scipy.stats import norm, entropy
    from scipy.ndimage import gaussian_filter1d
    orig = arr.copy()
    hist, centers = _get_histogram(arr)

    def model_normal(mu, sigma):
        return lambda x: norm.pdf(x, mu, sigma)
    def model_bimodal():
        return lambda x: 0.5 * norm.pdf(x, 0.3, 0.1) + 0.5 * norm.pdf(x, 0.7, 0.1)
    def model_left_peak():
        return lambda x: 0.7 * norm.pdf(x, 0.2, 0.1) + 0.3 * norm.pdf(x, 0.6, 0.15)
    def model_right_peak():
        return lambda x: 0.3 * norm.pdf(x, 0.2, 0.15) + 0.7 * norm.pdf(x, 0.8, 0.1)
    def model_uniform():
        return lambda x: np.ones_like(x)

    models = {
        'uniform': model_uniform(),
        'norm_05_02': model_normal(0.5, 0.2),
        'norm_02_01': model_normal(0.2, 0.1),
        'norm_08_01': model_normal(0.8, 0.1),
        'bimodal': model_bimodal(),
        'left_peak': model_left_peak(),
        'right_peak': model_right_peak()
    }

    if model == 'auto':
        target_pdfs = {}
        for name, pdf_func in models.items():
            vals = pdf_func(centers)
            vals = np.maximum(vals, 1e-12)
            vals /= vals.sum()
            target_pdfs[name] = vals
        input_pdf = hist / hist.sum()
        input_pdf = np.maximum(input_pdf, 1e-12)
        kl_divs = {}
        for name, tpdf in target_pdfs.items():
            kl_divs[name] = entropy(input_pdf, tpdf)
        best_model = min(kl_divs, key=kl_divs.get)
    else:
        if model not in models:
            raise ValueError(f"未知模型: {model}")
        best_model = model

    pdf_func = models[best_model]
    target_pdf = pdf_func(centers)
    target_pdf = np.maximum(target_pdf, 1e-12)
    target_pdf /= target_pdf.sum()
    target_cdf = np.cumsum(target_pdf)
    target_cdf /= target_cdf[-1]

    input_cdf = np.cumsum(hist) / hist.sum()
    map_table = np.zeros_like(centers)
    for i, cdf_val in enumerate(input_cdf):
        idx = np.argmin(np.abs(target_cdf - cdf_val))
        map_table[i] = centers[idx]

    if smooth_sigma > 0:
        map_table = gaussian_filter1d(map_table, sigma=smooth_sigma, mode='reflect')
        map_table = np.clip(map_table, 0, 1)
        map_table[0] = 0.0
        map_table[-1] = 1.0
        map_table = np.maximum.accumulate(map_table)

    flat = arr.ravel()
    mapped_flat = np.interp(flat, centers, map_table)
    processed = mapped_flat.reshape(arr.shape)
    return _apply_strength(orig, processed, strength)

# ---------- 引导滤波工具（输出增强与 Hybrid 共用） ----------

def _box_filter(x, r):
    """
    盒式均值滤波（窗口 (2r+1)），O(n) 积分图实现。
    x: 2D float32/float64 -> 2D float64（8K 大图累加精度要求 float64）。
    """
    h, w = x.shape
    x64 = x.astype(np.float64)
    if r <= 0:
        return x64
    pad = np.pad(x64, r, mode='edge')          # 边缘复制填充，避免边界变暗
    integral = np.cumsum(np.cumsum(pad, axis=0), axis=1)
    # I[i,j] = pad[0:i, 0:j] 的和；窗口 [a..a+2r] 的和 = 四角差分
    s = (integral[2*r:2*r + h, 2*r:2*r + w]
         - integral[:h, 2*r:2*r + w]
         - integral[2*r:2*r + h, :w]
         + integral[:h, :w])
    return s / float((2 * r + 1) ** 2)


def guided_filter_box(guide, src, radius=9, eps=1e-4):
    """
    引导滤波（单通道引导图，He et al. 2010 盒式版本）。
    guide: 2D float32 [0,1] 引导图（如 RGB 亮度或深度图自身）
    src:   2D float32 [0,1] 待滤波图
    返回: 2D float32 [0,1]。以 guide 的边缘约束 src 的平滑，
    避免双边滤波的梯度反转问题；天空/水面等无纹理区域被平滑，
    物体边缘保持锐利。
    """
    guide = np.asarray(guide, dtype=np.float64)
    src = np.asarray(src, dtype=np.float64)
    mean_i = _box_filter(guide, radius)
    mean_p = _box_filter(src, radius)
    corr_i = _box_filter(guide * guide, radius)
    corr_ip = _box_filter(guide * src, radius)
    var_i = corr_i - mean_i * mean_i
    cov_ip = corr_ip - mean_i * mean_p
    a = cov_ip / (var_i + eps)
    b = mean_p - a * mean_i
    mean_a = _box_filter(a, radius)
    mean_b = _box_filter(b, radius)
    q = mean_a * guide + mean_b
    return q.astype(np.float32)


# ---------- 统一入口 ----------
def apply_postprocess(depth, algorithm, params):
    """
    深度图后处理入口
    depth: np.ndarray, float32, [0,1]
    algorithm: 'S1','S5','N1_KMeans','N1A','N2'
    params: dict, 包含算法所需的参数
    """
    if algorithm == 'none':
        return depth
    defaults = {
        'S1': {'strength': 1.0},
        'S5': {'K_GMM': 5, 'min_segment_ratio': 0.001, 'strength': 1.0},
        'N1_KMeans': {'K': 5, 'strength': 1.0},
        'N1A': {'smooth_sigma': 0.02, 'height_ratio': 0.01, 'strength': 1.0},
        'N2': {'model': 'auto', 'smooth_sigma': 2.0, 'strength': 1.0}
    }
    merged = defaults.get(algorithm, {})
    merged.update(params)
    if algorithm == 'S1':
        return s1_cdf_equalization(depth, **merged)
    elif algorithm == 'S5':
        return s5_gmm_partition(depth, **merged)
    elif algorithm == 'N1_KMeans':
        return n1_kmeans_linear(depth, **merged)
    elif algorithm == 'N1A':
        return n1_auto_peak(depth, **merged)
    elif algorithm == 'N2':
        return n2_ideal_specification(depth, **merged)
    else:
        return depth
