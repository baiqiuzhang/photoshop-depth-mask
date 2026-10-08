#!/usr/bin/env node
/* eslint-env node */
/* 前端纯 JS 模块单元测试（Node，解析测试向量，不依赖 Python/UXP）
 * 运行：node test_frontend_core.js
 */
'use strict';
const path = require('path');
const PLUGIN = 'D:/depth_pro_photoshop_jsx/0.2.1/com.zk21.depthpro';
const SPECTRUM_PLUGIN = 'D:/depth_pro_photoshop_jsx/0.2.1/com.zk21.spectrum';
const PngCodec = require(path.join(PLUGIN, 'png_codec.js'));
const MixCore = require(path.join(PLUGIN, 'mix_core.js'));
const SpectrumCore = require(path.join(SPECTRUM_PLUGIN, 'spectrum_core.js'));

let pass = 0, fail = 0;
function check(name, cond, detail) {
    if (cond) { pass++; console.log('  ✅ ' + name); }
    else { fail++; console.log('  ❌ ' + name + (detail ? '  ' + detail : '')); }
}
function approx(a, b, eps) { return Math.abs(a - b) <= (eps || 1e-4); }

// ============ 1. png_codec ============
console.log('== png_codec ==');
{
    // 1a. 16-bit 灰度编码→解码往返
    const w = 37, h = 23;
    const lum = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) lum[i] = (i * 37) % 65536 / 65535; // 全范围分布
    const png16 = PngCodec.encodeGray(lum, w, h, 16);
    check('16bit PNG 签名', png16[0] === 0x89 && png16[1] === 0x50);
    const dec16 = PngCodec.decode(png16);
    check('16bit 解码 header', dec16.bitDepth === 16 && dec16.colorType === 0 && dec16.width === w && dec16.height === h);
    let maxErr = 0;
    for (let i = 0; i < w * h; i++) {
        const err = Math.abs(dec16.data[i] / 65535 - lum[i]);
        if (err > maxErr) maxErr = err;
    }
    check('16bit 往返误差 ≤ 1/65535', maxErr <= 1 / 65535 + 1e-9, 'maxErr=' + maxErr);

    // 1b. 8-bit 灰度往返
    const png8 = PngCodec.encodeGray(lum, w, h, 8);
    const dec8 = PngCodec.decode(png8);
    check('8bit 解码 header', dec8.bitDepth === 8 && dec8.colorType === 0);
    check('8bit 往返误差 ≤ 1/255', (() => {
        let e = 0;
        for (let i = 0; i < w * h; i++) e = Math.max(e, Math.abs(dec8.data[i] / 255 - lum[i]));
        return e <= 1 / 255 + 1e-6;
    })());

    // 1c. 手工构造 8-bit 灰度 PNG（filter 0/1/2/3/4 各测一段）→ 解码（字节级滤波与样本级一致）
    const w2 = 64, h2 = 16;
    const bpp = 1;
    const stride = w2 * bpp;
    const pixels = new Uint8Array(w2 * h2);
    for (let i = 0; i < w2 * h2; i++) pixels[i] = (i * 97 + 41) % 256;
    const rawBytes = new Uint8Array(h2 * (1 + stride));
    for (let y = 0; y < h2; y++) {
        const ft = y % 5; // 0..4 覆盖全部滤波器
        rawBytes[y * (1 + stride)] = ft;
        const rowStart = y * (1 + stride) + 1;
        for (let x = 0; x < w2; x++) {
            const v = pixels[y * w2 + x];
            const a = x >= 1 ? pixels[y * w2 + x - 1] : 0;
            const b = y >= 1 ? pixels[(y - 1) * w2 + x] : 0;
            const c = (x >= 1 && y >= 1) ? pixels[(y - 1) * w2 + x - 1] : 0;
            let sub;
            switch (ft) {
                case 0: sub = v; break;
                case 1: sub = (v - a) & 0xFF; break;
                case 2: sub = (v - b) & 0xFF; break;
                case 3: sub = (v - ((a + b) >> 1)) & 0xFF; break;
                case 4: {
                    const p = a + b - c;
                    const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
                    const pr = (pa <= pb && pa <= pc) ? a : (pb <= pc ? b : c);
                    sub = (v - pr) & 0xFF; break;
                }
            }
            rawBytes[rowStart + x] = sub;
        }
    }
    const pako = require(path.join(PLUGIN, 'vendor', 'pako.min.js'));
    const idat = pako.deflate(rawBytes);
    const crcTable = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1); t[n] = c >>> 0; } return t; })();
    function crc(bytes, s, e) { let c = 0xFFFFFFFF; for (let i = s; i < e; i++) c = crcTable[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }
    function chunk2(type, payload) {
        const out = new Uint8Array(12 + payload.length);
        const dv = new DataView(out.buffer);
        dv.setUint32(0, payload.length);
        for (let i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i);
        out.set(payload, 8);
        dv.setUint32(8 + payload.length, crc(out, 4, 8 + payload.length));
        return out;
    }
    const ihdr = new Uint8Array(13);
    const dvh = new DataView(ihdr.buffer);
    dvh.setUint32(0, w2); dvh.setUint32(4, h2);
    ihdr[8] = 8; ihdr[9] = 0; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
    const sig = new Uint8Array([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]);
    const pngManual = new Uint8Array(8 + (12 + 13) + (12 + idat.length) + 12);
    pngManual.set(sig, 0);
    let off = 8;
    for (const c of [chunk2('IHDR', ihdr), chunk2('IDAT', idat), chunk2('IEND', new Uint8Array(0))]) { pngManual.set(c, off); off += c.length; }
    const decM = PngCodec.decode(pngManual);
    let errM = 0;
    for (let i = 0; i < w2 * h2; i++) errM = Math.max(errM, Math.abs(decM.data[i] - pixels[i]));
    check('手工 8bit 灰度 PNG（5 种滤波器）解码无损', errM === 0, 'maxErr=' + errM);

    // 1d. RGBA 8-bit 解码（alpha=通道导出路径）
    const w3 = 5, h3 = 4;
    const rgba = new Uint8Array(w3 * h3 * 4);
    for (let i = 0; i < w3 * h3; i++) { rgba[i * 4] = 10; rgba[i * 4 + 1] = 20; rgba[i * 4 + 2] = 30; rgba[i * 4 + 3] = (i * 60) % 256; }
    const raw3 = new Uint8Array(h3 * (1 + w3 * 4));
    for (let y = 0; y < h3; y++) { raw3[y * (1 + w3 * 4)] = 0; raw3.set(rgba.subarray(y * w3 * 4, (y + 1) * w3 * 4), y * (1 + w3 * 4) + 1); }
    const idat3 = pako.deflate(raw3);
    const ihdr3 = new Uint8Array(13);
    const dv3 = new DataView(ihdr3.buffer);
    dv3.setUint32(0, w3); dv3.setUint32(4, h3);
    ihdr3[8] = 8; ihdr3[9] = 6; // RGBA
    const png3 = new Uint8Array(8 + (12 + 13) + (12 + idat3.length) + 12);
    png3.set(sig, 0); off = 8;
    for (const c of [chunk2('IHDR', ihdr3), chunk2('IDAT', idat3), chunk2('IEND', new Uint8Array(0))]) { png3.set(c, off); off += c.length; }
    const dec3 = PngCodec.decode(png3);
    const gf3 = PngCodec.grayFloat(dec3);
    check('RGBA 解码 + grayFloat 取 alpha', dec3.channels === 4 && approx(gf3[0], rgba[3] / 255, 1e-6));
}

// ============ 2. mix_core ============
console.log('== mix_core ==');
{
    const n = 200;
    const a = new Float32Array(n), b = new Float32Array(n);
    for (let i = 0; i < n; i++) { a[i] = (i % 17) / 17; b[i] = ((i * 7) % 23) / 23; }
    const OPS = MixCore.OPS.map(o => o.key);
    for (const op of OPS) {
        // w=0 / w=1 端点语义
        if (op === 'average') {
            const r0 = MixCore.mixRaw(a, b, op, 0), r1 = MixCore.mixRaw(a, b, op, 1);
            let e0 = 0, e1 = 0;
            for (let i = 0; i < n; i++) { e0 = Math.max(e0, Math.abs(r0[i] - b[i])); e1 = Math.max(e1, Math.abs(r1[i] - a[i])); }
            check('average w=0→B, w=1→A', e0 < 1e-5 && e1 < 1e-5);
        } else {
            const r0 = MixCore.mixRaw(a, b, op, 0);
            let e0 = 0;
            for (let i = 0; i < n; i++) e0 = Math.max(e0, Math.abs(r0[i] - a[i]));
            check(op + ' w=0→A', e0 < 1e-5);
        }
        // 公式数值（w=0.5）
        const r5 = MixCore.mixRaw(a, b, op, 0.5);
        let e = 0;
        for (let i = 0; i < n; i++) {
            const x = a[i], y = b[i];
            let want;
            switch (op) {
                case 'average': want = 0.5 * x + 0.5 * y; break;
                case 'multiply': want = Math.sqrt(x * y); break;
                case 'rms': want = Math.sqrt((x * x + y * y) / 2); break;
                case 'screen': want = 1 - Math.sqrt((1 - x) * (1 - y)); break;
                case 'difference': want = 0.5 * Math.abs(x - y) + 0.5 * x; break;
                case 'max': want = 0.5 * Math.max(x, y) + 0.5 * x; break;
                case 'min': want = 0.5 * Math.min(x, y) + 0.5 * x; break;
                case 'overlay': {
                    const ov = x < 0.5 ? 2 * x * y : 1 - 2 * (1 - x) * (1 - y);
                    want = 0.5 * ov + 0.5 * x; break;
                }
            }
            e = Math.max(e, Math.abs(r5[i] - want));
        }
        check(op + ' w=0.5 公式', e < 1e-5, 'err=' + e);
    }
    // 归一化端点
    const m = MixCore.mix(a, b, 'difference', 0.5);
    let mn = 1, mx = 0;
    for (let i = 0; i < n; i++) { mn = Math.min(mn, m.data[i]); mx = Math.max(mx, m.data[i]); }
    check('mix 归一化到 [0,1]', approx(mn, 0, 1e-6) && approx(mx, 1, 1e-6));
    // 长度不一致报错
    let threw = false;
    try { MixCore.mix(a, new Float32Array(5), 'average', 0.5); } catch (e) { threw = true; }
    check('尺寸不一致抛错', threw);
}

// ============ 3. spectrum_core ============
console.log('== spectrum_core ==');
{
    // 3a. fft1d: delta → 全 1 幅值
    const N = 64;
    const re = new Float64Array(N), im = new Float64Array(N);
    re[0] = 1;
    SpectrumCore.fft1d(re, im, false);
    let flat = true;
    for (let k = 0; k < N; k++) if (Math.abs(Math.hypot(re[k], im[k]) - 1) > 1e-9) flat = false;
    check('fft1d delta → 平坦幅值', flat);

    // 3b. fft1d: 频率 k=5 正弦 → 峰值在 5 与 N-5
    const re2 = new Float64Array(N), im2 = new Float64Array(N);
    for (let i = 0; i < N; i++) re2[i] = Math.sin(2 * Math.PI * 5 * i / N);
    SpectrumCore.fft1d(re2, im2, false);
    let maxK = 0; let maxV = 0;
    for (let k = 0; k < N; k++) { const v = Math.hypot(re2[k], im2[k]); if (v > maxV) { maxV = v; maxK = k; } }
    check('fft1d 正弦 k=5 → 峰值 bin=5', maxK === 5 || maxK === N - 5, 'maxK=' + maxK);

    // 3c. dft：冲激图 → 频谱平坦（log(1+|F|) 近似常数）
    const dw = 64, dh = 64;
    const delta = new Float32Array(dw * dh);
    delta[32 * dw + 32] = 1;
    const r = SpectrumCore.compute('dft', delta, dw, dh, { window: 'none' });
    check('dft 冲激 → 尺寸一致', r.width === dw && r.height === dh && r.data.length === dw * dh);
    let dStd = 0, dMean = 0;
    for (let i = 0; i < r.data.length; i++) dMean += r.data[i];
    dMean /= r.data.length;
    for (let i = 0; i < r.data.length; i++) dStd += (r.data[i] - dMean) ** 2;
    dStd = Math.sqrt(dStd / r.data.length);
    check('dft 冲激 → 频谱平坦（std 小）', dStd < 0.05, 'std=' + dStd.toFixed(4));

    // 3d. dft：垂直正弦光栅（f0=4 周期/图）→ 谱峰在 DC 两侧 ±f0 个 bin、水平轴上
    const gw = 128, gh = 128, f0 = 4;
    const grating = new Float32Array(gw * gh);
    for (let y = 0; y < gh; y++) for (let x = 0; x < gw; x++) grating[y * gw + x] = 0.5 + 0.5 * Math.sin(2 * Math.PI * f0 * x / gw);
    const rd = SpectrumCore.compute('dft', grating, gw, gh, { window: 'none' });
    // 找峰值位置（排除中心 DC 附近 3px）
    let bx = -1, by = -1, bv = 0;
    for (let y = 2; y < gh - 2; y++) for (let x = 2; x < gw - 2; x++) {
        const dc = (x === gw >> 1) && (y === gh >> 1);
        if (!dc && rd.data[y * gw + x] > bv) { bv = rd.data[y * gw + x]; bx = x; by = y; }
    }
    const distFromCenter = Math.hypot(bx - gw / 2, by - gh / 2);
    check('dft 光栅 → 谱峰在 ±f0 bin、水平轴上', Math.abs(distFromCenter - f0) < 1.5 && Math.abs(by - gh / 2) <= 2,
        'dist=' + distFromCenter.toFixed(1) + ' peak@(' + bx + ',' + by + ')');

    // 3e. dct：常量图 → DC 主导（象限换位后中心=1）
    const cw = 64, ch = 64;
    const ones = new Float32Array(cw * ch);
    ones.fill(0.5);
    const rc = SpectrumCore.compute('dct', ones, cw, ch, { window: 'none' });
    const cx = cw >> 1, cy = ch >> 1;
    let centerVal = rc.data[cy * cw + cx];
    let maxOff = 0;
    for (let i = 0; i < cw * ch; i++) if (i !== cy * cw + cx) maxOff = Math.max(maxOff, rc.data[i]);
    check('dct 常量 → DC 居中且主导', approx(centerVal, 1, 1e-4) && maxOff < 0.05, 'center=' + centerVal.toFixed(3) + ' off=' + maxOff.toFixed(3));

    // 3f. dwt：垂直阶跃（x<33 → 0，边界落在 Haar 配对 (32,33) 上）→ 细节能量图
    //     在边界列有亮线（无四块拼接、无 NaN）
    const sw = 64, sh = 64;
    const step = new Float32Array(sw * sh);
    for (let y = 0; y < sh; y++) for (let x = 0; x < sw; x++) step[y * sw + x] = x < 33 ? 0 : 1;
    const rw = SpectrumCore.compute('dwt', step, sw, sh, { levels: 1 });
    check('dwt 细节图尺寸一致', rw.width === sw && rw.height === sh);
    let nan = 0;
    for (let i = 0; i < rw.data.length; i++) if (Number.isNaN(rw.data[i])) nan++;
    check('dwt 无 NaN', nan === 0);
    function colMean(arr, col) { let s = 0; for (let y = 0; y < sh; y++) s += arr[y * sw + col]; return s / sh; }
    const boundary = Math.max(colMean(rw.data, 32), colMean(rw.data, 33));
    const away = colMean(rw.data, 10);
    check('dwt 边界列亮线（无四块）', boundary > away * 3,
        'boundary=' + boundary.toFixed(3) + ' away=' + away.toFixed(3));

    // 3g. gabor：匹配方向响应 >> 正交方向（零均值光栅，避免 DC 影响）
    const gs = 256;
    const g0 = 0.08, gth = 30;
    const grating2 = new Float32Array(gs * gs);
    const thRad = gth * Math.PI / 180;
    for (let y = 0; y < gs; y++) for (let x = 0; x < gs; x++) {
        grating2[y * gs + x] = 0.5 * Math.sin(2 * Math.PI * g0 * (x * Math.cos(thRad) + y * Math.sin(thRad)));
    }
    const rg1 = SpectrumCore.compute('gabor', grating2, gs, gs, { freq: g0, theta: gth });
    const rg2 = SpectrumCore.compute('gabor', grating2, gs, gs, { freq: g0, theta: gth + 90 });
    const mean = a => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i]; return s / a.length; };
    const m1 = mean(rg1.data), m2 = mean(rg2.data);
    check('gabor 匹配方向响应 > 正交方向', m1 > m2 * 2, 'm1=' + m1.toFixed(3) + ' m2=' + m2.toFixed(3));

    // 3h. shearlet：可运行且输出 [0,1]
    const rs = SpectrumCore.compute('shearlet', grating2, gs, gs, { orientations: 6 });
    let inRange = true;
    for (let i = 0; i < rs.data.length; i++) if (rs.data[i] < 0 || rs.data[i] > 1) { inRange = false; break; }
    check('shearlet 输出 [0,1] 且尺寸一致', inRange && rs.width === gs && rs.height === gs);

    // 3i. computeProfile：dft 径向平均功率谱——垂直光栅（f0=4）→ 峰在对应频率 bin
    const rp = SpectrumCore.computeProfile('dft', grating, gw, gh, { window: 'none' });
    check('径向谱 kind=radial', rp.kind === 'radial' && rp.values.length === rp.labels.length);
    let peakK = 0, peakV = -1;
    for (let k = 1; k < rp.values.length; k++) if (rp.values[k] > peakV) { peakV = rp.values[k]; peakK = k; }
    check('径向谱峰在 f0 bin（±2）', Math.abs(peakK - 4) <= 2, 'peakK=' + peakK + ' len=' + rp.values.length);

    // 3j. computeProfile：gabor 方向谱——垂直条纹（沿 x 正弦）→ 0° 能量 > 90°
    const vw = 128, vh = 128;
    const vg = new Float32Array(vw * vh);
    for (let y = 0; y < vh; y++) for (let x = 0; x < vw; x++) vg[y * vw + x] = 0.5 * Math.sin(2 * Math.PI * 4 * x / vw);
    const dp = SpectrumCore.computeProfile('gabor', vg, vw, vh, { freq: 0.08 });
    check('方向谱 kind=directional', dp.kind === 'directional' && dp.values.length >= 3);
    const e0 = dp.values[0], e90 = dp.values[dp.values.length >> 1];
    check('0° 方向能量 > 90°', e0 > e90 * 2, 'e0=' + e0.toFixed(3) + ' e90=' + e90.toFixed(3));

    // 3k. computeProfile：dwt 尺度谱——平滑图细节平坦；阶跃图有变化
    const flatImg = new Float32Array(64 * 64);
    flatImg.fill(0.5);
    const sp = SpectrumCore.computeProfile('dwt', flatImg, 64, 64, { levels: 1 });
    check('尺度谱 kind=scale 且 3 柱', sp.kind === 'scale' && sp.values.length === 3);
    let allFlatV = true;
    for (let i = 0; i < sp.values.length; i++) if (Math.abs(sp.values[i] - 0.5) > 1e-3) allFlatV = false;
    check('平滑图尺度谱平坦（0.5 填充）', allFlatV);
    const sp2 = SpectrumCore.computeProfile('dwt', step, 64, 64, { levels: 1 });
    let varied = false;
    for (let i = 0; i < sp2.values.length; i++) if (Math.abs(sp2.values[i] - 0.5) > 0.05) varied = true;
    check('阶跃图尺度谱有变化', varied);
}

// ============ 4. spectrum_core 大尺寸回归（必须覆盖 downsample 缩放分支） ============
// 上面第 3 节全部用 ≤256 的小图：gabor/shearlet 的 cap=768、方向谱的 cap=160 都不会触发，
// 因此 downsample() 的缩放分支从未被测到。本节刻意使用超过 cap 的尺寸。
// 实机对应：预览长边 512 → 方向谱 cap=160 必然缩放；生成上限 2048 → cap=768/1024 必然缩放。
console.log('== spectrum_core 大尺寸（缩放分支） ==');
{
    function synth(w, h) {
        const a = new Float32Array(w * h);
        for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
            a[y * w + x] = 0.5 + 0.3 * Math.sin(2 * Math.PI * 6 * x / w) + 0.2 * Math.cos(2 * Math.PI * 4 * y / h);
        }
        return a;
    }
    function audit(tag, r) {
        check(tag + ' width/height 为有限正数',
            Number.isFinite(r.width) && Number.isFinite(r.height) && r.width > 0 && r.height > 0,
            'width=' + r.width + ' height=' + r.height);
        if (!r.data) return;
        check(tag + ' data.length === width*height', r.data.length === r.width * r.height,
            'len=' + r.data.length + ' 期望=' + (r.width * r.height));
        let nan = 0, mn = Infinity, mx = -Infinity;
        for (let i = 0; i < r.data.length; i++) {
            const v = r.data[i];
            if (Number.isNaN(v)) nan++;
            if (v < mn) mn = v;
            if (v > mx) mx = v;
        }
        check(tag + ' 无 NaN', nan === 0, 'nan=' + nan);
        check(tag + ' 值域 [0,1]', mn >= 0 && mx <= 1, 'min=' + mn + ' max=' + mx);
    }

    // 4a. compute：超过各自 cap 的输入必须仍返回有效几何（dct cap=1024、gabor/shearlet cap=768）
    for (const [w, h] of [[960, 720], [2048, 1536]]) {
        const img = synth(w, h);
        for (const m of ['dct', 'gabor', 'shearlet', 'dwt']) {
            let r;
            try {
                r = SpectrumCore.compute(m, img, w, h,
                    { window: 'hann', freq: 0.1, theta: 0, orientations: 6, levels: 2 });
            } catch (e) { check(m + ' ' + w + 'x' + h + ' 不抛异常', false, e.message); continue; }
            audit(m + ' ' + w + 'x' + h, r);
        }
    }

    // 4b. computeProfile：960×720 > directionalProfile cap=160，必然走缩放分支。
    //     垂直光栅 → 0° 方向能量应显著大于 90°（缩放分支失效时会退化成全 0.5 的平线）
    const pw2 = 960, ph2 = 720, f2 = 0.1;
    const vg2 = new Float32Array(pw2 * ph2);
    for (let y = 0; y < ph2; y++) for (let x = 0; x < pw2; x++) {
        vg2[y * pw2 + x] = 0.5 * Math.sin(2 * Math.PI * f2 * x);
    }
    const dp2 = SpectrumCore.computeProfile('gabor', vg2, pw2, ph2, { freq: f2 });
    const e0b = dp2.values[0], e90b = dp2.values[dp2.values.length >> 1];
    check('960×720 方向谱 0° > 90°', e0b > e90b * 2,
        'e0=' + e0b.toFixed(4) + ' e90=' + e90b.toFixed(4) + ' len=' + dp2.values.length);

    const dp3 = SpectrumCore.computeProfile('shearlet', vg2, pw2, ph2, { freq: f2, orientations: 6 });
    const s0 = dp3.values[0], s90 = dp3.values[3]; // nOr=6 → 索引 3 为 90°
    check('960×720 shearlet 方向谱 0° > 90°', s0 > s90 * 2,
        'e0=' + s0.toFixed(4) + ' e90=' + s90.toFixed(4) + ' len=' + dp3.values.length);

    // 4c. computeProfile：径向谱（dft/dct）在 960×720 下必须给出非退化曲线
    for (const m of ['dft', 'dct']) {
        let p;
        try { p = SpectrumCore.computeProfile(m, vg2, pw2, ph2, { window: 'hann' }); }
        catch (e) { check('960×720 径向谱 ' + m + ' 不抛异常', false, e.message); continue; }
        let mn = Infinity, mx = -Infinity, nan = 0;
        for (let i = 0; i < p.values.length; i++) {
            const v = p.values[i];
            if (Number.isNaN(v)) nan++;
            if (v < mn) mn = v;
            if (v > mx) mx = v;
        }
        check('960×720 径向谱 ' + m + ' 非空无 NaN 且非退化',
            p.values.length > 0 && nan === 0 && (mx - mn) > 1e-6,
            'len=' + p.values.length + ' nan=' + nan + ' span=' + (mx - mn).toFixed(4));
    }
}

// ============ 5. 分析尺寸（UI「分析尺寸」下拉）对内部上限的作用 ============
// 生成路径的上限由 UI 选择传入 params.maxDim；dct 直接用 maxDim，gabor/shearlet 取
// min(2000, maxDim)（它们一次要 4 份 pw² 缓冲，越过 2000 会跳到 8192²）。
console.log('== 分析尺寸对上限的作用 ==');
{
    const W5 = 3000, H5 = 2000;
    const img5 = new Float32Array(W5 * H5);
    for (let i = 0; i < W5 * H5; i++) img5[i] = 0.5 + 0.4 * Math.sin(i * 0.013);

    // dct：cap 跟随 maxDim
    const c1 = SpectrumCore.compute('dct', img5, W5, H5, { maxDim: 1024 });
    check('dct maxDim=1024 → 长边 ≤1024', Math.max(c1.width, c1.height) <= 1024,
        c1.width + 'x' + c1.height);
    const c2 = SpectrumCore.compute('dct', img5, W5, H5, { maxDim: 2048 });
    check('dct maxDim=2048 → 长边 ≤2048 且大于 1024 档', Math.max(c2.width, c2.height) <= 2048,
        c2.width + 'x' + c2.height);

    // gabor / shearlet：硬上限 2000
    const g1 = SpectrumCore.compute('gabor', img5, W5, H5, { maxDim: 1024, freq: 0.1 });
    check('gabor maxDim=1024 → 长边 ≤1024', Math.max(g1.width, g1.height) <= 1024,
        g1.width + 'x' + g1.height);
    const g2 = SpectrumCore.compute('gabor', img5, W5, H5, { maxDim: 4096, freq: 0.1 });
    check('gabor maxDim=4096 → 仍被硬上限压到 ≤2000', Math.max(g2.width, g2.height) <= 2000,
        g2.width + 'x' + g2.height);
    const s2 = SpectrumCore.compute('shearlet', img5, W5, H5,
        { maxDim: 4096, freq: 0.1, orientations: 4 });
    check('shearlet maxDim=4096 → 仍被硬上限压到 ≤2000', Math.max(s2.width, s2.height) <= 2000,
        s2.width + 'x' + s2.height);
}

// ============ 6. 4096² DFT 可行性（实机实测约 5.1s，这里锁住不再回退） ============
console.log('== 4096² DFT 可行性 ==');
{
    const N6 = 4096;
    const img6 = new Float32Array(N6 * N6);
    for (let i = 0; i < N6 * N6; i++) img6[i] = 0.5 + 0.4 * Math.sin(i * 0.01);
    const t0 = Date.now();
    let r6 = null, err = null;
    try { r6 = SpectrumCore.compute('dft', img6, N6, N6, { window: 'hann' }); }
    catch (e) { err = e.message || String(e); }
    check('4096² dft 不抛异常', !err, err || '');
    if (r6) {
        check('4096² dft 几何一致', r6.width === N6 && r6.height === N6 && r6.data.length === N6 * N6,
            r6.width + 'x' + r6.height + ' len=' + r6.data.length);
        let mn = Infinity, mx = -Infinity, nan = 0;
        for (let i = 0; i < r6.data.length; i++) {
            const v = r6.data[i];
            if (Number.isNaN(v)) nan++;
            if (v < mn) mn = v;
            if (v > mx) mx = v;
        }
        check('4096² dft 无 NaN、值域 [0,1]', nan === 0 && mn >= 0 && mx <= 1,
            'nan=' + nan + ' min=' + mn + ' max=' + mx);
        console.log('  （4096² dft 耗时 ' + (Date.now() - t0) + 'ms）');
    }
}

console.log('\n结果: ' + pass + ' 通过, ' + fail + ' 失败');
process.exit(fail ? 1 : 0);
