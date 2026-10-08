/*!
 * mix_core.js — 灰度图加权融合运算（纯 JS，Node/UXP 双兼容）
 * a、b 为 Float32Array（0-1，长度相同），w 为比例 [0,1]。
 * 每个运算 w=0 时结果=纯 A，w=1 时结果=纯运算结果（比例可感知）。
 * 结果统一 min-max 归一化到 [0,1]。
 */
(function (root, factory) {
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = factory();
    } else {
        root.MixCore = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    // 运算清单（前端下拉与后端校验共用语义）
    var OPS = [
        { key: 'average',    label: '加权平均 +' },
        { key: 'multiply',   label: '乘法 ×' },
        { key: 'rms',        label: '平方平均 RMS' },
        { key: 'screen',     label: '滤色 Screen' },
        { key: 'difference', label: '差值 Difference' },
        { key: 'max',        label: '最大值 Max' },
        { key: 'min',        label: '最小值 Min' },
        { key: 'overlay',    label: '叠加 Overlay' },
    ];

    function clip(x) {
        return x < 0 ? 0 : (x > 1 ? 1 : x);
    }

    // 每个运算的"纯运算结果" R = op(a, b)（不依赖 w）
    function opResult(op, a, b, i) {
        var x = a[i], y = b[i];
        switch (op) {
            case 'average':    return 0.5 * x + 0.5 * y;
            case 'multiply':   return x * y;
            case 'rms':        return Math.sqrt((x * x + y * y) / 2);
            case 'screen':     return 1 - (1 - x) * (1 - y);
            case 'difference': return Math.abs(x - y);
            case 'max':        return x > y ? x : y;
            case 'min':        return x < y ? x : y;
            case 'overlay':    return x < 0.5 ? 2 * x * y : 1 - 2 * (1 - x) * (1 - y);
            default:           throw new Error('unknown op: ' + op);
        }
    }

    // 加权合成：average 为双向加权平均（w=0→B, w=1→A）；
    // 其余 w=0→A，w=1→R=op(a,b)；乘法/滤色用幂域自然插值，其余线性插值——w 全程可感知且连续。
    function weighted(op, a, b, w, i) {
        var x = clip(a[i]), y = clip(b[i]);
        if (op === 'average') return w * x + (1 - w) * y;
        if (w <= 0) return x;
        if (w >= 1) return opResult(op, a, b, i);
        switch (op) {
            case 'multiply':
                // 对数域几何插值：a^w · b^(1-w)
                if (x <= 1e-6) return 0;
                if (y <= 1e-6) return 0;
                return Math.exp(w * Math.log(x) + (1 - w) * Math.log(y));
            case 'rms':
                return Math.sqrt(w * x * x + (1 - w) * y * y);
            case 'screen':
                // 反域几何插值
                return 1 - Math.exp(w * Math.log(1 - x) + (1 - w) * Math.log(1 - y));
            default:
                // 线性插值到纯运算结果
                return w * opResult(op, a, b, i) + (1 - w) * x;
        }
    }

    /**
     * 未归一化的加权混合（测试与精确公式验证用）。
     */
    function mixRaw(a, b, op, w) {
        if (a.length !== b.length) throw new Error('两张通道尺寸不一致');
        w = Math.max(0, Math.min(1, Number(w) || 0));
        var n = a.length;
        var out = new Float32Array(n);
        for (var i = 0; i < n; i++) out[i] = weighted(op, a, b, w, i);
        return out;
    }

    /**
     * 混合两个灰度图。
     * @param {Float32Array} a 灰度图 A（0-1）
     * @param {Float32Array} b 灰度图 B（0-1，同长度）
     * @param {string} op 运算 key
     * @param {number} w 比例 [0,1]（A 的权重；average 时 = w·A+(1-w)·B）
     * @returns {{data: Float32Array, min: number, max: number, applied: string}}
     */
    function mix(a, b, op, w) {
        var raw = mixRaw(a, b, op, w);
        var n = raw.length;
        // min-max 归一化（避免除以 0）
        var mn = 1e30, mx = -1e30;
        for (var j = 0; j < n; j++) {
            if (raw[j] < mn) mn = raw[j];
            if (raw[j] > mx) mx = raw[j];
        }
        if (mx - mn > 1e-9) {
            var scale = 1 / (mx - mn);
            for (var k = 0; k < n; k++) raw[k] = (raw[k] - mn) * scale;
        } else {
            raw.fill(0.5);
        }
        return { data: raw, min: mn, max: mx, applied: op };
    }

    return { OPS: OPS, mix: mix, mixRaw: mixRaw };
});
