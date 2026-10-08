# -*- coding: utf-8 -*-
"""upscale_sr / postprocess.upscale 新超分模式自检（本地验证用，不入打包清单）。

运行：depth_v8_builder\\python.exe test_upscale_sr.py
覆盖：常数图保恒、形状/有限性、k<=1 与 none 早退、guide 缺失/尺寸不符回退、
sigma_r 越界钳制、算法异常回退、未知 mode 报错、bilinear 路径不变。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import postprocess as pipeline
import upscale_sr

FAILURES = []


def check(name, cond, detail=''):
    tag = 'PASS' if cond else 'FAIL'
    print(f"[{tag}] {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILURES.append(name)


def make_inputs(h=240, w=320, scale=4):
    rng = np.random.default_rng(7)
    lr = rng.random((h, w)).astype(np.float32) * 0.8 + 0.1
    rgb = rng.integers(0, 256, (h * scale, w * scale, 3), dtype=np.uint8)
    return lr, rgb.astype(np.float32) / 255.0


def main():
    lr, rgb01 = make_inputs()
    H, W = rgb01.shape[:2]

    # 1. 常数图保恒（引导滤波/联合双边的基本性质）；guide 需与 target 同尺寸
    for mode in ('wgif', 'jbu-nc'):
        out, diag = pipeline.upscale(np.full((60, 80), 0.37, np.float32),
                                     (240, 320), mode, {}, guide=rgb01[:240, :320])
        check(f'{mode} 常数图保恒', out.shape == (240, 320)
              and float(np.max(np.abs(out - 0.37))) < 1e-5,
              f'max|Δ|={float(np.max(np.abs(out - 0.37))):.2e} diag={diag.get("mode")}')
        check(f'{mode} 常数图无回退', 'fallback' not in diag, str(diag.get('fallback')))

    # 2. 随机图：形状、finite、float32、无回退
    for mode in ('wgif', 'jbu-nc'):
        out, diag = pipeline.upscale(lr, (H, W), mode, {}, guide=rgb01)
        check(f'{mode} 输出形状/finite', out.shape == (H, W)
              and out.dtype == np.float32 and np.isfinite(out).all())
        check(f'{mode} 无回退', 'fallback' not in diag, str(diag.get('fallback')))
        check(f'{mode} 诊断含 k', 'k' in diag, str(diag))

    # 3. wgif 参数：radius 越界钳制（服务端已限 1..32，这里验证不炸）
    out, diag = pipeline.upscale(lr, (H, W), 'wgif', {'radius': 999}, guide=rgb01)
    check('wgif radius=999 不炸且无回退', 'fallback' not in diag, str(diag.get('fallback')))

    # 4. jbu-nc sigma_r 越界钳制到 [0.02, 0.10]
    _, diag_lo = pipeline.upscale(lr, (H, W), 'jbu-nc', {'sigma_r': 0.001}, guide=rgb01)
    _, diag_hi = pipeline.upscale(lr, (H, W), 'jbu-nc', {'sigma_r': 5.0}, guide=rgb01)
    check('jbu-nc sigma_r 下界钳制', diag_lo.get('sigma_r') == upscale_sr.JBU_SIGMA_R_MIN,
          str(diag_lo.get('sigma_r')))
    check('jbu-nc sigma_r 上界钳制', diag_hi.get('sigma_r') == upscale_sr.JBU_SIGMA_R_MAX,
          str(diag_hi.get('sigma_r')))

    # 5. guide 缺失 / 尺寸不符 → 回退 bilinear+AA
    for guide, tag in ((None, '缺失'), (rgb01[: H // 2], '尺寸不符')):
        out, diag = pipeline.upscale(lr, (H, W), 'wgif', {}, guide=guide)
        check(f'wgif guide {tag} → 回退', diag.get('fallback') == 'guide_unavailable'
              and out.shape == (H, W) and np.isfinite(out).all(), str(diag.get('fallback')))

    # 5b. guide 含 NaN → 算法层 ValueError → 回退 bilinear
    bad_guide = rgb01.copy()
    bad_guide[100:110, 200:210] = np.nan
    out, diag = pipeline.upscale(lr, (H, W), 'wgif', {}, guide=bad_guide)
    check('wgif guide 含 NaN → 回退', str(diag.get('fallback', '')).startswith('wgif_failed:')
          and np.isfinite(out).all(), str(diag.get('fallback')))

    # 6. 算法异常 → 回退（模拟内部失败）
    orig_wgif, orig_jbu = upscale_sr.wgif, upscale_sr.jbu_nc_lr
    try:
        upscale_sr.wgif = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom'))
        out, diag = pipeline.upscale(lr, (H, W), 'wgif', {}, guide=rgb01)
        check('wgif 异常 → 回退 bilinear',
              str(diag.get('fallback', '')).startswith('wgif_failed:')
              and np.isfinite(out).all(), str(diag.get('fallback')))
        upscale_sr.jbu_nc_lr = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom'))
        out, diag = pipeline.upscale(lr, (H, W), 'jbu-nc', {}, guide=rgb01)
        check('jbu-nc 异常 → 回退 bilinear',
              str(diag.get('fallback', '')).startswith('jbu-nc_failed:')
              and np.isfinite(out).all(), str(diag.get('fallback')))
    finally:
        upscale_sr.wgif, upscale_sr.jbu_nc_lr = orig_wgif, orig_jbu

    # 7. k<=1（缩小目标）：n_smooth=0 路径
    guide_small = rgb01[: H // 2, : W // 2]
    out, diag = pipeline.upscale(lr, (H // 2, W // 2), 'jbu-nc', {}, guide=guide_small)
    check('k<1 缩小目标 jbu-nc 正常执行',
          out.shape == (H // 2, W // 2) and 'fallback' not in diag, str(diag))

    # 8. none 与 shape==target 早退
    out, diag = pipeline.upscale(lr, (120, 160), 'none', {}, guide=None)
    check('none 早退', diag == {'mode': 'none', 'applied': False}, str(diag))
    out, diag = pipeline.upscale(lr, lr.shape, 'bilinear', {})
    check('shape==target 早退', diag['applied'] is True and out.shape == lr.shape, str(diag))

    # 9. bilinear：本次有意改为高斯 AA + 门控拉直（质量修复），不再与旧版逐位一致；
    #    改查：finite/形状、直方图开路下 diag 带 straighten、无 guide 时无 straighten
    lr2, _ = make_inputs(150, 210, 3)
    a, da = pipeline.upscale(lr2, (450, 630), 'bilinear', {},
                             guide=rgb01[: 450, : 630])
    check('bilinear 新链 finite/带拉直', a.shape == (450, 630) and np.isfinite(a).all()
          and 'straighten' in da and da.get('sigma_aa'), str(da.get('straighten')))
    b, db = pipeline.upscale(lr2, (450, 630), 'bilinear', {}, guide=None)
    check('bilinear 无 guide → 无拉直', 'straighten' not in db and np.isfinite(b).all(),
          str(db.get('fallback')))

    # 10. 未知 mode 报错；gf 已退役
    try:
        pipeline.upscale(lr, (H, W), 'gf', {}, guide=rgb01)
        check('gf 模式已退役', False, '未抛错')
    except ValueError:
        check('gf 模式已退役', True)

    # 11. upscale_sr 直接调用的边界
    for bad in (np.zeros((2, 2, 3), np.float32), np.full((4, 4), np.nan, np.float32)):
        try:
            upscale_sr.wgif(bad, np.zeros((4, 4), np.float32), 2, 1e-2)
            check('wgif 非法输入抛 ValueError', False, str(bad.shape))
        except ValueError:
            check('wgif 非法输入抛 ValueError', True)
        except Exception as exc:
            check('wgif 非法输入抛 ValueError', False, f'错误类型: {type(exc).__name__}')
    try:
        upscale_sr.jbu_nc(np.zeros((4, 4), np.float32), np.zeros((5, 5), np.float32))
        check('jbu-nc 形状不一致抛 ValueError', False)
    except ValueError:
        check('jbu-nc 形状不一致抛 ValueError', True)
    try:
        upscale_sr.jbu_nc_lr(np.zeros((41, 41), np.float32),
                             np.zeros((10, 10), np.float32), (40, 40))
        check('jbu-nc LR 版非法输入抛 ValueError', False)
    except ValueError:
        check('jbu-nc LR 版非法输入抛 ValueError', True)

    # 11b. LR 网格制边缘吸附回归（2026-09-19 实机修正的行为锚）：
    # LR 深度阶跃与 RGB 阶跃错位 2 个 LR 像素时，输出的 50% 穿越位置应比
    # 双线性基线显著向 RGB 边一侧移动（旧 HR 网格制在大 k 下无此能力）。
    from PIL import Image as _PILImage
    k = 8
    lr_step = np.where(np.arange(64)[None, :] < 32, 0.2, 0.8).astype(np.float32)
    lr_step = np.repeat(lr_step, 64, axis=0)
    hr = (64 * k, 64 * k)
    gx = np.arange(hr[1])
    row = np.where(gx >= 32 * k + 16, 0.9, 0.1).astype(np.float32)
    rgb_step = np.broadcast_to(row[None, :, None], (hr[0], hr[1], 3)).copy()
    base = np.asarray(_PILImage.fromarray(lr_step, mode='F').resize(
        (hr[1], hr[0]), _PILImage.Resampling.BILINEAR), np.float32)
    gray_step = row[None, :].repeat(hr[0], axis=0).astype(np.float32)
    out_snap = upscale_sr.jbu_nc_lr(gray_step, lr_step, hr, sigma_r=0.05)
    y = hr[0] // 2
    row_new = out_snap[y]
    row_base = base[y]
    # 1) RGB 边右侧恢复亮深度；深度边左侧保持暗深度
    right_ok = float(row_new[288:352].mean()) > 0.75
    left_ok = float(row_new[192:240].mean()) < 0.28
    # 2) 主跳变位置：|diff| 最大的像素应落在 RGB 边缘附近（≥264），且比基线更靠右
    def main_step(rowv):
        gd = np.abs(np.diff(rowv[240:300]))
        return 240 + int(np.argmax(gd))
    step_new, step_base = main_step(row_new), main_step(row_base)
    check('jbu-nc LR 版边缘向 RGB 阶跃吸附',
          right_ok and left_ok and step_new >= 264 and step_new > step_base,
          f'右亮={float(row_new[288:352].mean()):.3f} 左暗={float(row_new[192:240].mean()):.3f} '
          f'主跳变 new@{step_new} base@{step_base} RGB目标=272')

    # 12. depth_server._validate_request 的 upscale 校验（需 flask）
    try:
        import depth_server as ds
    except Exception as exc:
        print(f"[SKIP] depth_server 校验（导入失败: {exc}）")
    else:
        base_img = 'iVBORw0KGgoAAAANSUhEUg=='  # 会在图像校验前先测 upscale 字段
        def expect_err(name, data, frag):
            try:
                ds._validate_request(dict(data))
                check(name, False, '未抛错')
            except ValueError as exc:
                check(name, frag in str(exc), str(exc))
        good = {'image': base_img, 'upscale': {'mode': 'wgif', 'params': {'radius': 4}}}
        try:
            ds._validate_request(dict(good))
            check('validate: wgif radius=4 通过', True)
        except ValueError as exc:
            # 图像字段是假 base64，若错误落在 image 校验说明 upscale 校验已通过
            check('validate: wgif radius=4 通过', 'image' in str(exc), str(exc))
        expect_err('validate: jbu-nc sigma_r 越界拒绝',
                   {'image': base_img,
                    'upscale': {'mode': 'jbu-nc', 'params': {'sigma_r': 0.5}}},
                   'sigma_r')
        expect_err('validate: jbu-nc 未知参数拒绝',
                   {'image': base_img,
                    'upscale': {'mode': 'jbu-nc', 'params': {'radius': 4}}},
                   '未知参数')
        expect_err('validate: gf 模式拒绝',
                   {'image': base_img, 'upscale': {'mode': 'gf'}}, 'wgif')

    print()
    if FAILURES:
        print(f'结果：{len(FAILURES)} 项失败 → {FAILURES}')
        sys.exit(1)
    print('结果：全部通过')


if __name__ == '__main__':
    main()
